# Validation

**English** · [Português](VALIDATION.pt.md)

## What the automated tests prove

Run in a throw-away network namespace with a private copy of `/etc/ufw` (never the host's):

- real `ufw` behaviour: profiles, the managed `before.rules` block, snapshot/restore, defaults,
  cleanup on uninstall (`tests/ufw`);
- the daemon on a private D-Bus, including the group policy of a system-like bus (`tests/integration`);
- real traffic between two namespaces: what a profile allows and blocks, and that a revert
  brings it back (`tests/integration/test_traffic.py`);
- the GTK4/libadwaita windows on a virtual display (Xvfb), with real key presses (`tests/ui`),
  including the first-run wizard with a fake service;
- injection attempts against every argument the daemon accepts (`tests/security`);
- the installer and uninstaller, staged into a directory (`tests/install`); the real
  install/uninstall round trip runs with `FWMANAGER_TEST_REAL_INSTALL=1` in a VM.

## What only a real machine can prove

Do this in a VM with a desktop session, **from its console, not over SSH**. Take a snapshot first.

| # | Check | How | Expected |
|---|-------|-----|----------|
| 1 | Install | `sudo scripts/dev-install.sh` | Ends with “the setup continues in its window”; nothing is started yet |
| 2 | First-run wizard | open `fwmanager` from the menu | Wizard lists the missing items; **Fix this** shows one polkit prompt; afterwards fwmanager restarts itself and shows the profile choice |
| 3 | Polkit denied | cancel the prompt, or use a non-admin account | A clear message and the commands to run instead; nothing changed |
| 4 | systemd hardening | `systemctl status fwmanagerd`, `journalctl -u fwmanagerd` | Active, no “permission denied” on `/etc/ufw`, `/run`, netlink |
| 5 | Profile switch | apply **Home**, confirm; apply **Work**, wait 30 s | Confirmed profile stays; the unconfirmed one is undone |
| 6 | NetworkManager | connect to a second Wi-Fi mapped to another profile | The profile follows the network; the gateway stays reachable |
| 7 | DHCP | `sudo dhclient -r && sudo dhclient` under **Public** | A lease is obtained |
| 8 | IPv6 | on a host with IPv6: `ping -6`, then **Test** | Neighbour discovery works; the test reports no breakage |
| 9 | Policy test | **Test** on every profile | Safe, or warnings that match reality |
| 10 | Desktop look | light/dark theme, 200 % scale, narrow window, Orca | Readable, nothing clipped, page switcher moves to the bottom |
| 11 | Reboot | reboot | Last confirmed profile re-applied with no timer |
| 12 | Uninstall from the window | **Settings → Uninstall fwmanager…**, enter the password | Done page, the app closes; `ufw status` is back to how it was; ufw is off only if fwmanager had enabled it; no files left, no menu entry |
| 13 | Setup and uninstall from the terminal | reinstall, then `fwmanager-cli setup` (as yourself), later `fwmanager-cli uninstall` | One password for setup, profile asked in the terminal; opening the window afterwards shows no wizard; uninstall leaves nothing |
| 14 | Icon | look at the app grid, the dock and the window in the task bar (GNOME, and XFCE or KDE if you can) | The shield icon everywhere; the open window is grouped with its launcher |

Report anything that differs with the output of `journalctl -u fwmanagerd` and `ufw status verbose`.
