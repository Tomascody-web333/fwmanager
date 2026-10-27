# fwmanager

Modern nftables firewall manager with profiles per network (work in progress).

Status: phase 1 (core + CLI). Full documentation (PT/EN) comes in phase 7.

## Development (phase 1)

```sh
python3 -m venv --system-site-packages .venv   # system site packages provide PyGObject
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/unit tests/security      # no privileges needed
sudo .venv/bin/pytest tests/integration         # real nft, inside a private network namespace
```

Integration tests never touch the host firewall: they run `nft` inside a throw-away
network namespace created with `unshare -n` and entered with `nsenter`.

## Dev install (phase 3, VM/container only)

```sh
sudo scripts/dev-install.sh --user YOURNAME --lang pt   # or --lang en
sudo scripts/dev-uninstall.sh                           # also removes the inet fwmanager table
```

`dev-install.sh` copies the files, creates the `fwmanager` group, adds the user to it and
runs `systemctl enable --now fwmanagerd`. Log out and back in so the group applies.
`--no-systemd` skips the systemd step. Both scripts never touch tables other than
`inet fwmanager`.

Real install tests change the machine, so they are opt-in:
`sudo FWMANAGER_TEST_REAL_INSTALL=1 .venv/bin/pytest tests/install`.

At boot the daemon re-applies the last confirmed profile (no timer). With no saved state it
applies `publico`. If ufw or firewalld is active it applies nothing and the UI warns.

## Testing a policy before you trust it

```sh
fwmanager-cli test-policy casa --static   # analyse only, changes nothing
fwmanager-cli test-policy casa            # analyse + live test (applied for seconds, then reverted)
```

Exit code: `0` safe, `2` safe with warnings, `3` unsafe (would break something), `1` could not run.

- **Static** (daemon, read-only): `nft -c` dry run, plus checks that the rules still carry what a
  normal machine needs (replies, loopback, DHCP/DHCPv6, IPv6 neighbour discovery and PMTU,
  open output, untouched forwarding) and touch nothing but `inet fwmanager`. It also warns if
  your current SSH session could not reconnect under the profile.
- **Live** (client): measures gateway (IPv4/IPv6), DNS and loopback before and after applying the
  profile, then always rolls back. Anything that worked before and fails after is reported as
  broken. If the test is killed midway, the daemon reverts by itself after the timeout.
