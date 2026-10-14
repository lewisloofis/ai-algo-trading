package recon

import (
	"context"
	"errors"
	"strings"
	"testing"

	"strategy/account"
	"strategy/engine"
)

type fakeBroker struct {
	list  account.PortfolioList
	err   error
	calls int
}

func (f *fakeBroker) PortfolioList(ctx context.Context) (account.PortfolioList, error) {
	f.calls++
	return f.list, f.err
}

func holding(symbol string, shares float64) account.Position {
	return account.Position{Symbol: symbol, Qty: account.Settlement{Balance: account.LotShare{Share: shares, Lot: shares / 100}}}
}

func twoBuys() []engine.StoreFill {
	return []engine.StoreFill{
		sf("a", "buy", "2026-10-13", "14:12:02", 27, 1, 4),
		sf("b", "buy", "2026-10-14", "15:38:01", 28, 1, 4),
	}
}

func TestBrokerHoldings_ReadsBalanceInShares(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 100), holding("BBCA", 0)}}}
	h, err := BrokerHoldings(context.Background(), fb)
	if err != nil {
		t.Fatal(err)
	}
	if h["GOTO"] != 100 || len(h) != 1 {
		t.Fatalf("holdings = %v, want only GOTO=100 (flat symbols omitted)", h)
	}
}

func TestBrokerHoldings_ErrorPropagates(t *testing.T) {
	want := errors.New("boom")
	if _, err := BrokerHoldings(context.Background(), &fakeBroker{err: want}); !errors.Is(err, want) {
		t.Fatalf("err = %v, want wrapped boom", err)
	}
}

func TestCheckPositions_MismatchRefusesWithClearMessage(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 100)}}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{Fills: twoBuys()})
	if err != nil {
		t.Fatal(err)
	}
	if res.Match {
		t.Fatal("stores say 2 lots, broker 1: must not match")
	}
	msg := res.Message("account-recon --foo")
	for _, want := range []string{"GOTO", "expected 2", "broker holds 1", "--allow-position-mismatch", "account-recon --foo"} {
		if !strings.Contains(msg, want) {
			t.Errorf("message missing %q:\n%s", want, msg)
		}
	}
}

func TestCheckPositions_ImportedManualSaleMakesItMatchAndSetsInitial(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 100)}}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{
		Fills: twoBuys(),
		Imported: []ImportedTrade{{BrokerID: "900000002", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:14:15",
			Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Match {
		t.Fatalf("should match once the manual sale is imported: %s", res.Message("x"))
	}
	// The strategy must start from the broker-consistent position (1), not
	// the stores' 2.
	if res.ExpectedLots["GOTO"] != 1 || len(res.ExpectedLots) != 1 {
		t.Fatalf("expected lots = %v, want GOTO=1", res.ExpectedLots)
	}
}

func TestCheckPositions_ManualTradeFileCounts(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 100)}}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{
		Fills:  twoBuys(),
		Manual: []ManualTrade{{Date: "2026-10-13", Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5}},
	})
	if err != nil || !res.Match {
		t.Fatalf("res = %+v err = %v", res, err)
	}
}

// An import that a store fill explains must not be counted twice.
func TestCheckPositions_ImportExplainedByFillIsNotDoubleCounted(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 200)}}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{
		Fills: twoBuys(),
		Imported: []ImportedTrade{{BrokerID: "x", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:12:00",
			Symbol: "GOTO", Side: "buy", Lots: 1, PriceIDR: 27, FeeIDR: 4.05}},
	})
	if err != nil || !res.Match || res.ExpectedLots["GOTO"] != 2 {
		t.Fatalf("res = %+v err = %v", res, err)
	}
}

func TestCheckPositions_BrokerErrorIsAnErrorNotAMatch(t *testing.T) {
	fb := &fakeBroker{err: account.ErrUnauthorized}
	if _, err := CheckPositions(context.Background(), fb, GateInputs{Fills: twoBuys()}); !errors.Is(err, account.ErrUnauthorized) {
		t.Fatalf("err = %v, want ErrUnauthorized (an unreadable broker must never read as flat)", err)
	}
}

// The gate loads only the stores --prior-stores names. A manual/imported
// trade from before the earliest loaded fill belongs to history those stores
// do not cover (it sold lots bought in a store that is not loaded), so it must
// not be subtracted from the loaded fills.
func TestCheckPositions_LedgerBeforeLoadedStoresIsIgnored(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 100)}}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{
		Fills: []engine.StoreFill{sf("b", "buy", "2026-10-14", "15:38:01", 28, 1, 4)},
		Imported: []ImportedTrade{{BrokerID: "900000002", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:14:15",
			Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Match || res.ExpectedLots["GOTO"] != 1 {
		t.Fatalf("res = %+v: the 10-13 sale predates the loaded stores and must be ignored", res)
	}
	if res.IgnoredBeforeStores != 1 {
		t.Errorf("IgnoredBeforeStores = %d, want 1", res.IgnoredBeforeStores)
	}
}

func TestCheckPositions_LedgerAfterLoadedStoresCounts(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{Results: []account.Position{holding("GOTO", 0)}}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{
		Fills: []engine.StoreFill{sf("b", "buy", "2026-10-14", "15:38:01", 28, 1, 4)},
		Imported: []ImportedTrade{{BrokerID: "S", Source: SourceBrokerImport, Date: "2026-10-14", TimeWIB: "16:00:00",
			Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 28, FeeIDR: 7}},
	})
	if err != nil || !res.Match {
		t.Fatalf("res = %+v err = %v", res, err)
	}
}

func TestCheckPositions_FlatBothSides(t *testing.T) {
	fb := &fakeBroker{list: account.PortfolioList{}}
	res, err := CheckPositions(context.Background(), fb, GateInputs{})
	if err != nil || !res.Match || len(res.ExpectedLots) != 0 {
		t.Fatalf("res = %+v err = %v", res, err)
	}
}
