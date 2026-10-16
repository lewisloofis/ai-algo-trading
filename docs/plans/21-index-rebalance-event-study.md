# Index-Rebalance Event Study: Pre-Registration

> **Status:** 🔒 Frozen 2026-10-16 (§9a) · pre-registration · **Owner:** @lewis · **Updated:** 2026-10-16 (MSCI reclassification re-scope, §0)
> **Related:** [first-candidate shortlist](../research/2026-10-15-first-candidate-shortlist.md) (why the catalogue is exhausted) · [alpha-search-2026-10](../research/alpha-search-2026-10.md) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [18-alpha-and-infrastructure-split.md](./18-alpha-and-infrastructure-split.md) · [20-bnbr-forward-test-preregistration.md](./20-bnbr-forward-test-preregistration.md) (template) · [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md)

> **What this is.** The data-sourcing plan and the frozen test rules for one
> hypothesis family: **IDX index additions and deletions move prices because
> passive money is forced to trade on a known date.** It is written before any
> event data is collected, so the gates cannot be fitted to the result.
>
> **Why this family.** The 2026-10-15 shortlist found that cost is the binding
> constraint. A round trip costs ~100–138 bps (40 bps fee + tax, one tick, impact),
> and the catalogue families earn 0–65 bps over the null at holds of 1–20 days.
> An edge has to come from moves that are large per trade *and* caused by
> something other than market direction. Forced index flows fit both: the dates
> are known in advance and the flow happens regardless of the regime. In a bear
> market that matters, because long-only drift strategies cannot work there.
>
> **What it is not.** A strategy, or evidence of one. Nothing here places an order.

---

## 0. MSCI freeze and reclassification (2026-10-16 re-scope)

**Facts (primary: MSCI).**
* **2026-01-28:** MSCI froze Indonesia from the **Feb-2026 review** onward: no
  FIF or NOS increases, no additions to the Indonesia IMI, and no upward
  size-segment migrations. Deletions continue, including removals of names
  flagged under the authorities' High Shareholding Concentration (HSC)
  framework. The freeze was kept in place for the May-2026 and Aug-2026 reviews,
  and MSCI has said it will remain until further notice.
* **2026-10-15:** MSCI opened a consultation to reclassify Indonesia from
  **Emerging to Standalone**. Feedback is due 2027-04-30 and the decision by
  2027-06-30. MSCI "expects to proceed" unless the consultation shows material
  improvement. Frontier status was considered and rejected.

**Consequences for this plan.**
1. **MSCI additions (H1) are a mechanism probe only; MSCI deletions (H2) stay
   live until Standalone.** Under the freeze MSCI makes no additions, but
   deletions continue and are heavy: 2026-02/05/08 deleted or migrated down 35
   Indonesian names. So an MSCI H2 pass can still lead to a forward test, for
   as long as MSCI EM trackers hold Indonesian names. Under Standalone status,
   EM trackers stop trading Indonesian names at reviews at all, and the
   forward case ends on the implementation date (corrected 2026-10-16 from
   "MSCI cannot lead to a forward test").
2. **Freeze reviews are a different regime.** From Feb-2026 on, MSCI reviews
   contain deletions only, often HSC-driven, in a stressed market. Tag them
   `review_type=freeze`. They are **excluded from the MSCI G5 holdout** and
   reported separately as descriptive results (§4.2).
3. **IDX and FTSE carry every forward case.** They are the weaker flows (§7),
   so this family's prior is lower than it was on 2026-10-15. FTSE becomes the
   only large foreign-passive provider left.
3a. **FTSE froze too.** FTSE postponed its Mar-2026 Indonesia review (FAQ
   effective 2026-02-09) and then cut heavily in Sep-2026. FTSE reviews from
   Mar-2026 on get `review_type=freeze` and are excluded from the FTSE G5
   holdout, the same treatment as MSCI. FTSE's holdout is Mar-2022 → Sep-2025.
4. **The reclassification itself** would be a one-off forced deletion of every
   Indonesian name from MSCI EM. With N = 1 it cannot be tested under G2/G7.
   It is logged as a watch item (§8 Q4) and is outside this plan's gates.
5. **Phase order changes (§6):** run §1a on the MSCI data already fetched
   *before* the manual IDX/FTSE transcription, as a cheap go/no-go.

## 1. Hypotheses (frozen at §9 sign-off)

