# Efficient Rust build commands for the cact engine.
#
# Memory is the constraint on this machine (8 cores, ~7GB): a parallel rustc job costs
# 300-500MB, so every target caps jobs at 4 by default and the dev profile ships no debuginfo.
# Override with `make build JOBS=2` when other things are running, or `JOBS=8` when it is idle.
JOBS ?= 4
CARGO ?= cargo
BIN := target/release/cact
DEV := target/debug/cact

.PHONY: help build dev test check selfcheck fmt lint clippy clean install live

help:
	@echo "make build      release binary, $(JOBS) jobs, no debuginfo (the production worker)"
	@echo "make dev        debug binary (opt-level 1, no debuginfo): the fast iteration build"
	@echo "make test       cargo test"
	@echo "make selfcheck  the offline contract check (no key, no network)"
	@echo "make fmt        rustfmt --check"
	@echo "make lint       clippy, warnings are errors"
	@echo "make live       two live tasks with DEEPSEEK_API_KEY (real network, cheap model)"
	@echo "make clean      remove target/"

build:
	$(CARGO) build --release -j $(JOBS)
	@echo "built $(BIN)"

dev:
	$(CARGO) build -j $(JOBS)

test:
	$(CARGO) test -j $(JOBS)

selfcheck:
	$(CARGO) run -q -j $(JOBS) -- selfcheck

fmt:
	$(CARGO) fmt --all --check

lint:
	$(CARGO) clippy -j $(JOBS) --all-targets -- -D warnings

# Every gate in one call: the same set `checks()` runs from the TUI side.
check: lint test selfcheck

live:
	./scripts/live.sh

install: build
	install -Dm755 $(BIN) $(HOME)/.local/bin/cact

clean:
	$(CARGO) clean
