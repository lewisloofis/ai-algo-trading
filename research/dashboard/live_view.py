"""
dashboard/live_view.py — the Overview's "Online now" panel, and the small script that keeps it fresh.

The panel is rendered statically at build time from live.py, so a plain ``make dashboard`` still
shows it (with an "as of" time). When the page is served by ``make dashboard-live`` the inline
script polls ``live.json`` every few seconds and redraws the table rows in place. Over ``file://``
or when a fetch fails the static version is left alone and the note says how to get live updates.

The script builds every cell with ``textContent``, never ``innerHTML``, so a value from a status
file cannot inject markup. It uses no external resources.
"""

from __future__ import annotations

import json

from dashboard.live import LiveSummary
from dashboard.render import esc, pill

POLL_MS = 5000

STATIC_NOTE = "static snapshot — run make dashboard-live for live updates"

COLUMNS: tuple[tuple[str, str], ...] = (
    ("Strategy", "strategy_id"),
    ("Mode", "mode"),
    ("Data source", "source"),
    ("State", "state_label"),
    ("Last heartbeat", "heartbeat_text"),
    ("Written", "written_wib"),
    ("Events seen", "events_seen"),
    ("Positions held", "positions_text"),
    ("Notional today", "notional_text"),
    ("Kill switch", "kill_text"),
    ("Error", "error"),
)
"""(header, key in live.json's runs[]) — the one list both the static table and the script use."""

_TONES = ("green", "amber", "red", "grey")

_SCRIPT = """
(function () {
  var COLS = %(cols)s, TONES = %(tones)s, STATIC_NOTE = %(static_note)s, POLL_MS = %(poll)d;
  var body = document.getElementById("live-body");
  if (!body) { return; }
  var table = document.getElementById("live-table"), empty = document.getElementById("live-empty");
  var note = document.getElementById("live-note"), count = document.getElementById("live-count");
  var rebuilt = document.getElementById("last-rebuilt"), notes = document.getElementById("live-notes");
  function text(v) { return v === null || v === undefined || v === "" ? "\\u2013" : String(v); }
  function cell(run, key) {
    var td = document.createElement("td");
    if (key === "state_label") {
      var s = document.createElement("span");
      s.className = "pill " + (TONES.indexOf(run.tone) >= 0 ? run.tone : "grey");
      s.textContent = text(run.state_label);
      td.appendChild(s);
    } else {
      td.textContent = text(run[key]);
    }
    return td;
  }
  function draw(j) {
    var runs = (j.runs || []).filter(function (r) { return r.state === "online"; });
    body.textContent = "";
    runs.forEach(function (r) {
      var tr = document.createElement("tr");
      COLS.forEach(function (c) { tr.appendChild(cell(r, c[1])); });
      body.appendChild(tr);
    });
    table.hidden = runs.length === 0;
    empty.hidden = runs.length !== 0;
    count.textContent = "(" + runs.length + ")";
    note.textContent = "Live: as of " + text(j.as_of_wib) + ", refreshed every " + (POLL_MS / 1000) + "s.";
    if (j.site_built_wib) { rebuilt.textContent = j.site_built_wib; }
    notes.textContent = (j.notes || []).join(" ");
  }
  function tick() {
    fetch("live.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error("http " + r.status); } return r.json(); })
      .then(draw)
      .catch(function () { note.textContent = STATIC_NOTE; });
  }
  if (location.protocol !== "http:" && location.protocol !== "https:") { return; }
  tick();
  setInterval(tick, POLL_MS);
})();
"""


def script() -> str:
    return "<script>" + _SCRIPT % {
        "cols": json.dumps([[h, k] for h, k in COLUMNS]),
        "tones": json.dumps(list(_TONES)),
        "static_note": json.dumps(STATIC_NOTE),
        "poll": POLL_MS,
    } + "</script>"


def _row(run) -> str:
    cells = []
    for _header, key in COLUMNS:
        if key == "state_label":
            tone = run.tone if run.tone in _TONES else "grey"
            cells.append(f"<td>{pill(tone, run.state_label)}</td>")
            continue
        value = getattr(run, key)
        cells.append(f"<td>{esc('–' if value in (None, '') else value)}</td>")
    cls = "" if run.state == "online" else ' class="dim"'
    return f"<tr{cls}>{''.join(cells)}</tr>"


def panel(summary: LiveSummary, built_wib: str) -> str:
    """The panel's HTML. ``built_wib`` is the build time as text, e.g. '2026-10-15 10:00 WIB'."""
    head = "".join(f"<th>{esc(h)}</th>" for h, _k in COLUMNS)
    online = [r for r in summary.runs if r.state == "online"]
    rows = "".join(_row(r) for r in online)
    has = bool(online)
    return (
        '<section id="live-panel">'
        f'<h2>Online now <span id="live-count">({summary.online_count})</span></h2>'
        '<p class="small">Only sessions running right now are listed (heartbeat rewritten within the last 2 minutes). '
        'Earlier runs are under &ldquo;Recent runs&rdquo; on the <a href="strategies.html">Strategies</a> page.</p>'
        f'<table id="live-table"{"" if has else " hidden"}><thead><tr>{head}</tr></thead>'
        f'<tbody id="live-body">{rows}</tbody></table>'
        f'<p class="empty" id="live-empty"{" hidden" if has else ""}>No strategy is running right now.</p>'
        f'<p class="small" id="live-note">As of {esc(summary.as_of_wib)} · {esc(STATIC_NOTE)}</p>'
        f'<p class="small" id="live-notes">{esc(" ".join(summary.notes))}</p>'
        f'<p class="small">Last rebuilt <span id="last-rebuilt">{esc(built_wib)}</span></p>'
        f"{script()}</section>"
    )
