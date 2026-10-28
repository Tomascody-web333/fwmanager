# fwmanager

**English** · [Português](README.pt.md)

A beginner-friendly graphical interface for **ufw**. It is not a firewall of its own: it only
makes ufw easier to use. Pick a profile (**Home**, **Work**, **Public**), let the app switch
profile by itself when you change Wi-Fi, and rely on a safety net that undoes any change that
cuts your connection.

100% local: no accounts, no servers, no telemetry.

> Status: feature complete for the development install; packaging (apt) comes last. The parts
> that need a real machine are listed in [docs/VALIDATION.md](docs/VALIDATION.md).

## Quick start

```sh
sudo apt install ufw python3-gi gir1.2-gtk-4.0 gir1.2-adw-1   # Ubuntu 24.04 / Debian 13
sudo scripts/dev-install.sh                                   # installs and stops there
fwmanager                                                     # everything else is set up in the window
```

Installing only copies files. The first time you open **fwmanager** a short wizard checks the
system (service running, your account allowed, ufw installed, no other firewall manager in the
way) and fixes what is missing with **one** administrator password prompt (polkit). After that
it never asks again, and you choose how this computer should behave on the current network.
Nothing needs a terminal. If the password window is not available, the wizard shows the exact
commands to run instead.

Prefer the terminal? `fwmanager-cli setup` does the same checks, asks the same single password
and lets you pick the profile there. Setup is recorded for the whole machine, so whichever you
use first, the other has nothing left to ask.

**Uninstalling** works the same way: **Settings → Uninstall fwmanager…** or
`fwmanager-cli uninstall`. Either one removes the window, the command line and the service, and
leaves the firewall as it was before fwmanager (its own rules and settings removed, ufw's
defaults restored, ufw switched off only if fwmanager had switched it on, your own ufw rules
kept). It asks for an administrator password every time.

The interface needs GTK 4 and libadwaita 1.5 or newer (Ubuntu 24.04, Fedora 40 or later);
`fwmanager-cli` works on older systems.

## How it works

```
fwmanager (GTK4 UI, normal user)  ─┐
fwmanager-cli (normal user / root) ─┼─ D-Bus ─►  fwmanagerd (root)  ─►  ufw  ─►  iptables/nftables
NetworkManager dispatcher (root)  ─┘            only fixed commands
```

- **Profiles** are sets of ufw rules tagged with the comment `fwmanager:<profile>`. Switching
  profile adds and removes only those rules. **Your own ufw rules are never touched.**
- **Safe changes**: every change keeps a copy of ufw's configuration and starts a 30 s timer.
  If you do not confirm, the previous configuration is restored byte for byte.
- **Per-network profiles**: the NetworkManager dispatcher applies the profile mapped to the
  network that just connected, then checks the gateway is still reachable; if not, it reverts.
  A Wi-Fi or mobile network with no profile gets **Public**; a cable with no profile keeps the
  current one, so plugging in never locks out an SSH session.
- **The daemon never accepts rule text.** Clients can only call fixed commands (apply a
  profile by name, confirm, roll back, read state); every argument is validated, and the
  only thing run is `ufw` with an argument list (no shell). Callers must be in the
  `fwmanager` group.
- ufw cannot turn off ping or mDNS from its command line, so those per-profile switches are
  a small, clearly delimited block that fwmanager keeps in `/etc/ufw/before.rules` (and
  `before6.rules`). It is removed on uninstall.

Requirements: Python 3.11+, PyGObject, **ufw**, systemd, D-Bus, polkit (first-run setup) and, for
the window, GTK 4 + libadwaita 1.5+. NetworkManager is optional (needed only for automatic
per-network profiles).

## The interface

Run `fwmanager` as your normal user (it refuses to run as root). On the first run the setup
wizard appears (see above); it can be skipped and is never shown again once finished. Five pages:

