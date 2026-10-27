# fwmanager

A beginner-friendly graphical manager for **ufw**, the Linux firewall. Pick a profile
(`casa`, `trabalho`, `publico`), let the app switch profile by itself when you change Wi-Fi,
and rely on a safety net that undoes any change that cuts your connection.

100% local: no accounts, no servers, no telemetry.

> Status: work in progress. The core, the daemon, the NetworkManager hook, the command-line
> client and the GTK4 interface are done; the plain-language rules come next.

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
- **The daemon never accepts rule text.** Clients can only call fixed commands (apply a
  profile by name, confirm, roll back, read state); every argument is validated, and the
  only thing run is `ufw` with an argument list (no shell). Callers must be in the
  `fwmanager` group.
- ufw cannot turn off ping or mDNS from its command line, so those per-profile switches are
  a small, clearly delimited block that fwmanager keeps in `/etc/ufw/before.rules` (and
  `before6.rules`). It is removed on uninstall.

Requirements: Python 3.11+, PyGObject, **ufw**, systemd and D-Bus. NetworkManager is optional
(needed only for automatic per-network profiles).

## The interface

Run `fwmanager` as your normal user (it refuses to run as root). Pages: **Home** (state, quick
profile switching, a "waiting for confirmation" card with a countdown), **Profiles** (edit what
each profile opens), **Networks** (which profile for which Wi-Fi), **Rules** (plain-language sentences, below), **Log** (what the service did
and recent blocked attempts) and **Settings**. Every profile has a **Test** button that runs the
policy test below before you apply anything. Ctrl+1..6 jump between pages, and the page switcher
moves to the bottom on narrow windows.

If the service is not running, your account is not in the `fwmanager` group, ufw is missing or
firewalld is active, the window explains it in your language and shows the command that fixes it.

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
sudo apt install ufw                                       # fwmanager drives ufw; install it first
sudo scripts/dev-install.sh --user YOURNAME --lang pt      # or --lang en
sudo scripts/dev-uninstall.sh
```

`dev-install.sh` copies the files, creates the `fwmanager` group, adds your user (and root,
for the NetworkManager hook) and runs `systemctl enable --now fwmanagerd`. Log out and back
in so the group applies. `--no-systemd` skips the systemd step.

`dev-uninstall.sh` removes the files, fwmanager's ufw rules and its block in `before.rules`,
and switches ufw off again **only if fwmanager was the one that turned it on**, so the
machine is never left filtering with nobody managing it. A ufw you enabled yourself stays on.

> **Warning:** on first start with no saved state the daemon applies `publico` (nothing
> incoming is allowed). If you install over SSH, confirm a profile that allows SSH first
> (`fwmanager-cli apply casa`, then `confirm`), or you will not be able to reconnect.

At boot the daemon re-applies the last confirmed profile with no timer. If `firewalld` is
active it applies nothing and the interface explains why.

## Testing a policy before you trust it

```sh
fwmanager-cli test-policy casa --static   # analyse only, changes nothing
fwmanager-cli test-policy casa            # analyse + live test (applied for seconds, then reverted)
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

## Known limits

- ufw cannot apply a profile atomically: there is a split second between removing the old
  rules and adding the new ones, always in the safe direction (closing before opening).
  If a change fails halfway it is undone automatically.
- IPv6 behaviour is covered by checks on ufw's configuration, but a real IPv6 test needs a
  host with IPv6 enabled.
- The systemd unit is relaxed from the minimal set (`CAP_NET_RAW`, `AF_INET`, write access to
  `/etc/ufw`) because ufw and iptables need it.
