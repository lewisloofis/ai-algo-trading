// Package feed provides the reusable transport, authentication, and codec
// layer for the Stockbit market-data WebSocket feed. It is dependency-free
// (uses only the Go standard library) and compatible with Go 1.19.
//
// Callers construct a Session via NewSession, call Login/RefreshMain, then
// dial a WsConn via DialWS and exchange binary frames using the Build*
// encoder helpers and the Decode* decoder helpers.
package feed

import (
	"bufio"
	"crypto/rand"
	"crypto/sha1"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// WebSocket opcodes.
const (
	OpContinuation byte = 0x0
	OpText         byte = 0x1
	OpBinary       byte = 0x2
	OpClose        byte = 0x8
	OpPing         byte = 0x9
	OpPong         byte = 0xA
)

const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

var wsDialer = net.Dialer{Timeout: 15 * time.Second}

// WsConn is a minimal RFC 6455 WebSocket connection (client side only).
// It supports the wss:// handshake, masked client frames, server frame
// reassembly, and automatic pong replies.
type WsConn struct {
	conn io.ReadWriteCloser
	br   *bufio.Reader
}

// DialWS opens a TLS WebSocket connection to rawURL (wss://host/path),
// requesting the given sub-protocol (e.g. "web" or "ios").
func DialWS(rawURL, subProtocol string) (*WsConn, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	if u.Scheme != "wss" {
		return nil, fmt.Errorf("only wss:// is supported, got %q", u.Scheme)
	}
	host := u.Host
	if !strings.Contains(host, ":") {
		host += ":443"
	}

	conn, err := tls.DialWithDialer(&wsDialer, "tcp", host, &tls.Config{ServerName: u.Hostname()})
	if err != nil {
		return nil, fmt.Errorf("tls dial: %w", err)
	}

	nonce := make([]byte, 16)
	_, _ = rand.Read(nonce)
	key := base64.StdEncoding.EncodeToString(nonce)

	path := u.RequestURI()
	var req strings.Builder
	fmt.Fprintf(&req, "GET %s HTTP/1.1\r\n", path)
	fmt.Fprintf(&req, "Host: %s\r\n", u.Host)
	req.WriteString("Upgrade: websocket\r\n")
	req.WriteString("Connection: Upgrade\r\n")
	fmt.Fprintf(&req, "Sec-WebSocket-Key: %s\r\n", key)
	req.WriteString("Sec-WebSocket-Version: 13\r\n")
	if subProtocol != "" {
		fmt.Fprintf(&req, "Sec-WebSocket-Protocol: %s\r\n", subProtocol)
	}
	req.WriteString("\r\n")

	if _, err := conn.Write([]byte(req.String())); err != nil {
		conn.Close()
		return nil, fmt.Errorf("write handshake: %w", err)
	}

	br := bufio.NewReader(conn)
	httpReq, _ := http.NewRequest("GET", rawURL, nil)
	resp, err := http.ReadResponse(br, httpReq)
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("read handshake response: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusSwitchingProtocols {
		conn.Close()
		return nil, fmt.Errorf("handshake failed: HTTP %d", resp.StatusCode)
	}
	expected := computeAcceptKey(key)
	if got := resp.Header.Get("Sec-WebSocket-Accept"); got != expected {
		conn.Close()
		return nil, fmt.Errorf("bad Sec-WebSocket-Accept: got %q want %q", got, expected)
	}

	return &WsConn{conn: conn, br: br}, nil
}

func computeAcceptKey(key string) string {
	h := sha1.New()
	h.Write([]byte(key + wsGUID))
	return base64.StdEncoding.EncodeToString(h.Sum(nil))
}

// writeFrame sends a single masked client frame.
func (c *WsConn) writeFrame(opcode byte, payload []byte) error {
	var header []byte
	header = append(header, 0x80|opcode) // FIN + opcode

	n := len(payload)
	switch {
	case n < 126:
		header = append(header, 0x80|byte(n))
	case n < 65536:
		header = append(header, 0x80|126)
		var ext [2]byte
		binary.BigEndian.PutUint16(ext[:], uint16(n))
		header = append(header, ext[:]...)
	default:
		header = append(header, 0x80|127)
		var ext [8]byte
		binary.BigEndian.PutUint64(ext[:], uint64(n))
		header = append(header, ext[:]...)
	}

	var mask [4]byte
	_, _ = rand.Read(mask[:])
	header = append(header, mask[:]...)

	masked := make([]byte, n)
	for i := 0; i < n; i++ {
		masked[i] = payload[i] ^ mask[i%4]
	}

	if _, err := c.conn.Write(header); err != nil {
		return err
	}
	_, err := c.conn.Write(masked)
	return err
}

// WriteBinary sends a binary message.
func (c *WsConn) WriteBinary(b []byte) error { return c.writeFrame(OpBinary, b) }

// readFrame reads one frame (server frames are unmasked).
func (c *WsConn) readFrame() (fin bool, opcode byte, payload []byte, err error) {
	var h [2]byte
	if _, err = io.ReadFull(c.br, h[:]); err != nil {
		return
	}
	fin = h[0]&0x80 != 0
	opcode = h[0] & 0x0f
	masked := h[1]&0x80 != 0
	length := uint64(h[1] & 0x7f)

	switch length {
	case 126:
		var ext [2]byte
		if _, err = io.ReadFull(c.br, ext[:]); err != nil {
			return
		}
		length = uint64(binary.BigEndian.Uint16(ext[:]))
	case 127:
		var ext [8]byte
		if _, err = io.ReadFull(c.br, ext[:]); err != nil {
			return
		}
		length = binary.BigEndian.Uint64(ext[:])
	}

	var mask [4]byte
	if masked {
		if _, err = io.ReadFull(c.br, mask[:]); err != nil {
			return
		}
	}

	payload = make([]byte, length)
	if _, err = io.ReadFull(c.br, payload); err != nil {
		return
	}
	if masked {
		for i := range payload {
			payload[i] ^= mask[i%4]
		}
	}
	return
}

// ReadMessage returns the next complete data message (text or binary),
// transparently answering pings and reassembling fragments.
func (c *WsConn) ReadMessage() (opcode byte, data []byte, err error) {
	var msgOpcode byte
	var buf []byte
	for {
		fin, op, payload, e := c.readFrame()
		if e != nil {
			return 0, nil, e
		}
		switch op {
		case OpPing:
			_ = c.writeFrame(OpPong, payload)
			continue
		case OpPong:
			continue
		case OpClose:
			_ = c.writeFrame(OpClose, payload)
			return 0, nil, io.EOF
		case OpContinuation:
			buf = append(buf, payload...)
		default: // OpText / OpBinary
			msgOpcode = op
			buf = payload
		}
		if fin {
			return msgOpcode, buf, nil
		}
	}
}

// Close closes the underlying connection.
func (c *WsConn) Close() error { return c.conn.Close() }

// SetReadDeadline sets a read deadline on the underlying connection.
func (c *WsConn) SetReadDeadline(t time.Time) {
	if cn, ok := c.conn.(interface{ SetReadDeadline(time.Time) error }); ok {
		_ = cn.SetReadDeadline(t)
	}
}
