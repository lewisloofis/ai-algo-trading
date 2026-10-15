"""Never-live guard. Every child process the runner starts passes through ``validate_argv``.

The runner only runs paper. It has no code path that arms live trading or submits an order:
live-session / strategy-run are never executed directly, ``--retry`` is refused, a non-paper
spec is refused, and the arming variables are stripped from every child environment."""

from __future__ import annotations

import os
from typing import Mapping, Sequence

STRIPPED_ALWAYS = ("ALGO_LIVE", "ALGO_CONFIRM")
TRADING_TOKEN = "SB_TRADING_ACCESS_TOKEN"
ALLOWED_MAKE_TARGETS = frozenset({"build-strategy", "refresh-prices", "recon", "paper-scorecard", "dashboard"})
PAPER_DAY_MODULE = "strategy_fit.paper_day"
FORBIDDEN_TOKENS = ("live-session", "strategy-run", "--retry", "ALGO_LIVE", "ALGO_CONFIRM",
                    "emit-target", "--allow-retired")


class LiveGuardError(RuntimeError):
    """A command that could arm live trading, or that is not on the runner's allowlist."""


def _require_paper(strategy_id: str, registry) -> None:
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    spec = registry.get(strategy_id)
    if spec is None or not spec.is_paper:
        status = "unknown" if spec is None else spec.status
        raise LiveGuardError(f"strategy {strategy_id!r} has status {status!r}; the runner only runs 'paper'")


def validate_argv(argv: Sequence[str], registry=None) -> None:
    """Raise LiveGuardError unless argv is an allowlisted, paper-only command."""
    if not argv:
        raise LiveGuardError("empty command")
    for tok in argv:
        for bad in FORBIDDEN_TOKENS:
            if bad in str(tok):
                raise LiveGuardError(f"forbidden token {bad!r} in command")
    for i, tok in enumerate(argv):
        if tok == "--sink" and (i + 1 >= len(argv) or argv[i + 1] != "paper"):
            raise LiveGuardError("--sink must be 'paper'")
        if tok.startswith("--sink=") and tok != "--sink=paper":
            raise LiveGuardError("--sink must be 'paper'")
        if tok.startswith("STRATEGY="):
            _require_paper(tok.split("=", 1)[1], registry)
    head = os.path.basename(argv[0])
    if head == "make":
        targets = [t for t in argv[1:] if not t.startswith("-") and "=" not in t]
        if not targets or any(t not in ALLOWED_MAKE_TARGETS for t in targets):
            raise LiveGuardError(f"make target not allowed: {targets}")
        return
    if head.startswith("python") and len(argv) >= 3 and argv[1] == "-m" and argv[2] == PAPER_DAY_MODULE:
        if "--strategy" not in argv or argv.index("--strategy") + 1 >= len(argv):
            raise LiveGuardError("paper_day needs --strategy")
        _require_paper(argv[argv.index("--strategy") + 1], registry)
        return
    raise LiveGuardError(f"command not on the runner allowlist: {head}")


def child_env(env: Mapping[str, str], allow_trading_token: bool = False) -> dict:
    """The environment a child gets: arming variables never; the trading token only for recon."""
    drop = set(STRIPPED_ALWAYS)
    if not allow_trading_token:
        drop.add(TRADING_TOKEN)
    return {k: v for k, v in env.items() if k not in drop}
