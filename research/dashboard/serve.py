"""
dashboard/serve.py — ``python -m dashboard serve``: the static site, kept fresh, on localhost.

Three parts:

* a tiny HTTP server (stdlib only) bound to 127.0.0.1 and nothing else. It serves the built site
  and one extra route, ``GET /live.json``, computed fresh from the heartbeat files on every request.
* a rebuild thread that rebuilds the whole site every ``rebuild_every`` seconds, and sooner when
  the inputs change (a new or changed runs/*.db, a heartbeat file appearing, disappearing or
  changing its outcome, a daily price file). Never two builds at once.
* an atomic swap: each build goes into a fresh temp directory and the server switches to it only
  when it finished, so a request never sees a half-written site. A failed build is logged and the
  previous site keeps being served.

Heartbeat files are rewritten every few seconds by a running session, so their *mtime* is not a
rebuild trigger (it would rebuild forever). The Online panel does not need a rebuild: it reads
``/live.json`` directly.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from typing import Callable, Hashable

from dashboard import config
from dashboard.live import collect
from dashboard.site import SiteOptions, build_site

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
DEFAULT_PORT = 8765
DEFAULT_REBUILD_EVERY_S = 300.0
POLL_S = 10.0
MIN_GAP_S = 30.0
"""An early (change-triggered) rebuild never starts sooner than this after the previous one."""

Log = Callable[[str], None]


class BindRefused(ValueError):
    """The dashboard only ever listens on this machine."""


@dataclass(frozen=True)
class ServeOptions:
    site: SiteOptions
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    rebuild_every_s: float = DEFAULT_REBUILD_EVERY_S
    poll_s: float = POLL_S
    min_gap_s: float = MIN_GAP_S


def check_host(host: str) -> str:
    if host not in LOOPBACK_HOSTS:
        raise BindRefused(f"refusing to listen on {host!r}: the live dashboard binds to 127.0.0.1 only")
    return "127.0.0.1" if host == "localhost" else host


# ---------------------------------------------------------------------------
# The site currently being served
# ---------------------------------------------------------------------------

class SiteHolder:
    """The directory being served, swapped as a unit. Readers take ``snapshot()``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._root: str | None = None
        self._built_at: datetime | None = None
        self._retired: str | None = None

    def snapshot(self) -> tuple[str | None, datetime | None]:
        with self._lock:
            return self._root, self._built_at

    def swap(self, root: str, built_at: datetime) -> None:
        """Serve ``root`` from now on. The directory before it is kept one more generation (a request
        that resolved its path a moment ago can still finish) and the one before that is deleted."""
        with self._lock:
            doomed, self._retired = self._retired, self._root
            self._root, self._built_at = root, built_at
        if doomed:
            shutil.rmtree(doomed, ignore_errors=True)

    def discard_all(self) -> None:
        with self._lock:
            dirs = [d for d in (self._root, self._retired) if d]
            self._root = self._retired = None
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# What to watch, and when to rebuild
# ---------------------------------------------------------------------------

