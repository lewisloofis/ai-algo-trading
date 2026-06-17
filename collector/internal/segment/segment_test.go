package segment_test

import (
	"bytes"
	"encoding/binary"
	"io"
	"testing"

	"sb-algo-prototype/internal/segment"
)

// TestRoundTrip writes a mix of data and control records and reads them back,
// verifying byte-for-byte equality on each payload.
func TestRoundTrip(t *testing.T) {
	t.Parallel()

	type wantRecord struct {
		recType     byte
		timestampNs int64
		payload     []byte
	}

	wants := []wantRecord{
		{segment.RecordData, 1_700_000_000_000_000_000, []byte{0x01, 0x02, 0x03}},
		{segment.RecordControl, 1_700_000_001_000_000_000, []byte(`{"event":"CONNECT","ws_url":"wss://example.com"}`)},
		{segment.RecordData, 1_700_000_002_000_000_000, []byte{0xDE, 0xAD, 0xBE, 0xEF}},
		{segment.RecordControl, 1_700_000_003_000_000_000, []byte(`{"event":"SUBSCRIBE","watchlist":["BBCA","BMRI"]}`)},
		{segment.RecordData, 1_700_000_004_000_000_000, make([]byte, 1024)}, // larger frame
	}
	// Fill the large frame with a recognisable pattern.
	for i := range wants[4].payload {
		wants[4].payload[i] = byte(i % 256)
	}

	var buf bytes.Buffer
	w := segment.NewWriter(&buf)

	for _, wr := range wants {
		var err error
		switch wr.recType {
		case segment.RecordData:
			err = w.WriteData(wr.timestampNs, wr.payload)
		case segment.RecordControl:
			err = w.WriteControl(wr.timestampNs, wr.payload)
		}
		if err != nil {
			t.Fatalf("write record type=%02x: %v", wr.recType, err)
		}
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}

	r := segment.NewReader(&buf)
	for i, want := range wants {
		got, err := r.Read()
		if err != nil {
			t.Fatalf("record %d: Read returned %v", i, err)
		}
		if got.Type != want.recType {
			t.Errorf("record %d: type got 0x%02x want 0x%02x", i, got.Type, want.recType)
		}
		if got.TimestampNs != want.timestampNs {
			t.Errorf("record %d: TimestampNs got %d want %d", i, got.TimestampNs, want.timestampNs)
		}
		if !bytes.Equal(got.Payload, want.payload) {
			t.Errorf("record %d: payload mismatch (len got=%d want=%d)", i, len(got.Payload), len(want.payload))
		}
	}

	// Expect clean EOF after all records.
	_, err := r.Read()
	if err != io.EOF {
		t.Errorf("expected io.EOF after last record, got %v", err)
	}
}

// TestTruncatedFinalRecord verifies that a segment with an incomplete
// trailing record yields all prior complete records and then io.EOF with no
// panic, matching the truncation-tolerance contract in §2.3.
func TestTruncatedFinalRecord(t *testing.T) {
	t.Parallel()

	var buf bytes.Buffer
	w := segment.NewWriter(&buf)

	// Write one complete record.
	if err := w.WriteData(100, []byte{0xAA, 0xBB}); err != nil {
		t.Fatal(err)
	}
	if err := w.Flush(); err != nil {
		t.Fatal(err)
	}

	// Manually append a truncated second record: type + partial timestamp
	// (only 3 bytes of the 8-byte timestamp).
	buf.WriteByte(segment.RecordData)
	buf.Write([]byte{0x00, 0x11, 0x22}) // 3 of 8 timestamp bytes

	r := segment.NewReader(&buf)

	// First record must decode cleanly.
	rec, err := r.Read()
	if err != nil {
		t.Fatalf("first record: %v", err)
	}
	if rec.Type != segment.RecordData {
		t.Errorf("first record type: got 0x%02x want 0x%02x", rec.Type, segment.RecordData)
	}
	if !bytes.Equal(rec.Payload, []byte{0xAA, 0xBB}) {
		t.Errorf("first record payload mismatch")
	}

	// Second (truncated) record must yield io.EOF, not a hard error or panic.
	_, err = r.Read()
	if err != io.EOF {
		t.Errorf("truncated record: expected io.EOF, got %v", err)
	}
}

// TestBigEndianEncoding verifies that the timestamp and payload-length fields
// are written in big-endian byte order by inspecting the raw bytes directly.
func TestBigEndianEncoding(t *testing.T) {
	t.Parallel()

	const ts int64 = 0x0102030405060708
	payload := []byte{0xDE, 0xAD}

	var buf bytes.Buffer
	w := segment.NewWriter(&buf)
	if err := w.WriteData(ts, payload); err != nil {
		t.Fatal(err)
	}
	if err := w.Flush(); err != nil {
		t.Fatal(err)
	}

	raw := buf.Bytes()
	// Layout: [1B type][8B timestamp][4B payload_len][N payload]
	if len(raw) < 1+8+4+len(payload) {
		t.Fatalf("raw bytes too short: %d", len(raw))
	}

	// type byte
	if raw[0] != segment.RecordData {
		t.Errorf("type byte: got 0x%02x want 0x%02x", raw[0], segment.RecordData)
	}

	// timestamp must be big-endian
	gotTS := int64(binary.BigEndian.Uint64(raw[1:9]))
	if gotTS != ts {
		t.Errorf("timestamp: got 0x%016x want 0x%016x", gotTS, ts)
	}

	// payload_len must be big-endian
	gotLen := binary.BigEndian.Uint32(raw[9:13])
	if int(gotLen) != len(payload) {
		t.Errorf("payload_len: got %d want %d", gotLen, len(payload))
	}

	// payload bytes
	if !bytes.Equal(raw[13:13+len(payload)], payload) {
		t.Errorf("payload mismatch")
	}
}

// TestControlEventHelpers exercises every ControlEvent helper function,
// verifying that the JSON contains the expected "event" field value.
func TestControlEventHelpers(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		build   func() ([]byte, error)
		wantStr string
	}{
		{"CONNECT", func() ([]byte, error) { return segment.ConnectEvent("wss://x.com") }, `"CONNECT"`},
		{"SUBSCRIBE", func() ([]byte, error) { return segment.SubscribeEvent([]string{"BBCA"}) }, `"SUBSCRIBE"`},
		{"REAUTH proactive", func() ([]byte, error) { return segment.ReauthEvent("proactive") }, `"REAUTH"`},
		{"REAUTH 401", func() ([]byte, error) { return segment.ReauthEvent("401") }, `"REAUTH"`},
		{"RECONNECT", func() ([]byte, error) { return segment.ReconnectEvent(1, 750) }, `"RECONNECT"`},
		{"SWITCH_RESET", func() ([]byte, error) { return segment.SwitchResetEvent("BBCA") }, `"SWITCH_RESET"`},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			b, err := tc.build()
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			if !bytes.Contains(b, []byte(tc.wantStr)) {
				t.Errorf("event JSON %q does not contain %s", b, tc.wantStr)
			}
		})
	}
}
