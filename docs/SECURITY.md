# Security model

- **The window never runs as root.** `fwmanager` refuses to start as root. It talks to the
  service over D-Bus.
- **The service (`fwmanagerd`, root) accepts fixed commands only**: apply a profile by name,
  confirm, roll back, read state, and the validated profile/rule/network editors. There is no
  method that takes ufw or rule text. Every argument is validated; the only program run is `ufw`
  (and `systemctl` is never run by the service), always as an argument list with no shell.
- **Who may call it**: only members of the group `fwmanager`. The service checks the caller's
  uid and groups for every call (`GetConnectionUnixUser`), on top of the D-Bus policy.
- **Export and import** (`ExportSettings`, `ImportSettings`) are fixed commands too. An
  imported file is parsed as JSON only (nothing is executed), limited to 512 KiB, and checked
  field by field with the same rules as the editors one by one: profile names must exist,
  services must be in the catalogue, networks and CIDRs must validate, rules must compile and
  stay small enough. One bad entry refuses the whole file before anything is written. The
  exported file is written readable by its owner only, because it lists your networks.
- **A network's router** (its hardware address, read from the kernel's neighbour table by the
  NetworkManager hook) is validated as a MAC address before it reaches the service. A
  remembered network seen behind another router gets the strict profile, not its own; only
  the person, from the window or `fwmanager-cli networks trust-router`, can accept the new one.
  A MAC address is not secret, so this raises the bar rather than closing the door.
- **Notifications with the window closed** run as the person, never as root: `fwmanager
  --background` is started at login by an entry in `~/.config/autostart`, and is removed when
  fwmanager is uninstalled or the switch is turned off.
- **Plain-language rules** are chosen from fixed sentences; blanks are lists or validated
  addresses. They are compiled into ordinary profiles, so they get the same checks.
- **Safe changes**: before each change a byte-exact snapshot of `/etc/ufw` and `/etc/default/ufw`
  is taken; unless confirmed within 30 s (or if applying fails halfway) it is restored.
- **First-run setup** is the only step that needs administrator rights. It goes through polkit
  (`pkexec`) to one helper, `/usr/libexec/fwmanager/fwmanager-setup` (under `/usr/local` for
  the development install). The helper takes no
  free arguments: it adds the account that ran `pkexec` (`PKEXEC_UID`, never a command-line
  value) to the group `fwmanager` and starts the service. Authorisation is
  `auth_admin_keep`: one password, no stored credential.
- **Programs that run as root never import from the current directory.** The installed
  launchers start Python in isolated mode (`-I`), so a file such as `json.py` left in `/tmp` is
  not run when an administrator types `sudo fwmanager-cli` there.
- **Text from outside is shown as typed.** Wi-Fi names come from any access point in range; the
  window never reads them (or any other text) as markup.
- **The “recent blocked attempts” list comes from the kernel.** It is read from the kernel's own
  journal, which users cannot write to; `/var/log/ufw.log`, where anyone can add a look-alike
  line with `logger`, is only a fallback, and only lines tagged as kernel lines are shown.
- **State files are private.** `/var/lib/fwmanager` is readable by root only (it lists the
  networks you use).
- **The service writes only what ufw needs**: `/etc/ufw`, the single file `/etc/default/ufw`
  (not the rest of `/etc/default`), its lock in `/run`, and its own `/var/lib/fwmanager`.
- **Size limits.** A profile may need at most 40 ufw rules, so applying one takes seconds, not
  minutes during which the service would answer nobody.
- **Uninstalling** (Settings button or `fwmanager-cli uninstall`) goes through the same helper
  with a second polkit action, `org.fwmanager.uninstall`, told apart by the helper's first
  argument. It asks for the administrator password every time (`auth_admin`, nothing kept) and
  runs only the installed uninstaller (in the package, `apt-get purge fwmanager`).
- **Packages are signed.** The PPA upload and the APT repository on GitHub Pages are signed
  with the key `413E F57C A7FE 5D37 6108 0D61 D519 2575 2DA1 7E4A`; apt refuses an index that
  does not verify. A `.deb` downloaded from Releases is checked with `SHA256SUMS`.
- **Nothing is sent anywhere.** No network access, accounts or telemetry; logs never contain rule
  content.
- **Other firewalls are never disabled automatically.** firewalld blocks fwmanager (with an
  explanation); Docker or libvirt only produce a warning.
- **Uninstalling** restores ufw's original defaults and disables ufw only if fwmanager enabled it.

Reporting a problem: open an issue on the repository, without posting real network addresses.
