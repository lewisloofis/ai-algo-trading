# Point-in-Time Reference-Data Recorder

> **Status:** 📝 Draft, scoping only (nothing built) · **Owner:** @lewis · **Created:** 2026-10-17
> **Related:** [18-alpha-and-infrastructure-split.md](./18-alpha-and-infrastructure-split.md) (§ Track A, item A-3, "Start recording point-in-time eligibility now") · [12-data-gap-backlog-and-demand-driven-capture.md](./12-data-gap-backlog-and-demand-driven-capture.md) (requirement-record format, §"Record" fields) · [21-index-rebalance-event-study.md](./21-index-rebalance-event-study.md) (IDX gap Jul-2018 to 2021) · [22-foreign-selling-rebound.md](./22-foreign-selling-rebound.md) (§2.2, §10.1 survivorship) · [03-token-rotation.md](./03-token-rotation.md) · [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md)

> **In plain words.** Stockbit tells us what is true today: which stocks exist,
> which are tradeable, which carry a warning notation, which are in LQ45. It
> keeps no history of that. If we do not write each day's answer down, the day
> is lost for good, and every later backtest silently drops the stocks that
> failed. We already write down one list per day (the funding-eligible names).
> This plan widens that to everything else Stockbit will tell us, stores each
> day's raw answer once and never rewrites it, and alerts when a day is missing.
> It places no orders and trades nothing.

## 0. Goal and non-goals

**Goal.** From the first run onward, hold an append-only, one-snapshot-per-trading-day record of
the reference data a point-in-time (PIT) universe needs, so studies can answer "what could we have
known on day *t*" without survivorship.

**Not in scope.** Prices and flows (collector, chartbit, foreign-flow already cover them), any
order or account endpoint, trading signals, a dashboard (plan 19 can read it later), and
reconstructing history that was never recorded (§7).

**Why now.** Plan 21 lost its IDX LQ45 reviews for Jul-2018 to 2021 (§"IDX gap"). Plan 22 could not see
top-40 names that were later delisted, because its universe comes from 243 mostly-surviving local
bar files (§2.2, §10.1). Plan 18 A-3 called this the one time-sensitive Track A item and built the
first slice. This plan is the second slice and does not repeat A-3.

## 1. What already exists (investigated 2026-10-17, repo read only, no network)

