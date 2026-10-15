"""Screening drops names the classification feed marks tradeable=0 (and fails closed on unknown)."""
from __future__ import annotations

import pytest

from screener.screen_universe import FixtureDataSource, LiveDataSource, run_screen


def _universe():
    return sorted(FixtureDataSource().funding_universe())


def _screen(tmp_path, flags):
    return run_screen(FixtureDataSource(), start="2025-01-01", end="2026-12-31",
                      outdir=str(tmp_path), tradeable_flags=None if flags is None else (lambda: flags))


def _ranked(u):
    return {r.symbol for r in u.ranked} | {d.symbol for d in u.dropped}


def test_without_flags_nothing_changes(tmp_path):
    assert _ranked(_screen(tmp_path, None)) == set(_universe())


def test_not_tradeable_and_unknown_are_excluded_and_reported_separately(tmp_path, capsys):
    syms = _universe()
    bad, unk, missing = syms[0], syms[1], syms[2]
    flags = {s: True for s in syms if s != missing} | {bad: False, unk: None}
    out = _ranked(_screen(tmp_path, flags))
    assert out == set(syms) - {bad, unk, missing}
    text = capsys.readouterr().out
    assert f"{bad}: not tradeable" in text
    unknown_part = text.split("unknown tradeability")[1]
    assert unk in unknown_part and missing in unknown_part


def _row(symbol, tradeable):
    return {"symbol": symbol, "tradeable": tradeable, "company_status": "Active", "notations": [], "uma": False}


def _group(*rows, total_page=1):
    return {"data": [{"companies": list(rows), "pagination": {
        "total_page": total_page, "total_data": len(rows)}}], "message": "ok"}


def _live(monkeypatch, bodies):
    ds = LiveDataSource("x")
    monkeypatch.setattr(ds, "_get", lambda url: bodies["1000004658" if "1000004658" in url else "1000005139"])
    return ds


def test_live_funding_universe_and_flags_from_classification(monkeypatch):
    ds = _live(monkeypatch, {
        "1000004658": _group(_row("AAAA", 1), _row("BBBB", 1), _row("CCCC", 1), _row("ONLY", 1)),
        "1000005139": _group(_row("AAAA", 1), _row("BBBB", 0), _row("CCCC", None)),
    })
    assert ds.funding_universe() == {"AAAA", "BBBB", "CCCC"}
    flags = ds.tradeable_flags()
    assert flags["AAAA"] is True and flags["BBBB"] is False and flags["CCCC"] is None


def test_live_lists_disagree_false_wins(monkeypatch):
    ds = _live(monkeypatch, {"1000004658": _group(_row("AAAA", 0)), "1000005139": _group(_row("AAAA", 1))})
    ds.funding_universe()
    assert ds.tradeable_flags()["AAAA"] is False


def test_live_paginated_response_is_not_silently_truncated(monkeypatch):
    ds = _live(monkeypatch, {"1000004658": _group(_row("AAAA", 1), total_page=2),
                             "1000005139": _group(_row("AAAA", 1))})
    assert ds.funding_universe() == set()  # screening swallows errors by design: visible empty universe


def test_live_run_needs_no_trading_token(monkeypatch):
    import sys
    from screener import screen_universe as su
    monkeypatch.setenv("SB_BEARER_TOKEN", "x")
    monkeypatch.delenv("SB_TRADING_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["screen_universe", "--source", "live", "--no-tradable-check"])
    args = su.parse_args()
    assert args.no_tradable_check is True
    assert not hasattr(su, "_live_tradable_fetch")
