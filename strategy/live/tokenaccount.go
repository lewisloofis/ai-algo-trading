package live

import "strategy/tokenacct"

// ErrTokenAccountUnknown is wrapped by every TokenAccount failure: the
// account a trading token routes to could not be determined.
var ErrTokenAccountUnknown = tokenacct.ErrUnknown

// TokenAccount returns the brokerage account (the JWT payload's data.acn
// claim) that orders placed with this trading token route to. Carina order
// requests carry no account field; routing follows this claim (observed
// 2026-10-14, see doc.go). The decoder lives in strategy/tokenacct so the
// read-only reconciliation code can use it without importing this package,
// which can place orders.
func TokenAccount(token string) (string, error) { return tokenacct.Account(token) }
