"""
screener/universe_snapshot.py — point-in-time eligibility snapshots (plan 18 A-3).

The tradeable universe is the intersection of two Stockbit funding lists:
day-trade eligible and trading-limit eligible.  Both endpoints report what is
eligible **now** — there is no history and ``effective_date`` comes back null,
so the point-in-time universe **cannot be reconstructed**.  It can only be
recorded going forward (ADR-0015).

Why that matters: a backtest over 2024-2026 run against today's eligible set is
measuring how names that are *currently in good standing* performed, which is
survivorship.  The delisting half of that was tested and is small (adding 43
delisted names moved the momentum result 0.33pp); Indonesian delistings are
rare.  The untested half is **eligibility churn**, which is common — a name in
trouble typically falls off the funding lists well before it delists, and those
names are invisible to every backtest run so far.

Snapshots land in S3 rather than on a workstation: the record is permanent,
append-only, and must accrue on the collector's schedule rather than whenever a
laptop happens to be awake.  ``research/data/*`` is gitignored, so a local file
would be neither tracked nor backed up.

Run::

    python -m screener.universe_snapshot --bucket marketdata-047915756052
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re
import subprocess
import tempfile

from screener.emitten import (
    SUBSECTOR_DAY_TRADE,
    SUBSECTOR_TRADING_LIMIT,
    EmittenClient,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 1

S3_PREFIX: str = "universe"
"""Date-partitioned like the market data (``raw/dt=.../``) so both read alike."""

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EmptySnapshotError(RuntimeError):
    """
    Raised when a leg or the intersection comes back empty.

    ``_fetch_subsector_symbols`` deliberately swallows HTTP errors and returns
    an empty set so one dead endpoint cannot corrupt an intersection.  That is
    right for a screening run and wrong for a recording run: writing an empty
    snapshot would record "nothing was tradeable today" as fact, and unlike a
    screening run the day cannot be re-recorded later.
    """


def resolve_bearer_token(ssm_prefix: str | None = None, region: str = "") -> str:
    """
    Bearer token from ``SB_BEARER_TOKEN``, else from SSM Parameter Store.

    On a workstation the env var is the convenient path. On the collector box
    there is no env var: the token lives at ``{ssm_prefix}/access`` and is
    rotated by the collector itself, so this reads it fresh on each run.

    **Read-only by design.** It never touches ``{ssm_prefix}/refresh``: minting
    a token rotates the refresh token, which would invalidate the collector's
    own session mid-capture. If ``/access`` has expired this raises, the
    snapshot is skipped for the day, and the collector is left alone.
    """
    token = os.environ.get("SB_BEARER_TOKEN")
    if token:
        return token
    if not ssm_prefix:
        raise EnvironmentError(
            "no SB_BEARER_TOKEN and no --ssm-prefix given; cannot authenticate"
        )
    out = subprocess.run(
        ["aws", "ssm", "get-parameter", "--name", f"{ssm_prefix}/access",
         "--with-decryption", "--query", "Parameter.Value", "--output", "text"],
        check=True, capture_output=True, text=True,
        env={**os.environ, **({"AWS_DEFAULT_REGION": region} if region else {})},
    )
    return out.stdout.strip()


def build_snapshot(source, as_of: str) -> dict:
    """
    Fetch both funding legs and assemble the snapshot payload for *as_of*.

    Both legs are kept, not just their intersection: a name can leave the
    eligible set by falling off either list, and which one is not recoverable
    from the intersection alone.
    """
    day_trade = {s.strip().upper() for s in source.fetch_day_trade()}
    trading_limit = {s.strip().upper() for s in source.fetch_trading_limit()}

    if not day_trade:
        raise EmptySnapshotError(
            "day_trade leg returned zero symbols — refusing to record an empty "
            "universe. The usual cause is an expired bearer token (HTTP 401)."
        )
    if not trading_limit:
        raise EmptySnapshotError(
            "trading_limit leg returned zero symbols — refusing to record an "
            "empty universe. The usual cause is an expired bearer token (HTTP 401)."
        )

    eligible = day_trade & trading_limit
    if not eligible:
        raise EmptySnapshotError(
            f"intersection is empty ({len(day_trade)} day-trade, "
            f"{len(trading_limit)} trading-limit) — refusing to record it."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source": {
            "day_trade_subsector": SUBSECTOR_DAY_TRADE,
            "trading_limit_subsector": SUBSECTOR_TRADING_LIMIT,
        },
        "counts": {
            "day_trade": len(day_trade),
            "trading_limit": len(trading_limit),
            "eligible": len(eligible),
        },
        "day_trade": sorted(day_trade),
        "trading_limit": sorted(trading_limit),
        "eligible": sorted(eligible),
    }


def s3_key_for(as_of: str) -> str:
    """S3 key for a snapshot dated *as_of* (``YYYY-MM-DD``)."""
    if not _DATE_RE.match(as_of):
        raise ValueError(f"as_of must be YYYY-MM-DD, got {as_of!r}")
    return f"{S3_PREFIX}/dt={as_of}/eligibility.json"


def put_snapshot(snapshot: dict, bucket: str, region: str) -> str:
    """Upload *snapshot* to S3 and return the ``s3://`` URI it landed at."""
    key = s3_key_for(snapshot["as_of"])
    uri = f"s3://{bucket}/{key}"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(snapshot, fh, indent=2)
        fh.write("\n")
        path = fh.name
    try:
        subprocess.run(
            ["aws", "s3", "cp", path, uri, "--content-type", "application/json"],
            check=True, capture_output=True,
            env={**os.environ, "AWS_DEFAULT_REGION": region},
        )
    finally:
        os.unlink(path)
    return uri


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Record today's eligible universe (plan 18 A-3).")
    parser.add_argument("--bucket", required=True, help="S3 bucket to write into.")
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-3"))
    parser.add_argument("--as-of", default=None, help="Snapshot date (default: today, WIB).")
    parser.add_argument(
        "--ssm-prefix", default=os.environ.get("SSM_PARAM_PREFIX"),
        help="SSM prefix holding the rotated access token (e.g. /sb-collector). "
             "Used only when SB_BEARER_TOKEN is unset.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build and print; do not upload.")
    args = parser.parse_args(argv)

    wib = _dt.timezone(_dt.timedelta(hours=7))
    as_of = args.as_of or _dt.datetime.now(wib).date().isoformat()

    token = resolve_bearer_token(args.ssm_prefix, args.region)
    snapshot = build_snapshot(EmittenClient(token), as_of=as_of)
    logger.info(
        "%s: %d day-trade ∩ %d trading-limit = %d eligible",
        as_of, snapshot["counts"]["day_trade"],
        snapshot["counts"]["trading_limit"], snapshot["counts"]["eligible"],
    )
    if args.dry_run:
        print(json.dumps(snapshot, indent=2)[:600])
        return
    logger.info("written to %s", put_snapshot(snapshot, args.bucket, args.region))


if __name__ == "__main__":
    main()
