#!/bin/sh
# Builds dist/fwmanager.<version>.deb and dist/SHA256SUMS from this source tree.
#
#   packaging/deb/build-deb.sh [--out DIR]
#
# Needs dpkg-deb and a Python 3 with PyGObject and GdkPixbuf (for the PNG icons). No root, no
# signing key: the .deb is installed with  sudo apt install ./fwmanager.<version>.deb
set -eu

REPO=$(cd "$(dirname "$0")/../.." && pwd)
HERE="$REPO/packaging/deb"
OUT="$REPO/dist"

die() { echo "build-deb: $1" >&2; exit 1; }
while [ $# -gt 0 ]; do
    case "$1" in
        --out) [ $# -ge 2 ] || die "--out needs a value"; OUT=$2; shift 2 ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found"

PYTHON=${PYTHON:-}
if [ -z "$PYTHON" ]; then
    for cand in python3 /usr/bin/python3 /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3.11; do
        if command -v "$cand" >/dev/null 2>&1 \
            && "$cand" -I -c 'import gi; gi.require_version("GdkPixbuf", "2.0")' 2>/dev/null; then
            PYTHON=$cand
            break
        fi
    done
fi
[ -n "$PYTHON" ] || die "no Python with PyGObject and GdkPixbuf found"

VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$REPO/pyproject.toml" | head -n 1)
[ -n "$VERSION" ] || die "no version in pyproject.toml"
# Reproducible: every file gets the date of the last commit (or now, outside git).
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$REPO" log -1 --format=%ct 2>/dev/null || date +%s)}
export SOURCE_DATE_EPOCH

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PKG="$WORK/fwmanager"
umask 022

# -- the program ---
LIB="$PKG/usr/lib/fwmanager"
install -d "$LIB" "$PKG/usr/bin" "$PKG/usr/libexec/fwmanager"
cp -R "$REPO/src/fwmanager" "$LIB/fwmanager"
find "$LIB" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$LIB" -name '*.pyc' -delete
# Installed under /usr, not /usr/local: every path in the code follows PREFIX.
sed -i 's|^PREFIX = "/usr/local"$|PREFIX = "/usr"|' "$LIB/fwmanager/paths.py"
LOCALEDIR=/usr/share/fwmanager/locale
sed -i "s|^LOCALEDIR = \"/usr/share/locale\"\$|LOCALEDIR = \"$LOCALEDIR\"|" "$LIB/fwmanager/paths.py"
grep -qx 'PREFIX = "/usr"' "$LIB/fwmanager/paths.py" || die "could not set PREFIX in paths.py"
grep -qx "LOCALEDIR = \"$LOCALEDIR\"" "$LIB/fwmanager/paths.py" || die "could not set LOCALEDIR"

# The distribution's own Python (the one python3-gi is built for); another python3 earlier on
# a customised system is skipped.
write_wrapper() {  # path module
    cat > "$1" <<WRAP
#!/bin/sh
for py in /usr/bin/python3 /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3.11; do
    [ -x "\$py" ] && "\$py" -I -c 'import gi' 2>/dev/null && break
done
exec "\$py" -I -c 'import sys; sys.path.insert(0, "/usr/lib/fwmanager"); from $2 import main; sys.exit(main())' "\$@"
WRAP
    chmod 755 "$1"
}
write_wrapper "$PKG/usr/bin/fwmanagerd" fwmanager.daemon.main
write_wrapper "$PKG/usr/bin/fwmanager-cli" fwmanager.cli.main
write_wrapper "$PKG/usr/bin/fwmanager" fwmanager.ui.main
write_wrapper "$PKG/usr/libexec/fwmanager/fwmanager-setup" fwmanager.setup_helper
install -m 755 "$HERE/uninstall" "$PKG/usr/libexec/fwmanager/uninstall"

# -- system integration, at the places packages use (/usr; /etc is left to the administrator) ---
to_usr() { sed 's|/usr/local/|/usr/|g' "$1"; }
install -d "$PKG/usr/share/dbus-1/system.d" "$PKG/usr/lib/systemd/system" \
    "$PKG/usr/share/polkit-1/actions" "$PKG/usr/lib/NetworkManager/dispatcher.d" \
    "$PKG/usr/share/applications"
install -m 644 "$REPO/data/dbus/org.fwmanager.Control.conf" "$PKG/usr/share/dbus-1/system.d/"
to_usr "$REPO/data/systemd/fwmanagerd.service" > "$PKG/usr/lib/systemd/system/fwmanagerd.service"
to_usr "$REPO/data/polkit/org.fwmanager.setup.policy" \
    > "$PKG/usr/share/polkit-1/actions/org.fwmanager.setup.policy"
to_usr "$REPO/data/networkmanager/90-fwmanager" \
    > "$PKG/usr/lib/NetworkManager/dispatcher.d/90-fwmanager"
chmod 755 "$PKG/usr/lib/NetworkManager/dispatcher.d/90-fwmanager"
install -m 644 "$REPO/data/applications/org.fwmanager.App.desktop" "$PKG/usr/share/applications/"
if grep -rq /usr/local "$PKG/usr/share" "$PKG/usr/lib/systemd" "$PKG/usr/lib/NetworkManager"; then
    die "a /usr/local path is left in the system files"
fi

# -- translations ---
"$PYTHON" -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from fwmanager.i18n_build import main; sys.exit(main(["compile", sys.argv[2], sys.argv[3]]))' \
    "$REPO/src" "$REPO/po" "$WORK/locale" >/dev/null
for mo in "$WORK"/locale/*/LC_MESSAGES/fwmanager.mo; do
    lang=$(basename "$(dirname "$(dirname "$mo")")")
    install -d "$PKG$LOCALEDIR/$lang/LC_MESSAGES"
    install -m 644 "$mo" "$PKG$LOCALEDIR/$lang/LC_MESSAGES/"
done

# -- the icon: SVG, symbolic, PNG sizes and a copy for old menus ---
HICOLOR="$PKG/usr/share/icons/hicolor"
install -d "$HICOLOR/scalable/apps" "$HICOLOR/symbolic/apps" "$PKG/usr/share/pixmaps"
install -m 644 "$REPO/data/icons/org.fwmanager.App.svg" "$HICOLOR/scalable/apps/"
install -m 644 "$REPO/data/icons/org.fwmanager.App-symbolic.svg" "$HICOLOR/symbolic/apps/"
"$PYTHON" -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from fwmanager.icon_build import main; sys.exit(main(sys.argv[2:]))' \
    "$REPO/src" "$REPO/data/icons/org.fwmanager.App.svg" "$WORK/icons" org.fwmanager.App
for png in "$WORK"/icons/*/apps/org.fwmanager.App.png; do
    size=$(basename "$(dirname "$(dirname "$png")")")
    install -d "$HICOLOR/$size/apps"
    install -m 644 "$png" "$HICOLOR/$size/apps/"
done
[ -f "$HICOLOR/128x128/apps/org.fwmanager.App.png" ] || die "the PNG icons were not made"
install -m 644 "$HICOLOR/128x128/apps/org.fwmanager.App.png" "$PKG/usr/share/pixmaps/"

# -- documentation and licence ---
DOC="$PKG/usr/share/doc/fwmanager"
install -d "$DOC"
install -m 644 "$REPO/README.md" "$REPO/README.pt.md" "$DOC/"
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: fwmanager"
    echo "Source: https://github.com/Tomascody-web333/fwmanager"
    echo
    echo "Files: *"
    echo "Copyright: 2026 Tomascody-web333"
    echo "License: PolyForm-Noncommercial-1.0.0"
    sed 's/^$/./; s/^/ /' "$REPO/LICENSE"
} > "$DOC/copyright"
DATE=$(date -u -R -d "@$SOURCE_DATE_EPOCH")
printf 'fwmanager (%s) unstable; urgency=medium\n\n  * Release %s.\n\n -- Tomascody-web333 <Tomascody-web333@users.noreply.github.com>  %s\n' \
    "$VERSION" "$VERSION" "$DATE" | gzip -n -9 > "$DOC/changelog.gz"
chmod 644 "$DOC/copyright" "$DOC/changelog.gz"

# -- package metadata ---
install -d "$PKG/DEBIAN"
SIZE=$(du -s -k --exclude=DEBIAN "$PKG" | cut -f1)
sed "s/@VERSION@/$VERSION/; s/@SIZE@/$SIZE/" "$HERE/control.in" > "$PKG/DEBIAN/control"
for script in postinst prerm postrm; do
    install -m 755 "$HERE/$script" "$PKG/DEBIAN/$script"
done
(cd "$PKG" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | LC_ALL=C sort \
    | xargs md5sum) > "$PKG/DEBIAN/md5sums"
chmod 644 "$PKG/DEBIAN/control" "$PKG/DEBIAN/md5sums"

find "$PKG" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
install -d "$OUT"
DEB="fwmanager.${VERSION}.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT/$DEB" >/dev/null
(cd "$OUT" && sha256sum "$DEB" > SHA256SUMS)
echo "$OUT/$DEB"
echo "$OUT/SHA256SUMS"
