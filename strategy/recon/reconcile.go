package recon

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"strategy/account"
	"strategy/engine"
)

const (
	StatusMatch    = "match"
	StatusMismatch = "mismatch"

	// ReportVersion is bumped when the JSON shape changes incompatibly; the
	// dashboard reads it.
	ReportVersion = 1

	sharesPerLot = 100
)

// Inputs is everything Reconcile needs. It holds no token and no account
// number: the caller fetches broker data and reads files, then passes values.
type Inputs struct {
	// AccountLabel is a short human label ("sleeve"); the report carries it
	// instead of the account number.
	AccountLabel string
	AsOf         time.Time

	StoreFiles []string // file base names only
	StoreFills []engine.StoreFill
	Manual     []ManualTrade
	Imported   []ImportedTrade // the ledger as it stands before this run

	// Holdings is the broker's balance per symbol, in shares.
	Holdings map[string]float64

	// History is the broker trade history, nil when it could not be fetched
	// (HistoryError says why).
	History      *account.History
	HistoryError string
	// WindowStart is the earliest instant the history covers; store fills
	// before it cannot be judged against it. Zero means no window.
	WindowStart time.Time
	Tolerance   time.Duration
}

// Breakdown is the signed lots each source contributes to a symbol.
type Breakdown struct {
	BotLots          int `json:"bot_lots"`
	ManualLots       int `json:"manual_lots"`
	BrokerImportLots int `json:"broker_import_lots"`
}

// SymbolRow is one symbol's expected vs broker position.
type SymbolRow struct {
	Symbol       string    `json:"symbol"`
	ExpectedLots int       `json:"expected_lots"`
	BrokerLots   float64   `json:"broker_lots"`
	Status       string    `json:"status"`
	Explanation  string    `json:"explanation"`
	Breakdown    Breakdown `json:"breakdown"`
}

// MatchedEntry is a broker trade paired with a bot fill.
type MatchedEntry struct {
	BrokerID       string  `json:"broker_id"`
	StoreOrderID   string  `json:"store_order_id"`
	Symbol         string  `json:"symbol"`
	Side           string  `json:"side"`
	Date           string  `json:"date"`
	TimeWIB        string  `json:"time_wib"`
	Lots           int     `json:"lots"`
	PriceIDR       float64 `json:"price_idr"`
	BrokerFeeIDR   float64 `json:"broker_fee_idr"`
	StoreFeeIDR    float64 `json:"store_fee_idr"`
	FeeGapIDR      float64 `json:"fee_gap_idr"`
	FeeGapFlagged  bool    `json:"fee_gap_flagged"`
	TimeDiffSecond float64 `json:"time_diff_s"`
}

// BrokerOnlyEntry is a broker trade no bot fill explains: a manual app trade.
type BrokerOnlyEntry struct {
	BrokerID        string  `json:"broker_id"`
	Symbol          string  `json:"symbol"`
	Side            string  `json:"side"`
	Date            string  `json:"date"`
	TimeWIB         string  `json:"time_wib"`
	Lots            int     `json:"lots"`
	PriceIDR        float64 `json:"price_idr"`
	FeeIDR          float64 `json:"fee_idr"`
	AlreadyImported bool    `json:"already_imported"`
}

// StoreOnlyEntry is a bot fill the broker has no trade for: a phantom fill.
type StoreOnlyEntry struct {
	OrderID  string  `json:"order_id"`
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Date     string  `json:"date"`
	TimeWIB  string  `json:"time_wib"`
	Lots     int     `json:"lots"`
	PriceIDR int64   `json:"price_idr"`
	FeeIDR   float64 `json:"fee_idr"`
}

