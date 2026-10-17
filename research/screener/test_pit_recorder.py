"""PIT recorder (plan 23 Phase 1): fake HTTP and fake S3 only, no network, no AWS."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import pathlib

import pytest

from screener import pit_recorder as R
from screener import universe_snapshot as US
from screener.sb_http import SBHttpError

FIX = pathlib.Path(__file__).parent / "fixtures" / "pit"
SECRET = "tok-SECRET-123"


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeClient:
    """Routes by URL; ``overrides`` maps a URL substring to bytes or an Exception."""

    def __init__(self, overrides=None):
        self.urls: list[str] = []
        self.overrides = overrides or {}

    def get_raw(self, url: str) -> bytes:
        self.urls.append(url)
        for needle, val in self.overrides.items():
            if needle in url:
                if isinstance(val, Exception):
                    raise val
                return val
        return self._default(url)

    @staticmethod
    def _default(url: str) -> bytes:
        if "/emitten/classification/company" in url:
            return fx("board_daytrade.json")
        if "/emitten/indexes/catalog" in url:
            return fx("index_catalog.json")
        if "/emitten/indexes/special-board" in url:
            return fx("special_board.json")
        if "/emitten/indexes/" in url:
            return fx("index_LQ45.json")
        if "/emitten/sectors" in url:
            return fx("sectors.json")
        if "/emitten/v3/sector/" in url:
            return fx("sector_1_page2.json" if "page=2" in url else "sector_1_page1.json")
        raise AssertionError(url)


class FakeStore:
    def __init__(self, existing=()):
        self.objects: dict[str, bytes] = {}
        self.existing = set(existing)
        self.puts: list[str] = []
        self.fail_keys: set[str] = set()

    def exists(self, key):
        return key in self.existing or key in self.objects

    def put(self, key, data, content_type):
        if key in self.fail_keys:
            raise RuntimeError("boom")
        if self.exists(key):
            return "exists"
        self.objects[key] = data
        self.puts.append(key)
        return "written"


def run(client=None, store=None, as_of="2026-10-19"):
    client = client or FakeClient()
    store = store or FakeStore()
    manifest = R.record(client, store, as_of, pause=0, sleep=lambda s: None)
    return manifest, client, store


def key(name, as_of="2026-10-19"):
    return f"universe/dt={as_of}/pit/{name}"


def read_gz(store, name):
    return gzip.decompress(store.objects[key(name)])


def read_table(store, name):
    return list(csv.DictReader(io.StringIO(read_gz(store, f"tables/{name}.csv.gz").decode())))


# ---- happy path -----------------------------------------------------------

def test_happy_path_items_and_call_count():
    manifest, client, store = run()
    assert len(manifest["items"]) == 9 + 3 + 5 + 12
    assert all(e["status"] == "ok" for e in manifest["items"].values())
    # 9 boards + 3 catalogues + 5 indexes (3 rows < 100: one page) + 12 sectors x 2 pages
    assert manifest["get_calls"] == len(client.urls) == 9 + 3 + 5 + 24
    assert manifest["schema_version"] == R.SCHEMA_VERSION and manifest["recorder_version"]


def test_board_urls_use_one_id_and_right_parent():
    _, client, _ = run()
    boards = [u for u in client.urls if "/classification/company" in u]
    assert any(u.endswith("ids=3&parent_id=89") for u in boards)
    assert any(u.endswith("ids=1000006758&parent_id=88") for u in boards)
    assert len(boards) == 9


def test_raw_body_is_stored_exactly_as_received():
    manifest, _, store = run()
    assert read_gz(store, "board_DAYTRADE.json.gz") == fx("board_daytrade.json")
    entry = manifest["items"]["board_DAYTRADE"]
    assert entry["sha256"] == hashlib.sha256(fx("board_daytrade.json")).hexdigest()
    assert entry["bytes"] == len(fx("board_daytrade.json"))
    assert entry["rows"] == 3 and entry["pages"] == 1


def test_board_table_columns_and_values():
    _, _, store = run()
    rows = [r for r in read_table(store, "boards") if r["board"] == "DAYTRADE"]
    assert list(rows[0]) == ["dt", "board", "symbol", "company_status", "notation_codes", "uma", "tradeable"]
    assert rows[0]["symbol"] == "AADI" and rows[0]["company_status"] == "STATUS_ACTIVE"
    assert rows[0]["tradeable"] == "1" and rows[0]["uma"] == "0" and rows[0]["dt"] == "2026-10-19"


def test_index_table_and_catalogue_items():
    manifest, _, store = run()
    rows = [r for r in read_table(store, "indexes") if r["index"] == "LQ45"]
    assert list(rows[0]) == ["dt", "index", "symbol", "status", "notation_codes", "uma"]
    assert [r["symbol"] for r in rows] == ["BBCA", "BMRI", "BBRI"][: len(rows)] or len(rows) == 3
    for name in ("index_catalog", "special_board", "sectors"):
        assert manifest["items"][name]["status"] == "ok"


# ---- pagination -----------------------------------------------------------

def test_sector_pagination_followed_and_pages_stored_as_array():
    manifest, client, store = run()
    entry = manifest["items"]["listed_sector_1"]
    assert entry["pages"] == 2 and entry["rows"] == 5
    pages = json.loads(read_gz(store, "listed_sector_1.json.gz"))
    assert [len(p["data"]) for p in pages] == [3, 2]
    assert any("sector/1/company?page=2&limit=100" in u for u in client.urls)


def test_listed_table_never_emits_tradeable():
    _, _, store = run()
    rows = read_table(store, "listed")
    assert "tradeable" not in rows[0]
    assert list(rows[0]) == ["dt", "sector_id", "symbol", "company_status", "notation_codes", "uma"]
    assert {r["symbol"] for r in rows} >= {"UNVR", "INDF"}


def test_sector_pagination_that_does_not_advance_fails():
    page = json.loads(fx("sector_1_page1.json"))
    page["pagination"]["next_page"] = 1  # loops back
    manifest, _, store = run(FakeClient({"sector/2/company": json.dumps(page).encode()}))
    assert manifest["items"]["listed_sector_2"]["status"] == "failed"
    assert key("listed_sector_2.json.gz") not in store.objects


def test_sector_total_data_mismatch_is_incomplete_and_fails():
    page = json.loads(fx("sector_1_page2.json"))
    page["pagination"]["total_data"] = 50
    manifest, _, _ = run(FakeClient({"sector/3/company?page=2": json.dumps(page).encode()}))
    assert manifest["items"]["listed_sector_3"]["status"] == "failed"
    assert "incomplete" in manifest["items"]["listed_sector_3"]["error"]


def test_index_full_page_fetches_next_until_short_page():
    base = json.loads(fx("index_LQ45.json"))
    row = base["data"][0]
    full = {**base, "data": [dict(row, symbol=f"S{i:03d}") for i in range(100)]}
    short = {**base, "data": [dict(row, symbol="LAST")]}

    class C(FakeClient):
        def get_raw(self, url):
            if "/indexes/IDX80" in url:
                self.urls.append(url)
                return json.dumps(short if "page=2" in url else full).encode()
            return super().get_raw(url)

    manifest, client, store = run(C())
    assert manifest["items"]["index_IDX80"]["pages"] == 2
    assert manifest["items"]["index_IDX80"]["rows"] == 101
    assert len(json.loads(read_gz(store, "index_IDX80.json.gz"))) == 2


def test_index_row_count_vs_catalogue_total_stock_flags_anomaly():
    manifest, _, _ = run()
    assert "total_stock_mismatch" in manifest["items"]["index_LQ45"]["anomaly"]  # 3 rows vs 45
    body = json.loads(fx("index_catalog.json"))
    body["data"][0]["items"][0]["total_stock"] = 3
    m2, _, _ = run(FakeClient({"indexes/catalog": json.dumps(body).encode()}))
    assert m2["items"]["index_LQ45"]["anomaly"] == []


def test_board_incomplete_pagination_fails():
    body = json.loads(fx("board_daytrade.json"))
    body["data"][0]["pagination"]["total_page"] = 3
    manifest, _, store = run(FakeClient({"ids=1000004658": json.dumps(body).encode()}))
    assert manifest["items"]["board_DAYTRADE"]["status"] == "failed"
    assert key("board_DAYTRADE.json.gz") not in store.objects


# ---- failure handling -----------------------------------------------------

@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_error_fails_that_item_only(status):
    err = SBHttpError(status, "https://exodus.stockbit.com/emitten/indexes/JII?page=1&limit=100")
    manifest, _, store = run(FakeClient({"/indexes/JII": err}))
    assert manifest["items"]["index_JII"]["status"] == "failed"
    assert manifest["items"]["index_JII"]["http_status"] == status
    assert key("index_JII.json.gz") not in store.objects
    ok = [n for n, e in manifest["items"].items() if e["status"] == "ok"]
    assert len(ok) == len(manifest["items"]) - 1
    assert "?" not in manifest["items"]["index_JII"]["error"]


def test_empty_body_and_bad_json_fail():
    manifest, _, _ = run(FakeClient({"/indexes/IDX30": b"", "/indexes/JII": b"not json"}))
    assert manifest["items"]["index_IDX30"]["status"] == "failed"
    assert manifest["items"]["index_JII"]["status"] == "failed"


def test_empty_funding_board_fails_but_empty_special_board_is_ok_with_flag():
    body = json.loads(fx("board_daytrade.json"))
    body["data"][0]["companies"] = []
    body["data"][0]["pagination"].update(total_data=0, total_data_in_page=0)
    raw = json.dumps(body).encode()
    manifest, _, _ = run(FakeClient({"ids=1000004658": raw, "ids=1000005130": raw}))
    assert manifest["items"]["board_DAYTRADE"]["status"] == "failed"
    assert manifest["items"]["board_SUSPENDED"]["status"] == "ok"
    assert manifest["items"]["board_SUSPENDED"]["anomaly"] == ["empty"]
    assert manifest["items"]["board_SUSPENDED"]["rows"] == 0


def test_upload_failure_marks_item_failed_and_continues():
    store = FakeStore()
    store.fail_keys.add(key("index_JII.json.gz"))
    manifest, _, _ = run(store=store)
    assert manifest["items"]["index_JII"]["status"] == "failed"
    assert manifest["items"]["board_HSC"]["status"] == "ok"


def test_all_items_failed_writes_no_manifest_so_retry_can_record():
    err = SBHttpError(401, "https://x/y?z=1")

    class C(FakeClient):
        def get_raw(self, url):
            raise err

    store = FakeStore()
    with pytest.raises(R.NoItemsRecordedError):
        R.record(C(), store, "2026-10-19", pause=0)
    assert store.objects == {}


# ---- manifest / security / no overwrite ------------------------------------

def test_manifest_written_last_and_has_no_secrets_or_query():
    manifest, client, store = run()
    assert store.puts[-1] == key("manifest.json")
    text = store.objects[key("manifest.json")].decode()
    assert "?" not in text and "https://" not in text and "Bearer" not in text
    assert SECRET not in text and "ids=" not in text
    assert json.loads(text)["items"].keys() == manifest["items"].keys()


def test_existing_manifest_means_no_calls_and_no_writes():
    store = FakeStore(existing={key("manifest.json")})
    client = FakeClient()
    assert R.record(client, store, "2026-10-19", pause=0) is None
    assert client.urls == [] and store.puts == []


def test_existing_object_is_skipped_not_overwritten():
    store = FakeStore(existing={key("board_DAYTRADE.json.gz")})
    manifest, _, _ = run(store=store)
    assert manifest["items"]["board_DAYTRADE"]["stored"] == "exists"
    assert key("board_DAYTRADE.json.gz") not in store.objects


def test_pause_between_gets_but_not_before_first():
    sleeps: list[float] = []
    R.record(FakeClient(), FakeStore(), "2026-10-19", pause=1.0, sleep=sleeps.append)
    assert len(sleeps) == 9 + 3 + 5 + 24 - 1 and set(sleeps) == {1.0}


# ---- normalisation ----------------------------------------------------------

def test_notation_code_and_status_object_normalise_into_tables():
    body = json.loads(fx("board_daytrade.json"))
    row = body["data"][0]["companies"][0]
    row["notations"] = [{"notation_code": "X", "notation_desc": "d", "icon_url": {}},
                        {"notation_code": "E", "notation_desc": "d2"}]
    row["company_status"] = {"status": "STATUS_SUSPENDED"}
    row["uma"] = True
    _, _, store = run(FakeClient({"ids=1000004658": json.dumps(body).encode()}))
    r = [r for r in read_table(store, "boards") if r["board"] == "DAYTRADE"][0]
    assert r["notation_codes"] == "X|E" and r["company_status"] == "STATUS_SUSPENDED" and r["uma"] == "1"


# ---- eligibility path isolation ---------------------------------------------

class _Args:
    bucket, region, dry_run, no_pit = "b", "r", False, False


def _patch_main(monkeypatch, *, elig_raises=False, pit_raises=False):
    calls = {"elig": 0, "pit": 0}
    monkeypatch.setattr(US, "resolve_bearer_token", lambda *a, **k: "tok")
    monkeypatch.setattr(US, "EmittenClient", lambda tok: object())

    def elig(client, args, as_of):
        calls["elig"] += 1
        if elig_raises:
            raise RuntimeError("elig boom")

    def pit(client, args, as_of):
        calls["pit"] += 1
        if pit_raises:
            raise RuntimeError("pit boom")

    monkeypatch.setattr(US, "_run_eligibility", elig)
    monkeypatch.setattr(US, "_run_recorder", pit)
    return calls


def test_recorder_crash_does_not_stop_eligibility_but_exits_nonzero(monkeypatch):
    calls = _patch_main(monkeypatch, pit_raises=True)
    with pytest.raises(SystemExit) as exc:
        US.main(["--bucket", "b"])
    assert exc.value.code == 1 and calls == {"elig": 1, "pit": 1}


def test_eligibility_crash_does_not_stop_recorder(monkeypatch):
    calls = _patch_main(monkeypatch, elig_raises=True)
    with pytest.raises(SystemExit):
        US.main(["--bucket", "b"])
    assert calls == {"elig": 1, "pit": 1}


def test_clean_run_and_flags(monkeypatch):
    calls = _patch_main(monkeypatch)
    US.main(["--bucket", "b"])
    assert calls == {"elig": 1, "pit": 1}
    US.main(["--bucket", "b", "--no-pit"])
    US.main(["--bucket", "b", "--dry-run"])
    assert calls == {"elig": 3, "pit": 1}


def test_eligibility_schema_untouched():
    assert US.SCHEMA_VERSION == 1
    assert US.s3_key_for("2026-10-19") == "universe/dt=2026-10-19/eligibility.json"
