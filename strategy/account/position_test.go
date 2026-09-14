package account

import "testing"

func TestLotShare_Shares(t *testing.T) {
	tests := []struct {
		name string
		q    LotShare
		want float64
	}{
		{"share field populated", LotShare{Lot: 10, Share: 1000}, 1000},
		{"share field zero, falls back to lot*100", LotShare{Lot: 5, Share: 0}, 500},
		{"both zero", LotShare{}, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.q.Shares(); got != tt.want {
				t.Errorf("Shares() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestSettlement_Sellable_ReturnsAvailableNotBalance(t *testing.T) {
	s := Settlement{
		Available: LotShare{Lot: 10, Share: 1000},
		Balance:   LotShare{Lot: 20, Share: 2000},
		Unsettled: LotShare{Lot: 10, Share: 1000},
	}
	got := s.Sellable()
	if got.Shares() != 1000 {
		t.Errorf("Sellable().Shares() = %v, want 1000 (Available, not Balance)", got.Shares())
	}
	if got.Shares() == s.Balance.Shares() {
		t.Fatal("Sellable() must not equal Balance — selling unsettled shares is a server rejection")
	}
}

func TestSettlement_QuantitiesPossible(t *testing.T) {
	tests := []struct {
		name string
		s    Settlement
		want bool
	}{
		{
			name: "ordinary settled position",
			s:    Settlement{Available: LotShare{Share: 1000}, Unsettled: LotShare{Share: 0}, Balance: LotShare{Share: 1000}},
			want: true,
		},
		{
			name: "resting sell drives available and unsettled to 0 — still possible",
			s:    Settlement{Available: LotShare{Share: 0}, Unsettled: LotShare{Share: 0}, Balance: LotShare{Share: 2500}},
			want: true,
		},
		{
			name: "available alone exceeds balance — impossible under any reservedSell",
			s:    Settlement{Available: LotShare{Share: 3000}, Unsettled: LotShare{Share: 0}, Balance: LotShare{Share: 2500}},
			want: false,
		},
		{
			name: "unsettled alone exceeds balance — impossible under any reservedSell",
			s:    Settlement{Available: LotShare{Share: 0}, Unsettled: LotShare{Share: 3000}, Balance: LotShare{Share: 2500}},
			want: false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.s.QuantitiesPossible(); got != tt.want {
				t.Errorf("QuantitiesPossible() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestSettlement_SettlementConsistent(t *testing.T) {
	tests := []struct {
		name         string
		s            Settlement
		reservedSell float64
		want         bool
	}{
		{
			name: "no resting order: available + unsettled = balance",
			s:    Settlement{Available: LotShare{Share: 1000}, Unsettled: LotShare{Share: 1000}, Balance: LotShare{Share: 2000}},
			want: true,
		},
		{
			// The scenario that broke the old two-term BalanceConsistent:
			// a resting GTC sell for the entire position drives both
			// Available and Unsettled to 0. Confirmed live (synthetic
			// numbers here).
			name:         "resting sell explains the full balance",
			s:            Settlement{Available: LotShare{Share: 0}, Unsettled: LotShare{Share: 0}, Balance: LotShare{Share: 2500}},
			reservedSell: 2500,
			want:         true,
		},
		{
			name:         "resting sell explains only part of the balance",
			s:            Settlement{Available: LotShare{Share: 500}, Unsettled: LotShare{Share: 0}, Balance: LotShare{Share: 2500}},
			reservedSell: 2000,
			want:         true,
		},
		{
			name:         "three legs genuinely don't add up",
			s:            Settlement{Available: LotShare{Share: 100}, Unsettled: LotShare{Share: 50}, Balance: LotShare{Share: 200}},
			reservedSell: 20, // 100+50+20=170, not 200
			want:         false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.s.SettlementConsistent(tt.reservedSell); got != tt.want {
				t.Errorf("SettlementConsistent(%v) = %v, want %v", tt.reservedSell, got, tt.want)
			}
		})
	}
}