// TradeCheck is the trade-by-trade comparison with the broker's history.
type TradeCheck struct {
	Checked bool   `json:"checked"`
	Error   string `json:"error,omitempty"`

	BrokerTrades     int `json:"broker_trades"`
	Matched          int `json:"matched"`
	BrokerOnly       int `json:"broker_only"`
	StoreOnly        int `json:"store_only"`
	PendingHistory   int `json:"pending_history"`
	NewlyImported    int `json:"newly_imported"`
	AlreadyImported  int `json:"already_imported"`
	ManualSuperseded int `json:"manual_superseded"`
	OutOfWindow      int `json:"out_of_window"`
	FeeGapsFlagged   int `json:"fee_gaps_flagged"`
	CashEvents       int `json:"cash_events"`

	Skipped map[string]int `json:"skipped"`
	Unknown map[string]int `json:"unknown_commands"`

	MatchedTrades    []MatchedEntry    `json:"matched_trades"`
	BrokerOnlyTrades []BrokerOnlyEntry `json:"broker_only_trades"`
	StoreOnlyTrades  []StoreOnlyEntry  `json:"store_only_trades"`
	// PendingHistoryTrades are store-only fills dated today (WIB): the broker
	// lists a day's trades from the next trading day, so they are expected, not phantom.
	PendingHistoryTrades []StoreOnlyEntry `json:"pending_history_trades"`
}

// Sources says what the expected position was derived from.
type Sources struct {
	Stores            []string `json:"stores"`
	StoreFills        int      `json:"store_fills"`
	UnattributedFills int      `json:"unattributed_fills"`
	ManualTrades      int      `json:"manual_trades"`
	BrokerImports     int      `json:"broker_imports"`
}

// Report is the JSON written to runs/recon/<date>.json. It contains no token
// and no account number — AccountLabel is a short label.
type Report struct {
	Version      int         `json:"version"`
	AsOf         string      `json:"as_of"`
	Date         string      `json:"date"`
	AccountLabel string      `json:"account_label"`
	Status       string      `json:"status"`
	Positions    []SymbolRow `json:"positions"`
	TradeCheck   *TradeCheck `json:"trade_check"`
	Sources      Sources     `json:"sources"`
	Errors       []string    `json:"errors"`
	Warnings     []string    `json:"warnings"`
}

// Output is Reconcile's result: the report plus the broker trades that are
// new to the ledger.
type Output struct {
	Report     Report
	NewImports []ImportedTrade
}

// leg is one signed contribution to a position.
type leg struct {
	source string
	symbol string
	side   string
	lots   int
	desc   string
}

func (l leg) signed() int {
	if l.side == "sell" {
		return -l.lots
	}
	return l.lots
}

// fillKey identifies one store fill.
type fillKey struct {
	run, order string
	seq        int64
}

func keyOf(f engine.StoreFill) fillKey { return fillKey{f.RunID, f.OrderID, f.Seq} }

func importFromBroker(t account.HistoryTrade) ImportedTrade {
	return ImportedTrade{
		BrokerID: t.ID, Source: SourceBrokerImport, Date: t.Date(),
		TimeWIB: t.Time.In(account.WIB).Format("15:04:05"),
		Symbol:  t.Symbol, Side: t.Side, Lots: t.Lots, PriceIDR: t.PriceIDR, FeeIDR: t.FeeIDR,
	}
}

func sameTrade(m ManualTrade, i ImportedTrade) bool {
	return m.Symbol == i.Symbol && m.Side == i.Side && m.Date == i.Date && m.Lots == i.Lots &&
		math.Abs(m.PriceIDR-i.PriceIDR) <= priceEpsilon
}

// supersede drops each manual entry that duplicates an imported broker trade
// (same symbol/side/date/price/lots), consuming one manual entry per import so
// a trade is counted once. It returns the surviving manual trades and how many
// were superseded.
func supersede(manual []ManualTrade, imports []ImportedTrade) ([]ManualTrade, int) {
	used := make([]bool, len(manual))
	n := 0
	for _, imp := range imports {
		for i, m := range manual {
			if !used[i] && sameTrade(m, imp) {
				used[i] = true
				n++
				break
			}
		}
	}
	var keep []ManualTrade
	for i, m := range manual {
		if !used[i] {
			keep = append(keep, m)
		}
	}
	return keep, n
}

func plural(n int, word string) string {
	if n == 1 || n == -1 {
		return fmt.Sprintf("%d %s", n, word)
	}
	return fmt.Sprintf("%d %ss", n, word)
}

