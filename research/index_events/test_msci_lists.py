"""Tests for index_events.msci_lists. Snippets are copied from real MSCI Public Lists."""
import pytest

from index_events import msci_lists as m

HEAD_2014 = """Geneva, August 13, 2014

MSCI GLOBAL SMALL CAP INDEXES

The following are changes in constituents for the MSCI Global Small Cap Indexes
which will take place as of the close of
August 29, 2014.

SUMMARY PER COUNTRY and PER REGION:

Region         Country          Added   Deleted
Asia Pacific
               INDONESIA        0                            1
"""

BLOCK_2014 = """
ASIA PACIFIC

MSCI INDONESIA INDEX
Additions                                  Deletions
None                                       SELAMAT SEMPURNA


MSCI KOREA INDEX
Additions                                  Deletions
None                                       STX ENGINE CO
"""

BLOCK_2025_PAGEBREAK = """MSCI INDONESIA INDEX
Additions                        Deletions
INDAH KIAT PULP & PAPER          BANK PEMBANGUNAN DAERAH
MERDEKA COPPER GOLD              ERAJAYA SWASEMBADA TBK

Page 2                     MSCI Global Small Cap Indexes - February 11, 2025   © MSCI 2025, All rights reserved
SARIGUNA PRIMATIRTA             METRODATA ELECTRONICS
                                WIJAYA KARYA


MSCI PHILIPPINES INDEX
Additions                        Deletions
None                             AYALA LAND
"""

BLOCK_2026_STACKED = """MSCI INDONESIA INDEX
Additions                                 Deletions
None                                      CHAROEN POKPHAND INDO
                                          GOTO GOJEK TOKOPEDIA



Page 1                                MSCI Global Standard Indexes - August 12, 2026   © MSCI 2026, All rights reserved
MSCI PHILIPPINES INDEX
"""

BLOCK_LAST_IN_REGION = """MSCI INDONESIA INDEX
Additions                 Deletions
AMMAN MINERAL INTL        VALE INDONESIA


EUROPE, MIDDLE EAST AND AFRICA

MSCI FRANCE INDEX
Additions                 Deletions
SOMETHING SA              None
"""


def block(text):
    return m.parse_block(m.extract_country_block(text))


def test_dates_2014_effective_date_wrapped_across_lines():
    assert m.parse_announce_date(HEAD_2014) == "2014-08-13"
    assert m.parse_effective_date(HEAD_2014) == "2014-08-29"
    assert m.review_label("2014-08-13") == "MSCI-2014-08"


def test_dateline_2026_and_non_month_end_effective():
    text = "Geneva, August 12, 2026\n\nwhich will take place as of the close of August 31, 2026.\n"
    assert m.parse_announce_date(text) == "2026-08-12"
    assert m.parse_effective_date(text) == "2026-08-31"


def test_missing_dates_raise_not_guess():
    with pytest.raises(m.ParseError):
        m.parse_announce_date("MSCI GLOBAL STANDARD INDEXES\nno dateline here\n")
    with pytest.raises(m.ParseError):
        m.parse_effective_date("Geneva, August 12, 2026\nnothing about a close\n")
    with pytest.raises(m.ParseError):
        m.parse_announce_date("Geneva, Smarch 12, 2026\n")


def test_2014_block_none_placeholder_and_other_countries_excluded():
    assert block(BLOCK_2014) == {"Additions": [], "Deletions": ["SELAMAT SEMPURNA"]}


def test_summary_table_counts():
    assert m.country_summary(HEAD_2014) == (0, 1)
    assert m.country_summary("no table") == (0, 0)


def test_page_break_inside_block_does_not_drop_rows():
    assert block(BLOCK_2025_PAGEBREAK) == {
        "Additions": ["INDAH KIAT PULP & PAPER", "MERDEKA COPPER GOLD", "SARIGUNA PRIMATIRTA"],
        "Deletions": ["BANK PEMBANGUNAN DAERAH", "ERAJAYA SWASEMBADA TBK", "METRODATA ELECTRONICS", "WIJAYA KARYA"],
    }


def test_stacked_names_in_one_column_are_separate_securities():
    assert block(BLOCK_2026_STACKED) == {
        "Additions": [],
        "Deletions": ["CHAROEN POKPHAND INDO", "GOTO GOJEK TOKOPEDIA"],
    }


def test_block_ends_at_region_heading_not_at_all_caps_names():
    assert block(BLOCK_LAST_IN_REGION) == {"Additions": ["AMMAN MINERAL INTL"], "Deletions": ["VALE INDONESIA"]}


def test_names_containing_country_word_are_not_mistaken_for_block_heading():
    text = "MSCI INDONESIA INDEX\nAdditions        Deletions\nACE HARDWARE INDONESIA     SEMEN INDONESIA\n"
    assert block(text) == {"Additions": ["ACE HARDWARE INDONESIA"], "Deletions": ["SEMEN INDONESIA"]}


def test_country_absent_gives_no_rows():
    text = HEAD_2014 + "\nMSCI KOREA INDEX\nAdditions   Deletions\nNone   X\n"
    assert m.parse_text(text, "f.pdf", "sc") == []


def test_unplaceable_line_raises():
    with pytest.raises(m.ParseError):
        m.parse_block(["SOME NAME   OTHER   THIRD"])


def test_parse_text_rows_and_new_flag():
    text = HEAD_2014.replace("0                            1", "1                            1") + (
        "\nMSCI INDONESIA INDEX\nAdditions        Deletions\nFOO (NEW)         BAR\n")
    rows = m.parse_text(text, "MSCI_Aug14_sc.pdf", "sc")
    assert [(r.change, r.msci_name, r.index_name, r.effective_date) for r in rows] == [
        ("add", "FOO (NEW)", "MSCI_SC", "2014-08-29"), ("del", "BAR", "MSCI_SC", "2014-08-29")]
    assert rows[0].note == "(NEW) flag in list" and rows[0].section == "Additions"


def test_tag_migrations_pairs_across_indexes_only():
    def row(idx, change, name, label="MSCI-2019-11"):
        return m.Row("MSCI", idx, label, "2019-11-12", "2019-11-26", "x", change, name, "f", "")
    rows = [row("MSCI_ST", "add", "ACE HARDWARE INDONESIA"), row("MSCI_SC", "del", "ACE HARDWARE INDONESIA"),
            row("MSCI_SC", "add", "INDOSAT"), row("MSCI_ST", "del", "INDOSAT", label="MSCI-2020-05")]
    out = m.tag_migrations(rows)
    assert [r.change for r in out] == ["migrate_in", "migrate_out", "add", "del"]
    assert "MSCI_SC" in out[0].note


@pytest.mark.skipif(not (m.RAW_DIR / "st" / "MSCI_Aug26_st.pdf").exists(), reason="raw MSCI PDFs absent")
def test_integration_real_pdf_matches_summary_table():
    path = m.RAW_DIR / "st" / "MSCI_Aug26_st.pdf"
    m._find_pdftotext()
    text = m.pdf_to_text(path)
    rows = m.parse_text(text, path.name, "st")
    assert m.country_summary(text) == (0, 2)
    assert sorted(r.msci_name for r in rows) == ["CHAROEN POKPHAND INDO", "GOTO GOJEK TOKOPEDIA"]
    assert {r.announce_date for r in rows} == {"2026-08-12"}
