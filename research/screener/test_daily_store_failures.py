"""Loud-failure behaviour of daily_store snapshot (2026-10-14 incident).

A fetch error or a stale-empty response must never look like success:
the symbol is FAILED, its parquet is left byte-identical, and the CLI exits
non-zero with a per-symbol summary naming the error class. HTTP is mocked.
"""
from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from screener.daily_store import main as store_main
from screener.daily_store import snapshot_daily
from screener.sb_http import SB_USER_AGENT, SBHttpError, classify_error
from screener.test_daily_store import _make_bars, _make_mock_ds

END = "2026-10-14"


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed(tmp_path, symbol="ITMG"):
    """Store ending 2026-09-11 (the real incident shape)."""
    df = _make_bars(n_days=10, start_date="2026-08-31", symbol=symbol)
    snapshot_daily([symbol], str(tmp_path), _make_mock_ds({symbol: df}), end="2026-09-11")
    return tmp_path / f"{symbol}.parquet"


def _raising(exc):
    ds = MagicMock()
    ds.daily_history.side_effect = exc
    return ds


class TestSnapshotFailures:
    @pytest.mark.parametrize("status", [401, 403, 500])
    def test_fetch_error_marks_failed_and_leaves_file_identical(self, tmp_path, status):
        path = _seed(tmp_path)
        before = _digest(path)
        out = snapshot_daily(["ITMG"], str(tmp_path), _raising(SBHttpError(status, "https://x/y?a=b")), end=END)
        assert out["ITMG"].failed is True
        assert str(status) in out["ITMG"].error
        assert _digest(path) == before

    def test_403_and_401_are_distinguishable(self):
        assert "WAF" in classify_error(SBHttpError(403, "u"))
        assert "token" in classify_error(SBHttpError(401, "u"))
        assert classify_error(SBHttpError(401, "u")) != classify_error(SBHttpError(403, "u"))
        assert classify_error(ValueError("x")) == "ValueError"

    def test_stale_empty_response_is_failed_and_file_identical(self, tmp_path):
        path = _seed(tmp_path)
        before = _digest(path)
        out = snapshot_daily(["ITMG"], str(tmp_path), _make_mock_ds({"ITMG": pd.DataFrame()}), end=END)
        assert out["ITMG"].failed is True
        assert "empty" in out["ITMG"].error
        assert _digest(path) == before

    def test_empty_response_within_few_trading_days_is_not_failed(self, tmp_path):
        path = _seed(tmp_path)  # last bar 2026-09-11 (Fri)
        out = snapshot_daily(["ITMG"], str(tmp_path), _make_mock_ds({"ITMG": pd.DataFrame()}), end="2026-09-14")
        assert out["ITMG"].failed is False

    def test_one_failure_does_not_stop_other_symbols(self, tmp_path):
        ds = MagicMock()

        def side(symbol, **kw):
            if symbol == "BAD":
                raise SBHttpError(403, "u")
            return _make_bars(n_days=3, symbol=symbol)

        ds.daily_history.side_effect = side
        out = snapshot_daily(["BAD", "GOOD"], str(tmp_path), ds, end=END)
        assert out["BAD"].failed and not out["GOOD"].failed
        assert (tmp_path / "GOOD.parquet").exists()
        assert not (tmp_path / "BAD.parquet").exists()


class TestCliExit:
    def test_cli_exits_nonzero_and_names_error_class(self, tmp_path, capsys):
        _seed(tmp_path)
        ds = _raising(SBHttpError(403, "https://x/y?token=SECRET"))
        with patch("screener.daily_store._build_live_data_source", return_value=ds):
            with pytest.raises(SystemExit) as ei:
                store_main(["snapshot", "--symbols", "ITMG", "--out-dir", str(tmp_path), "--end", END])
        assert ei.value.code not in (0, None)
        out = capsys.readouterr().out
        assert "ITMG" in out and "FAILED" in out and "403" in out
        assert "SECRET" not in out and "Bearer" not in out

    def test_cli_success_does_not_exit_nonzero(self, tmp_path):
        ds = _make_mock_ds({"PTRO": _make_bars(n_days=3, symbol="PTRO")})
        with patch("screener.daily_store._build_live_data_source", return_value=ds):
            store_main(["snapshot", "--symbols", "PTRO", "--out-dir", str(tmp_path)])


class TestHttpLayer:
    def _source(self):
        from screener.screen_universe import LiveDataSource
        return LiveDataSource(bearer_token="dummy-not-real")

    def test_user_agent_is_okhttp_constant(self):
        assert SB_USER_AGENT == "okhttp/4.9.3"
        seen = {}

        def fake_urlopen(req, timeout=None, context=None):
            seen["ua"] = req.get_header("User-agent")
            return io.BytesIO(b'{"data": {"chartbit": []}}')

        with patch("urllib.request.urlopen", fake_urlopen):
            self._source().daily_history("ITMG", "2026-01-01", END)
        assert seen["ua"] == SB_USER_AGENT

    @pytest.mark.parametrize("code", [401, 403])
    def test_http_error_becomes_sbhttperror_with_status(self, code):
        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

        with patch("urllib.request.urlopen", fake_urlopen):
            with pytest.raises(SBHttpError) as ei:
                self._source().daily_history("ITMG", "2026-01-01", END)
        assert ei.value.status == code
        assert "dummy-not-real" not in str(ei.value)
        assert "?" not in str(ei.value)  # query string stripped