| Item | State |
|---|---|
| Eligibility snapshot | **Exists.** `research/screener/universe_snapshot.py`, written to `s3://marketdata-<account>/universe/dt=YYYY-MM-DD/eligibility.json`. Accruing since **2026-09-13** (plan 18 A-3 says 2026-09-13 and 09-14 recorded: 118 day-trade / 198 trading-limit / 115 eligible). Not re-verified for 09-15 onward; Phase 0 lists the keys. |
| Fields stored | `schema_version, as_of, recorded_at, source{two subsector ids}, counts, day_trade[], trading_limit[], eligible[]`: **symbols only.** Per-company `tradeable`, `company_status`, `notations`, `uma`, `effective_date`, margin/day-trade/limit info are fetched by `EmittenClient` and **thrown away**. |
| Where it runs | Collector EC2 (t4g.nano), `universe-snapshot.service` in `infra/user_data.sh.tftpl`: boot-triggered, `python3 universe-snapshot.pyz` pulled from `s3://.../artifacts/` each boot, one retry (`Restart=on-failure`, 60 s). EventBridge (`infra/scheduler.tf`) starts the box 08:30 WIB Mon-Fri and stops it 16:25, so one boot = one pre-open snapshot, none on weekend (public holidays still boot it; harmless duplicates of the last state). |
| Auth on the box | Reads `SSM /sb-collector/access`, **read-only**; never touches `/refresh` (that would rotate the collector's own session). Expired token = skipped day, loud failure, no empty write (`EmptySnapshotError`). |
| IAM | Collector role: `PutObject` only on `raw/*` and `universe/*`; `GetObject` on `artifacts/*`. `universe/` sits outside the `raw/` lifecycle rules. |
| Consumers | **None.** Plan 19 inventory: "nothing reads it back". |
| Alerting | Local runner (`research/runner/alerts.py`, Telegram, webhook fallback) only. The box has no alerter and no "snapshot missing" check exists anywhere. |
| Local PIT | None. `research/data/*` is gitignored and not backed up; `Makefile` has no PIT target. |

**Two things found while reading `emitten.py`** (both matter for normalisation, neither loses data
because raw JSON is kept):
- `_notation()` reads `notation` / `code`, but the proto field is `notation_code` (+ `notation_desc`,
  `icon_url`), so real rows fall through to `json.dumps(dict)`. The normaliser must read `notation_code`.
- `company_status` is a `Status` message in the proto (`entity/v2/company_detail.proto`), a plain string
  in the test fixtures. Its real JSON shape is unconfirmed; the normaliser accepts both a string and
  an object, and Phase 0 pins it.

## 2. Candidate data and endpoints

All paths relative to `https://exodus.stockbit.com` (`SB_API_HOST`), bearer access token, Android UA
(`screener/sb_http.SB_USER_AGENT`; Go's default UA is 403'd by the WAF). Proto root:
`/Users/lewis/Documents/AutomationProjects.nosync/sb-protobuf/proto/`. **None called in this scoping;
every "key fields" column comes from the proto and must be confirmed by Phase 0.**

| # | Data | HTTP path | Params | Key response fields | Proto | In repo? |
|---|---|---|---|---|---|---|
| E1 | Funding lists **plus per-company status** | `GET /emitten/classification/company` | `ids=<one subsector>`, `parent_id=88`, `page`, `limit` | `data[].companies[]`: `symbol, tradeable(1/0), company_status, notations[]{notation_code,notation_desc}, uma, effective_date, is_exists, day_trade_info{multiplier}, trading_limit_info{haircut_percentage}, margin_info{is_margin_trading,percentage_raw}, concentration, marketcap, last`; `data[].pagination` | `financial/emitten/public/classification/v1/classification.proto`, entity `emitten/entity/v3/company_detail.proto` | **Yes** (`screener/emitten.py`, `universe_snapshot.py`), subsectors 1000004658 day-trade, 1000005139 trading-limit. **Only symbols kept.** |
| E2 | Classification tree (sectors, boards, indexes as nodes) | `GET /emitten/classification` | `id=<parent id>` | `data[]{id,name,level,parent_id,symbol}` (`symbol` set for index nodes) | same file | No. Needed to find ids for E1 on other nodes (the proto says `parent_id` is "needed only for listing board and index ids", example 89). |
| E3 | **Index constituents** (LQ45, IDX30, IDX80, KOMPAS100, JII, ...) | `GET /emitten/indexes/{index_name}`; catalogue `GET /emitten/indexes/catalog` | `index_name`, `page`, `limit` | `data[]` `ListCompanyInIndex`: `symbol, tradeable, notation[], uma, status, corp_action`; catalogue: `section, items[]{symbol,total_stock}` | `financial/emitten/public/index/v2/publicv2index.proto`, `emitten/entity/v2/index.proto` | No |
| E4 | **Special-notation / monitoring boards** (candidate for FCA / watchlist-board membership) | `GET /emitten/indexes/special-board` | `page`, `limit` | `special_board[]{parent_id,id,symbol,name}`; members then via E1 with `ids=<id>&parent_id=<parent_id>` | `publicv2index.proto`, `entity/v2/index.proto` | No. That special board = FCA is **unconfirmed**; Phase 0 reads the names. |
| E5 | Full listed universe incl. new listings and status | `GET /emitten/v3/sector/{sector}/company` (11 sectors; sector ids from `GET /emitten/sectors`) | `page`, `limit` | `CompanyInIndustry` as E1 (status, notations, uma, tradeable, `isexist`) | `emitten/public/company/v3/company.proto`, `public/company/v2/publicv2company.proto` | No (the screener used the `/subsector/{id}/company` variant earlier, per memory note) |
| E6 | Dividends (cum/ex/record/pay dates, value) | `GET /corpaction/dividend` | `year`, `symbol` | `dividend_cumdate, _exdate, _recdate, _paydate, _value, _id, _lastupdate, corp_action_active` | `financial/corpaction/public/dividend/v1/dividend.proto`, `corpaction/entity/v1/entity.proto` | No |
| E7 | Splits and reverse splits | `GET /corpaction/stocksplit`, `GET /corpaction/reversesplit` | `year`, `symbol` | `stocksplit_cumdate, _exdate, _old, _new, _ratio, _factor, _lastupdate` | `.../public/stock_split/v1`, `.../reverse_split/v1` | No |
| E8 | Rights issues | `GET /corpaction/rightissue` | `year`, `symbol` | `rightissue_cumdate, _exdate, _recdate, _subdate, _trading_end, _ratio, _price, _adj_factor` | `.../public/right_issue/v1` | No |
| E9 | Stock dividends, bonus shares | `GET /corpaction/stock_dividend`; bonus has only a backoffice route | `year`, `symbol` | cum/ex dates, ratio | `.../public/stock_dividend/v1` | No |
| E10 | **New listings (IPO)** | `GET /corpaction/ipo` | `year` | `company_symbol, ipo_listing_date, ipo_data_detail{price,shares,listing_board}` | `.../public/ipo/v1/ipo.proto` | No |
| E11 | Shares outstanding (per day) | chartbit `GET /chartbit/{SYM}/price/daily` | `from,to,limit=0` | `shareoutstanding` in each row | n/a (verified in memory note) | **Yes**, already per-date in the daily bars. Nothing to record. |
| E12 | Foreign flow | `GET /order-trade/foreign-domestic/historical` | per symbol-year | see plan 22 §2.1 | `financial/order_trade/public/foreign_domestic/v1` | **Yes** (plan 22). Has full history; not a PIT problem. |

**Not recordable from Stockbit (no public HTTP route found in the protos):**
- *Free float and foreign-ownership limit:* no field in any `emitten` or `emitten_metadata` entity. Optional external source only (IDX listed-company data, KSEI monthly ownership); not worth building yet.
- *Index weights on a trade date:* `GetIndexWeight(index_symbol, trade_date)` exists but only as a **private gRPC** method in `emitten/private/company/v3/company.proto` (no `google.api.http`). Not reachable with a user token. IDX membership (E3) is enough for plan 21-style tests.
- *Auto-rejection (ARA/ARB) limits per symbol:* exist only inside the live order-book payload and a private `GetHistoricalAutoRejects`. They are a deterministic function of the previous close and the IDX price-band rule, so recompute them in code and do not record.
- *Delisting and suspension:* no dedicated route. They show up through E1/E5 (`tradeable=0`, `company_status`, `isexist`, notation codes) and as a symbol vanishing. Optional external: IDX announcements (delisting, suspension), which also carry dates the API lacks.
- Backoffice routes (`/emitten/backoffice/...`, `/corpaction/backoffice/...`) are admin-only. Do not use them.

## 3. What to record, prioritised

| Pri | Item | Why it is here | Cadence | Cost |
|---|---|---|---|---|
| **P0** | E1 full rows, both funding subsectors (already fetched today) | Delisting-aware universe: `tradeable`, status, notation and UMA flips are exactly the "falls off before it dies" signal ADR-0015 predicted. Free: same two calls. | daily | 0 extra calls |
| **P0** | E3 constituents for LQ45, IDX30, IDX80, KOMPAS100, JII (+ catalogue) | Plan 21's gap cannot be repaired after the fact; reviews are semi-annual so a daily snapshot catches every effective date | daily | ~6 calls |
| **P0** | E5 full listed universe, all sectors | New listings and vanished symbols; the base set for any PIT screen. Replaces the "243 local files" universe | daily | ~12 calls |
| **P1** | E4 special boards and members | Candidate FCA / monitoring-board history; also a notation proxy | daily | ~2 calls |
| **P1** | E6-E10 corporate actions, current and next year | Ex-date and split flags for event studies and for adjusting prices; carries `_lastupdate` so revisions are visible | daily (cheap) | ~7 calls |
| **P2** | IDX announcements (external, §2) | Delisting/suspension effective dates | weekly | needs scraper + review |
| **P2** | E2 tree dump | Detect new boards or index nodes appearing | weekly | 1-3 calls |

About 30 calls per day, well inside what the box does at 08:30 already.

## 4. Where it runs: recommendation

**Extend the collector box's `universe-snapshot` (stdlib Python, same `.pyz`, same boot trigger).**
Not the local runner.

| | Collector box (recommend) | Local runner post-close |
|---|---|---|
| Reliability | Already runs every weekday with no human; unaffected by the Mac's proxy/DNS outage and the 2026-10-16 skipped token refresh | Mac must be awake and online; that outage is exactly the failure that loses an unrecoverable day |
| Auth | `/sb-collector/access` from SSM, rotated every ~20 min by the collector; read-only, proven | Needs `SB_ACCESS_TOKEN` in `.env.local`, rotated at 08:45 and 16:20; fresher but only if the rotation worked |
| Cost | ~0 (box is up anyway; S3 writes are cents) | 0 |
| Constraints | 512 MB, stdlib only, no pandas or parquet; so write gzip JSON plus CSV | Pandas and parquet available |
| Alerting | None on the box | Telegram available |
| Timing | Pre-open (08:30 boot); state as the market opens | After close, 16:20 |

The weaknesses are handled, not ignored: normalised parquet is produced **locally** by a sync step
(§5), and alerting comes from a **read-only S3 check run by the local runner** (§6), so a dead Mac
costs an alert, never a day of data. Pre-open timestamps are correct for backtests anyway: the
snapshot dated *D* is "what was known at the open of *D*", which is the information set an entry at
`open_D` actually has. A symbol delisted during *D* shows in the *D+1* snapshot.

Keep `universe/` as the prefix so the **collector IAM role needs no change** (it already has
`PutObject` on `universe/*`). This avoids a `tofu apply`, which memory flags as risky (plan first, SSM
credential landmine). The one deploy action is uploading the new `.pyz` to `artifacts/`, as today.

## 5. Storage

Append-only, one folder per trading day, never overwritten.

```
s3://marketdata-<account>/universe/dt=YYYY-MM-DD/
  eligibility.json                 # existing, untouched (schema_version 1)
  pit/classification_day_trade.json.gz     # raw response body, exactly as received
  pit/classification_trading_limit.json.gz
  pit/index_<NAME>.json.gz         # one per index; pit/index_catalog.json.gz
  pit/listed_sector_<id>.json.gz   # E5, one per sector (pages concatenated, page count recorded)
  pit/special_boards.json.gz, pit/corpaction_<type>_<year>.json.gz
  pit/tables/*.csv.gz              # normalised, stdlib csv, one row per (symbol[,index])
  pit/manifest.json                # written LAST = commit marker
```

- **Raw first.** The raw body is the record; the normalised CSV can be regenerated from it when the
  parser improves (see the `notation_code` defect). Raw is what makes a wrong parser harmless.
- **Manifest** per item: `status (ok|failed|skipped)`, `http_status`, `bytes`, `sha256`, `rows`,
  `pages`, `fetched_at`, `schema_version`, `recorder_version`, `anomaly` flags. No manifest = snapshot
  missing, by definition. The manifest never contains a token, URL query string or response header.
- **No overwrite.** Upload with a conditional put (`s3api put-object --if-none-match '*'`) so the
  60 s retry or a second boot cannot replace a day. Bucket versioning is already on as a backstop.
  Phase 0 confirms the conditional put works in `ap-southeast-3`; if not, `head-object` first.
- **Guards, as in A-3.** HTTP error, empty body, or a paginated response not fully read marks the item
  `failed` and writes **nothing** for it ("an empty result recorded as fact" is the A-3 lesson). A row
  count that moves more than 20% from the previous snapshot is written with `anomaly: row_count_jump`
  and alerts, since real index and listing counts move slowly.
- **Local copy.** `make pit-sync` (new) pulls `universe/` to `research/data/pit/` and builds
  `classification.parquet`, `index_membership.parquet`, `listed.parquet`, `corp_actions.parquet`,
  each with a `dt` column and a `pit=true` flag. Not committed (gitignored, rebuilt from S3).
- **Size:** tens of KB gzipped per day, about 10 MB a year. Storage cost is negligible.

## 6. Failure handling and alerting

- Items are independent: a failed P1 item never blocks P0, and the recorder never blocks
  `collector.service` (same decoupling A-3 chose).
- One boot retry stays (60 s). No retry after midday: the box stops at 16:25 and a late run adds
  nothing that a next-morning run would not.
- **Snapshot-missing alert.** The runner's 16:20 post-close gets one new step, `pit-check`
  (read-only, `head-object` on today's `pit/manifest.json`, no token needed beyond the existing AWS
  profile). Missing manifest or any P0 item `failed` is a **WARN** through the existing Telegram
  alerter; it is skipped on non-trading days using `lib.market_calendar.CALENDAR`.
- **Weekly completeness check** (Friday post-close): for each trading day since 2026-09-13, expected
  vs present manifests and per-item `ok` counts, printed as one table and sent as SUMMARY. A gap
  ledger (`research/data/pit/gaps.csv`) is appended so studies can exclude those days explicitly.
- Token trouble is the likeliest failure (A-3 notes it; plan 03). It shows up as 401 on every item;
  the alert text names the token path (SSM `/access`), never a value.

## 7. Backfill policy

Most items **cannot** be backfilled: classification, notation, UMA, tradeable, special-board and
index membership are present-tense only (`effective_date` is null; ADR-0015). Every missed day is
permanent, and that is the reason for the weekly check.

Exceptions, all flagged `pit=false` because the answer is *as known now*, revisions included:
- **E6-E10 corporate actions** accept `year`, so a one-time pull for 2014 to now is possible. Useful
  for event studies, not for "what was known then".
- **Index membership before 2026-09-13**: only plan 21's manual sources (IDX Wayback to Jan-2018,
  secondary press for the 2018-2021 gap). Do not mix into this store.
