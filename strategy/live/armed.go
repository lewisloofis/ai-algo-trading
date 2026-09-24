package live

import (
	"errors"
	"os"
	"strconv"
)

// LiveConfirmPhrase is the exact ALGO_CONFIRM value that, together with
// ALGO_LIVE=true, arms real order routing (ADR-0016 gate G4). It must match
// strategy/config.go's unexported liveConfirmPhrase byte-for-byte.
//
// It is duplicated here rather than imported: strategy's top-level config
// lives in package main, which nothing outside it can import, and this
// package cannot depend on the command that will eventually construct it
// without an import cycle the other direction. Keep the two literals in
// sync by hand; whoever wires this package into strategy/cmd (plan 18 §5's
// remaining B4 step) should add a test there that asserts the two strings
// are equal, since nothing in this package can check strategy/config.go's
// unexported constant itself.
const LiveConfirmPhrase = "I_UNDERSTAND_LIVE_TRADING"

// ErrNotArmed is returned by NewClient when the double-arm gate is not
// satisfied. See Arming's doc comment for why there is no dry-run fallback
// instead.
var ErrNotArmed = errors.New("live: not armed — set ALGO_LIVE=true and ALGO_CONFIRM=" + LiveConfirmPhrase + " (there is no dry-run fallback; see engine.PaperSink for that)")

// Arming is the result of checking the double-arm gate: ALGO_LIVE=true AND
// ALGO_CONFIRM equal to LiveConfirmPhrase, verbatim. The zero value,
// Arming{}, reports Armed() == false — NOT armed — on purpose: an Arming
// built any way other than ArmingFromEnv (a forgotten call, a zero-valued
// field in a larger config struct) must fail closed rather than read as
// "confirmed safe by omission", the same failure shape risk.TriState and
// engine.BoardType's doc comments describe elsewhere in this repo.
type Arming struct {
	armed bool
}

// ArmingFromEnv reads ALGO_LIVE and ALGO_CONFIRM from the process
// environment. ALGO_LIVE is parsed with strconv.ParseBool — the same
// convention strategy/config.go's envBool uses — so "true", "1", "TRUE" all
// arm it; anything else (including unset) does not. ALGO_CONFIRM must equal
// LiveConfirmPhrase exactly: no trimming, no case-folding. A phrase this
// consequential should not be "close enough" to count.
func ArmingFromEnv() Arming {
	live, _ := strconv.ParseBool(os.Getenv("ALGO_LIVE"))
	confirmed := os.Getenv("ALGO_CONFIRM") == LiveConfirmPhrase
	return Arming{armed: live && confirmed}
}

// Armed reports whether the double-arm gate was satisfied.
func (a Arming) Armed() bool { return a.armed }
