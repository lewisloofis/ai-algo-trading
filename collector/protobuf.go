package main

// Minimal Protocol Buffers (proto3) reader/writer — just enough to encode the
// outgoing WebsocketRequest and decode the incoming WebsocketWrapMessageChannel.
//
// Field numbers and wire types are taken directly from the sb-protos contracts:
//   - securities/transactional/datafeed/v1/websocket.proto
//   - securities/transactional/datafeed/consumer/entity/v1/entity.proto
//
// We hand-roll this so the prototype has ZERO external dependencies and builds
// offline on any Go >= 1.19. On Go >= 1.23 you can instead import the generated
// stubs from ../sb-protos/gen/go and delete this file (see README).

import (
	"encoding/binary"
	"errors"
	"math"
)

// Wire types (proto3).
const (
	wireVarint  = 0
	wireFixed64 = 1
	wireBytes   = 2
	wireFixed32 = 5
)

// ---------- Writer ----------

type pbWriter struct{ buf []byte }

func (w *pbWriter) tag(field, wire int) {
	w.varint(uint64(field)<<3 | uint64(wire))
}

func (w *pbWriter) varint(v uint64) {
	for v >= 0x80 {
		w.buf = append(w.buf, byte(v)|0x80)
		v >>= 7
	}
	w.buf = append(w.buf, byte(v))
}

func (w *pbWriter) string(field int, s string) {
	if s == "" {
		return
	}
	w.tag(field, wireBytes)
	w.varint(uint64(len(s)))
	w.buf = append(w.buf, s...)
}

func (w *pbWriter) message(field int, sub []byte) {
	if len(sub) == 0 {
		return
	}
	w.tag(field, wireBytes)
	w.varint(uint64(len(sub)))
	w.buf = append(w.buf, sub...)
}

// ---------- Reader ----------

type pbReader struct {
	buf []byte
	pos int
}

var errTruncated = errors.New("protobuf: truncated message")

func (r *pbReader) eof() bool { return r.pos >= len(r.buf) }

func (r *pbReader) readVarint() (uint64, error) {
	var v uint64
	var shift uint
	for {
		if r.pos >= len(r.buf) {
			return 0, errTruncated
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

// readTag returns the field number and wire type of the next field.
func (r *pbReader) readTag() (field int, wire int, err error) {
	key, err := r.readVarint()
	if err != nil {
		return 0, 0, err
	}
	return int(key >> 3), int(key & 7), nil
}

func (r *pbReader) readBytes() ([]byte, error) {
	n, err := r.readVarint()
	if err != nil {
		return nil, err
	}
	if r.pos+int(n) > len(r.buf) {
		return nil, errTruncated
	}
	b := r.buf[r.pos : r.pos+int(n)]
	r.pos += int(n)
	return b, nil
}

func (r *pbReader) readFixed64() (uint64, error) {
	if r.pos+8 > len(r.buf) {
		return 0, errTruncated
	}
	v := binary.LittleEndian.Uint64(r.buf[r.pos : r.pos+8])
	r.pos += 8
	return v, nil
}

func (r *pbReader) readDouble() (float64, error) {
	bits, err := r.readFixed64()
	if err != nil {
		return 0, err
	}
	return math.Float64frombits(bits), nil
}

// skip advances past a field whose value we don't care about.
func (r *pbReader) skip(wire int) error {
	switch wire {
	case wireVarint:
		_, err := r.readVarint()
		return err
	case wireFixed64:
		r.pos += 8
	case wireBytes:
		n, err := r.readVarint()
		if err != nil {
			return err
		}
		r.pos += int(n)
	case wireFixed32:
		r.pos += 4
	default:
		return errors.New("protobuf: unknown wire type")
	}
	if r.pos > len(r.buf) {
		return errTruncated
	}
	return nil
}
