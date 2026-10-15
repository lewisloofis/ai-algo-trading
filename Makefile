# Makefile — sb-algo-trading-explore
#
# All go commands run from collector/ (the Go module root).
# Binaries land in $(CURDIR)/bin/ regardless of invocation directory.
# Usage:
#   make build
#   make build-linux-arm64
#   make collect
#   make sbcap ARGS="stat segments/dt=2026-06-17/default/seg-044221.binpb.zst"
#   make test
#   make dashboard
#   make dashboard-live
#   make refresh-prices
#   make paper-day STRATEGY=wifi_momo_h5
#   make paper-scorecard STRATEGY=wifi_momo_h5
#   make publish-dashboard
#   make runner-dry-run | runner-install | runner-status | runner-uninstall   (unattended runner)
#   make runner-arm STRATEGY=<id> MAX_IDR=<n> [DATE=] | runner-disarm [DATE=]   (the only way to go live)
#   make clean

.PHONY: build build-linux-arm64 build-strategy collect sbcap test dashboard dashboard-live refresh-prices emit-target paper-day paper-scorecard publish-dashboard recon clean runner-install runner-uninstall runner-status runner-dry-run runner-test runner-arm runner-disarm

build:
	mkdir -p $(CURDIR)/bin
	cd collector && go build -o $(CURDIR)/bin/collector ./cmd/collector
	cd collector && go build -o $(CURDIR)/bin/sbcap ./cmd/sbcap

# build-linux-arm64 cross-compiles just the collector daemon (the artifact
# fetched by infra/user_data.sh.tftpl's update.sh from collector_artifact_s3_uri,
# and the only binary that runs on the deployed EC2 host) for the box's actual
# platform: Amazon Linux 2023 arm64 (infra/data.tf, infra/variables.tf:112).
# Lands in bin/linux-arm64/, distinct from the host build in bin/, so the two
# are never confused before an upload.
#
# -trimpath keeps local absolute source paths out of a binary that gets published
# to S3. It does NOT make the build reproducible across checkout locations — that
# was measured on 2026-09-05 and still differs even with -ldflags="-s -w -buildid=",
# which is exactly why scripts/collector_healthcheck.py scores artifact AGE and
# treats any hash comparison as informational. Provenance for a published artifact
# travels in S3 object metadata (git-commit/sha256/goos/goarch), not in the bytes:
# Go omits vcs stamping entirely when building from a git worktree, because .git
# there is a pointer file it will not follow.
build-linux-arm64:
	mkdir -p $(CURDIR)/bin/linux-arm64
	cd collector && GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -o $(CURDIR)/bin/linux-arm64/collector ./cmd/collector

collect: build
	CAPTURE_DIR=$(CURDIR)/segments ./bin/collector

sbcap: build
	./bin/sbcap $(ARGS)

test:
	cd collector && go test ./...

# dashboard builds the static, multi-page, plain-language dashboard (plan-19, rebuilt
# 2026-10-14): index, trades, strategies (+ one page per highlighted backtest case),
# research, glossary. One command; it finds its own inputs (runs/*.db, the newest status
# json, the latest corrected strategy-fit run, research/data/daily) and RUNS
# scripts/collector_healthcheck.py itself, so run it from a shell with AWS credentials
# loaded. Without them the collector/S3 rows say "Not checked: <why>. Run: <command>".
#
#   make dashboard                      full build (runs the health check; can take minutes)
#   make dashboard HEALTHCHECK=skip     skip the health check (rows say "Not checked")
#   make dashboard DASH_OUT=/some/dir
#
# The output directory is gitignored (research/out_live/). Open $(DASH_OUT)/index.html.
DASH_PY       ?= $(CURDIR)/research/.venv/bin/python
DASH_OUT      ?= research/out_live/dashboard
HEALTHCHECK   ?= run

dashboard:
	cd research && $(DASH_PY) -m dashboard build \
		--out $(CURDIR)/$(DASH_OUT) \
		$(if $(filter skip,$(HEALTHCHECK)),--no-healthcheck,)
	@echo "Open $(CURDIR)/$(DASH_OUT)/index.html"

