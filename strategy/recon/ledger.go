package recon

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"
)

// Sources a ledger leg can come from.
const (
	SourceBot          = "bot"
	SourceManual       = "manual"
	SourceBrokerImport = "broker-import"
)

var (
	dateRE   = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
	timeRE   = regexp.MustCompile(`^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$`)
	symbolRE = regexp.MustCompile(`^[A-Z0-9]{2,8}$`)
)

// ManualTrade is one trade the owner placed by hand in the broker app, from
// runs/manual_trades.json. The schema is the dashboard's
// (research/dashboard/manual_trades.py): the same file, the same strictness —
// an unknown field, bad side, non-positive lots/price or bad date is an error
// naming the trade. A missing file means no manual trades.
type ManualTrade struct {
	Date     string  `json:"date"`
	TimeWIB  string  `json:"time_wib"`
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Lots     int     `json:"lots"`
	PriceIDR float64 `json:"price_idr"`
	FeeIDR   float64 `json:"fee_idr"`
	Note     string  `json:"note"`

	NetAmountIDR         *float64 `json:"net_amount_idr,omitempty"`
	BrokerRealisedPnlIDR *float64 `json:"broker_realised_pnl_idr,omitempty"`
	ClosesOrderID        string   `json:"closes_order_id,omitempty"`
}

// ImportedTrade is a trade the broker's history shows that no bot fill
// explains (a manual app trade), written into the ledger by account-recon.
// BrokerID is the broker's own id and makes the import idempotent. It holds
// no account number or holder name, by construction.
type ImportedTrade struct {
	BrokerID string  `json:"broker_id"`
	Source   string  `json:"source"`
	Date     string  `json:"date"`
	TimeWIB  string  `json:"time_wib"`
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Lots     int     `json:"lots"`
	PriceIDR float64 `json:"price_idr"`
	FeeIDR   float64 `json:"fee_idr"`
}

func validateLeg(date, clock, symbol, side string, lots int, price, fee float64) error {
	if !dateRE.MatchString(date) {
		return fmt.Errorf("date must be YYYY-MM-DD, got %q", date)
	}
	if _, err := time.Parse("2006-01-02", date); err != nil {
		return fmt.Errorf("date %q is not a real calendar date", date)
	}
	if clock != "" && !timeRE.MatchString(clock) {
		return fmt.Errorf("time_wib must be HH:MM[:SS] or null, got %q", clock)
	}
	if !symbolRE.MatchString(symbol) {
		return fmt.Errorf("symbol must be an upper-case ticker, got %q", symbol)
	}
	if side != "buy" && side != "sell" {
		return fmt.Errorf("side must be 'buy' or 'sell', got %q", side)
	}
	if lots <= 0 {
		return fmt.Errorf("lots must be a whole number above 0, got %d", lots)
	}
	if !(price > 0) {
		return fmt.Errorf("price_idr must be above 0, got %v", price)
	}
	if fee < 0 {
		return fmt.Errorf("fee_idr must be 0 or more, got %v", fee)
	}
	return nil
}

// decodeStrict decodes JSON rejecting unknown fields.
func decodeStrict(data []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

// LoadManual reads path. A missing file is not an error.
func LoadManual(path string) ([]ManualTrade, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("manual trades: %w", err)
	}
	var doc struct {
		Schema json.RawMessage `json:"_schema"`
		Trades *[]ManualTrade  `json:"trades"`
	}
	if err := decodeStrict(data, &doc); err != nil {
		return nil, fmt.Errorf("manual trades %s: %w", filepath.Base(path), err)
	}
	if doc.Trades == nil {
		return nil, fmt.Errorf(`manual trades %s: top level must be an object with a "trades" list`, filepath.Base(path))
	}
	for i, tr := range *doc.Trades {
		if err := validateLeg(tr.Date, tr.TimeWIB, tr.Symbol, tr.Side, tr.Lots, tr.PriceIDR, tr.FeeIDR); err != nil {
			return nil, fmt.Errorf("manual trades %s: trade #%d: %w", filepath.Base(path), i+1, err)
		}
	}
	return *doc.Trades, nil
}

// LoadImports reads the broker-import ledger. A missing file is empty.
func LoadImports(path string) ([]ImportedTrade, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("broker ledger: %w", err)
	}
	var doc struct {
		Schema json.RawMessage  `json:"_schema"`
		Trades *[]ImportedTrade `json:"trades"`
	}
	if err := decodeStrict(data, &doc); err != nil {
		return nil, fmt.Errorf("broker ledger %s: %w", filepath.Base(path), err)
	}
	if doc.Trades == nil {
		return nil, fmt.Errorf(`broker ledger %s: top level must be an object with a "trades" list`, filepath.Base(path))
	}
	for i, tr := range *doc.Trades {
		if tr.BrokerID == "" {
			return nil, fmt.Errorf("broker ledger %s: trade #%d: broker_id is required", filepath.Base(path), i+1)
		}
		if tr.Source != SourceBrokerImport {
			return nil, fmt.Errorf("broker ledger %s: trade #%d: source must be %q, got %q", filepath.Base(path), i+1, SourceBrokerImport, tr.Source)
		}
		if err := validateLeg(tr.Date, tr.TimeWIB, tr.Symbol, tr.Side, tr.Lots, tr.PriceIDR, tr.FeeIDR); err != nil {
			return nil, fmt.Errorf("broker ledger %s: trade #%d: %w", filepath.Base(path), i+1, err)
		}
	}
	return *doc.Trades, nil
}

// MergeImports adds the trades in add whose BrokerID is not already in
// existing, and returns the merged list sorted by (date, time, broker id)
// with the number added. Merging the same trades again adds nothing.
func MergeImports(existing, add []ImportedTrade) ([]ImportedTrade, int) {
	seen := make(map[string]bool, len(existing)+len(add))
	out := make([]ImportedTrade, 0, len(existing)+len(add))
	for _, t := range existing {
		if !seen[t.BrokerID] {
			seen[t.BrokerID] = true
			out = append(out, t)
		}
	}
	added := 0
	for _, t := range add {
		if !seen[t.BrokerID] {
			seen[t.BrokerID] = true
			out = append(out, t)
			added++
		}
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i], out[j]
		if a.Date != b.Date {
			return a.Date < b.Date
		}
		if a.TimeWIB != b.TimeWIB {
			return a.TimeWIB < b.TimeWIB
		}
		return a.BrokerID < b.BrokerID
	})
	return out, added
}

// SaveImports writes the ledger atomically (temp file in the same directory,
// then rename), creating the directory if needed.
func SaveImports(path string, trades []ImportedTrade) error {
	if trades == nil {
		trades = []ImportedTrade{}
	}
	doc := struct {
		Schema string          `json:"_schema"`
		Trades []ImportedTrade `json:"trades"`
	}{
		Schema: "Trades the broker history shows that no bot fill explains; written by account-recon, keyed by broker_id (idempotent). Never hand-edit.",
		Trades: trades,
	}
	data, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(path, append(data, '\n'))
}

func writeFileAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("create %s: %w", dir, err)
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := os.Chmod(name, 0o644); err != nil {
		os.Remove(name)
		return err
	}
	if err := os.Rename(name, path); err != nil {
		os.Remove(name)
		return err
	}
	return nil
}
