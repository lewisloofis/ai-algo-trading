package live

import "testing"

func TestArmingFromEnv(t *testing.T) {
	tests := []struct {
		name    string
		live    string
		confirm string
		want    bool
	}{
		{"both correct", "true", LiveConfirmPhrase, true},
		{"ALGO_LIVE=1 also counts (ParseBool)", "1", LiveConfirmPhrase, true},
		{"neither set", "", "", false},
		{"only ALGO_LIVE", "true", "", false},
		{"only ALGO_CONFIRM", "", LiveConfirmPhrase, false},
		{"wrong phrase", "true", "i_understand_live_trading", false}, // case must match exactly
		{"phrase with trailing space", "true", LiveConfirmPhrase + " ", false},
		{"ALGO_LIVE=false with correct phrase", "false", LiveConfirmPhrase, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("ALGO_LIVE", tt.live)
			t.Setenv("ALGO_CONFIRM", tt.confirm)
			if got := ArmingFromEnv().Armed(); got != tt.want {
				t.Errorf("ArmingFromEnv().Armed() = %v, want %v", got, tt.want)
			}
		})
	}
}

// TestArming_ZeroValueIsNotArmed guards the failure mode Arming's doc
// comment exists to prevent: an Arming built any way other than
// ArmingFromEnv must fail closed.
func TestArming_ZeroValueIsNotArmed(t *testing.T) {
	var a Arming
	if a.Armed() {
		t.Fatal("zero-value Arming{}.Armed() = true, want false")
	}
}