All long-only (no shorting, plan 10 `long_short_infeasible`). Entry convention
G1: decision on information public at session `s`, entry at `close_{s+1}`
(`entry_convention.ENTRY_NEXT_CLOSE`), `open_{s+1}` as sensitivity.

| id | event | window | trade | mechanism |
|---|---|---|---|---|
| **H1** | addition announced | announcement → effective | buy at `close_{a+1}`, sell at the close of the **last session before passive buying** (§3 per provider) | front-running of the known passive buy |
| **H2** | deletion effective | after effective → +k | buy at `close_{e+1}`, hold k ∈ {5, 10, 20} | rebound after forced selling ends |
| **H3** | addition effective | effective → +k | *control only*: expected to be ≤ 0 (post-inclusion reversal). Not tradeable long; reported to confirm the mechanism. |
| H4 | predicted addition / deletion | before announcement | **conditional.** Promoted to the main hypothesis if §1a shows that most of the move happens before the announcement (rule in §1a). Needs a rules engine replicating each provider's published eligibility screen (IDX liquidity/free-float/concentration filters; MSCI/FTSE size and liquidity cut-offs) using only data available at the provider's cut-off date. A promoted H4 gets its own dated pre-registration amendment (§9), with cells and gates fixed **before** it is run. |

Providers, in priority order: **IDX** (LQ45, IDX30, IDX80; KOMPAS100 as a
sensitivity), **MSCI** (Global Standard and Small Cap, Indonesia), **FTSE GEIS**
(Large/Mid/Small, Indonesia). Providers are tested separately and also pooled.
Flow sizes differ by orders of magnitude (MSCI EM trackers ≫ local LQ45 ETFs), so
a pooled pass with no single-provider pass does not count (§5). Since the
2026-10-16 re-scope (§0), only an IDX or FTSE pass can become a forward
candidate; MSCI is tested for the mechanism only.

## 1a. Where the move happens (descriptive, runs before any gate)

Market participants publish predicted changes days or weeks before the
announcement. If the move is priced in early, H1 fails, and a bare pass/fail
cannot tell "no index effect" from "an effect that's all priced in before the
announcement". So before G1–G8, and on the **development window only**, plot
the event-time path:

* the mean and median **market-adjusted** cumulative return from `a−20` to
  `p+20`, separately for additions and deletions, per provider, with a band
  clustered by review;
* the move split into five legs that add up to the total, each with its share of
  the total and a clustered t:

| leg | window | who can capture it |
|---|---|---|
| L1 pre-announcement | `close_{a−20}` → `close_a` | anticipators only (H4) |
| L2 announcement gap | `close_a` → `open_{a+1}` | nobody (not executable) |
| L3 first session | `open_{a+1}` → `close_{a+1}` | `open_{a+1}` sensitivity only |
| L4 run-in | `close_{a+1}` → `close_p` | H1 |
| L5 post-passive | `close_p` → `close_{p+20}` | H2 (deletions); H3 control (additions) |

**What each outcome means (fixed now, read after):**

| result | reading | action |
|---|---|---|
| L1 ≥ 50% of the L1+L2+L3+L4 total for additions (or deletions), and L1's t ≥ 2 | the market anticipates the changes | **promote H4.** H1 still runs, but a fail is expected and is not a reason to close the family |
| L2 dominates | the move is priced in instantly at the open | nothing long-only to capture on that side; H1 is expected to fail |
| L4 positive and material | the passive buying still has a pull | H1 is the live candidate |
| deletions: L1–L4 negative, then L5 positive | early sellers deepen the dip, and it rebounds after the forced flow | H2 is the live candidate; anticipation helps it |

This step is descriptive. It adds no cells to the G7 Holm count, and it never
passes a candidate on its own. It decides which hypotheses are worth testing,
not whether any of them works.

## 1b. H4 candidate selection (used only if §1a promotes H4)

The question: which names are anticipated as additions or deletions, using
only data available at the provider's cut-off date?

**1. Rules replica (primary, the only backtestable source).** Rebuild each
provider's published screen as of its data cut-off and score each name by how
far it is from the line.

