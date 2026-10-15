package livesession

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// writeScript writes a tiny /bin/sh script (test-local, benign: it only echoes
// its arguments, exits, or waits for SIGINT) into dir.
func writeScript(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "child.sh")
	if err := os.WriteFile(p, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestExecRunner_ExitCodesAndArgs(t *testing.T) {
	var out bytes.Buffer
	r := ExecRunner{Bin: writeScript(t, `echo "args: $@"; exit 3`), Stdout: &out, Stderr: &out}
	code, err := r.Run(context.Background(), []string{"--store", "x.db"})
	if err != nil || code != 3 {
		t.Fatalf("code %d err %v, want 3 nil", code, err)
	}
	if !strings.Contains(out.String(), "args: --store x.db") {
		t.Errorf("out = %q", out.String())
	}
	code, err = ExecRunner{Bin: writeScript(t, `exit 0`)}.Run(context.Background(), nil)
	if err != nil || code != 0 {
		t.Fatalf("clean child: %d %v", code, err)
	}
}

func TestExecRunner_CancelSendsOneSIGINTAndChildExitsCleanly(t *testing.T) {
	ready := filepath.Join(t.TempDir(), "ready")
	r := ExecRunner{Bin: writeScript(t, `trap 'exit 0' INT; touch "`+ready+`"; while true; do sleep 0.05; done`), Grace: 5 * time.Second}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		for i := 0; i < 400; i++ { // wait (up to 20s) until the child has installed its trap
			if _, err := os.Stat(ready); err == nil {
				break
			}
			time.Sleep(50 * time.Millisecond)
		}
		cancel()
	}()
	code, err := r.Run(ctx, nil)
	if err != nil || code != 0 {
		t.Fatalf("code %d err %v, want a graceful 0 after SIGINT", code, err)
	}
}

func TestExecRunner_MissingBinaryIsAnError(t *testing.T) {
	code, err := ExecRunner{Bin: filepath.Join(t.TempDir(), "nope")}.Run(context.Background(), nil)
	if err == nil || code == 0 {
		t.Fatalf("code %d err %v", code, err)
	}
}
