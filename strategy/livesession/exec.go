package livesession

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"syscall"
	"time"
)

// ChildRunner runs one strategy-run attempt. Cancelling ctx asks it to stop
// gracefully (strategy-run drains open orders on SIGINT and exits 0).
type ChildRunner interface {
	Run(ctx context.Context, args []string) (exitCode int, err error)
}

// ExecRunner runs the strategy-run binary as a child process. The child
// inherits the environment, which is how the tokens and the ALGO_LIVE /
// ALGO_CONFIRM arming reach it; neither is ever put on the command line.
type ExecRunner struct {
	Bin    string
	Stdout io.Writer
	Stderr io.Writer
	// Grace is how long the child gets after SIGINT before it is killed.
	// It must exceed strategy-run's own drain timeout (30s).
	Grace time.Duration
}

func (r ExecRunner) Run(ctx context.Context, args []string) (int, error) {
	cmd := exec.CommandContext(ctx, r.Bin, args...)
	cmd.Stdout, cmd.Stderr = r.Stdout, r.Stderr
	// Own process group: a terminal Ctrl-C reaches live-session only, which
	// forwards exactly one SIGINT. Two SIGINTs make strategy-run skip its
	// drain ("Ctrl-C again to force exit").
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return cmd.Process.Signal(os.Interrupt) }
	grace := r.Grace
	if grace <= 0 {
		grace = 60 * time.Second
	}
	cmd.WaitDelay = grace

	err := cmd.Run()
	if err == nil {
		return 0, nil
	}
	// After Cancel, a child that exits 0 still makes Run return ctx.Err(); the
	// process's own exit status is what counts (a graceful stop is not a crash).
	if cmd.ProcessState != nil && cmd.ProcessState.Success() && ctx.Err() != nil {
		return 0, nil
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), nil // -1 when killed by a signal: still non-zero
	}
	if ctx.Err() != nil && errors.Is(err, exec.ErrWaitDelay) {
		return -1, fmt.Errorf("child did not exit within %v of SIGINT and was killed", grace)
	}
	return -1, err
}
