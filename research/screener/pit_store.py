"""
screener/pit_store.py - no-overwrite S3 writer for the PIT recorder (plan 23 section 5).

The box role has PutObject on ``universe/*`` and nothing else there, so
HeadObject answers 403 (not 404) and cannot prove absence. The real
no-overwrite guarantee is therefore the conditional put (``If-None-Match: *``);
the head check is a cheap early-out when it is allowed. Stdlib + aws CLI only.
"""
from __future__ import annotations

import os
import subprocess
import tempfile


class PutError(RuntimeError):
    """An upload failed. Message carries no URL, token or header."""


class AwsS3Store:
    """``exists``/``put`` over the aws CLI, same mechanism as universe_snapshot."""

    def __init__(self, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._env = {**os.environ, "AWS_DEFAULT_REGION": region}

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["aws", "s3api", *args], capture_output=True, text=True, env=self._env)

    def exists(self, key: str) -> bool | None:
        """True if present, False if confirmed absent, None if it cannot be told (403)."""
        proc = self._run(["head-object", "--bucket", self._bucket, "--key", key])
        if proc.returncode == 0:
            return True
        err = proc.stderr
        return False if ("404" in err or "Not Found" in err) else None

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Write *data* at *key* unless present. Returns ``written`` or ``exists``."""
        known = self.exists(key)
        if known:
            return "exists"
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(data)
            path = fh.name
        try:
            return self._put_file(key, path, content_type, known)
        finally:
            os.unlink(path)

    def _put_file(self, key: str, path: str, content_type: str, known: bool | None) -> str:
        base = ["put-object", "--bucket", self._bucket, "--key", key,
                "--body", path, "--content-type", content_type]
        proc = self._run([*base, "--if-none-match", "*"])
        if proc.returncode == 0:
            return "written"
        err = proc.stderr
        if "PreconditionFailed" in err or "412" in err:
            return "exists"
        if "nknown option" in err:  # old CLI without conditional writes
            # Bucket versioning keeps any overwritten version, so recording the day beats
            # losing it; the manifest marks the write as unguarded when absence was unconfirmed.
            return self._plain_put(base, "written" if known is False else "written_unguarded")
        raise PutError(f"put-object failed (rc={proc.returncode})")

    def _plain_put(self, base: list[str], outcome: str) -> str:
        proc = self._run(base)
        if proc.returncode != 0:
            raise PutError(f"put-object failed (rc={proc.returncode})")
        return outcome
