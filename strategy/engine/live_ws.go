package engine

import (
	"context"
	"fmt"
	"sync"
	"time"

	"collector/feed"
)

// heartbeatInterval matches collector/cmd/collector's heartbeatInterval: a
// keep-alive ping sent on this cadence so the feed does not consider the
// connection idle between market data frames.
const heartbeatInterval = 10 * time.Second

// WSDialer is the production Dialer: it authenticates via a *feed.Session,
// dials the real market-data WebSocket with collector/feed.DialWS, and sends
// the subscribe frame — mirroring collector/cmd/collector's runOnce
// connect+subscribe sequence, and reusing collector/feed for all of it (no
// second WebSocket client is written here).
//
// WSDialer writes exactly two kinds of frame to the socket: the subscribe
// frame once per connection, and a periodic keep-alive ping. Neither is an
// order: WSDialer has no order-routing capability and satisfies no sink
// interface.
//
// NewWSDialer's key/token handling never logs a credential value, per
// collector/feed.Session's own SECURITY comments.
type WSDialer struct {
	Session  *feed.Session
	WSURL    string
	SubProto string
	Symbols  []string
	Channels feed.Channels
}

// NewWSDialer returns a Dialer bound to session, dialing wsURL/subProto and
// subscribing symbols for the given channels on every connection attempt
// (the key is re-fetched every time, matching collector/feed's own doc that
// it is short-lived and should be re-fetched on every connect).
func NewWSDialer(session *feed.Session, wsURL, subProto string, symbols []string, channels feed.Channels) Dialer {
	d := &WSDialer{
		Session:  session,
		WSURL:    wsURL,
		SubProto: subProto,
		Symbols:  symbols,
		Channels: channels,
	}
	return d.dial
}

func (d *WSDialer) dial(ctx context.Context) (LiveConn, error) {
	key, err := d.Session.GetWebsocketKey()
	if err != nil {
		return nil, fmt.Errorf("websocket key: %w", err)
	}

	conn, err := feed.DialWS(d.WSURL, d.SubProto)
	if err != nil {
		return nil, fmt.Errorf("dial ws: %w", err)
	}

	subFrame := feed.BuildSubscribe(d.Session.UserID(), d.Session.AccessToken(), key, d.Symbols, d.Channels)
	if err := conn.WriteBinary(subFrame); err != nil {
		conn.Close()
		return nil, fmt.Errorf("write subscribe frame: %w", err)
	}

	return newHeartbeatConn(conn, d.Session), nil
}

// heartbeatConn wraps a *feed.WsConn with a background keep-alive ping,
// mirroring collector/cmd/collector's heartbeat goroutine. ReadMessage and
// SetReadDeadline are inherited directly from the embedded *feed.WsConn;
// Close is overridden to stop the heartbeat before closing the socket.
type heartbeatConn struct {
	*feed.WsConn
	stop     chan struct{}
	stopOnce sync.Once
}

var _ LiveConn = (*heartbeatConn)(nil)

func newHeartbeatConn(conn *feed.WsConn, session *feed.Session) *heartbeatConn {
	hc := &heartbeatConn{WsConn: conn, stop: make(chan struct{})}
	go hc.pingLoop(session)
	return hc
}

func (hc *heartbeatConn) pingLoop(session *feed.Session) {
	t := time.NewTicker(heartbeatInterval)
	defer t.Stop()
	for {
		select {
		case <-hc.stop:
			return
		case <-t.C:
			if err := hc.WsConn.WriteBinary(feed.BuildPing(session.UserID(), session.AccessToken())); err != nil {
				return
			}
		}
	}
}

func (hc *heartbeatConn) Close() error {
	hc.stopOnce.Do(func() { close(hc.stop) })
	return hc.WsConn.Close()
}
