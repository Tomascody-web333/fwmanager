"""Thin wrapper around the ``nft`` binary. Arguments are always fixed lists, never a shell."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from fwmanager.errors import NftError

NFT_TIMEOUT = 15


class Nft:
    """Runs ``nft``. ``prefix`` lets tests enter a private network namespace."""

    def __init__(self, binary: str = "nft", prefix: Sequence[str] = ()) -> None:
        self._cmd = [*prefix, binary]

    def run(self, args: Sequence[str], stdin: str | None = None) -> str:
        try:
            proc = subprocess.run(
                [*self._cmd, *args],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=NFT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NftError(f"cannot run nft: {exc}", code="nft_unavailable") from exc
        if proc.returncode != 0:
            raise NftError(proc.stderr.strip() or "nft failed")
        return proc.stdout

    def apply_script(self, script: str) -> None:
        """Load a script in one atomic transaction."""
        self.run(["-f", "-"], stdin=script)

    def apply_json(self, commands: list[dict]) -> None:
        self.run(["-j", "-f", "-"], stdin=json.dumps({"nftables": commands}))

    def list_table_json(self, family: str, table: str) -> list[dict] | None:
        """Return the JSON objects of a table, or ``None`` if it does not exist."""
        try:
            out = self.run(["-j", "list", "table", family, table])
        except NftError as exc:
            if "No such file or directory" in exc.message:
                return None
            raise
        return json.loads(out)["nftables"]
