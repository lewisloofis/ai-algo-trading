"""Daily ops review, summary half: redacted facts -> a short operator summary.

Preferred writer: headless Claude Code (`claude -p`), no tools, facts JSON on stdin. Fallback: a plain
deterministic text from the same facts (claude missing, timeout, non-zero exit, empty or ungrounded
output, or RUNNER_REVIEWER=off). The text is informational only: nothing here changes runner behaviour.

Flags used (verified against `claude --help`, Claude Code 2.1.x):
  -p                         print mode, prompt from stdin
  --model <alias>            haiku by default
  --tools ""                 "Use "" to disable all tools"
  --system-prompt <prompt>   replaces the default system prompt with the fixed one below
  --no-session-persistence   nothing is saved to disk
  --disable-slash-commands   no skills
  --strict-mcp-config        no MCP servers (none are passed)
  --permission-prompts none  anything that would prompt is denied
Not --bare: it skips keychain reads, which would break the user's Claude Code login."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, List, Mapping, Optional, Tuple

from runner.config import DEFAULT_REVIEWER_MODEL, REVIEWER_ENV, REVIEWER_MODEL_ENV

TIMEOUT_S = 60
MAX_LINES = 10
MAX_CHARS = 1500
SYSTEM_PROMPT = (
    "You are writing a 10-line-or-shorter Telegram summary for the operator of an unattended trading "
    "runner. The user message is a JSON object of facts from today's post-close review. Only restate the "
    "facts given; never invent numbers, symbols, causes or advice not in the facts. Lead with any ALERT. "
    "Plain text, no markdown, at most 10 lines.")
_PASS_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "TERM")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

Runner = Callable[..., "subprocess.CompletedProcess"]


def _run(*args, **kwargs):
    """Indirection so tests can stub the one place a subprocess is started."""
    return subprocess.run(*args, **kwargs)


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def claude_command(model: str) -> List[str]:
    return ["claude", "-p", "--model", model, "--tools", "", "--system-prompt", SYSTEM_PROMPT,
            "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
            "--permission-prompts", "none"]


def _child_env(env: Mapping[str, str]) -> dict:
    """Only what the claude CLI needs to find its login; never the runner's SB_* / Telegram values."""
    src = {**os.environ, **env}
    return {k: v for k, v in src.items() if k in _PASS_ENV or k.startswith(("CLAUDE_", "ANTHROPIC_"))}


def run_claude(model: str, stdin: str, env: Mapping[str, str], timeout: float = TIMEOUT_S,
               run: Optional[Runner] = None) -> str:
    """Raises on a missing binary, timeout, non-zero exit or empty output."""
    with tempfile.TemporaryDirectory(prefix="runner-review-") as cwd:     # an empty cwd: no CLAUDE.md, no repo
        proc = (run or _run)(claude_command(model), input=stdin, capture_output=True, text=True, timeout=timeout,
                   cwd=cwd, env=_child_env(env), check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("claude returned nothing")
    return out


def fallback_text(facts: dict) -> str:
    """Deterministic summary: every ALERT/WARN flag, then the headline sections. No LLM."""
    c = facts["counts"]
    head = f"{facts['date']} review: {c['ALERT']} ALERT, {c['WARN']} WARN, {c['INFO']} INFO"
    lines = [head]
    lines += [f"{f['severity']}: {f['text']}" for f in facts["flags"] if f["severity"] in ("ALERT", "WARN")]
    for s in facts["sessions"]:
        lines.append(f"{s['strategy']}: {s['mode']} session {s['status']}"
                     + (f", {s['restarts']} restart(s)" if s["restarts"] else ""))
    if not facts["sessions"]:
        lines.append("sessions: none started")
    lines += [f"{st['name']}: {st['result']}" for st in facts["steps"]]
    r = facts.get("recon")
    if r:
        lines.append(f"recon: {r['status']} as of {r['as_of'][:16]}")
    for sc in facts["scorecards"]:
        lines.append(f"scorecard {sc['strategy']}: {sc['verdict']}")
    lines += facts.get("paper_summary") or []
    lines += facts.get("live_summary") or []
    return "\n".join(lines)


def _grounded(text: str, facts_json: str) -> bool:
    """Every number the model wrote must appear in the facts (commas ignored): no invented figures."""
    known = {n.replace(",", "") for n in _NUM.findall(facts_json)}
    known |= {n.replace(",", "") for n in _NUM.findall(facts_json.replace(",", ""))}
    return all(n.replace(",", "").rstrip(".") in known for n in _NUM.findall(text))


def _clip(text: str) -> str:
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()][:MAX_LINES]
    return "\n".join(lines)[:MAX_CHARS]


def summarise(facts: dict, env: Mapping[str, str], run: Optional[Runner] = None,
              which: Optional[Callable[[str], Optional[str]]] = None) -> Tuple[str, str]:
    """(text, source). source: 'claude' or 'fallback:<reason>'. ``facts`` must already be redacted."""
    if (env.get(REVIEWER_ENV) or "claude").strip().lower() == "off":
        return fallback_text(facts), "fallback:off"
    if (which or _which)("claude") is None:
        return fallback_text(facts), "fallback:claude not found"
    facts_json = json.dumps(facts, indent=1, sort_keys=True)
    model = (env.get(REVIEWER_MODEL_ENV) or DEFAULT_REVIEWER_MODEL).strip()
    try:
        out = _clip(run_claude(model, facts_json, env, run=run))
    except subprocess.TimeoutExpired:
        return fallback_text(facts), "fallback:timeout"
    except (OSError, RuntimeError) as exc:
        return fallback_text(facts), f"fallback:{type(exc).__name__}"
    if not _grounded(out, facts_json):
        return fallback_text(facts), "fallback:ungrounded numbers in model output"
    if facts["counts"]["ALERT"] and "ALERT" not in out:
        return fallback_text(facts), "fallback:model output did not lead with the ALERT"
    return out, "claude"