# dashboard-live serves the same dashboard on http://127.0.0.1:$(PORT)/ (this machine only). The
# "Online now" panel refreshes every 5 seconds from the bot's heartbeat files, and the pages
# rebuild every 5 minutes or sooner when the run stores / daily prices change. Ctrl-C stops it.
#   make dashboard-live                      (runs the health check on each rebuild: slow, needs AWS credentials)
#   make dashboard-live HEALTHCHECK=skip     recommended for a live view
#   make dashboard-live PORT=9000
PORT ?= 8765

dashboard-live:
	@echo "Live dashboard: http://127.0.0.1:$(PORT)/"
	cd research && $(DASH_PY) -m dashboard serve --port $(PORT) \
		$(if $(filter skip,$(HEALTHCHECK)),--no-healthcheck,)

# recon is the broker reconciliation (strategy/cmd/account-recon --stores): the
# expected position from ALL live stores + runs/manual_trades.json + the broker-
# import ledger, against the broker's holdings, plus a trade-by-trade check against
# the broker's history. It writes runs/recon/<date>.json (the dashboard Overview
# reads the newest one) and imports trades made by hand in the app into
# runs/broker_trades.json. Read-only towards the broker. The broker lists a day's
# trades only from the next trading day, so a bot fill dated today is reported as
# "awaiting broker history" (a warning, exit 0), not a phantom; re-run recon then.
#
# Needs the trading token and the sleeve account in the environment:
#   set -a && source .env.local && set +a && make recon
# (.env.local must define SB_TRADING_ACCESS_TOKEN and SB_ACCOUNT_NO, the sleeve
# sub-account number; or pass ACC=<number> on the command line.)
ACC ?= $(SB_ACCOUNT_NO)

recon:
	@test -n "$(ACC)" || { echo "recon: set SB_ACCOUNT_NO (or ACC=<sleeve account>)"; exit 2; }
	cd strategy && go run ./cmd/account-recon --stores '$(CURDIR)/runs/*.db' --acc-no '$(ACC)' --sink-kind live \
		--manual-trades $(CURDIR)/runs/manual_trades.json \
		--broker-ledger $(CURDIR)/runs/broker_trades.json \
		--out-dir $(CURDIR)/runs/recon

# refresh-prices refreshes the daily price history of EVERY symbol already in
# research/data/daily (incremental: only from ~10 days before each symbol's last
# stored bar; a corporate-action rewrite triggers a full re-pull of that symbol).
# One bad symbol does not stop the rest; a summary lists failures and the target
# exits non-zero if any failed. Symbols whose last bar is >60 days behind the
# store (delisted names kept for survivorship tests) are skipped; pass
# REFRESH_ARGS="--include-ended" to fetch them. Before refreshing it fetches both
# emitten classification lists (day-trade, trading-limit), writes
# research/data/daily/universe_status.json, skips stored symbols flagged tradeable=0 and
# adds newly eligible tradeable ones (full history). Needs SB_BEARER_TOKEN only
# (REFRESH_ARGS="--no-tradable-check" bypasses the status step explicitly); this target
# never reads .env.local itself and never prints a token:
#   set -a && source .env.local && set +a && make refresh-prices
# Pass extra flags with REFRESH_ARGS="--full".
REFRESH_ARGS ?=

refresh-prices:
	@test -n "$$SB_BEARER_TOKEN" || { echo "refresh-prices: SB_BEARER_TOKEN is not set (set -a && source .env.local && set +a)"; exit 2; }
	cd research && $(DASH_PY) -m screener.daily_store snapshot --all-stored --out-dir data/daily $(REFRESH_ARGS)

# emit-target writes the day's target_portfolio.json for ONE registered strategy
# (research/strategy_fit/emitter_specs.py) and PRINTS the matching live-session
# command (paper sink for a paper spec; a live sink only for a candidate). It never
# runs live-session. Pre-open job, local daily parquet by default:
#
#   make emit-target STRATEGY=<id>                       AS_OF defaults to today (WIB)
#   make emit-target STRATEGY=<id> AS_OF=2026-10-16 REBALANCE_AT=10:00
#   make emit-target STRATEGY=<id> EMIT_ARGS="--source live"   (needs SB_BEARER_TOKEN in env)
#
# Output: runs/target_<strategy>_<date>.json (+ .signal.json sidecar). Exit codes:
# 4 = not an IDX trading day (nothing written), 5 = retired spec (add
# EMIT_ARGS=--allow-retired only deliberately), 6 = REBALANCE_AT outside the
# 09:00-11:30 window (the bot's default 15:00 is outside it), 2 = data error.
STRATEGY ?=
AS_OF ?= $(shell TZ=Asia/Jakarta date +%Y-%m-%d)
REBALANCE_AT ?= 09:30
EMIT_ARGS ?=