def fingerprint(runs_dir: str, daily_dir: str) -> Hashable:
    """Everything whose change should refresh the pages, as one comparable value."""
    stamps: list[tuple] = []
    for pattern in (os.path.join(runs_dir, "*.db"), os.path.join(daily_dir, "*.parquet")):
        for path in sorted(glob.glob(pattern)):
            try:
                stamps.append((path, os.stat(path).st_mtime_ns))
            except OSError:
                continue
    for path in sorted(glob.glob(os.path.join(runs_dir, "*status*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            stamps.append((path, str(doc.get("outcome")), str(doc.get("run_id"))))
        except (OSError, ValueError, AttributeError):
            stamps.append((path, "unreadable"))
    return tuple(stamps)


class Rebuilder:
    """Decides when to build, builds into a fresh directory, swaps on success. Driven by ``step``."""

    def __init__(
        self,
        holder: SiteHolder,
        build: Callable[[str], None],
        watch: Callable[[], Hashable],
        work_dir: str,
        rebuild_every_s: float,
        min_gap_s: float = MIN_GAP_S,
        clock: Callable[[], float] = time.monotonic,
        log: Log = lambda _m: None,
    ) -> None:
        self._holder, self._build, self._watch = holder, build, watch
        self._work_dir, self._every, self._min_gap = work_dir, rebuild_every_s, min_gap_s
        self._clock, self._log = clock, log
        self._build_lock = threading.Lock()
        self._built_fp: Hashable = None
        self._last_fp: Hashable = None
        self._last_build_t: float | None = None

    def build_now(self) -> bool:
        """One build + swap. Returns False (and logs) if it failed; the old site stays."""
        if not self._build_lock.acquire(blocking=False):
            return False  # never two at once
        try:
            fp = self._watch()
            tmp = tempfile.mkdtemp(prefix="site-", dir=self._work_dir)
            try:
                self._build(tmp)
            except BaseException as exc:  # noqa: BLE001 - a failed rebuild must never take the server down
                shutil.rmtree(tmp, ignore_errors=True)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self._log(f"rebuild failed, still serving the previous site: {type(exc).__name__}: {exc}")
                return False
            finally:
                self._built_fp, self._last_build_t = fp, self._clock()
            self._holder.swap(tmp, datetime.now(timezone.utc))
            self._log("site rebuilt")
            return True
        finally:
            self._build_lock.release()

    def step(self) -> bool:
        """Poll once; build if due. True if a build was attempted."""
        now = self._clock()
        fp = self._watch()
        settled = fp == self._last_fp
        self._last_fp = fp
        since = float("inf") if self._last_build_t is None else now - self._last_build_t
        periodic = since >= self._every
        early = fp != self._built_fp and settled and since >= self._min_gap
        if not (periodic or early):
            return False
        self.build_now()
        return True

    def run(self, stop: threading.Event, poll_s: float) -> None:
        while not stop.wait(poll_s):
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001
                self._log(f"rebuild check failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def resolve_path(root: str, url_path: str) -> str | None:
    """The file under ``root`` that ``url_path`` names, or None. Never leaves ``root``."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url_path).path)
    if "\x00" in path or "\\" in path:
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    real_root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(real_root, *parts)) if parts else real_root
    if os.path.commonpath([full, real_root]) != real_root:
        return None
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    return full if os.path.isfile(full) else None


def live_payload(runs_dir: str, holder: SiteHolder, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    doc = collect(runs_dir, now).to_json()
    _root, built = holder.snapshot()
    doc["site_built_at"] = built.isoformat() if built else None
    doc["site_built_wib"] = (
        (built + timedelta(hours=config.WIB_OFFSET_HOURS)).strftime("%Y-%m-%d %H:%M:%S WIB") if built else None
    )
    return doc


def make_handler(holder: SiteHolder, runs_dir: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "dashboard-live"

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - the 5s poll would drown the terminal
            return

        def _send(self, code: int, body: bytes, ctype: str, head_only: bool) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve(self, head_only: bool) -> None:
            route = urllib.parse.urlsplit(self.path).path
            if route == "/live.json":
                try:
                    body = json.dumps(live_payload(runs_dir, holder)).encode("utf-8")
                except Exception as exc:  # noqa: BLE001
                    err = json.dumps({"error": type(exc).__name__}).encode("utf-8")
                    return self._send(500, err, "application/json", head_only)
                return self._send(200, body, "application/json", head_only)
            root, _built = holder.snapshot()
            full = resolve_path(root, self.path) if root else None
            if full is None:
                return self._send(404, b"Not found", "text/plain; charset=utf-8", head_only)
            try:
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                return self._send(404, b"Not found", "text/plain; charset=utf-8", head_only)
            ctype = guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/json":
                ctype += "; charset=utf-8"
            self._send(200, data, ctype, head_only)

        def do_GET(self) -> None:  # noqa: N802
            self._serve(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(head_only=True)

    return Handler


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(host: str, port: int, holder: SiteHolder, runs_dir: str) -> DashboardServer:
    """Bind (loopback only; port 0 picks a free one) without starting to serve."""
    return DashboardServer((check_host(host), port), make_handler(holder, runs_dir))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(opt: ServeOptions, log: Log) -> int:
    check_host(opt.host)
    work_dir = tempfile.mkdtemp(prefix="dashboard-live-")
    holder = SiteHolder()

    def build(out_dir: str) -> None:
        build_site(replace(opt.site, out_dir=out_dir, now=None))

    def watch() -> Hashable:
        return fingerprint(opt.site.runs_dir, opt.site.daily_dir)

    rebuilder = Rebuilder(holder, build, watch, work_dir, opt.rebuild_every_s, opt.min_gap_s, log=log)
    stop = threading.Event()
    try:
        log("building the site before serving...")
        if not rebuilder.build_now():
            log("the first build failed, so there is nothing to serve")
            return 1
        server = create_server(opt.host, opt.port, holder, opt.site.runs_dir)
        port = server.server_address[1]
        thread = threading.Thread(target=rebuilder.run, args=(stop, opt.poll_s), name="rebuild", daemon=True)
        thread.start()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, signal.default_int_handler)  # stop on `kill` like Ctrl-C: clean up the temp site
        print(f"dashboard: live at http://127.0.0.1:{port}/  (Ctrl-C to stop)", flush=True)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            log("stopping")
        finally:
            stop.set()
            server.server_close()
            thread.join(timeout=5)
        return 0
    finally:
        stop.set()
        holder.discard_all()
        shutil.rmtree(work_dir, ignore_errors=True)
