"""The single gate for every child process the runner starts.

Two doors, and nothing else:

* ``validate_argv``: the PAPER door. It refuses anything that could arm live trading or submit
  an order: live-session / strategy-run are never executed through it, ``--retry`` is refused,
  a non-paper spec is refused, and ALGO_LIVE / ALGO_CONFIRM are stripped from every child env.

* ``build_live_command`` / ``validate_live_command`` / ``live_child_env``: the LIVE door. A live
  command can only be built from a ``modes.LiveToken``, which only ``modes.decide_mode`` issues
  (status 'candidate', valid arm file for today, preflight ok, no kill file). The result is a
  sealed ``LiveCommand`` that is re-verified against the arm file, the kill file and the spec
  registry at the moment of use (and again inside the supervise process). ``--retry`` stays
  forbidden, there is no restart, and the arming variables exist only in the environment
  ``live_child_env`` builds for that one live-session process.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from runner import arm as arm_mod
from runner import livepre, modes
from runner.config import ACCOUNT_ENV, WIB

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


# ---- the LIVE door ----------------------------------------------------------------------------

# live.LiveConfirmPhrase in strategy/live/armed.go. test_live_guard asserts the two stay equal.
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_LIVE_TRADING"
LIVE_SESSION_BASENAME = "live-session"
MIN_LIVE_TOKEN_HOURS = 0.5          # supervise re-check; the runner's own preflight demands more
_LIVE_SEAL = object()
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Passed to strategy-run after `--`. Equity is the armed cap, so these fractions are of the cap:
# one order or one name may use all of it, the day's buys+sells at most twice (an exit leg), and
# the loss limit stays at strategy-run's default (5% of the cap). The absolute buy cap is separate.
LIVE_RISK_ARGS = (("--risk-per-order-fraction", "1"), ("--risk-max-position-fraction", "1"),
                  ("--risk-max-gross-fraction", "1"), ("--risk-per-day-fraction", "2"))
_LIVE_FORBIDDEN_FLAGS = ("--retry", "--allow-position-mismatch", "--allow-retired", "--expect-binary-sha256")


class LiveCommand:
    """A validated live-session invocation, sealed: only ``build_live_command`` makes one."""

    __slots__ = ("token", "argv", "emit_args", "target", "repo_root", "runs_dir", "data_dir", "rebalance_at", "account")

    def __init__(self, seal: object, token, argv: Tuple[str, ...], emit_args: Tuple[str, ...], target: Path,
                 repo_root: Path, runs_dir: Path, data_dir: Path, rebalance_at: str, account: str) -> None:
        if seal is not _LIVE_SEAL:
            raise TypeError("LiveCommand can only be built by runner.guard.build_live_command")
        values = (token, argv, emit_args, target, repo_root, runs_dir, data_dir, rebalance_at, account)
        for name, value in zip(self.__slots__, values):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("LiveCommand is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("LiveCommand is immutable")

    def __reduce__(self):
        raise TypeError("LiveCommand cannot be copied or pickled")

    @property
    def strategy_id(self) -> str:
        return self.token.strategy_id

    @property
    def max_idr(self) -> int:
        return self.token.max_idr

    def display(self) -> str:
        """The command as shown in logs, plans and alerts: the account number is masked."""
        out, mask_next = [], False
        for tok in self.argv:
            out.append("[ACCOUNT]" if mask_next else tok)
            mask_next = tok == "--account"
        return " ".join(out)


def _live_argv(token, spec, repo_root: Path, runs_dir: Path, target: Path, rebalance_at: str,
               account: str) -> Tuple[str, ...]:
    cap = str(token.max_idr)
    argv = [str(repo_root / "bin" / LIVE_SESSION_BASENAME),
            "--sink", "live", "--source", "live", "--account", account, "--equity-idr", cap,
            "--strategy", "rebalance", "--target-portfolio", str(target),
            "--symbols", ",".join(spec.symbols), "--rebalance-at", rebalance_at,
            "--runs-dir", str(runs_dir), "--max-restarts", "0", "--max-buy-notional-idr", cap, "--"]
    for flag, value in LIVE_RISK_ARGS:
        argv += [flag, value]
    return tuple(argv)


def build_live_command(token, *, registry=None, repo_root: Path, rebalance_at: str, account: str,
                       data_dir: Optional[Path] = None) -> LiveCommand:
    """The live-session command for ``token``'s strategy. Raises LiveGuardError on anything off."""
    if not isinstance(token, modes.LiveToken):
        raise LiveGuardError("a live command needs a LiveToken from modes.decide_mode")
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    spec = registry.get(token.strategy_id)
    if spec is None or not spec.is_candidate:
        raise LiveGuardError(f"strategy {token.strategy_id!r} is not a 'candidate' spec")
    if not 0 < token.max_idr <= arm_mod.HARD_MAX_IDR:
        raise LiveGuardError(f"max_idr {token.max_idr} is outside 1..{arm_mod.HARD_MAX_IDR:,}")
    if not _ACCOUNT_RE.match(account or ""):
        raise LiveGuardError(f"{ACCOUNT_ENV} is missing or not a plain account number")
    if not _HHMM_RE.match(rebalance_at or ""):
        raise LiveGuardError(f"rebalance_at {rebalance_at!r} must be HH:MM")
    repo_root = Path(repo_root)
    runs_dir = repo_root / "runs"
    data_dir = Path(data_dir) if data_dir is not None else repo_root / "research" / "data" / "daily"
    target = runs_dir / f"target_{spec.strategy_id}_{token.day}.json"
    emit_args = ("emit", "--strategy", spec.strategy_id, "--as-of", str(token.day), "--out", str(target),
                 "--data-dir", str(data_dir), "--rebalance-at", rebalance_at, "--runs-dir", str(runs_dir),
                 "--equity-idr", str(token.max_idr))
    argv = _live_argv(token, spec, repo_root, runs_dir, target, rebalance_at, account)
    return LiveCommand(_LIVE_SEAL, token, argv, emit_args, target, repo_root, runs_dir, data_dir, rebalance_at, account)


