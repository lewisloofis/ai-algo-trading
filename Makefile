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
#   make publish-dashboard
#   make clean

.PHONY: build build-linux-arm64 collect sbcap test dashboard publish-dashboard clean

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

# dashboard regenerates the single static observability page (plan-19).
# It reads artifacts and writes one file: no server, no metrics daemon, no
# scrape config. Every input is optional — a panel with nothing to read says
# what is missing rather than rendering an empty table that looks like a
# result — so this target is safe to run at any time.
#
# Override any path on the command line, e.g.
#   make dashboard STORE='research/out_live/runs/*.db'
DASH_PY       ?= $(CURDIR)/research/.venv/bin/python
DASH_OUT      ?= research/out_live/dashboard.html
PORTFOLIO     ?= research/out_live/strategy_portfolio.json
STORE         ?= research/out_live/runs
STATUS        ?= research/out_live/status.json
HEALTHCHECK   ?= research/out_live/collector_health.json
ELIGIBILITY   ?=

dashboard:
	cd research && $(DASH_PY) -m dashboard \
		--portfolio $(CURDIR)/$(PORTFOLIO) \
		--store '$(CURDIR)/$(STORE)' \
		--status $(CURDIR)/$(STATUS) \
		--healthcheck $(CURDIR)/$(HEALTHCHECK) \
		$(if $(ELIGIBILITY),--eligibility $(CURDIR)/$(ELIGIBILITY),) \
		--out $(CURDIR)/$(DASH_OUT)

# publish-dashboard copies the page to S3 beside the market data, so it is
# readable without the laptop that generated it (plan-19 Stage 6, optional).
#
# Private by default: no public-read ACL, no bucket policy change, no website
# hosting. The page carries run ids, position sizes and PnL; it is read with
# credentials, via the console or a presigned URL, exactly like the segments
# next to it.
DASH_BUCKET ?= marketdata-047915756052
DASH_KEY    ?= dashboard/index.html

publish-dashboard: dashboard
	aws s3 cp $(CURDIR)/$(DASH_OUT) s3://$(DASH_BUCKET)/$(DASH_KEY) \
		--content-type 'text/html; charset=utf-8' \
		--cache-control 'no-cache'
	@echo "Published. Read it with:"
	@echo "  aws s3 presign s3://$(DASH_BUCKET)/$(DASH_KEY) --expires-in 3600"

clean:
	rm -rf bin
