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
#   make clean

.PHONY: build build-linux-arm64 collect sbcap test clean

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

clean:
	rm -rf bin