def _flag_values(argv: Sequence[str], flag: str) -> list:
    return [argv[i + 1] for i, t in enumerate(argv[:-1]) if t == flag]


def validate_live_command(cmd, registry=None, today: Optional[date] = None) -> None:
    """Re-verify a LiveCommand at the moment of use. Raises LiveGuardError unless every check holds.

    Independent of how the command was built: the shape of the argv, the arm file as it is NOW,
    the kill file as it is NOW, and the spec's status in the registry."""
    if not isinstance(cmd, LiveCommand):
        raise LiveGuardError("not a LiveCommand: live commands are only built by guard.build_live_command")
    token, argv = cmd.token, list(cmd.argv)
    if not isinstance(token, modes.LiveToken):
        raise LiveGuardError("the command carries no LiveToken")
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    spec = registry.get(token.strategy_id)
    if spec is None or not spec.is_candidate:
        raise LiveGuardError(f"strategy {token.strategy_id!r} is not a 'candidate' spec")
    rebuilt = _live_argv(token, spec, cmd.repo_root, cmd.runs_dir, cmd.target, cmd.rebalance_at, cmd.account)
    if tuple(argv) != rebuilt:
        raise LiveGuardError("the live command does not match the one authorised for this token")
    if os.path.basename(argv[0]) != LIVE_SESSION_BASENAME:
        raise LiveGuardError("the live command must run live-session")
    cap = str(token.max_idr)
    for flag, want in (("--sink", "live"), ("--source", "live"), ("--strategy", "rebalance"), ("--max-restarts", "0"),
                       ("--max-buy-notional-idr", cap), ("--equity-idr", cap)):
        if _flag_values(argv, flag) != [want]:
            raise LiveGuardError(f"live command must carry exactly one {flag} {want}")
    split = argv.index("--")
    head, extras = argv[:split], argv[split + 1:]
    for tok in head + extras:
        if any(tok == bad or tok.startswith(bad + "=") for bad in _LIVE_FORBIDDEN_FLAGS):
            raise LiveGuardError(f"forbidden flag {tok!r} in a live command")
        if "ALGO_LIVE" in tok or "ALGO_CONFIRM" in tok or tok.startswith("eyJ"):
            raise LiveGuardError("a live command must not carry arming variables or tokens")
    if extras != [a for pair in LIVE_RISK_ARGS for a in pair]:
        raise LiveGuardError("unexpected arguments for strategy-run in a live command")
    # The world as it is now, not as it was at decision time.
    arm = arm_mod.read_arm_file(cmd.runs_dir, token.day)
    if not arm.valid or arm.max_idr.get(token.strategy_id) != token.max_idr:
        raise LiveGuardError("the arm file is gone, invalid or changed: not armed any more")
    if os.path.lexists(cmd.runs_dir / "KILL"):
        raise LiveGuardError("the kill switch (runs/KILL) is present")
    if today is not None and token.day != today:
        raise LiveGuardError(f"live runs only on the actual date ({token.day} is not {today})")


