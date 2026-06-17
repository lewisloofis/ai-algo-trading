# Makefile — sb-algo-trading-explore
#
# All go commands run from collector/ (the Go module root).
# Binaries land in $(CURDIR)/bin/ regardless of invocation directory.
# Usage:
#   make build
#   make collect
#   make sbcap ARGS="stat segments/dt=2026-06-17/default/seg-044221.binpb.zst"
#   make test
#   make clean

.PHONY: build collect sbcap test clean

build:
	mkdir -p $(CURDIR)/bin
	cd collector && go build -o $(CURDIR)/bin/collector ./cmd/collector
	cd collector && go build -o $(CURDIR)/bin/sbcap ./cmd/sbcap

collect: build
	CAPTURE_DIR=$(CURDIR)/segments ./bin/collector

sbcap: build
	./bin/sbcap $(ARGS)

test:
	cd collector && go test ./...

clean:
	rm -rf bin
