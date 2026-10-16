"""Tests for index_events.ftse_geis (synthetic workbooks + real-file integration)."""
import datetime as dt

import openpyxl
import pytest

from index_events import ftse_geis as g


def _book(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


MODERN = [
    [], [None],
    ["FTSE Global Equity Index Series "],
    ["Asia Pacific Ex Japan Ex China September 2026 Semi-Annual Review"],
    ["(c) FTSE International Limited 2026"],
    [],
    ["Large Cap"], [],
    ["Inclusions"],
    ["SEDOL", "Company Name", "Country", None, "Reason"],
    ["B1234567", "Foo Corp", "Malaysia", None, "Add to Large"],
    [6283979.0, "Indofood Sukses Makmur", "Indonesia", None, "Mid to Large"],
    ["*Footnote about something"],
    ["Exclusions"],
    ["SEDOL", "Company Name", "Country", None, "Reason"],
    ["B00FYK2", "Indosat", "Indonesia", None, "Large to Mid"],
    ["FTSE All-World (LM)"], [],
    ["Inclusions"],
    ["SEDOL", "Company Name", "Country"],
    [6283979, "Indofood Sukses Makmur", "Indonesia"],
    ["*Nationality Change"],
    ["SEDOL", "Company Name", "Current Country", "New Country"],
    ["BAAAAAA", "Movedco", "Indonesia", "Singapore"],
    ["BBBBBBB", "Other", "Thailand", "Indonesia"],
]


def test_review_month_year_from_title_row(tmp_path):
    rows = g.read_rows(_book(tmp_path / "x.xlsx", MODERN))
    assert g.review_from_rows(rows) == (2026, 9)


def test_sections_subsections_and_non_indonesia_ignored():
    out = list(g.parse_notice_rows(MODERN, "x.xlsx", "FTSE-2026-09", False))
    key = [(r["section"], r["subsection"], r["change"], r["sedol"]) for r in out]
    assert ("Large Cap", "Inclusions", "add", "6283979") in key
    assert ("Large Cap", "Exclusions", "del", "B00FYK2") in key
    assert ("FTSE All-World (LM)", "Inclusions", "add", "6283979") in key
    assert not any(r["ftse_name"] == "Foo Corp" for r in out)
    inc = next(r for r in out if r["section"] == "Large Cap" and r["change"] == "add")
    assert inc["reason"] == "Mid to Large" and inc["country"] == "Indonesia"


def test_nationality_change_matches_either_country():
    out = [r for r in g.parse_notice_rows(MODERN, "x", "L", False)
           if r["change"] == "other"]
    assert {r["sedol"] for r in out} == {"BAAAAAA", "BBBBBBB"}
    assert next(r for r in out if r["sedol"] == "BAAAAAA")["note"] == "new_country=Singapore"


OLD_ERA = [
    ["Asia Pacific ex Japan March 2015 Review"],
    ["Small Cap"],
    ["Inclusions"],
    ["SEDOL", "Company Name", "Country", "Notes"],
    ["B7VW3S2", "Waskita Karya Persero", "Indonesia"],
    [6671422.0, "Panin Financial", "Indonesia"],
    ["B0000AA", "Elsewhere", "Vietnam"],
    ["Exclusion"],
    ["Sedol", "Company Name", "Country", "Notes"],
    ["B3T97L8", "Garuda Indonesia", "Indonesia", "x"],
]


def test_old_era_header_without_reason_and_singular_subsection():
    assert g.review_from_rows(OLD_ERA) == (2015, 3)
    out = list(g.parse_notice_rows(OLD_ERA, "old.xls", "FTSE-2015-03", False))
    assert [r["sedol"] for r in out] == ["B7VW3S2", "6671422", "B3T97L8"]
    assert out[0]["reason"] == "" and out[2]["reason"] == "x"
    assert out[2]["change"] == "del"


def test_microcap_file_flag_and_filename_cross_check(tmp_path):
    p = _book(tmp_path / "GEIS_Microcap_Sep_2018_TN.xlsx", [
        ["Asia Pacific Ex Japan September 2018 Semi-Annual Review"],
        ["Micro Cap"], ["Inclusions"], ["SEDOL", "Company Name", "Country"],
        ["B8FQLP0", "Bank Jatim", "Indonesia"]])
    rows, info = g.parse_notice_file(p)
    assert rows[0]["is_microcap_file"] is True
    assert rows[0]["review_label"] == "FTSE-2018-09" and not info["mismatch"]
    q = _book(tmp_path / "GEIS_Mar_2019.xlsx", [
        ["Asia Pacific Ex Japan September 2018 Semi-Annual Review"]])
    assert g.parse_notice_file(q)[1]["mismatch"] is True


def test_section_kind_small_cap_is_not_all_cap():
    assert g.section_kind("Small Cap") == "S"
    assert g.section_kind("FTSE AllCap (LMS)") == "AC"
    assert g.section_kind("FTSE Total-Cap (LMSµ)") == "TC"
    assert g.section_kind("FTSE All-World (LM)") == "AW"


def test_consistency_flags_unexplained_view_add():
    base = {"is_microcap_file": False, "review_label": "FTSE-2026-09", "change": "add"}
    ok = [dict(base, section="Mid Cap", sedol="A"), dict(base, section="FTSE All-World (LM)", sedol="A")]
    assert g.consistency_violations(ok) == []
    bad = ok + [dict(base, section="FTSE All-World (LM)", sedol="Z")]
    assert len(g.consistency_violations(bad)) == 1


# Snippet copied from geis-2026.pdf (pdftotext -layout), columns Mar/Jun/Sep/Dec.
CAL_2026 = """
FTSE Global Equity Index Series
review schedule 2026

FTSE Global Equity Index Series review schedule 2026
                                                            Semi-annual review             Quarterly review                Semi-annual review             Quarterly review
                                                            March 2026                     June 2026                       September 2026                 December 2026
    Index rebalance market capitalisation
                                                            31 December 2025               N/A                             30 June 2026                   N/A
    data cut-off1 & 2

    Release of FTSE GEIS Monitor List
                                                            13 February 2026               15 May 2026                     10 August 2026                 9 November 2026
    (GML)a & 10

    Release of indicative review files and
    technical notices (detailing proposed                   20 February 2026               22 May 2026                     21 August 2026                 13 November 2026
    index, free float and share in issue
    changes)

    Release of final index review files -
                                                            6 March 2026                   5 June 2026                     4 September 2026               4 December 2026
    incorporates any revisions to indicative file

    Review effective
                                                            23 March 2026                  22 June 2026                    21 September 2026              21 December 2026
    date (open of)8
"""


def test_calendar_text_parsing_2026():
    rows = {r["review_label"]: r for r in g.parse_calendar_text(CAL_2026, 2026, "geis-2026.pdf")}
    sep = rows["FTSE-2026-09"]
    assert sep["review_type"] == "semi"
    assert sep["data_cutoff"] == "2026-06-30"
    assert sep["announce_date"] == "2026-08-21"   # not the Monitor List row
    assert sep["final_date"] == "2026-09-04"
    assert sep["effective_date"] == "2026-09-21"
    assert sep["passive_trade_date"] == "2026-09-18"
    jun = rows["FTSE-2026-06"]
    assert jun["review_type"] == "quarterly" and jun["data_cutoff"] == ""
    assert dt.date.fromisoformat(jun["passive_trade_date"]).weekday() == 4


RAW = g.NOTICE_DIR


@pytest.mark.skipif(not RAW.exists(), reason="raw FTSE notices not on disk")
def test_real_files_sep2026_and_mar2026():
    files = {p.name: p for p in RAW.iterdir()}
    sep = next(p for n, p in files.items() if "September_2026" in n)
    rows, info = g.parse_notice_file(sep)
    assert info["label"] == "FTSE-2026-09" and not info["mismatch"]
    assert rows and all(r["country"].lower() == "indonesia" for r in rows)
    mar = next(p for n, p in files.items() if "March_2026" in n)
    rows, info = g.parse_notice_file(mar)
    assert info["label"] == "FTSE-2026-03" and rows == []   # Indonesia postponed


@pytest.mark.skipif(not RAW.exists(), reason="raw FTSE notices not on disk")
def test_real_legacy_xls_parses():
    p = next(p for p in RAW.iterdir() if p.suffix == ".xls")
    rows, info = g.parse_notice_file(p)
    assert info["label"] == "FTSE-2015-03" and len(rows) == 14
