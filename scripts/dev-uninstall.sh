#!/bin/sh
# Removes everything dev-install.sh put in place, including the `inet fwmanager` table,
# so the machine is left with working networking. Run with sudo.
#
#   sudo scripts/dev-uninstall.sh [--no-systemd]
#
# --root DIR   (tests only) remove from DIR and skip group/systemd/nft steps.
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

if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl disable --now fwmanagerd.service 2>/dev/null || true
fi

# Drop our firewall table first: with a `policy drop` input chain left behind and nothing
# managing it, the machine would stay locked down. Other tables are never touched.
if [ "$STAGED" -eq 0 ] && command -v nft >/dev/null 2>&1; then
    nft delete table inet fwmanager 2>/dev/null || true
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

if [ "$STAGED" -eq 0 ] && [ "$CREATED_GROUP" -eq 1 ]; then
    groupdel "$GROUP" 2>/dev/null || echo "Could not remove group $GROUP (still in use?)."
fi

if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl daemon-reload
    systemctl reset-failed fwmanagerd.service 2>/dev/null || true
fi
echo "fwmanager removed."
