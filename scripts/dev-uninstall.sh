#!/bin/sh
# Removes everything dev-install.sh put in place, including fwmanager's changes to ufw,
# so the machine is left with working networking. Run with sudo.
#
#   sudo scripts/dev-uninstall.sh [--no-systemd]
#
# --root DIR   (tests only) remove from DIR and skip group/systemd/ufw steps.
set -eu

GROUP=fwmanager
ROOT=""
USE_SYSTEMD=1
STAGED=0

die() { echo "dev-uninstall: $*" >&2; exit 1; }
need_value() { [ $# -ge 2 ] && [ "${2#-}" = "$2" ] || die "$1 needs a value"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --root)       need_value "$@"; ROOT=${2%/}; STAGED=1; shift 2 ;;
        --no-systemd) USE_SYSTEMD=0; shift ;;
        -h|--help)    sed -n '2,7p' "$0"; exit 0 ;;
        *)            die "unknown argument: $1" ;;
    esac
done

if [ "$STAGED" -eq 0 ]; then
    [ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
else
    USE_SYSTEMD=0
fi

LIB="$ROOT/usr/local/lib/fwmanager"
CREATED_GROUP=0
[ -f "$LIB/.created-group" ] && CREATED_GROUP=1
ROOT_ADDED=0
[ -f "$LIB/.root-in-group" ] && ROOT_ADDED=1

if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl disable --now fwmanagerd.service 2>/dev/null || true
fi

# Undo our changes to ufw first: our rules, our managed block in before.rules and, if
# fwmanager is the one that switched ufw on, ufw itself. Otherwise the machine could stay
# locked down with nobody managing it. The user's own ufw rules are never touched.
if [ "$STAGED" -eq 0 ]; then
    if [ -x /usr/local/bin/fwmanagerd ] && command -v ufw >/dev/null 2>&1; then
        /usr/local/bin/fwmanagerd --cleanup \
            || echo "Warning: could not clean ufw; check 'ufw status' and /etc/ufw/before.rules."
    fi
fi

rm -f "$ROOT/usr/local/bin/fwmanagerd" "$ROOT/usr/local/bin/fwmanager-cli" \
      "$ROOT/usr/local/bin/fwmanager"
rm -rf "$LIB"
rm -f "$ROOT/etc/dbus-1/system.d/org.fwmanager.Control.conf" \
      "$ROOT/etc/systemd/system/fwmanagerd.service" \
      "$ROOT/etc/NetworkManager/dispatcher.d/90-fwmanager"
rm -rf "$ROOT/etc/fwmanager" "$ROOT/var/lib/fwmanager"
for mo in "$ROOT"/usr/share/locale/*/LC_MESSAGES/fwmanager.mo; do
    [ -f "$mo" ] && rm -f "$mo"
done

if [ "$STAGED" -eq 0 ] && [ "$ROOT_ADDED" -eq 1 ] && [ "$CREATED_GROUP" -eq 0 ]; then
    gpasswd -d root "$GROUP" >/dev/null 2>&1 || true
fi

if [ "$STAGED" -eq 0 ] && [ "$CREATED_GROUP" -eq 1 ]; then
    groupdel "$GROUP" 2>/dev/null || echo "Could not remove group $GROUP (still in use?)."
fi

if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl daemon-reload
    systemctl reset-failed fwmanagerd.service 2>/dev/null || true
fi
echo "fwmanager removed."