def wrapper_env(cmd: LiveCommand, env: Mapping[str, str], registry=None) -> dict:
    """Environment for the supervise wrapper: the trading token, never the arming variables."""
    validate_live_command(cmd, registry)
    return child_env(env, allow_trading_token=True)


def live_child_env(cmd: LiveCommand, env: Mapping[str, str], registry=None, today: Optional[date] = None) -> dict:
    """The ONE place ALGO_LIVE / ALGO_CONFIRM are set: the env of the live-session process itself."""
    validate_live_command(cmd, registry, today)
    out = child_env(env, allow_trading_token=True)
    out["ALGO_LIVE"] = "true"
    out["ALGO_CONFIRM"] = LIVE_CONFIRM_PHRASE
    return out


def reauthorize_live(grant: Mapping[str, Any], argv: Sequence[str], env: Mapping[str, str], registry=None,
                     today: Optional[date] = None, now_epoch: Optional[float] = None) -> LiveCommand:
    """What the supervise process runs before it starts live-session: take the grant the runner
    wrote, redo the live decision from the files as they are now, rebuild the command and require
    it to equal the argv it was given. Raises LiveGuardError (never starts anything) on any gap."""
    try:
        sid, day = str(grant["strategy_id"]), date.fromisoformat(str(grant["day"]))
        repo_root, rebalance_at = Path(str(grant["repo_root"])), str(grant["rebalance_at"])
        data_dir = Path(str(grant["data_dir"]))
        preflight_ok = bool(grant["preflight_ok"])
    except (KeyError, ValueError, TypeError) as exc:
        raise LiveGuardError(f"live grant is unreadable ({type(exc).__name__})") from exc
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    spec = registry.get(sid)
    if spec is None:
        raise LiveGuardError(f"strategy {sid!r} is not in the registry")
    runs_dir = repo_root / "runs"
    now = time.time() if now_epoch is None else now_epoch
    real_today = today or datetime.fromtimestamp(now, WIB).date()
    gate = modes.LiveGate(
        day=day, today=real_today, arm=arm_mod.read_arm_file(runs_dir, day),
        kill_present=os.path.lexists(runs_dir / "KILL"), preflight_ok=preflight_ok,
        live_problems=livepre.live_problems(env, now, MIN_LIVE_TOKEN_HOURS))
    mode = modes.decide_mode(spec, gate)
    if mode.mode != modes.LIVE:
        raise LiveGuardError(f"not authorised to go live: {mode.reason}")
    cmd = build_live_command(mode.token, registry=registry, repo_root=repo_root, rebalance_at=rebalance_at,
                             account=(env.get(ACCOUNT_ENV) or "").strip(), data_dir=data_dir)
    if list(cmd.argv) != list(argv):
        raise LiveGuardError("the command line does not match the command authorised for this grant")
    validate_live_command(cmd, registry, real_today)
    return cmd
