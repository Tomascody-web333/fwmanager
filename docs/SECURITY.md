# Security model

**English** · [Português](SECURITY.pt.md)

- **The window never runs as root.** `fwmanager` refuses to start as root. It talks to the
  service over D-Bus.
- **The service (`fwmanagerd`, root) accepts fixed commands only**: apply a profile by name,
  confirm, roll back, read state, and the validated profile/rule/network editors. There is no
  method that takes ufw or rule text. Every argument is validated; the only program run is `ufw`
  (and `systemctl` is never run by the service), always as an argument list with no shell.
- **Who may call it**: only members of the group `fwmanager`. The service checks the caller's
  uid and groups for every call (`GetConnectionUnixUser`), on top of the D-Bus policy.
- **Plain-language rules** are chosen from fixed sentences; blanks are lists or validated
  addresses. They are compiled into ordinary profiles, so they get the same checks.
- **Safe changes**: before each change a byte-exact snapshot of `/etc/ufw` and `/etc/default/ufw`
  is taken; unless confirmed within 30 s (or if applying fails halfway) it is restored.
- **First-run setup** is the only step that needs administrator rights. It goes through polkit
  (`pkexec`) to one helper, `/usr/local/libexec/fwmanager/fwmanager-setup`. The helper takes no
  free arguments: it adds the account that ran `pkexec` (`PKEXEC_UID`, never a command-line
  value) to the group `fwmanager` and starts the service. Authorisation is
  `auth_admin_keep`: one password, no stored credential.
- **Nothing is sent anywhere.** No network access, accounts or telemetry; logs never contain rule
  content.
- **Other firewalls are never disabled automatically.** firewalld blocks fwmanager (with an
  explanation); Docker or libvirt only produce a warning.
- **Uninstalling** restores ufw's original defaults and disables ufw only if fwmanager enabled it.

Reporting a problem: open an issue on the repository, without posting real network addresses.
