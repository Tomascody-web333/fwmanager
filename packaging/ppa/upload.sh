#!/bin/sh
# Builds the source package (debian/) once per Ubuntu release, signs it and uploads it to a
# Launchpad PPA, which then builds the .deb itself.
#
#   packaging/ppa/upload.sh PPA KEY [--no-upload]
#
# PPA is "launchpad-user/ppa-name" (as in ppa:launchpad-user/ppa-name). KEY is the GPG key
# registered in that Launchpad account; a passphrase, if any, is read from APT_GPG_PASSPHRASE.
#
# The releases are every Ubuntu from 24.04 on that Launchpad still builds for, asked from
# api.launchpad.net. LAUNCHPAD_SERIES ("noble=24.04 resolute=26.04") chooses them instead.
# Each one gets its own version, VERSION~ubuntuNN.NN.1, so a newer Ubuntu sorts higher and
# Launchpad never sees the same version twice.
#
# --no-upload stops after signing (the files are left in the printed folder).
# Needs dpkg-dev, debhelper, gpg, python3 and, to upload, dput.
set -eu

die() { echo "ppa-upload: $1" >&2; exit 1; }
[ $# -ge 2 ] || die "usage: upload.sh PPA KEY [--no-upload]"
PPA=$1
KEY=$2
UPLOAD=1
[ "${3:-}" = "--no-upload" ] && UPLOAD=0
case "$PPA" in
    */*) ;;
    *) die "PPA must look like launchpad-user/ppa-name" ;;
esac

REPO=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$(sed -n '1s/^fwmanager (\([^)]*\)).*/\1/p' "$REPO/debian/changelog")
[ -n "$VERSION" ] || die "no version in debian/changelog"
gpg --batch --list-secret-keys "$KEY" >/dev/null 2>&1 || die "no secret key $KEY in the keyring"
command -v dpkg-buildpackage >/dev/null 2>&1 || die "dpkg-buildpackage not found (apt install dpkg-dev)"
[ "$UPLOAD" -eq 0 ] || command -v dput >/dev/null 2>&1 || die "dput not found (apt install dput)"

# -- which releases ---
if [ -n "${LAUNCHPAD_SERIES:-}" ]; then
    SERIES=$(printf '%s\n' $LAUNCHPAD_SERIES | tr '=' ' ')
else
    SERIES=$(curl -fsS "https://api.launchpad.net/devel/ubuntu/series?ws.size=100" \
        | python3 "$REPO/packaging/ppa/series.py") || die "could not ask Launchpad for the Ubuntu releases"
fi
[ -n "$SERIES" ] || die "no Ubuntu release to build for"
for pair in $(echo "$SERIES" | tr ' ' '='); do
    name=${pair%%=*}
    number=${pair#*=}
    case "$name" in *[!a-z]*|"") die "bad release name: $name" ;; esac
    case "$number" in *[!0-9.]*|"") die "bad release number for $name: $number" ;; esac
done

WORK=$(mktemp -d)
# Sign without a password window: the passphrase comes from the environment.
GPG_WRAPPER="$WORK/gpg"
if [ -n "${APT_GPG_PASSPHRASE:-}" ]; then
    printf '%s' "$APT_GPG_PASSPHRASE" > "$WORK/pass"
    chmod 600 "$WORK/pass"
    printf '#!/bin/sh\nexec gpg --batch --pinentry-mode loopback --passphrase-file "%s" "$@"\n' \
        "$WORK/pass" > "$GPG_WRAPPER"
else
    printf '#!/bin/sh\nexec gpg --batch "$@"\n' > "$GPG_WRAPPER"
fi
chmod 700 "$GPG_WRAPPER"
trap 'rm -f "$WORK/pass"' EXIT

CHANGES_FILES=""
for pair in $(echo "$SERIES" | tr ' ' '='); do
    name=${pair%%=*}
    number=${pair#*=}
    version="$VERSION~ubuntu$number.1"
    SRC="$WORK/$name/fwmanager-$version"
    mkdir -p "$SRC"
    # Exactly what is in git (no virtualenv, caches or build output).
    git -C "$REPO" ls-files -z --cached --others --exclude-standard \
        | (cd "$REPO" && xargs -0 tar -cf - --no-recursion) | tar -xf - -C "$SRC"
    sed -i "1s/^fwmanager ([^)]*) [a-z]*;/fwmanager ($version) $name;/" "$SRC/debian/changelog"
    head -n 1 "$SRC/debian/changelog" | grep -q "($version) $name;" || die "could not set $name in debian/changelog"
    (cd "$SRC" && dpkg-buildpackage -S -d -sa --sign-command="$GPG_WRAPPER" --sign-key="$KEY") \
        >"$WORK/$name/build.log" 2>&1 \
        || { cat "$WORK/$name/build.log" >&2; die "building the source package for $name failed"; }
    changes="$WORK/$name/fwmanager_${version}_source.changes"
    [ -f "$changes" ] || die "no $changes"
    CHANGES_FILES="$CHANGES_FILES $changes"
done
rm -f "$WORK/pass"

if [ "$UPLOAD" -eq 1 ]; then
    for changes in $CHANGES_FILES; do
        # -u: the files were signed a moment ago, and Launchpad checks the signature itself.
        dput -u "ppa:$PPA" "$changes"
    done
    rm -rf "$WORK"
    echo "Uploaded fwmanager $VERSION to ppa:$PPA for: $(echo "$SERIES" | cut -d' ' -f1 | tr '\n' ' ')"
    echo "Launchpad e-mails when each one is built."
else
    echo "$WORK"
fi