- Phase 0 should also check whether `E1`/`E5` expose any `listing_date` or delisting date field.

The honest consequence: this store starts on 2026-09-13 (eligibility) and on its own go-live date
(everything else). A study covering 2016-2026 still needs a survivorship caveat for everything
before that. The value is forward: after about 6 months it supports a true PIT universe for any new study.

## 8. How studies consume it

New module `research/lib/pit.py` (small, pandas, reads `research/data/pit/`):

- `universe_as_of(day, rule)`: symbols with `tradeable=1`, status active, no excluded notation, in the
  funding set and (optionally) in a named index on *day*. `rule` is a frozen, named dataclass so a
  pre-registration can cite it.
- `lifecycle(symbol)`: first and last snapshot date, and the date `tradeable` or status flipped. Gives
  a **forward delisting ledger**: names that left or flipped are kept in the universe up to the flip
  date, so a plan-22-style test sees the failures.
- `snapshot_days()` and `gaps()`: so a study states its coverage the way plan 22 §2.3 does.
- Studies with a window before PIT start must **union** local survivor bars with PIT names and
  print the coverage table; they may not claim PIT cleanliness before the first snapshot.

Cross-check once per week: every symbol in E5 has local daily bars or is queued for `refresh-prices`
(this also fixes the "A.parquet" kind of stray in plan 22 §10.1).

