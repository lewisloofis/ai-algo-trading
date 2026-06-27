// Package segment implements the binary record format for market-data capture
// segments, as specified in docs/plans/02-storage-format-and-replay.md §2.
//
// A segment file is a flat sequence of records. Two record types share the
// same 13-byte framing header:
//
//	[1B type][8B timestamp_ns big-endian uint64][4B payload_len big-endian uint32][N payload bytes]
//
// Record types:
//
//	RecordData    (0x01) — one raw WebSocket frame with its arrival timestamp.
//	RecordControl (0x02) — a UTF-8 JSON lifecycle event.
//
// Reading is tolerant of crash-truncated final records: a short read on any
// field after the type byte returns io.EOF rather than an error, so strategies
// can replay right up to the last complete record in a crashed segment.
package segment

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
)

// Record type constants.
const (
	RecordData    byte = 0x01
	RecordControl byte = 0x02
)

// Record is the value returned by Reader.Read.
type Record struct {
	Type        byte
	TimestampNs int64
	Payload     []byte
}

// ---------- Control event types ----------

// ControlEvent is the top-level JSON object written into a control record.
// Only the fields relevant to each event kind are populated.
type ControlEvent struct {
	Event     string   `json:"event"`
	WSURL     string   `json:"ws_url,omitempty"`
	Watchlist []string `json:"watchlist,omitempty"`
	Reason    string   `json:"reason,omitempty"`
	Attempt   int      `json:"attempt,omitempty"`
	BackoffMs int      `json:"backoff_ms,omitempty"`
	Symbol    string   `json:"symbol,omitempty"`
}

// ConnectEvent returns a CONNECT control event JSON payload.
func ConnectEvent(wsURL string) ([]byte, error) {
	return json.Marshal(ControlEvent{Event: "CONNECT", WSURL: wsURL})
}

// SubscribeEvent returns a SUBSCRIBE control event JSON payload.
func SubscribeEvent(watchlist []string) ([]byte, error) {
	return json.Marshal(ControlEvent{Event: "SUBSCRIBE", Watchlist: watchlist})
}

// ReauthEvent returns a REAUTH control event JSON payload.
// reason should be "proactive" or "401".
func ReauthEvent(reason string) ([]byte, error) {
	return json.Marshal(ControlEvent{Event: "REAUTH", Reason: reason})
}

// ReconnectEvent returns a RECONNECT control event JSON payload.
func ReconnectEvent(attempt, backoffMs int) ([]byte, error) {
	return json.Marshal(ControlEvent{Event: "RECONNECT", Attempt: attempt, BackoffMs: backoffMs})
}

// SwitchResetEvent returns a SWITCH_RESET control event JSON payload.
func SwitchResetEvent(symbol string) ([]byte, error) {
	return json.Marshal(ControlEvent{Event: "SWITCH_RESET", Symbol: symbol})
}

// ---------- Writer ----------

// Writer appends data and control records to an underlying io.Writer.
// Writes are buffered; call Flush before closing or rolling the segment.
type Writer struct {
	bw *bufio.Writer
}

// NewWriter wraps w with an 8 KiB write buffer.
func NewWriter(w io.Writer) *Writer {
	return &Writer{bw: bufio.NewWriterSize(w, 8<<10)}
}

// WriteData appends a data record: the raw WebSocket frame bytes with the
// given arrival timestamp in Unix nanoseconds.
func (w *Writer) WriteData(recvNs int64, frame []byte) error {
	return w.writeRecord(RecordData, recvNs, frame)
}

// WriteControl appends a control record with the given event timestamp in
// Unix nanoseconds and a UTF-8 JSON payload (e.g. from ConnectEvent).
func (w *Writer) WriteControl(eventNs int64, jsonPayload []byte) error {
	return w.writeRecord(RecordControl, eventNs, jsonPayload)
}

// Flush flushes the write buffer to the underlying writer.
func (w *Writer) Flush() error {
	return w.bw.Flush()
}

func (w *Writer) writeRecord(recType byte, timestampNs int64, payload []byte) error {
	// 1-byte type
	if err := w.bw.WriteByte(recType); err != nil {
		return fmt.Errorf("segment write type: %w", err)
	}
	// 8-byte timestamp (big-endian uint64, reinterpreted from int64)
	var ts [8]byte
	binary.BigEndian.PutUint64(ts[:], uint64(timestampNs))
	if _, err := w.bw.Write(ts[:]); err != nil {
		return fmt.Errorf("segment write timestamp: %w", err)
	}
	// 4-byte payload length (big-endian uint32)
	var pl [4]byte
	binary.BigEndian.PutUint32(pl[:], uint32(len(payload)))
	if _, err := w.bw.Write(pl[:]); err != nil {
		return fmt.Errorf("segment write payload_len: %w", err)
	}
	// payload bytes
	if _, err := w.bw.Write(payload); err != nil {
		return fmt.Errorf("segment write payload: %w", err)
	}
	return nil
}

// ---------- Reader ----------

// Reader reads records sequentially from a segment file.  It tolerates a
// crash-truncated final record by treating any short read that occurs after
// the type byte as end-of-segment (returning io.EOF).
type Reader struct {
	r io.Reader
}

// NewReader wraps r.  r must already be positioned at the start of the
// segment (or at the start of any valid record boundary).
func NewReader(r io.Reader) *Reader { return &Reader{r: r} }

// Read returns the next record.  It returns (zero, io.EOF) at a clean end of
// file or at a truncated final record.  Any other error indicates a real
// read failure.
func (r *Reader) Read() (Record, error) {
	// Read record type — clean EOF here is normal end-of-segment.
	var typeBuf [1]byte
	if _, err := io.ReadFull(r.r, typeBuf[:]); err != nil {
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			return Record{}, io.EOF
		}
		return Record{}, fmt.Errorf("segment read type: %w", err)
	}
	recType := typeBuf[0]

	// Timestamp — truncated tail → treat as EOF.
	var tsBuf [8]byte
	if _, err := io.ReadFull(r.r, tsBuf[:]); err != nil {
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			return Record{}, io.EOF
		}
		return Record{}, fmt.Errorf("segment read timestamp: %w", err)
	}
	timestampNs := int64(binary.BigEndian.Uint64(tsBuf[:]))

	// Payload length — truncated tail → treat as EOF.
	var plBuf [4]byte
	if _, err := io.ReadFull(r.r, plBuf[:]); err != nil {
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			return Record{}, io.EOF
		}
		return Record{}, fmt.Errorf("segment read payload_len: %w", err)
	}
	payloadLen := binary.BigEndian.Uint32(plBuf[:])

	// Payload — truncated tail → treat as EOF.
	payload := make([]byte, payloadLen)
	if _, err := io.ReadFull(r.r, payload); err != nil {
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			return Record{}, io.EOF
		}
		return Record{}, fmt.Errorf("segment read payload: %w", err)
	}

	return Record{Type: recType, TimestampNs: timestampNs, Payload: payload}, nil
}
