package livesession

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"strategy/recon"
)

// RetryCommand returns the live-session command line with --retry set to
// level (replacing any existing --retry). It is only ever PRINTED: a
// deliberate same-day resubmission stays an operator decision. argv carries
// no secrets (tokens live in the environment).
func RetryCommand(argv []string, level int) string {
	var out []string
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		switch {
		case a == "--retry" || a == "-retry":
			i++ // drop the value
		case strings.HasPrefix(a, "--retry=") || strings.HasPrefix(a, "-retry="):
		default:
			out = append(out, a)
		}
	}
	out = append(out, "--retry", fmt.Sprint(level))
	return strings.Join(out, " ")
}

// totals aggregates the session's stores.
type totals struct {
	orders, fills, rejects, killRejects int
	fees                                float64
}

// finalize reads the stores, verifies the binary hash recorded by strategy-run,
// post-reconciles against the broker, prints the one-screen summary, raises the
// remaining alerts and returns the exit code.
func (s *Session) finalize(ctx context.Context, stores []string, binarySHA string, clean, killed bool, lastExit int) int {
	cfg := s.cfg
	var tot totals
	integrity := false
	var rejectText []string
	hashNote := "verified"

	for i, p := range stores {
		sum, err := ReadStoreSummary(p)
		if err != nil {
			s.logf("cannot read %s: %v", filepath.Base(p), err)
			integrity = true
			continue
		}
		tot.orders += sum.Orders
		tot.fills += sum.Fills
		tot.fees += sum.FeesIDR
		tot.rejects += sum.Rejects
		tot.killRejects += sum.KillRejects
		if sum.RejectText != "" {
			rejectText = append(rejectText, sum.RejectText)
		}
		// Self-check: the hash strategy-run recorded in the run row must be the
		// hash of the binary this session preflighted.
		if sum.HasRun && sum.BinaryHash != binarySHA {
			integrity = true
			hashNote = "MISMATCH"
			s.alert(KindBinaryHash, SeverityCritical, p, i+1, "run row binary_hash %q != preflighted strategy-run sha256 %q: the binary that ran is not the binary that was checked", shortHash(sum.BinaryHash), shortHash(binarySHA))
		}
		if clean && i == len(stores)-1 && sum.Exists && !sum.HasRun {
			integrity = true
			s.alert(KindBinaryHash, SeverityCritical, p, i+1, "clean exit but the store has no run row")
		}
	}

	if tot.killRejects > 0 && !killed {
		s.alert(KindKillSwitch, SeverityCritical, "", 0, "kill switch tripped in-process: %d order(s) rejected by it", tot.killRejects)
	}
	if other := tot.rejects - tot.killRejects; other > 0 {
		s.alert(KindReject, SeverityWarn, "", 0, "%d order(s) rejected: %s", other, strings.Join(rejectText, " | "))
	}
	if clean && cfg.Sink == "live" && tot.orders == 0 {
		s.alert(KindZeroRecord, SeverityWarn, "", 0, "clean run attempted 0 orders: expected only if the target already equals the position")
	}

	verdict := "n/a (paper sink: no broker position to reconcile)"
	divergent := false
	if cfg.Sink == "live" {
		verdict, divergent = s.postReconcile(ctx, stores)
	} else {
		verdict += s.paperPositionNote(stores)
	}

	code := ExitClean
	switch {
	case !clean || integrity:
		code = ExitRefused
	case divergent || tot.rejects > 0 || killed:
		code = ExitProblem
	}

	result := map[int]string{ExitClean: "CLEAN", ExitProblem: "PROBLEM (divergence, reject or kill switch)", ExitRefused: "FAILED (crashed, refused or integrity failure)"}[code]
	names := make([]string, len(stores))
	for i, p := range stores {
		names[i] = filepath.Base(p)
	}
	w := s.d.Out
	fmt.Fprintf(w, "\n== live-session summary ==\n")
	fmt.Fprintf(w, "strategy %s | sink %s | source %s | attempts %d | last exit %d\n", s.strategyID, cfg.Sink, cfg.Source, len(stores), lastExit)
	fmt.Fprintf(w, "stores   %s\n", strings.Join(names, ", "))
	fmt.Fprintf(w, "orders %d | fills %d | fees %.0f IDR | rejects %d\n", tot.orders, tot.fills, tot.fees, tot.rejects)
	fmt.Fprintf(w, "recon    %s\n", verdict)
	fmt.Fprintf(w, "binary   %s (sha256 %s)\n", hashNote, shortHash(binarySHA))
	fmt.Fprintf(w, "result   %s -> exit %d\n", result, code)
	return code
}

// paperPositionNote is the paper summary's position line: derived from every
// paper store of this strategy (all days) plus this session's own stores.
func (s *Session) paperPositionNote(stores []string) string {
	globs := append(PriorStoreGlobs(s.cfg, s.storeKey), stores...)
	pos, names, err := PaperPositions(globs)
	if err != nil {
		return "; paper position unreadable: " + err.Error()
	}
	return fmt.Sprintf("; paper position after this session (%d store(s)): %s", len(names), FormatLots(pos))
}

// postReconcile compares the broker with the position derived from every
// prior store plus this session's own stores, retrying briefly because the
// broker's portfolio can lag a fill. It uses a fresh context: after an
// operator Ctrl-C the run's context is already cancelled.
func (s *Session) postReconcile(ctx context.Context, stores []string) (string, bool) {
	cfg := s.cfg
	if s.d.Broker == nil {
		s.alert(KindDivergence, SeverityCritical, "", 0, "post-reconcile impossible: no broker client")
		return "UNAVAILABLE (no broker client)", true
	}
	globs := PriorStoreGlobs(cfg, s.storeKey)
	var last PositionCheck
	var lastErr error
	for try := 1; try <= cfg.ReconTries; try++ {
		cctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		last, lastErr = CheckPosition(cctx, s.d.Broker, cfg, globs, stores)
		cancel()
		if lastErr == nil && ExpectedPosition(last) {
			return fmt.Sprintf("MATCH (%d store(s) read)", len(last.Stores)), false
		}
		if try < cfg.ReconTries {
			s.d.Sleep(cfg.ReconSettle)
		}
	}
	if lastErr != nil {
		s.alert(KindDivergence, SeverityCritical, "", 0, "post-reconcile could not run: %v", lastErr)
		return "UNAVAILABLE (" + lastErr.Error() + ")", true
	}
	var rows []string
	for _, r := range last.Result.Rows {
		if r.Status != recon.StatusMatch {
			rows = append(rows, fmt.Sprintf("%s expected %d lot(s), broker %v: %s", r.Symbol, r.ExpectedLots, r.BrokerLots, r.Explanation))
		}
	}
	detail := strings.Join(rows, "; ")
	s.alert(KindDivergence, SeverityCritical, "", 0, "post-reconcile divergence: %s", detail)
	return "DIVERGENT: " + detail, true
}
