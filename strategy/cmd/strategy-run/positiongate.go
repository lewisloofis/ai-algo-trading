package main

import (
	"context"
	"fmt"
	"log"
	"sort"
	"strings"

	"strategy/recon"
)

// Default locations of the manual-trades file and the broker-import ledger,
// relative to the repo root where strategy-run is run from.
const (
	defaultManualTradesPath = "runs/manual_trades.json"
	defaultBrokerLedgerPath = "runs/broker_trades.json"
)

// positionGateConfig drives runPositionGate.
type positionGateConfig struct {
	priorStores   string // --prior-stores glob
	account       string // --account (strict: only this account's fills count)
	manualPath    string
	ledgerPath    string
	allowMismatch bool
	// reconCommand is shown verbatim in the refusal as the way to fix it.
	reconCommand string
}

// positionGateResult is what a passing (or overridden) gate hands back.
type positionGateResult struct {
	// initial is the position, in lots, the strategy should start from:
	// prior stores' fills plus manual and imported trades. When the gate
	// passed it equals what the broker holds.
	initial map[string]int
	// override is non-empty when the run was allowed to start past a
	// mismatch; it is written to the run row.
	override string
}

// runPositionGate is the pre-trade position gate (plan: broker is the source
// of truth). Before a live run arms it compares the broker's holdings with the
// position the run would load — the --prior-stores fills plus manual and
// imported trades — and refuses to start on a mismatch: a bot that loads a
// wrong position places wrong orders.
//
// It is read-only: src is a portfolio reader with no order method. A broker
// that cannot be read is always an error; --allow-position-mismatch overrides
// a mismatch only, never an unreadable broker.
func runPositionGate(ctx context.Context, cfg positionGateConfig, src recon.PortfolioSource) (positionGateResult, error) {
	paths, err := recon.ResolveStorePaths(splitGlobs(cfg.priorStores), nil)
	if err != nil {
		return positionGateResult{}, fmt.Errorf("position gate: %w", err)
	}
	// Strict: a fill whose account is unknown is not assumed to be ours.
	_, fills, err := recon.LoadStoreFills(paths, "live", cfg.account, false)
	if err != nil {
		return positionGateResult{}, fmt.Errorf("position gate: %w", err)
	}
	manual, err := recon.LoadManual(cfg.manualPath)
	if err != nil {
		return positionGateResult{}, fmt.Errorf("position gate: %w", err)
	}
	imported, err := recon.LoadImports(cfg.ledgerPath)
	if err != nil {
		return positionGateResult{}, fmt.Errorf("position gate: %w", err)
	}

	res, err := recon.CheckPositions(ctx, src, recon.GateInputs{Fills: fills, Manual: manual, Imported: imported})
	if err != nil {
		return positionGateResult{}, fmt.Errorf("position gate: %w", err)
	}
	if res.IgnoredBeforeStores > 0 {
		log.Printf("position gate: ignoring %d manual/imported trade(s) dated before the earliest fill in --prior-stores (they belong to stores that are not loaded)", res.IgnoredBeforeStores)
	}
	if res.Match {
		log.Printf("position gate: broker holdings match the position loaded from --prior-stores + manual/imported trades (%s)", describeLots(res.ExpectedLots))
		return positionGateResult{initial: res.ExpectedLots}, nil
	}

	if !cfg.allowMismatch {
		return positionGateResult{}, fmt.Errorf("%s", res.Message(cfg.reconCommand))
	}
	override := overrideText(res)
	log.Printf("WARNING: *** POSITION MISMATCH OVERRIDDEN *** %s", override)
	log.Printf("WARNING: this run will trade from a position the broker does not agree with; the override is recorded on the run row")
	return positionGateResult{initial: res.ExpectedLots, override: override}, nil
}

// reconCommandFor is the refusal's suggested fix. Run from the repo root, with
// the trading token in the environment (it reads SB_TRADING_ACCESS_TOKEN).
func reconCommandFor(acct string) string {
	return "account-recon --stores 'runs/*.db' --acc-no " + acct + " --sink-kind live"
}

func overrideText(res recon.GateResult) string {
	var parts []string
	for _, r := range res.Rows {
		if r.Status != recon.StatusMatch {
			parts = append(parts, fmt.Sprintf("%s expected %d lot(s), broker holds %v", r.Symbol, r.ExpectedLots, r.BrokerLots))
		}
	}
	return "position mismatch overridden with " + recon.OverrideFlag + ": " + strings.Join(parts, "; ")
}

func describeLots(lots map[string]int) string {
	if len(lots) == 0 {
		return "flat"
	}
	syms := make([]string, 0, len(lots))
	for s := range lots {
		syms = append(syms, s)
	}
	sort.Strings(syms)
	var parts []string
	for _, s := range syms {
		parts = append(parts, fmt.Sprintf("%s %d lot(s)", s, lots[s]))
	}
	return strings.Join(parts, ", ")
}