| provider | screen (confirm against the methodology docs in Phase 0) | cut-off used |
|---|---|---|
| IDX LQ45 / IDX30 / IDX80 | listed ≥ ~3 months; ranked by regular-market traded value, trading frequency, days traded, free-float market cap; minimum free float; high-shareholding-concentration exclusion from Apr-2026 (**this rule did not exist before 2026; applying it to earlier reviews leaks the future**) | end of the evaluation period stated in the IDX index handbook / each PDF |
| MSCI Global Standard / Small Cap | free-float market cap vs. the Indonesia size-segment cut-off; liquidity (ATVR); free-float and foreign-room minimums; buffers (a newcomer must clear about 1.8× the cut-off, while incumbents keep their place down to about two-thirds of it) | price cut-off on an **undisclosed** day among the last 10 business days of Jan/Apr/Jul/Oct, so score on the whole window and treat names that flip inside it as uncertain |
| FTSE GEIS | size percentile bands with buffers, free-float minimum, monthly turnover tests | last business day of Jun (Sep review) and Dec (Mar review); fixed and known |

* **Candidates:** a non-member ranked above the weakest incumbent *beyond the
  buffer* is a likely addition; an incumbent below its buffer is a likely deletion.
* **Trade only the clear cases:** a margin threshold set on the development
  window and frozen before the holdout. A wrong prediction costs a full round
  trip for nothing, so H4's hurdle is `cost / hit_rate`; precision matters more
  than recall.
* **Entry:** `close_{c+1}`, where `c` is the cut-off session (for MSCI, the
  last day of the 10-day window). Exit at `close_a` (announcement) or `close_p`
  (passive trade), both pre-registered as separate cells.

**2. Market confirmation (secondary feature, never a candidate source).**
Between cut-off and announcement: abnormal turnover, net foreign buying
(existing `foreign_flow` data), and return relative to the market among the
replica's candidates. Used only to filter the replica's list. On its own it is
momentum, which the shortlist already rejected.

**3. Published predictions (forward check only).** Broker notes, Stockbit
Snips and MSCI-flow estimates are not archived for each date, so they cannot
be backtested honestly. Log them going forward next to the replica's output and
compare hit rates. Never use them in the backtest.