- **Overview**: one sentence on how protected the computer is and what that means ("Lets
  nothing in. Hidden on the network."), the current network, and the profiles. Each profile
  says in plain words what it lets in, and has **Use**, **Test** (the policy test below) and an
  edit button. After a switch a card asks "Is your connection still working?" with a countdown.
- **Networks**: which profile for which Wi-Fi.
- **Rules**: plain-language sentences (below).
- **Log**: what the service did, and recent blocked attempts.
- **Settings**: language; **notifications**, one switch per kind (a change undone, the profile
  changing by itself on another network, blocked attempts, connections let in — the last two
  turn ufw's recording on at the level they need and are summed up at most once a minute);
  recording level; router mode (also filter traffic this computer passes on to a hotspot, VMs
  or Docker); and Uninstall.

Ctrl+1..5 jump between pages, and the page switcher moves to the bottom on narrow windows.

If the service is not running, your account is not in the `fwmanager` group, ufw is missing or
firewalld is active, the window explains it in your language and offers a **Fix this** button
(or the command to run, when a password window is not possible).

Being added to the group only applies to new processes, so after the one-time setup fwmanager
restarts itself under the new group (`sg fwmanager`). If that is not possible it asks you to log
out and back in.

## Plain-language rules

Instead of ports and sources, the **Rules** page offers fixed sentences with blanks:

- *Allow [service] only in [profiles]*
- *Block everything coming in, except [services], in [profiles]*
- *Only accept connections from the local network / [networks] in [profiles]*
- *Hide this computer (no ping, no mDNS) in [profiles]*
- *Close [service] in every profile*

There is no free-text parsing: every blank is a list (services from the catalogue, profile names,
a scope) or a validated network address. Before saving you see the finished sentence and a preview
of what changes in each profile. Rules sit on top of the built-in profiles and your own edits,
are applied in order (a later rule wins over an earlier one), and are compiled into ordinary
profiles, so the safety checks, the 30 s confirmation and the policy test work on them unchanged.
**Saving a rule never touches the firewall**; it takes effect when you apply the profile, which is
also the moment to press **Test**. Rules are stored in `/var/lib/fwmanager/rules.json`.

## Languages

Portuguese (Portugal) and English. The language is chosen in this order: your choice in
**Settings** (`~/.config/fwmanager/settings.toml`) > `/etc/fwmanager/config.toml` > the system
locale (`pt*` gives Portuguese, anything else English). Changing it in Settings rebuilds the window
at once; it needs no password and never touches the service. The service itself always logs in
English; the interface translates its status and error codes.

Translations live in `po/*.po`; the `.mo` files are compiled by `dev-install.sh` (no gettext
tools needed). After changing user-visible text run `python3 scripts/update-po.py`, translate the
new entries, and `python3 scripts/update-po.py --check` (also run by the tests) must pass.

## Dev install (VM/container only)

```sh
sudo apt install ufw                       # fwmanager drives ufw; install it first
sudo scripts/dev-install.sh                # --user NAME and --lang pt|en are optional shortcuts
sudo scripts/dev-uninstall.sh
```

`dev-install.sh` copies the files, the polkit policy and the setup helper
(`/usr/local/libexec/fwmanager/fwmanager-setup`, the only program that asks for administrator
rights), and adds root to the `fwmanager` group for the NetworkManager hook. Creating the group,
adding your account and starting the service happen in the window, or right away if you pass
`--user NAME`. `--no-systemd` skips the systemd step. Its messages follow the system language
(Portuguese or English).

`dev-uninstall.sh` (also installed as `/usr/local/libexec/fwmanager/uninstall`, which is what the
app's Uninstall button and `fwmanager-cli uninstall` run) removes the files, fwmanager's ufw rules and its block in `before.rules`, and
switches ufw off again **only if fwmanager was the one that turned it on**, so the machine is
never left filtering with nobody managing it. A ufw you enabled yourself stays on.

> **Warning:** on first start with no saved state the daemon applies **Public** (nothing
> incoming is allowed). If you install over SSH the installer therefore leaves the service
> stopped (override with `--allow-ssh-lockout`); start it from the machine itself, or confirm a
> profile that allows SSH first (`fwmanager-cli apply home`, then `confirm`).

At boot the daemon re-applies the last confirmed profile with no timer. If `firewalld` is
active it applies nothing and the interface explains why.

## Command line

`fwmanager-cli` does what the window does, in a terminal: for servers, SSH sessions, older
systems without libadwaita 1.5, or scripts. Profiles are typed by their English names (`home`, `work`, `public`,
`custom`); the Portuguese ones work too.

```sh
fwmanager-cli setup               # first-run setup, as in the window
fwmanager-cli status              # is it on, which profile, what it lets in
fwmanager-cli profiles            # every profile, in plain words
fwmanager-cli apply home          # switch; press Enter to keep it, r to undo (30 s)
fwmanager-cli edit home --block samba --ping off   # change what a profile allows and blocks
fwmanager-cli reset home          # back to the defaults
fwmanager-cli networks list       # which profile on which network
fwmanager-cli uninstall           # remove fwmanager (asks for a yes, then the password)
```

Ctrl+C during `apply` or `test-policy` undoes the change at once. `--json` on `status` and
`networks list` gives output for scripts.

## Testing a policy before you trust it

```sh
fwmanager-cli test-policy home --static   # analyse only, changes nothing
fwmanager-cli test-policy home            # analyse + live test (applied for seconds, then reverted)
```

Exit code: `0` safe, `2` safe with warnings, `3` unsafe (would break something), `1` could not run.

- **Static** (daemon, read-only): shows which rules would be added and removed, validates each
  with `ufw --dry-run`, and checks ufw's real configuration still carries what a normal machine
  needs (replies, loopback, DHCP/DHCPv6, IPv6 neighbour discovery and PMTU). It also warns if
  your current SSH session could not reconnect under the profile, or if IPv6 is not filtered.
- **Live** (client): measures gateway (IPv4/IPv6), DNS and loopback before and after applying
  the profile, then always rolls back. Anything that worked before and fails after is reported
  as broken. If the test is killed midway, the daemon reverts by itself after the timeout.

## Development

```sh
python3 -m venv --system-site-packages .venv   # system site packages provide PyGObject
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/unit tests/security      # no privileges needed
sudo .venv/bin/pytest tests                     # everything: real ufw + D-Bus in a private netns
xvfb-run -a pytest tests/ui                     # GTK windows on a virtual display (or let the
                                                # tests start their own Xvfb)
```

Integration tests never touch the host firewall: they run ufw inside a throw-away network
namespace (`unshare -n`) with a private copy of `/etc/ufw` (`ufw --rootdir`). The real
install/uninstall test changes the machine, so it is opt-in:
`sudo FWMANAGER_TEST_REAL_INSTALL=1 .venv/bin/pytest tests/install`.

## Documentation

- [docs/VALIDATION.md](docs/VALIDATION.md): what the automated tests cover, and the checklist for
  what only a real machine can prove (systemd, NetworkManager, IPv6, DHCP, desktop, polkit).
- [docs/SECURITY.md](docs/SECURITY.md): the security model in one page.
- Every document exists in English and Portuguese (`*.pt.md`).

## Known limits

- ufw cannot apply a profile atomically: there is a split second between removing the old
  rules and adding the new ones, always in the safe direction (closing before opening).
  If a change fails halfway it is undone automatically.
- IPv6 behaviour is covered by checks on ufw's configuration, but a real IPv6 test needs a
  host with IPv6 enabled.
- Not yet exercised on a real machine: the systemd hardening, the NetworkManager hook, DHCP,
  the polkit prompt and the desktop look (see [docs/VALIDATION.md](docs/VALIDATION.md)).
- A network is recognised by its NetworkManager connection (Wi-Fi name and connection id). A
  fake access point with the same name and password is treated as the real one, so keep
  services open only on networks protected by a password you trust (WPA2/WPA3). The window
  warns when you pick Home, Work or Custom for a Wi-Fi without a password.
- The systemd unit is relaxed from the minimal set (`CAP_NET_RAW`, `AF_INET`, write access to
  `/etc/ufw`) because ufw and iptables need it.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). In short (the [license text](LICENSE) is what counts):

- You may use, study, change and share fwmanager for any **noncommercial** purpose: at home,
  for learning, in hobby projects, in schools, charities and public institutions.
- You may **not** sell it or use it commercially without the author's permission.
- Whoever passes it on must keep the license and the line
  `Required Notice: Copyright (c) 2026 Tomascody-web333`, so nobody can present the work as
  their own.

For commercial use, ask the author by opening an issue.
