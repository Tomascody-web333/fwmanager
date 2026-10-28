#!/bin/sh
# Builds the source package (debian/), signs it and uploads it to a Launchpad PPA, which then
# builds the .deb itself.
#
#   packaging/ppa/upload.sh PPA KEY [--no-upload]
#
# PPA is "launchpad-user/ppa-name" (as in ppa:launchpad-user/ppa-name). KEY is the GPG key
# registered in that Launchpad account; a passphrase, if any, is read from APT_GPG_PASSPHRASE.
# --no-upload stops after signing (the files are left in the printed folder).
# Needs dpkg-dev, debhelper, gpg and, to upload, dput.
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

WORK=$(mktemp -d)
SRC="$WORK/fwmanager-$VERSION"
mkdir "$SRC"
# Exactly what is in git (no virtualenv, caches or build output).
git -C "$REPO" ls-files -z --cached --others --exclude-standard \
    | (cd "$REPO" && xargs -0 tar -cf - --no-recursion) | tar -xf - -C "$SRC"

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

(cd "$SRC" && dpkg-buildpackage -S -d -sa --sign-command="$GPG_WRAPPER" --sign-key="$KEY") \
    >"$WORK/build.log" 2>&1 || { cat "$WORK/build.log" >&2; die "building the source package failed"; }
rm -f "$WORK/pass"
CHANGES="$WORK/fwmanager_${VERSION}_source.changes"
[ -f "$CHANGES" ] || die "no $CHANGES"

if [ "$UPLOAD" -eq 1 ]; then
    dput "ppa:$PPA" "$CHANGES"
    rm -rf "$WORK"
    echo "Uploaded fwmanager $VERSION to ppa:$PPA; Launchpad e-mails when it is built."
else
    echo "$WORK"
fi
