#!/bin/sh
# Remove tudo o que o dev-install.sh instalou, incluindo as alterações ao ufw, para a máquina
# ficar com a rede a funcionar / Removes everything dev-install.sh put in place, including
# fwmanager's changes to ufw, so the machine is left with working networking. Run with sudo.
#
#   sudo scripts/dev-uninstall.sh [--no-systemd]
#
# --root DIR   (tests only) remove from DIR and skip group/systemd/ufw steps.
set -eu

GROUP=fwmanager
ROOT=""
USE_SYSTEMD=1
STAGED=0

# say "English" "Português"  (the language of the system picks the line)
say() {
    case "${LC_ALL:-${LANG:-}}" in
        pt*) printf '%s\n' "$2" ;;
        *)   printf '%s\n' "$1" ;;
    esac
}
die() { say "dev-uninstall: $1" "dev-uninstall: $2" >&2; exit 1; }
need_value() {
    [ $# -ge 2 ] && [ "${2#-}" = "$2" ] \
        || die "$1 needs a value" "$1 precisa de um valor"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root)       need_value "$@"; ROOT=${2%/}; STAGED=1; shift 2 ;;
        --no-systemd) USE_SYSTEMD=0; shift ;;
        -h|--help)    sed -n '2,8p' "$0"; exit 0 ;;
        *)            die "unknown argument: $1" "argumento desconhecido: $1" ;;
    esac
done

if [ "$STAGED" -eq 0 ]; then
    [ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)" "tem de correr como root (use sudo)"
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
            || say "Warning: could not clean ufw; check 'ufw status' and /etc/ufw/before.rules." \
                   "Aviso: não foi possível limpar o ufw; verifique 'ufw status' e /etc/ufw/before.rules."
    fi
fi

rm -f "$ROOT/usr/local/bin/fwmanagerd" "$ROOT/usr/local/bin/fwmanager-cli" \
      "$ROOT/usr/local/bin/fwmanager"
rm -rf "$LIB" "$ROOT/usr/local/libexec/fwmanager"
rmdir "$ROOT/usr/local/libexec" 2>/dev/null || true
rm -f "$ROOT/usr/share/polkit-1/actions/org.fwmanager.setup.policy"
rm -f "$ROOT"/usr/share/icons/hicolor/*/apps/org.fwmanager.App.png \
      "$ROOT/usr/share/icons/hicolor/scalable/apps/org.fwmanager.App.svg" \
      "$ROOT/usr/share/icons/hicolor/symbolic/apps/org.fwmanager.App-symbolic.svg" \
      "$ROOT/usr/share/pixmaps/org.fwmanager.App.png"
rm -f "$ROOT/etc/dbus-1/system.d/org.fwmanager.Control.conf" \
      "$ROOT/etc/systemd/system/fwmanagerd.service" \
      "$ROOT/etc/NetworkManager/dispatcher.d/90-fwmanager" \
      "$ROOT/usr/share/applications/org.fwmanager.App.desktop"
rm -rf "$ROOT/etc/fwmanager" "$ROOT/var/lib/fwmanager"
for mo in "$ROOT"/usr/share/locale/*/LC_MESSAGES/fwmanager.mo; do
    [ -f "$mo" ] && rm -f "$mo"
done

if [ "$STAGED" -eq 0 ] && [ "$ROOT_ADDED" -eq 1 ] && [ "$CREATED_GROUP" -eq 0 ]; then
    gpasswd -d root "$GROUP" >/dev/null 2>&1 || true
fi

if [ "$STAGED" -eq 0 ] && [ "$CREATED_GROUP" -eq 1 ]; then
    groupdel "$GROUP" 2>/dev/null \
        || say "Could not remove group $GROUP (still in use?)." \
               "Não foi possível remover o grupo $GROUP (ainda em uso?)."
fi

if [ "$STAGED" -eq 0 ]; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi

if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl daemon-reload
    systemctl reset-failed fwmanagerd.service 2>/dev/null || true
fi
say "fwmanager removed." "fwmanager removido."
