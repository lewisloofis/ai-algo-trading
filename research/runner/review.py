"""Post-close daily ops review: rules -> redaction -> summary -> files. One message, informational only.

Nothing here gates or changes the runner: a failure anywhere falls back to a deterministic text (and
postclose falls back to its plain summary if even that raises)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Sequence

from runner import review_rules, review_summary
from runner.config import Config
from runner.review_redact import redact_text, sanitize


@dataclass(frozen=True)
class ReviewResult:
    text: str
    source: str            # 'claude' | 'fallback:<why>'
    facts: dict            # the redacted facts that were (or would have been) sent
    level: str             # ALERT when any ALERT flag, else SUMMARY


def review_paths(cfg: Config, day: date) -> tuple:
    return cfg.runner_dir / f"review-{day}.json", cfg.runner_dir / f"review-{day}.txt"


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def run_review(cfg: Config, day: date, state: dict, now_epoch: float, *, steps: Sequence[tuple] = (),
               live_summary: Optional[Sequence[str]] = None, paper_summary: Optional[Sequence[str]] = None,
               run: Optional[Callable] = None, which: Optional[Callable] = None) -> ReviewResult:
    raw = review_rules.build_facts(cfg, day, state, now_epoch, steps=steps, live_summary=live_summary,
                                   paper_summary=paper_summary)
    facts = sanitize(raw, cfg.env)
    text, source = review_summary.summarise(facts, cfg.env, run=run, which=which)
    text = redact_text(text, cfg.env)        # the model's output is redacted too before it is logged or pushed
    jp, tp = review_paths(cfg, day)
    _write(jp, json.dumps({**facts, "summary_source": source}, indent=2, sort_keys=True) + "\n")
    _write(tp, text + "\n")
    return ReviewResult(text, source, facts, "ALERT" if facts["counts"]["ALERT"] else "SUMMARY")