// legsFor turns the three sources into signed legs.
func legsFor(fills []engine.StoreFill, manual []ManualTrade, imports []ImportedTrade) []leg {
	var legs []leg
	for _, f := range fills {
		legs = append(legs, leg{SourceBot, f.Symbol, f.Side, f.Lots,
			fmt.Sprintf("%s %d @ %d on %s", f.Side, f.Lots, f.PriceIDR, fillDate(f))})
	}
	for _, m := range manual {
		legs = append(legs, leg{SourceManual, m.Symbol, m.Side, m.Lots,
			fmt.Sprintf("%s %d @ %v on %s", m.Side, m.Lots, m.PriceIDR, m.Date)})
	}
	for _, i := range imports {
		legs = append(legs, leg{SourceBrokerImport, i.Symbol, i.Side, i.Lots,
			fmt.Sprintf("%s %d @ %v on %s, broker id %s", i.Side, i.Lots, i.PriceIDR, i.Date, i.BrokerID)})
	}
	return legs
}

// netBySymbol sums legs per symbol.
func netBySymbol(legs []leg) (map[string]*Breakdown, map[string][]leg) {
	bd := map[string]*Breakdown{}
	by := map[string][]leg{}
	for _, l := range legs {
		b := bd[l.symbol]
		if b == nil {
			b = &Breakdown{}
			bd[l.symbol] = b
		}
		switch l.source {
		case SourceBot:
			b.BotLots += l.signed()
		case SourceManual:
			b.ManualLots += l.signed()
		default:
			b.BrokerImportLots += l.signed()
		}
		by[l.symbol] = append(by[l.symbol], l)
	}
	return bd, by
}

func describeLegs(legs []leg) string {
	counts := map[string]int{}
	net := map[string]int{}
	var order []string
	var details []string
	for _, l := range legs {
		if _, ok := counts[l.source]; !ok {
			order = append(order, l.source)
		}
		counts[l.source]++
		net[l.source] += l.signed()
		if l.source != SourceBot {
			details = append(details, l.source+": "+l.desc)
		}
	}
	var parts []string
	for _, s := range order {
		parts = append(parts, fmt.Sprintf("%+d from %s (%s)", net[s], s, plural(counts[s], "trade")))
	}
	out := strings.Join(parts, ", ")
	if len(details) > 0 {
		out += " [" + strings.Join(details, "; ") + "]"
	}
	return out
}

// compareHoldings builds one row per symbol either side mentions.
func compareHoldings(bd map[string]*Breakdown, by map[string][]leg, holdings map[string]float64, historyChecked bool) []SymbolRow {
	syms := map[string]bool{}
	for s, b := range bd {
		if b.BotLots+b.ManualLots+b.BrokerImportLots != 0 || len(by[s]) > 0 {
			syms[s] = true
		}
	}
	for s := range holdings {
		syms[s] = true
	}
	var rows []SymbolRow
	for s := range syms {
		var b Breakdown
		if p := bd[s]; p != nil {
			b = *p
		}
		expected := b.BotLots + b.ManualLots + b.BrokerImportLots
		brokerShares := holdings[s]
		brokerLots := brokerShares / sharesPerLot
		if expected == 0 && brokerShares == 0 {
			continue // flat on both sides
		}
		row := SymbolRow{Symbol: s, ExpectedLots: expected, BrokerLots: brokerLots, Breakdown: b}
		whole := math.Abs(brokerLots-math.Round(brokerLots)) < 1e-9
		switch {
		case !whole:
			row.Status = StatusMismatch
			row.Explanation = fmt.Sprintf("broker holds %v shares, not a whole number of lots; expected %d lots", brokerShares, expected)
		case float64(expected) == brokerLots:
			row.Status = StatusMatch
			row.Explanation = fmt.Sprintf("expected %d = %s; broker holds %v", expected, describeLegs(by[s]), brokerLots)
		default:
			row.Status = StatusMismatch
			row.Explanation = mismatchExplanation(expected, brokerLots, by[s], historyChecked)
		}
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Symbol < rows[j].Symbol })
	return rows
}

