"""Tiny nft wrapper for tests. ufw drives iptables-nft, whose rules live in nftables, so
`flush ruleset` in a private namespace is how tests reset the firewall between cases."""

import subprocess


class NftCli:
    def __init__(self, prefix):
        self._prefix = list(prefix)

    def run(self, args):
        return subprocess.run(
            [*self._prefix, "nft", *args], capture_output=True, text=True, check=True, timeout=15
        ).stdout
