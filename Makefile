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
#   make refresh-prices
#   make publish-dashboard
#   make clean

.PHONY: build build-linux-arm64 collect sbcap test dashboard refresh-prices publish-dashboard recon clean

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

# recon is the broker reconciliation (strategy/cmd/account-recon --stores): the
# expected position from ALL live stores + runs/manual_trades.json + the broker-
# import ledger, against the broker's holdings, plus a trade-by-trade check against
# the broker's history. It writes runs/recon/<date>.json (the dashboard Overview
# reads the newest one) and imports trades made by hand in the app into
# runs/broker_trades.json. Read-only towards the broker.
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
# exits non-zero if any failed. Needs SB_BEARER_TOKEN in the environment; this
# target never reads .env.local itself and never prints the token:
#   set -a && source .env.local && set +a && make refresh-prices
# Pass extra flags with REFRESH_ARGS="--full".
REFRESH_ARGS ?=

refresh-prices:
	@test -n "$$SB_BEARER_TOKEN" || { echo "refresh-prices: SB_BEARER_TOKEN is not set (set -a && source .env.local && set +a)"; exit 2; }
	cd research && $(DASH_PY) -m screener.daily_store snapshot --all-stored --out-dir data/daily $(REFRESH_ARGS)

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
