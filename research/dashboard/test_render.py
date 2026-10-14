"""Render primitives: account scrubbing, glossary-linked headers, shared nav."""
from __future__ import annotations

import pytest

from dashboard import render as r


@pytest.mark.parametrize(
    "text",
    [
        "sleeve 007777L.",
        "(007777L)",
        "account 007777L, then more",
        "old account 0001234 too",
        "id=007777L;",
    ],
)
def test_account_numbers_are_scrubbed(text):
    out = r.scrub(text)
    assert "007777" not in out and "0001234" not in out
    assert r.ACCOUNT_PLACEHOLDER in out


@pytest.mark.parametrize(
    "text",
    ["Rp 16,832,000", "2026-10-14 15:13", "run 20261014T081304Z-8575169b", "0.5% of 1,000,000", "5,925", "64 lots"],
)
def test_ordinary_numbers_survive_scrubbing(text):
    assert r.scrub(text) == text


def test_every_page_is_scrubbed_on_the_way_out():
    html = r.page("T", "index.html", "<p>sleeve 007777L.</p>", "footer 0001234")
    assert "007777" not in html and "0001234" not in html


def test_column_headers_link_to_plain_glossary_terms():
    t = r.table([r.Column("Lots", term="lot")], [["1"]])
    assert 'href="glossary.html#t-lot"' in t


def test_a_column_pointing_at_a_missing_term_fails_loudly():
    with pytest.raises(KeyError, match="no-such-term"):
        r.table([r.Column("X", term="no-such-term")], [["1"]])


def test_every_page_has_the_shared_nav_with_the_active_page_marked():
    html = r.page("T", "trades.html", "<p>x</p>", "f")
    for href, _label in r.NAV:
        assert f'href="{href}"' in html
    assert 'href="trades.html" class=on' in html


def test_money_and_percent_formats():
    assert r.money(16832000) == "Rp 16,832,000"
    assert r.money(-4, signed=True) == "-Rp 4"
    assert r.money(31, signed=True) == "+Rp 31"
    assert r.money(None) == "–"
    assert r.pct(3.1) == "+3.10%" and r.pct(None) == "–"


def test_no_script_or_external_resource_in_a_page():
    html = r.page("T", "index.html", "<p>x</p>", "f").lower()
    assert "<script" not in html and "http://" not in html and "https://" not in html