**Data gap (blocks the replica for MSCI/FTSE).** The screens need shares
outstanding and free float *as they stood at each date*. Chartbit daily bars
carry a `shareoutstanding` column (check in Phase 1 whether it is point-in-time
or back-filled with today's count), but no free float. Candidate sources, in order: KSEI monthly
ownership / free-float reports, IDX free-float announcements. The emitten
profile is current-only and would leak today's free float into past dates.
**Don't use it for history.** If point-in-time free float cannot be found back
to ~2015, the replica falls back to traded value + frequency. That is adequate
for IDX and too weak to test MSCI/FTSE. Logged as a plan-12 requirement record:
`kind=length, target=free_float+shares_outstanding point-in-time, need=2015→now,
gates=H4`.

## 2. Event sources: where each fact comes from

The rule: **every event row must trace to a primary document from the index
provider.** Press and broker write-ups are for cross-checking only. They
already disagree with each other (e.g. the Aug-2026 IDX80 removals list differs
between Stockbit Snips and Ajaib).

### 2.1 IDX indices (LQ45, IDX30, IDX80, KOMPAS100)

| field | primary source | notes |
|---|---|---|
| additions / deletions | IDX announcement PDF **"Pengumuman Evaluasi Indeks IDX30, LQ45, IDX80, KOMPAS100, …"**, published in idx.co.id → *Berita & Pengumuman* → *Pengumuman*. Files live under `idx.co.id/StaticData/NewsAndAnnouncement/ANNOUNCEMENTSTOCK/Exchange/` (example: `Evaluasi Indeks 24Apr2026_Bahasa-No. Peng-00067BEI.POP04-2026.pdf`). The Bahasa and English versions are separate files. | Each PDF carries a `Peng-xxxxx/BEI.POP/MM-YYYY` number. Store it as the event's provenance key. |
| announcement date | the PDF's date line, **and** the timestamp on the idx.co.id listing | IDX often publishes after the close. If the timestamp is after 15:00 WIB (16:00 on Friday), the first tradeable session is the next one. When the timestamp is missing, assume after-close (conservative: it delays entry). |
| effective date / period | stated in the same PDF (e.g. "berlaku 3 Agustus – 30 Oktober 2026") | **Passive rebalancing happens at the close of the last session before the effective date.** That close is H1's exit. |
| cadence | read off the PDFs, not assumed | Historically: major reviews in Jan and Jul, minor reviews in Apr and Oct. The 2026 PDFs describe the April review as a major evaluation too, and IDX added a high-shareholding-concentration filter in Apr-2026 (BREN and DSSA out). **Record the review type per event.** Do not hard-code a schedule. |
| membership snapshots (validation) | IDX index fact sheets, `idx.co.id/Media/.../fs-lq45-YYYY-MM.pdf` | Use these to check that the reconstructed membership adds up to 45 / 30 / 80 at every review. |

**Retrieval.** The announcement list is a JS page that loads JSON behind
Cloudflare. There is no documented public API. Two options:
(a) **manual download** of ~4 PDFs/year × ~11 years ≈ 45 files, plus transcription
into the CSV (§2.4). This is the recommended option: it is a one-off, and a
human reading each PDF is the quality control.
(b) Open-source wrappers such as `NeaByteLab/IDX-API` show the endpoint the site
uses. They are unofficial and may break, so use them only to *list* the PDFs,
never as the data of record.

### 2.2 MSCI (Indonesia, Global Standard + Small Cap)

| field | primary source | notes |
|---|---|---|
| additions / deletions | MSCI **"Public List"** PDFs per review: Standard = `app2.msci.com/eqb/gimi/stdindex/MSCI_<Mon><YY>_STPublicList.pdf` (verified pattern: Feb21, May19, Nov22, Nov23, May24, Aug25). Small Cap is expected at a sibling `…SCPublicList.pdf`; **unverified, confirm in Phase 0.** | The lists are grouped by country. Take the INDONESIA block only. They include migrations (Standard ↔ Small Cap). Record each as **two** events: deletion from one index and addition to the other. |
| announcement date / time | MSCI's yearly index review calendar on msci.com, confirmed about a week ahead by a pre-announcement press release (`ir.msci.com`, mirrored on BusinessWire), e.g. *"MSCI August Index Review Announcement Scheduled for August 12, 2026"*. Usually the **second week** of the review month (2026: Feb 10, May 12, Aug 12). | Results post at **~23:00 CET/CEST, which is ~04:00 WIB (CEST) / ~05:00 WIB (CET) the next calendar day**, before the IDX open. The first tradeable IDX session is the one opening that morning. Treat `a` as the IDX session *before* that morning, so H1 enters at `close_{a+1}`, which is a full session after the news. |
| effective date | the same release: "as of the close of" the last business day of Feb / May / Aug / Nov | If that day is an IDX holiday, passive funds trade at the last IDX session before it. Resolve with `calendar/idx_holidays.json`. |
| freeze (from Feb-2026) | MSCI announcement of 2026-01-28 and its follow-ups for each review (§0) | Every MSCI review from Feb-2026 onward gets `review_type=freeze`. Record whether each deletion is a standard review deletion or an HSC removal in `note`. Out of the G5 holdout (§4.2). |
| cadence | quarterly (Feb, May, Aug, Nov). The May and Nov reviews are the semi-annual ones with more changes. **Price cut-off:** an undisclosed day among the last 10 business days of Jan / Apr / Jul / Oct (matters for H4, §1b). Announcement → passive trade ≈ 13 IDX sessions. | Index-specific out-of-cycle changes (e.g. corporate events, free-float actions on Indonesian names in 2025–26) come as separate MSCI announcements. Keep them **only** if a dated primary notice exists; tag them `out_of_cycle`. |

### 2.3 FTSE Russell GEIS (Indonesia)

| field | primary source | notes |
|---|---|---|
| review calendar | FTSE GEIS **FAQ / calendar PDF** per year on `lseg.com/content/dam/ftse-russell/…/ftse-faq-document-geis-YYYY.pdf` (2018, 2022, 2024, 2025 verified to exist), published ahead of the year | Gives the data cut-off, the announcement date, the date final files are released, and the effective date (open of a Monday, after the third Friday of Mar / Jun / Sep / Dec). **Mar and Sep are the full semi-annual reviews; Jun and Dec changes are small and mostly IPO additions** (tag `review_type`). Data cut-off: last business day of Dec (Mar review) / Jun (Sep review). Announcement ≈ 4 weeks before effective, after the IDX close on a Friday (Sep-2026: announced Aug 21, effective Sep 21). Announcement → passive trade ≈ 20 IDX sessions. |
| additions / deletions | FTSE Russell **index review notices** for GEIS on `lseg.com` (*FTSE Russell → Index notices*, filtered by Global Equity Index Series and country Indonesia) | FTSE publishes provisional results, then a revision, then final ones (e.g. Sep-2026: revised to 4 Sep, final 7 Sep). **Use the first public version for the decision date and the final version for membership.** Log any name the revision dropped. |
| effective | "after the close of" the Friday, i.e. passive trades at that Friday's IDX close | Same Lebaran/holiday caveat as MSCI; FTSE publishes a holiday FAQ (e.g. *Eid al-Fitr 2026*). |
| access | Some notices require a free LSEG account | A personal free login is fine. Do not script it with stored credentials. Download by hand. |

### 2.3a Phase 0 results (2026-10-15)

Raw files under `research/data/index_events/raw/` (gitignored by `research/data/*`).

| provider | status | coverage | notes |
|---|---|---|---|
| MSCI Standard | ✅ fetched | **51 reviews, Feb-2014 → Aug-2026, none missing** (`raw/msci/st/`, log `fetch_log.tsv`) | Indonesia: **26 additions in 19 reviews, 47 deletions in 26 reviews**. Each PDF states its own announcement date ("Geneva, August 07, 2025") and effective close. **The effective date is not always the month's last business day** (Aug-2025: Aug 26; Nov-2024: Nov 25; May-2019: May 28), so dates come from the PDF, never from a rule. Names are MSCI security names, not tickers, so a name→ticker map is needed. |
| MSCI Small Cap | ✅ fetched | 51 reviews, same span (`raw/msci/sc/`). URL: `app2.msci.com/eqb/gimi/smallcap/MSCI_<Mon><YY>_SCPublicList.pdf` | Indonesia: **138 additions in 34 reviews, 172 deletions in 41 reviews**. Small Cap flows are much smaller per name; tested as a separate cell. |
| FTSE calendar | ✅ fetched | yearly GEIS calendar PDFs **2016 → 2026** (2015 is 404) (`raw/ftse/calendar/`) | "Release of indicative review files" = announcement; "Release of final index review" = final; "Review effective" = Monday open. |
| FTSE change lists | ✅ fetched 2026-10-16 (owner, by hand with a free FTSE Russell login) | **24 Mar/Sep semi-annual reviews, Mar-2015 → Sep-2026, none missing** (`raw/ftse/notices/`), plus a Sep-2018 Microcap-only file | The notice PDFs are public, but the xlsx attachments are behind a login, and the Wayback Machine holds only login redirects for them (164 captures checked). One file per review: the Asia-Pacific-ex-Japan(-ex-China) technical notice, final or latest amendment. From 2019 the file name carries `ex_China`. Mar-2015 is a legacy `.xls`. **Mar-2026 has no Indonesia rows**: FTSE postponed the Indonesia review (FTSE FAQ *Treatment of Indonesian securities*, effective 2026-02-09). Development window: 13 reviews (Mar-15 → Sep-21, incl. Sep-18 and Mar-19); holdout: 8 (Mar-22 → Sep-25); freeze: Mar-26, Sep-26. |
| IDX, live site | owner: archive reaches **2022** only | 2022 → 2026 | Download by hand. |
| IDX, Wayback | ✅ 16 original PDFs fetched (`raw/idx/wayback/`) | **LQ45: 9 reviews, Jan-2014 → Jan-2018**; IDX30: 5 (2014–2017, gaps); KOMPAS100: 2 | These are the full constituent lists, not change lists. Changes are the diff between consecutive lists, and the announcement date is in the filename (`YYYYMMDD_…`). They are IDX's own documents archived unmodified (`id_` mode), so they count as primary. |
| **IDX gap** | ❌ | **Jul-2018 → 2021 (~7 LQ45 reviews)** | Not on the live site, not in Wayback. See below. |

**Consequence for G5.** With the ≤2021 / 2022+ split, IDX's development window
has only 2014–Jan-2018. The options, decided at §9 sign-off:
(a) fill the gap from **secondary sources quoting the full list** (Kontan,
Bisnis, CNBC Indonesia, broker notes), tagged `source_tier=secondary`, with a
required sensitivity run that excludes them;
(b) use any internal Stockbit history of index membership, if one exists and
its use is approved (owner);
(c) accept the gap and report IDX's N per window.
**Decided 2026-10-16 (owner): (c), accept the gap for now.** IDX's N is reported per window, and the gap can be filled later without changing the gates. MSCI is unaffected: complete from 2014.

### 2.4 Event table (the single output of data collection)

`research/data/index_events/events.csv`, one row per (provider, index, symbol, change):

```
event_id, provider, index_name, symbol, change(add|del|migrate_in|migrate_out),
review_label (e.g. MSCI-2026-08), review_type(major|minor|quarterly|semi|out_of_cycle|freeze),
announce_ts_local, first_tradeable_session, passive_trade_session, effective_session,
source_doc (filename or URL), source_ref (e.g. Peng-00067/BEI.POP/04-2026),
cross_check(ok|mismatch|none), note
```

Sessions are resolved through `calendar/idx_holidays.json`. Rows with
`cross_check = mismatch` stay in the table and are excluded only by a pre-stated
rule (§4.3), not case by case.

### 2.5 Price data

* Daily bars come from chartbit (`research/data/daily`, listing-date history;
  memory: `sb-daily-history-depth-vs-lookback-default`).
* **Survivorship gap:** the local set has 243 symbols. Deleted names, and names
  later suspended or delisted, are the likeliest to be missing. Phase 1 lists
  every `events.csv` symbol that has no local bars and fetches them. Any event
  that still has no bars is **reported with its count**, never silently dropped.
  If > 10% of deletion events have no bars, H2 is `data_blocked` (plan 12 record).
* The market null needs an IHSG / LQ45 index series, which is **not in
  `research/data/daily` today**. Phase 1 adds it, or uses the existing
  equal-weight eligible-universe null (`build_event_trades`' `null_mkt`) and
  says so.

## 3. Event windows per provider

`a` = last IDX session at or before the announcement, `p` = passive-trade session
(the close at which trackers rebalance), `e` = first session the new index is live.

| provider | `a` | H1 entry | H1 exit | H2 entry |
|---|---|---|---|---|
| IDX | announcement session (after-close rule §2.1) | `close_{a+1}` | `close_p` = last session before the effective period | `close_{p+1}` |
| MSCI | session before the ~05:00 WIB release | `close_{a+1}` | `close_p` = last IDX session ≤ MSCI effective date | `close_{p+1}` |
| FTSE | the Friday of the announcement (after IDX close) | `close_{a+1}` | `close_p` = the effective Friday | `close_{p+1}` |

Variable H1 hold lengths (MSCI ~13 sessions, FTSE ~20, IDX ~3–5) are allowed.
Impact is still costed at the entry session.

## 4. Test rules (frozen at §9 sign-off)

### 4.1 Harness

Reuse `research/strategy_fit/families_idx_structural.py`: `build_event_trades`
for costs (tick + 40 bps + point-in-time sqrt impact, `impact_coeffs.json`),
the null construction, and the 10× capacity check. Add one module,
`families_index_rebalance.py`, that turns `events.csv` into an event mask plus
per-event exit sessions. **No change to cost, null or gate code.**
Notional: 1 lot *and* the harness Rp 3M, both reported.

### 4.2 Gates (all blocking, same as the 2026-10-15 shortlist)

| gate | rule |
|---|---|
| G1 | live-feasible entry (§3); `open_{a+1}` sensitivity reported |
| G2 | mean edge over the always-long null (same name, same calendar window) with **t ≥ 2, clustered by `review_label`**. Events in one review share a market day and are not independent, so effective N = number of reviews, not number of names. |
| G3 | edge − cost > 0 under the current coeffs, the 2026-10-14 candidate coeffs, and the 1-lot cost |
| G4 | mean net still > 0 after dropping the 5 best events |
| G5 | **chronological holdout:** develop on reviews announced ≤ 2021-12-31, then confirm on 2022-01-01 → latest **once**. Both windows must clear G2-sign (edge > 0) and G3. Nothing is tuned between them. **MSCI:** reviews tagged `review_type=freeze` (Feb-2026 onward, §0) are excluded from the holdout and reported separately; the MSCI holdout is Feb-2022 → Nov-2025. |
| G6 | market-adjusted: net − (IHSG or equal-weight universe over the same window) > 0. This blocks a pass that is just bull drift and keeps the bear-market result honest |
| G7 | multiple testing: Holm across every (hypothesis × provider × hold) cell actually run. The cell list is fixed in §1/§3, so the denominator is known in advance (≈ 2 × 3 providers + pooled, × 3 holds for H2 ≈ 16 cells) |
| G8 | regime split, **reported but not blocking**: the result for reviews where IHSG was below its 200-day average at `a` |

**Minimum sample:** ≥ 30 events *and* ≥ 12 distinct reviews per tested cell.
Expected: IDX LQ45+IDX80 ≈ 5–10 changes × 2–4 reviews/yr × 11 yrs, which clears
easily. MSCI Indonesia ≈ 1–3 per review, which is marginal: if MSCI fails the
floor, it is `insufficient_n`, not a fail.

### 4.3 Exclusions (pre-stated)

* `cross_check = mismatch` where the primary document itself is ambiguous → exclude, and report the count.
* Entry-day limit lock (ARA at `close_{a+1}`): `skip_locked=True`, because you cannot buy a locked-up name. Report how many events this drops; a large count is itself a finding (the move already happened at the announcement).
* Events where the symbol is suspended between `a` and `p` → exclude, report.
* Bad bars: the existing > 50% one-day filter.

## 5. Decision rule

* **H1 or H2 passes G1–G7 for IDX or FTSE:** write plan 22, a
  forward-test pre-registration modelled on plan 20. The event calendar is known
  ahead of time, so the emitter is a lookup in `events.csv`. Size follows the
  frugal live-test rule (memory: `user-personal-funds-frugal`): a rupiah
  worst case stated before the first order.
* **Only MSCI passes (§0):** for H1, the mechanism is confirmed but there is no
  forward candidate while the freeze holds. For H2 (deletions), plan 22 may be
  written, with a hard stop at the Standalone implementation date. Record
  the numbers. Use them as the effect-size prior for IDX/FTSE, and revisit only
  if MSCI withdraws the proposal and lifts the freeze.
* **Only the pooled cell passes:** not a candidate. Record it and investigate which provider is driving it.
* **Nothing passes, and §1a shows no pre-announcement move:** close the family
  and record the numbers in plan 10's status.
* **Nothing passes, but §1a promoted H4:** the family stays open. Pre-register
  H4 (§9 amendment), build the eligibility replica, and test it on the same
  development/holdout split. Every prediction error costs a round trip on a name
  that never gets added, so H4's cost hurdle is the cost divided by the
  prediction hit rate, not the plain cost.
* **H3 comes out positive** (the post-inclusion drift does *not* reverse): treat it as
  a warning that the windows or dates are wrong, and audit `events.csv` before
  reading H1/H2.

## 6. Phases

| phase | work | output | effort |
|---|---|---|---|
| 0 | Confirm source access: one IDX PDF, one MSCI ST and SC list, one FTSE notice. Confirm the MSCI SC URL pattern, the IDX historical archive depth (does it reach 2015?), the pre-2024 MSCI/FTSE calendars and announcement times (cycles have changed before), and the methodology docs behind §1b. Check whether point-in-time free float exists (KSEI / IDX). | §2 and §1b tables updated with ✓/✗ | ½–1 day |
| 0b | **MSCI go/no-go (§0).** Transcribe the MSCI Standard + Small Cap Indonesia events from the PDFs already fetched, build the name→ticker map, and run §1a on the development window only (≤2021). Descriptive, no gates. **If MSCI-sized flows show no material L3–L5 move, close the family** (IDX/FTSE flows are smaller); otherwise continue to Phase 1. | `docs/research/<date>-index-rebalance-msci-1a.md` | 1–1½ days |
| 1 | Transcribe events into `events.csv`. IDX first (largest N), then FTSE, then the remaining MSCI checks. Cross-check each review against one secondary source. Fetch missing bars and an index series. | `events.csv` + coverage table (events/provider/year, missing bars) | 2–4 days, mostly manual |
| 2 | Freeze §1–§4 (§9 sign-off). Run the §1a event-time breakdown, then `families_index_rebalance.py`, on the development window only. | `docs/research/<date>-index-rebalance-dev.md` (event-time plot + legs table first) | 1–1½ days |
| 3 | Holdout run, once. | the same report, extended | ½ day |

Phase 1 is the expensive one, and it is where errors creep in. Events are
hand-transcribed, so a second pass on a random 20% sample against the primary
PDF is required before Phase 2. The error rate is reported.

## 7. Risks and known biases

* **Announcement-day jump.** Much of the addition premium may land at the
  `open_{a+1}` gap, before our first possible entry. If so, H1 fails honestly;
  §4.3's lock count will show it.
* **Crowding.** Indonesian retail and brokers publish predicted LQ45 / MSCI
  changes widely (e.g. Stockbit Snips). Any remaining premium may have shifted
  *before* announcement, into H4's territory. §1a measures this directly
  instead of assuming it.
* **Rule changes.** IDX's 2025–26 methodology changes (free-float and
  concentration filters) change who gets added and why. G5's split sits
  roughly at that boundary, so a regime break shows up as a G5 fail rather
  than being averaged away.
* **MSCI exit (§0).** The largest foreign-passive flow is leaving the market.
  If the reclassification goes ahead, FTSE and IDX are all that remain, and
  pre-2026 MSCI-era effect sizes may overstate what IDX/FTSE events deliver.
  FTSE could also act on the same governance concerns; check its country
  classification notices before Phase 1 FTSE work.
* **Small flows.** Local LQ45/IDX30 ETF AUM is small relative to ADV. IDX
  events may show no effect while MSCI ones do. That is why §5 requires a
  single-provider pass.

## 8. Open questions

1. ~~Does the idx.co.id archive go back to 2015?~~ Answered 2026-10-15: live site to 2022; Wayback covers LQ45 2014 → Jan-2018; **Jul-2018 → 2021 is a gap** (§2.3a). Open: which fill option.
2. ~~Is a free LSEG login acceptable for FTSE notices?~~ Answered 2026-10-16: yes. The owner downloaded all 24 semi-annual review files by hand (§2.3a).
3. Include KOMPAS100 / IDX High Dividend 20 / sharia indices (JII, ISSI)? JII has real passive and sharia-fund AUM. Proposed: JII as a sensitivity, the others out.
4. **Watch item, outside the gates:** the MSCI Standalone decision (by
   2027-06-30) and its implementation date. If it goes ahead, every Indonesian
   name leaves MSCI EM at one close. That is a single forced-selling event, the
   largest H2-type setup this market will see. With N = 1 it is not a test case;
   any trade on it would need its own pre-registration with a rupiah worst case.

## 9. Sign-off

Frozen when the owner fills this in. Gates and cells are not edited after this
line. Any later change is a new dated row with the reason.

### 9a. Freeze (2026-10-16)

Drafted after Phase 0b
([report](../research/2026-10-16-index-rebalance-msci-1a.md)). That run read
only the development window, so the holdout is untouched. Signed by the owner
(table at the end of this section).

**Cells (the G7 Holm denominator is fixed here, 15 cells):**

| hypothesis | providers | holds | cells |
|---|---|---|---|
| H2 deletions, `close_{p+1}` → `close_{p+1+k}` | MSCI_ST, MSCI_SC, FTSE, IDX, pooled | k ∈ {5, 10, 20} | 15 |

* **H1 is dropped from the gated test (owner decision, 2026-10-16).** §1a read
  the additions as "priced in at the open": MSCI_ST L2 = +212 bps (t 6.5)
  against L4 = +74 bps (t 0.6), and MSCI_SC/FTSE additions show no reliable
  run-in. §1a's job is to decide which hypotheses are worth testing, and for
  this reading it says H1 is expected to fail. H1's legs stay in the Phase 0b
  report as descriptive results. H1 is not run on the holdout and is not
  counted in G7. (The "promote H4" rule, the only §1a case that keeps H1 in
  regardless, did not trigger.)
* H3 stays a control (§1): reported, not gated.
* IDX stays in the denominator although IDX events are not transcribed yet
  (§2.3a gap accepted). If IDX is never run, the denominator stays 15, which is conservative.
* MSCI_ST and MSCI_SC are separate providers for the §5 single-provider rule.
* Deletions = `del` ∪ `migrate_out`; additions = `add` ∪ `migrate_in`. FTSE
  uses the L/M/S segment events; Micro-only moves and Mid↔Large moves are out.

**Pre-stated additions to §4.3 (found while building Phase 0b):**
* Suspension rule: ≥ 5 consecutive zero-volume sessions in `a+1 … p`, because
  chartbit fills halted names with flat zero-volume bars. For H2, an event is
  also excluded if the entry session `p+1` has zero volume.
* Ticker map: rows with `map_confidence = low` or no ticker are excluded and
  counted. Today that is the 2 MSCI "BANK PEMBANGUNAN DAE(RAH)" rows (BJBR vs.
  BJTM) and "NUSANTARA PROPERTI INTL".
* FTSE-2015-03/09 have no calendar dates and are excluded (`no_dates`).
* G6 market: IHSG (now in `research/data/daily/IHSG.parquet`); the equal-weight
  universe is the sensitivity.

**Windows:** development = reviews announced ≤ 2021-12-31; holdout =
2022-01-01 → 2025-12-31, non-freeze only, run once; freeze reviews are reported
separately, descriptive only.

**Everything else** (G1–G8, minimum sample, §5 decision rule) is as written in
§4–§5.

| date | change | by |
|---|---|---|
| 2026-10-16 | Freeze §1–§4 as amended by §9a: 15 H2 cells, H1 dropped, §4.3 additions, windows. Gates and cells are not edited after this line. | @lewis (owner; instructed in session, entered by Claude) |

| date | change | by |
|---|---|---|
| | | |