## 9. Phases and effort

| Phase | Work | Effort | Done when |
|---|---|---|---|
| 0 | Owner runs 6 authenticated calls (E1, E2, E3 one index, E4, E5 one sector, E6) and saves **redacted** samples under `research/screener/fixtures/`; confirm shapes (`company_status`, `notation_code`, pagination, index names, special-board names); `aws s3 ls universe/` to confirm A-3 has run daily; test conditional put | 0.5 day (owner) | Shapes pinned in this doc's §2 |
| 1 | P0 recorder: `screener/pit_recorder.py` (stdlib), widen `universe_snapshot.main` to also run it, manifest, guards, no-overwrite; rebuild `.pyz`, owner uploads to `artifacts/` | 1.5 days | Two consecutive real boots write a manifest |
| 2 | `make pit-sync`, `lib/pit.py`, runner `pit-check` step, weekly report | 1.5 days | Missing-day alert fires in a dry run; `universe_as_of` returns a set for a recorded day |
| 3 | P1 items (special boards, corp actions), one-time `pit=false` corp-action backfill | 1 day | Manifest shows all P1 `ok` |
| 4 | P2 only if a study blocks on it (IDX announcements) | decide later | n/a |

Order matters: Phase 1 first, because each day without it is lost.

## 10. Tests and security