emit-target:
	@test -n "$(STRATEGY)" || { echo "emit-target: set STRATEGY=<id> (see research/strategy_fit/emitter_specs.py)"; exit 2; }
	@mkdir -p $(CURDIR)/runs
	cd research && $(DASH_PY) -m strategy_fit.target_emitter emit --strategy $(STRATEGY) --as-of $(AS_OF) \
		--out $(CURDIR)/runs/target_$(STRATEGY)_$(AS_OF).json --rebalance-at $(REBALANCE_AT) $(EMIT_ARGS)

# build-strategy builds the two binaries live-session needs, side by side in bin/
# (live-session finds strategy-run next to itself and checks its sha256 against the
# binary_hash every run row records, so `go run` is not an option).
build-strategy:
	mkdir -p $(CURDIR)/bin
	cd strategy && go build -o $(CURDIR)/bin/strategy-run ./cmd/strategy-run
	cd strategy && go build -o $(CURDIR)/bin/live-session ./cmd/live-session

# paper-day: ONE command per trading day for a zero-cost PAPER forward test.
# Emits today's target (bars strictly before today) and runs
# `live-session --sink paper --source live` (live prices, simulated taker fills).
# Needs SB_ACCESS_TOKEN in the environment (market data only): no trading token,
# no ALGO_LIVE/ALGO_CONFIRM, no broker. Only status "paper" specs run.
#
#   make paper-day STRATEGY=wifi_momo_h5                  # AS_OF = today (WIB)
#   make paper-day STRATEGY=ptro_momo_h5 REBALANCE_AT=10:00
#
# START IT BEFORE REBALANCE_AT minus 60 seconds (default 09:30 -> before 09:29:00 WIB).
# Non-trading day: prints why and exits 0. Needs daily bars through the previous
# session: run `make refresh-prices` after the close the day before.
# Results: runs/target_<id>_<date>.json, runs/<date>-<id>-paper-aN.db, then
# `make paper-scorecard STRATEGY=<id>`.
paper-day:
	@test -n "$(STRATEGY)" || { echo "paper-day: set STRATEGY=<id> (wifi_momo_h5 | ptro_momo_h5)"; exit 2; }
	@$(MAKE) --no-print-directory build-strategy
	@mkdir -p $(CURDIR)/runs
	cd research && $(DASH_PY) -m strategy_fit.paper_day --strategy $(STRATEGY) --as-of $(AS_OF) \
		--rebalance-at $(REBALANCE_AT) --runs-dir $(CURDIR)/runs

# paper-scorecard: completed paper round trips of one strategy (all days), net of
# fee and one tick, vs the always-long null, and the go-live screen. Read-only.
paper-scorecard:
	@test -n "$(STRATEGY)" || { echo "paper-scorecard: set STRATEGY=<id>"; exit 2; }
	cd research && $(DASH_PY) -m strategy_fit.paper_scorecard --strategy $(STRATEGY) --runs-dir $(CURDIR)/runs

# publish-dashboard copies the built site to S3 beside the market data, so it is
# readable without the laptop that generated it (plan-19 Stage 6, optional).
#
# Private by default: no public-read ACL, no bucket policy change, no website
# hosting. The site carries run ids, position sizes and PnL; it is read with
# credentials, via the console or a presigned URL, exactly like the segments
# next to it.
DASH_BUCKET ?= marketdata-047915756052
DASH_PREFIX ?= dashboard/

publish-dashboard: dashboard
	aws s3 sync $(CURDIR)/$(DASH_OUT) s3://$(DASH_BUCKET)/$(DASH_PREFIX) --delete \
		--content-type 'text/html; charset=utf-8' \
		--cache-control 'no-cache'
	@echo "Published. Read it with:"
	@echo "  aws s3 presign s3://$(DASH_BUCKET)/$(DASH_PREFIX)index.html --expires-in 3600"

clean:
	rm -rf bin