func mismatchExplanation(expected int, brokerLots float64, legs []leg, historyChecked bool) string {
	var msg string
	diff := brokerLots - float64(expected)
	switch {
	case len(legs) == 0:
		msg = fmt.Sprintf("broker holds %v lots with no record in any store, manual or imported trade (older than the history window, or bought outside this bot)", brokerLots)
	case diff < 0:
		msg = fmt.Sprintf("broker holds %v fewer lot than the records explain (expected %d, broker %v): a sale the records do not show, most likely made by hand in the app", -diff, expected, brokerLots)
	default:
		msg = fmt.Sprintf("broker holds %v more lot than the records explain (expected %d, broker %v): a buy the records do not show, or older than the history window", diff, expected, brokerLots)
	}
	if expected < 0 {
		msg += "; the records sell more than they bought"
	}
	if !historyChecked {
		msg += "; broker trade history was NOT checked, so manual app trades could not be imported"
	}
	if len(legs) > 0 {
		msg += ". Records: " + describeLegs(legs)
	}
	return msg
}

// Reconcile derives the expected position from every store fill, manual trade
// and imported broker trade, compares it with the broker's holdings, and (when
// history is available) checks the trades one by one. Pure: no I/O.
func Reconcile(in Inputs) Output {
	tol := in.Tolerance
	if tol <= 0 {
		tol = DefaultMatchTolerance
	}
	tc := &TradeCheck{Skipped: map[string]int{}, Unknown: map[string]int{},
		MatchedTrades: []MatchedEntry{}, BrokerOnlyTrades: []BrokerOnlyEntry{}, StoreOnlyTrades: []StoreOnlyEntry{}, PendingHistoryTrades: []StoreOnlyEntry{}}
	var errs, warnings []string

	imports := append([]ImportedTrade(nil), in.Imported...)
	var newImports []ImportedTrade
	matchedIDs := map[string]bool{}
	var pairs []Pair

	if in.History == nil {
		tc.Error = in.HistoryError
		if tc.Error == "" {
			tc.Error = "broker history was not checked"
		}
	} else {
		tc.Checked = true
		h := in.History
		tc.BrokerTrades = len(h.Trades)
		tc.CashEvents = len(h.CashEvents)
		for k, n := range h.Skipped {
			tc.Skipped[k] = n
		}
		for k, n := range h.Unknown {
			tc.Unknown[k] = n
		}

		var inWindow []engine.StoreFill
		for _, f := range in.StoreFills {
			if !in.WindowStart.IsZero() && fillTime(f).Before(in.WindowStart) {
				tc.OutOfWindow++
				continue
			}
			inWindow = append(inWindow, f)
		}
		m := MatchTrades(h.Trades, inWindow, tol)
		pairs = m.Matched

		tc.Matched = len(m.Matched)
		for _, p := range m.Matched {
			matchedIDs[p.Broker.ID] = true
			e := MatchedEntry{
				BrokerID: p.Broker.ID, StoreOrderID: p.Fill.OrderID, Symbol: p.Broker.Symbol, Side: p.Broker.Side,
				Date: p.Broker.Date(), TimeWIB: p.Broker.Time.In(account.WIB).Format("15:04:05"),
				Lots: p.Broker.Lots, PriceIDR: p.Broker.PriceIDR, BrokerFeeIDR: p.Broker.FeeIDR, StoreFeeIDR: p.Fill.FeeIDR,
				FeeGapIDR: p.FeeGap(), FeeGapFlagged: p.FeeGapFlagged(), TimeDiffSecond: p.TimeDiff().Seconds(),
			}
			if e.FeeGapFlagged {
				tc.FeeGapsFlagged++
			}
			tc.MatchedTrades = append(tc.MatchedTrades, e)
		}

		have := map[string]bool{}
		for _, i := range imports {
			have[i.BrokerID] = true
		}
		tc.BrokerOnly = len(m.BrokerOnly)
		for _, b := range m.BrokerOnly {
			already := have[b.ID]
			e := BrokerOnlyEntry{
				BrokerID: b.ID, Symbol: b.Symbol, Side: b.Side, Date: b.Date(),
				TimeWIB: b.Time.In(account.WIB).Format("15:04:05"), Lots: b.Lots, PriceIDR: b.PriceIDR, FeeIDR: b.FeeIDR,
				AlreadyImported: already,
			}
			tc.BrokerOnlyTrades = append(tc.BrokerOnlyTrades, e)
			if already {
				tc.AlreadyImported++
				continue
			}
			imp := importFromBroker(b)
			newImports = append(newImports, imp)
			imports = append(imports, imp)
			tc.NewlyImported++
		}

		today := in.AsOf.In(account.WIB).Format("2006-01-02")
		for _, f := range m.StoreOnly {
			e := StoreOnlyEntry{
				OrderID: f.OrderID, Symbol: f.Symbol, Side: f.Side, Date: fillDate(f),
				TimeWIB: fillTime(f).Format("15:04:05"), Lots: f.Lots, PriceIDR: f.PriceIDR, FeeIDR: f.FeeIDR,
			}
			desc := fmt.Sprintf("store order %s (%s %d %s @ %d on %s)", f.OrderID, f.Side, f.Lots, f.Symbol, f.PriceIDR, e.Date)
			if e.Date == today {
				tc.PendingHistory++
				tc.PendingHistoryTrades = append(tc.PendingHistoryTrades, e)
				warnings = append(warnings, fmt.Sprintf("awaiting broker history: %s — the broker lists today's trades from the next trading day; re-run recon then", desc))
				continue
			}
			tc.StoreOnly++
			tc.StoreOnlyTrades = append(tc.StoreOnlyTrades, e)
			errs = append(errs, fmt.Sprintf("phantom fill: %s has no matching broker trade — a bot bug", desc))
		}
	}

	// A ledger import that a store fill now explains counts once, as the fill.
	// This also covers a run with no history: match the earlier ledger against
	// the fills directly. A fill already paired with a broker trade above cannot
	// explain a second one, or two identical trades with one bot fill would
	// lose the manual one.
	paired := map[fillKey]bool{}
	for _, p := range pairs {
		paired[keyOf(p.Fill)] = true
	}
	var unpaired []engine.StoreFill
	for _, f := range in.StoreFills {
		if !paired[keyOf(f)] {
			unpaired = append(unpaired, f)
		}
	}
	for id := range explainedImportIDs(in.Imported, unpaired, tol) {
		matchedIDs[id] = true
	}
	var activeImports []ImportedTrade
	for _, i := range imports {
		if !matchedIDs[i.BrokerID] {
			activeImports = append(activeImports, i)
		}
	}
	manual, superseded := supersede(in.Manual, activeImports)
	tc.ManualSuperseded = superseded

	legs := legsFor(in.StoreFills, manual, activeImports)
	bd, by := netBySymbol(legs)
	rows := compareHoldings(bd, by, in.Holdings, tc.Checked)
	if rows == nil {
		rows = []SymbolRow{}
	}

	status := StatusMatch
	for _, r := range rows {
		if r.Status != StatusMatch {
			status = StatusMismatch
		}
	}
	unattr := 0
	for _, f := range in.StoreFills {
		if f.Unattributed {
			unattr++
		}
	}
	asOf := in.AsOf.In(account.WIB)
	if errs == nil {
		errs = []string{}
	}
	if warnings == nil {
		warnings = []string{}
	}
	return Output{
		NewImports: newImports,
		Report: Report{
			Version: ReportVersion, AsOf: asOf.Format(time.RFC3339), Date: asOf.Format("2006-01-02"),
			AccountLabel: in.AccountLabel, Status: status, Positions: rows, TradeCheck: tc, Errors: errs, Warnings: warnings,
			Sources: Sources{
				Stores: append([]string{}, in.StoreFiles...), StoreFills: len(in.StoreFills), UnattributedFills: unattr,
				ManualTrades: len(manual), BrokerImports: len(activeImports),
			},
		},
	}
}
