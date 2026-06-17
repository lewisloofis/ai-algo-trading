package main

// Minimal RFC 6455 WebSocket client (client side only), pure stdlib.
// Supports the wss:// handshake, masked client frames, server frame reassembly,
// and automatic pong replies. Enough to talk to the Stockbit market-data feed.

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

var wsDialer = net.Dialer{Timeout: 15 * time.Second}

const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

// WebSocket opcodes.
const (
	opContinuation = 0x0
	opText         = 0x1
	opBinary       = 0x2
	opClose        = 0x8
	opPing         = 0x9
	opPong         = 0xA
)

type wsConn struct {
	conn io.ReadWriteCloser
	br   *bufio.Reader
}

// dialWS opens a TLS WebSocket connection to rawURL (wss://host/path),
// requesting the given sub-protocol (e.g. "web" or "ios").
func dialWS(rawURL, subProtocol string) (*wsConn, error) {
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

	return &wsConn{conn: conn, br: br}, nil
}

func computeAcceptKey(key string) string {
	h := sha1.New()
	h.Write([]byte(key + wsGUID))
	return base64.StdEncoding.EncodeToString(h.Sum(nil))
}

// writeFrame sends a single masked client frame.
func (c *wsConn) writeFrame(opcode byte, payload []byte) error {
	var header []byte
	header = append(header, 0x80|opcode) // FIN + opcode

	n := len(payload)
	switch {
	case n < 126:
		header = append(header, 0x80|byte(n)) // mask bit + len
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
func (c *wsConn) WriteBinary(b []byte) error { return c.writeFrame(opBinary, b) }

// readFrame reads one frame (server frames are unmasked).
func (c *wsConn) readFrame() (fin bool, opcode byte, payload []byte, err error) {
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
func (c *wsConn) ReadMessage() (opcode byte, data []byte, err error) {
	var msgOpcode byte
	var buf []byte
	for {
		fin, op, payload, e := c.readFrame()
		if e != nil {
			return 0, nil, e
		}
		switch op {
		case opPing:
			_ = c.writeFrame(opPong, payload)
			continue
		case opPong:
			continue
		case opClose:
			_ = c.writeFrame(opClose, payload)
			return 0, nil, io.EOF
		case opContinuation:
			buf = append(buf, payload...)
		default: // opText / opBinary
			msgOpcode = op
			buf = payload
		}
		if fin {
			return msgOpcode, buf, nil
		}
	}
}

func (c *wsConn) Close() error { return c.conn.Close() }

// SetReadDeadline lets the caller bound how long a read blocks.
func (c *wsConn) SetReadDeadline(t time.Time) {
	if cn, ok := c.conn.(interface{ SetReadDeadline(time.Time) error }); ok {
		_ = cn.SetReadDeadline(t)
	}
}