- **Tests use fake HTTP only** (the `_client(monkeypatch, body)` pattern in
  `test_emitten_classification.py`): pagination refusal, empty body, HTTP 401/403/429, row-count jump,
  duplicate-day no-overwrite, manifest written last, partial failure leaves other items intact,
  `notation_code` and both `company_status` shapes normalise, gap detection across a holiday.
  No live call in CI. Fixtures are synthetic or redacted samples (plain symbols only; this data holds
  no customer data, but keep any account-linked field out).
- **Token handling, as `foreign_flow/client.py`:** token only in the `Authorization` header; errors
  go through `screener.sb_http`, which strips query string and headers; no URL, header or token in the
  manifest, logs or alert text. The recorder only uses the **access** token and, on the box, reads it
  read-only from SSM; it never reads `/refresh` or any `.env.local`/`.sb-tokens.json`.
- **Read-only API use:** GET only, no order or account route, no trading-token path. No customer
  data is requested; the endpoints are market reference data.
- **Politeness:** one request at a time, a short pause, ~30 calls/day, honest UA as the existing code
  does, stop on HTTP 429 and mark the item failed.
- IAM stays write-only for the box; reads for `pit-sync` and `pit-check` use the owner's local AWS profile.

## 11. Open questions for the owner

1. **Pre-open timestamps acceptable?** The box only runs 08:30-16:25, so snapshot *D* is the state at
   the open. *Proposed default:* yes, and document it in `lib/pit.py`; no second post-close run.
2. **Keep the `universe/` prefix to avoid an IAM `tofu apply`?** *Proposed default:* yes; split to
   a `pit/` bucket prefix only if the folder gets crowded.
3. **One-time corporate-action backfill (2014 to now, about 70 calls) flagged `pit=false`?**
   *Proposed default:* yes, in Phase 3, kept apart from the PIT snapshots.
4. **IDX announcements scraper (delisting and suspension dates) as P2?** *Proposed default:* no for now;
   revisit after 4 weeks of snapshots show whether `tradeable`/status alone catch delistings.

---

Back to [plans index](./README.md)
