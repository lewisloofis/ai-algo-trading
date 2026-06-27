// Package sbproto is a minimal Protocol Buffers (proto3) reader/writer — just
// enough to encode outgoing WebsocketRequest frames and decode incoming
// WebsocketWrapMessageChannel frames for the Stockbit market-data feed.
//
// Field numbers and wire types are taken from the sb-protos contracts:
//
//	securities/transactional/datafeed/v1/websocket.proto
//	securities/transactional/datafeed/consumer/entity/v1/entity.proto
//
// The package has zero external dependencies and is compatible with Go 1.19.
package sbproto

import (
	"encoding/binary"
	"errors"
	"math"
)

// Wire type constants (proto3).
const (
	WireVarint  = 0
	WireFixed64 = 1
	WireBytes   = 2
	WireFixed32 = 5
)

// ---------- Writer ----------

// Writer encodes proto3 fields into an in-memory buffer. The zero value is
// ready to use.
type Writer struct{ buf []byte }

// Tag writes a field tag (field number + wire type).
func (w *Writer) Tag(field, wire int) {
	w.Varint(uint64(field)<<3 | uint64(wire))
}

// Varint writes a varint-encoded uint64.
func (w *Writer) Varint(v uint64) {
	for v >= 0x80 {
		w.buf = append(w.buf, byte(v)|0x80)
		v >>= 7
	}
	w.buf = append(w.buf, byte(v))
}

// String writes a length-delimited string field. Empty strings are skipped.
func (w *Writer) String(field int, s string) {
	if s == "" {
		return
	}
	w.Tag(field, WireBytes)
	w.Varint(uint64(len(s)))
	w.buf = append(w.buf, s...)
}

// Message writes a length-delimited nested message field. Empty sub-messages
// are skipped.
func (w *Writer) Message(field int, sub []byte) {
	if len(sub) == 0 {
		return
	}
	w.Tag(field, WireBytes)
	w.Varint(uint64(len(sub)))
	w.buf = append(w.buf, sub...)
}

// Bytes returns the encoded bytes accumulated so far. The returned slice is
// the Writer's internal buffer; callers should copy it if they need to retain
// it after further writes.
func (w *Writer) Bytes() []byte { return w.buf }

// ---------- Reader ----------

// ErrTruncated is returned when the buffer ends in the middle of a field.
var ErrTruncated = errors.New("protobuf: truncated message")

// Reader decodes proto3 fields from a byte slice.
type Reader struct {
	buf []byte
	pos int
}

// NewReader creates a Reader over data.
func NewReader(data []byte) *Reader { return &Reader{buf: data} }

// EOF reports whether all bytes have been consumed.
func (r *Reader) EOF() bool { return r.pos >= len(r.buf) }

// ReadVarint reads a varint-encoded uint64.
func (r *Reader) ReadVarint() (uint64, error) {
	var v uint64
	var shift uint
	for {
		if r.pos >= len(r.buf) {
			return 0, ErrTruncated
		}
		b := r.buf[r.pos]
		r.pos++
		v |= uint64(b&0x7f) << shift
		if b < 0x80 {
			return v, nil
		}
		shift += 7
		if shift >= 64 {
			return 0, errors.New("protobuf: varint overflow")
		}
	}
}

// ReadTag reads the next field number and wire type.
func (r *Reader) ReadTag() (field, wire int, err error) {
	key, err := r.ReadVarint()
	if err != nil {
		return 0, 0, err
	}
	return int(key >> 3), int(key & 7), nil
}

// ReadBytes reads a length-delimited byte slice.
func (r *Reader) ReadBytes() ([]byte, error) {
	n, err := r.ReadVarint()
	if err != nil {
		return nil, err
	}
	if r.pos+int(n) > len(r.buf) {
		return nil, ErrTruncated
	}
	b := r.buf[r.pos : r.pos+int(n)]
	r.pos += int(n)
	return b, nil
}

// ReadFixed64 reads an 8-byte little-endian uint64.
func (r *Reader) ReadFixed64() (uint64, error) {
	if r.pos+8 > len(r.buf) {
		return 0, ErrTruncated
	}
	v := binary.LittleEndian.Uint64(r.buf[r.pos : r.pos+8])
	r.pos += 8
	return v, nil
}

// ReadDouble reads an IEEE 754 double stored as a fixed64.
func (r *Reader) ReadDouble() (float64, error) {
	bits, err := r.ReadFixed64()
	if err != nil {
		return 0, err
	}
	return math.Float64frombits(bits), nil
}

// Skip advances past a field whose value we do not need.
func (r *Reader) Skip(wire int) error {
	switch wire {
	case WireVarint:
		_, err := r.ReadVarint()
		return err
	case WireFixed64:
		r.pos += 8
	case WireBytes:
		n, err := r.ReadVarint()
		if err != nil {
			return err
		}
		r.pos += int(n)
	case WireFixed32:
		r.pos += 4
	default:
		return errors.New("protobuf: unknown wire type")
	}
	if r.pos > len(r.buf) {
		return ErrTruncated
	}
	return nil
}
