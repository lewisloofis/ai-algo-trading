package engine

import (
	"context"
	"log"

	"collector/replay"
)

// PacingMode re-exports the replayer's pacing modes.
type PacingMode = replay.PacingMode

const (
	ModeFast  = replay.ModeFast
	ModePaced = replay.ModePaced
)

// ReplaySource replays the given segments on a background goroutine and returns
// a channel of MarketEvents. The channel is closed when replay finishes or ctx
// is cancelled. Replay errors are logged; partial streams still close cleanly.
func ReplaySource(ctx context.Context, segments []string, mode PacingMode) <-chan MarketEvent {
	ch := make(chan MarketEvent, 256)
	go func() {
		defer close(ch)
		err := replay.Replay(segments, mode, func(e replay.MarketEvent) {
			select {
			case ch <- fromReplay(e):
			case <-ctx.Done():
			}
		})
		if err != nil {
			log.Printf("replay source: %v", err)
		}
	}()
	return ch
}
