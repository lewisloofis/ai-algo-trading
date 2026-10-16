"""Tests for index_events.build_events (session mapping, FTSE from/to, MSCI typing, FTSE rule)."""
import pandas as pd
import pytest

from index_events import build_events as be

# Mon 2024-03-04 .. Fri 2024-03-08, then Mon 03-11 and Tue 03-12 are holidays, Wed 03-13 trades.
SESSIONS = pd.DatetimeIndex(pd.to_datetime(
    ["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08", "2024-03-13", "2024-03-14"]))


def test_announce_on_weekend_maps_to_prior_friday_and_next_session_is_after_holidays():
    m = be.map_dates(SESSIONS, "2024-03-09", "2024-03-14")     # Saturday
    assert m["announce_session"] == pd.Timestamp("2024-03-08")
    assert m["first_tradeable_session"] == pd.Timestamp("2024-03-13")


def test_announce_on_holiday_and_session_dates_stay_on_sessions():
    m = be.map_dates(SESSIONS, "2024-03-12", "2024-03-12")     # holiday
    assert m["announce_session"] == pd.Timestamp("2024-03-08")
    assert m["passive_trade_session"] == pd.Timestamp("2024-03-08")


def test_effective_date_not_a_session_uses_last_session_before_it():
    m = be.map_dates(SESSIONS, "2024-03-04", "2024-03-11")
    assert m["passive_trade_session"] == pd.Timestamp("2024-03-08")
    assert m["effective_session"] == pd.Timestamp("2024-03-13")


def test_date_on_session_is_itself_and_out_of_range_is_nat():
    assert be.session_on_or_before(SESSIONS, "2024-03-05") == pd.Timestamp("2024-03-05")
    assert pd.isna(be.session_on_or_before(SESSIONS, "2023-01-01"))
    assert pd.isna(be.session_on_or_before(SESSIONS, ""))
    assert pd.isna(be.session_after(SESSIONS, pd.Timestamp("2024-03-14")))


@pytest.mark.parametrize("frm,to,exp", [
    ("Small", "Mid", "migrate_in"), ("Small", "Large", "migrate_in"),
    ("Mid", "Small", "migrate_out"), ("Large", "Small", "migrate_out"),
    ("Large", "Mid", "other"), ("Mid", "Large", "other"),
    ("none", "Small", "add"), ("Micro", "Mid", "add"),
    ("Small", "none", "del"), ("Large", "Micro", "del"),
    ("none", "Micro", None), ("Micro", "none", None),
])
def test_classify_ftse(frm, to, exp):
    assert be.classify_ftse(frm, to) == exp


def _raw(rows):
    cols = ["provider", "review_label", "section", "subsection", "change", "sedol", "ftse_name",
            "country", "reason", "is_microcap_file", "source_doc", "note"]
    return pd.DataFrame([["FTSE", *r, "Indonesia", "", False, "doc.xls", ""] for r in rows],
                        columns=cols[:2] + cols[2:7] + cols[7:]) if False else pd.DataFrame(
        [{"provider": "FTSE", "review_label": r[0], "section": r[1], "subsection": r[2], "change": r[3],
          "sedol": r[4], "ftse_name": r[5], "country": "Indonesia", "reason": "", "is_microcap_file": False,
          "source_doc": "doc.xls", "note": ""} for r in rows])