# ---------------------------------------------------------------------------
# Unattended daily runner (research/runner, docs/runbooks/unattended-runner.md).
# Two launchd jobs, Mon-Fri, local time must be WIB: 08:45 `runner morning`
# (preflight, start, watch) and 16:20 `runner post-close`. Paper by default. A strategy
# goes LIVE only if its spec status is 'candidate' AND you armed it for that day:
#   make runner-arm STRATEGY=<id> MAX_IDR=<n> [DATE=YYYY-MM-DD]   arm ONE day, buys at most n IDR
#   make runner-disarm [DATE=YYYY-MM-DD]                          revoke (also stops a running live session)
#   make runner-dry-run [RUNNER_DATE=2026-10-16]   print the plan (incl. the exact live command), execute nothing
#   make runner-install                            install + load both launchd jobs
#   make runner-status                             loaded?  last log lines
#   make runner-uninstall
# ---------------------------------------------------------------------------
RUNNER_DATE   ?=
MAX_IDR       ?=
DATE          ?=
RUNNER_AGENTS := $(HOME)/Library/LaunchAgents
RUNNER_LABELS := com.sbalgo.runner.morning com.sbalgo.runner.postclose
RUNNER_PATH   ?= /opt/homebrew/bin:/usr/local/bin:/usr/local/go/bin:/usr/bin:/bin:/usr/sbin:/sbin

runner-dry-run:
	cd research && $(DASH_PY) -m runner all --dry-run $(if $(RUNNER_DATE),--date $(RUNNER_DATE),)

runner-test:
	cd research && $(DASH_PY) -m pytest -q runner/

# runner-arm writes runs/arm/ARM-<DATE>.json (DATE defaults to today, WIB): permission for the
# runner to trade ONE candidate strategy live on that one date, buying at most MAX_IDR (whole
# IDR, no default, hard ceiling in research/runner/arm.py). It refuses a spec that is not a
# 'candidate', a past or non-trading date, and an amount outside 1..ceiling.
runner-arm:
	@test -n "$(STRATEGY)" || { echo "runner-arm: set STRATEGY=<candidate strategy id>"; exit 2; }
	@test -n "$(MAX_IDR)" || { echo "runner-arm: set MAX_IDR=<most IDR it may buy that day, whole rupiah>"; exit 2; }
	cd research && $(DASH_PY) -m runner arm --strategy $(STRATEGY) --max-idr $(MAX_IDR) $(if $(DATE),--date $(DATE),)

runner-disarm:
	cd research && $(DASH_PY) -m runner disarm $(if $(DATE),--date $(DATE),)

runner-install:
	@test -x "$(DASH_PY)" || { echo "runner-install: $(DASH_PY) not found (create research/.venv or pass DASH_PY=)"; exit 2; }
	@test "$$(date +%z)" = "+0700" || { echo "runner-install: this Mac's time zone is not WIB (+0700); launchd uses local time. Set it to Asia/Jakarta first."; exit 2; }
	@mkdir -p $(RUNNER_AGENTS) $(CURDIR)/runs/runner
	@for l in $(RUNNER_LABELS); do \
		sed -e 's#@REPO_ROOT@#$(CURDIR)#g' -e 's#@PYTHON@#$(DASH_PY)#g' -e 's#@PATH@#$(RUNNER_PATH)#g' \
			ops/launchd/$$l.plist.template > $(RUNNER_AGENTS)/$$l.plist; \
		launchctl bootout gui/$$(id -u)/$$l 2>/dev/null || true; \
		launchctl bootstrap gui/$$(id -u) $(RUNNER_AGENTS)/$$l.plist && echo "installed $$l"; \
	done

runner-uninstall:
	@for l in $(RUNNER_LABELS); do \
		launchctl bootout gui/$$(id -u)/$$l 2>/dev/null && echo "unloaded $$l" || echo "$$l was not loaded"; \
		rm -f $(RUNNER_AGENTS)/$$l.plist; \
	done

runner-status:
	@for l in $(RUNNER_LABELS); do \
		if launchctl print gui/$$(id -u)/$$l >/dev/null 2>&1; then echo "$$l: loaded"; else echo "$$l: NOT loaded"; fi; \
	done
	@echo "--- last runner log lines ($(CURDIR)/runs/runner/runner.log)"
	@tail -n 15 $(CURDIR)/runs/runner/runner.log 2>/dev/null || echo "(no log yet)"
