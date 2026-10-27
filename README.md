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