def test_ftse_from_to_derivation_from_exclusion_inclusion_pair():
    raw = _raw([
        ("FTSE-2020-03", "Small Cap", "Exclusions", "del", "S1", "Alpha"),
        ("FTSE-2020-03", "Mid Cap", "Inclusions", "add", "S1", "Alpha"),            # Small -> Mid
        ("FTSE-2020-03", "Mid Cap", "Exclusions", "del", "S2", "Beta"),
        ("FTSE-2020-03", "Small Cap", "Inclusions", "add", "S2", "Beta"),           # Mid -> Small
        ("FTSE-2020-03", "Small Cap", "Inclusions", "add", "S3", "Gamma"),          # none -> Small
        ("FTSE-2020-03", "Micro Cap", "Exclusions", "del", "S4", "Delta"),
        ("FTSE-2020-03", "Small Cap", "Inclusions", "add", "S4", "Delta"),          # Micro -> Small
        ("FTSE-2020-03", "Small Cap", "Exclusions", "del", "S5", "Eps"),
        ("FTSE-2020-03", "Micro Cap", "Inclusions", "add", "S5", "Eps"),            # Small -> Micro
        ("FTSE-2020-03", "Micro Cap", "Inclusions", "add", "S6", "Zeta"),           # none -> Micro (dropped)
        ("FTSE-2020-03", "Large Cap", "Exclusions", "del", "S7", "Eta"),
        ("FTSE-2020-03", "Mid Cap", "Inclusions", "add", "S7", "Eta"),              # Large -> Mid (other)
    ])
    mv, stats = be.ftse_moves(raw)
    got = mv.set_index("sedol")[["from_segment", "to_segment", "change"]]
    assert tuple(got.loc["S1"]) == ("Small", "Mid", "migrate_in")
    assert tuple(got.loc["S2"]) == ("Mid", "Small", "migrate_out")
    assert tuple(got.loc["S3"]) == ("none", "Small", "add")
    assert tuple(got.loc["S4"]) == ("Micro", "Small", "add")
    assert tuple(got.loc["S5"]) == ("Small", "Micro", "del")
    assert pd.isna(got.loc["S6", "change"]) and stats["micro_only_dropped"] == 1
    assert tuple(got.loc["S7"]) == ("Large", "Mid", "other")


def test_ftse_view_check_flags_disagreement_and_ok():
    raw = _raw([
        ("FTSE-2020-03", "Small Cap", "Exclusions", "del", "S1", "Alpha"),
        ("FTSE-2020-03", "Mid Cap", "Inclusions", "add", "S1", "Alpha"),
        ("FTSE-2020-03", "FTSE All-World (LM)", "Inclusions", "add", "S1", "Alpha"),   # agrees
        ("FTSE-2020-03", "Small Cap", "Inclusions", "add", "S3", "Gamma"),            # no AW row: agrees
    ])
    mv, _ = be.ftse_moves(raw)
    mv, orph = be.ftse_view_check(mv, raw)
    assert set(mv["view_check"]) == {"ok"} and orph.empty
    raw2 = pd.concat([raw, _raw([("FTSE-2020-03", "FTSE All-World (LM)", "Inclusions", "add", "S3", "Gamma")])])
    mv2, _ = be.ftse_moves(raw2)
    mv2, _ = be.ftse_view_check(mv2, raw2)
    assert mv2.set_index("sedol").loc["S3", "view_check"] == "mismatch"      # Small add is not All-World


def test_msci_review_type_and_freeze_override():
    assert be.msci_review_type("MSCI-2024-05") == "semi"
    assert be.msci_review_type("MSCI-2024-11") == "semi"
    assert be.msci_review_type("MSCI-2024-02") == "quarterly"
    assert be.msci_review_type("MSCI-2025-08") == "quarterly"
    assert be.msci_review_type("MSCI-2026-02") == "freeze"
    assert be.msci_review_type("MSCI-2026-08") == "freeze"


def test_msci_segments_and_new_suffix():
    assert be.msci_segments("MSCI_ST", "migrate_out") == ("ST", "SC")
    assert be.msci_segments("MSCI_SC", "migrate_in") == ("ST", "SC")
    assert be.msci_segments("MSCI_SC", "add") == ("none", "SC")
    assert be.strip_new("FOO BAR (NEW)") == "FOO BAR"


def test_ftse_third_friday_and_rule_check_requires_four_years():
    assert be.ftse_third_friday(2018, 9) == pd.Timestamp("2018-09-21")
    assert be.ftse_third_friday(2016, 3) == pd.Timestamp("2016-03-18")
    cal = pd.read_csv(be.DATA_DIR / "ftse_calendar.csv", dtype=str, keep_default_na=False)
    rule = be.check_ftse_rule(cal)
    assert rule["passive_rule_confirmed"] is True
    assert rule["announce_rule_confirmed"] is False       # 23-day offset holds only 2016..2018-03
    inferred, _ = be.infer_ftse_2015(cal, ["FTSE-2015-03"])
    assert inferred == {}


def test_event_table_rows_have_source_doc_and_unique_ids():
    ev = pd.read_csv(be.EVENTS_CSV, dtype=str, keep_default_na=False)
    assert (ev["source_doc"] != "").all()
    assert ev["event_id"].is_unique
    assert set(ev["source_tier"]) == {"primary"}
    assert (ev.loc[ev["review_label"] >= "MSCI-2026-02", "review_type"][ev["provider"] == "MSCI"] == "freeze").all()
