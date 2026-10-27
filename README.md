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
