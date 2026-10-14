"""
dashboard/svg.py — tiny inline-SVG charts. No JavaScript, no external fonts, no libraries.

Two charts are enough for this site:

* ``line_chart``  — several dated series on shared axes (cumulative profit vs holding)
* ``price_chart`` — one stock's daily closes with the bot's buys and sells marked
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

INK = "#1a1a1a"
MUTED = "#6b7280"
GRID = "#e5e7eb"
BLUE = "#1d4ed8"
GREEN = "#15803d"
RED = "#b91c1c"

W, H = 720, 280
PAD_L, PAD_R, PAD_T, PAD_B = 70, 16, 14, 34


@dataclass(frozen=True)
class Series:
    name: str
    points: Sequence[tuple[str, float]]
    """(ISO date, value), ascending by date."""
    color: str = BLUE
    dashed: bool = False


@dataclass(frozen=True)
class Mark:
    day: str
    price: float
    side: str
    """'buy' or 'sell'."""
    filled: bool
    label: str = ""
    manual: bool = False
    """Placed by hand in the broker app, not by the bot: drawn as a diamond."""


def _ord(day: str) -> int:
    return date.fromisoformat(day).toordinal()


def rupiah(v: float) -> str:
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{sign}Rp {v / 1e6:.1f}M"
    if v >= 10_000:
        return f"{sign}Rp {v / 1e3:.0f}k"
    return f"{sign}Rp {v:,.0f}"


def _nice_domain(lo: float, hi: float, n: int = 5) -> tuple[float, float, list[float]]:
    """Round the y range outwards to 1/2/5 x 10^k steps; returns (lo, hi, ticks)."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / (n - 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t < hi + step * 0.999:
        ticks.append(t)
        t += step
        if len(ticks) > 12:
            break
    return ticks[0], ticks[-1], ticks


def _frame(title: str, body: str, desc: str) -> str:
    return (
        f'<svg role="img" aria-label="{html.escape(title)}" viewBox="0 0 {W} {H}" '
        f'width="100%" style="max-width:{W}px;height:auto" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{html.escape(title)}</title><desc>{html.escape(desc)}</desc>{body}</svg>"
    )


class _Scale:
    def __init__(self, x0: int, x1: int, y0: float, y1: float) -> None:
        self.x0, self.x1 = x0, max(x1, x0 + 1)
        self.y0, self.y1 = y0, y1 if y1 > y0 else y0 + 1

    def x(self, day: str) -> float:
        return PAD_L + (_ord(day) - self.x0) / (self.x1 - self.x0) * (W - PAD_L - PAD_R)

    def y(self, v: float) -> float:
        return H - PAD_B - (v - self.y0) / (self.y1 - self.y0) * (H - PAD_T - PAD_B)


def _axes(sc: _Scale, y_fmt: Callable[[float], str], zero_line: bool) -> str:
    out = []
    for t in _nice_domain(sc.y0, sc.y1)[2]:
        yy = sc.y(t)
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{GRID}"/>')
        out.append(
            f'<text x="{PAD_L - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">'
            f"{html.escape(y_fmt(t))}</text>"
        )
    if zero_line and sc.y0 < 0 < sc.y1:
        out.append(
            f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{sc.y(0):.1f}" y2="{sc.y(0):.1f}" stroke="{MUTED}"/>'
        )
    for frac, anchor in ((0.0, "start"), (0.5, "middle"), (1.0, "end")):
        d = date.fromordinal(round(sc.x0 + frac * (sc.x1 - sc.x0))).isoformat()
        xx = PAD_L + frac * (W - PAD_L - PAD_R)
        out.append(
            f'<text x="{xx:.1f}" y="{H - 12}" text-anchor="{anchor}" font-size="11" fill="{MUTED}">{d}</text>'
        )
    return "".join(out)


def _path(sc: _Scale, pts: Sequence[tuple[str, float]], color: str, dashed: bool) -> str:
    if not pts:
        return ""
    d = " ".join(f"{'M' if i == 0 else 'L'}{sc.x(day):.1f},{sc.y(v):.1f}" for i, (day, v) in enumerate(pts))
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{dash} stroke-linejoin="round"/>'


def _legend(items: Sequence[tuple[str, str, bool]]) -> str:
    out, x = [], PAD_L + 8
    for name, color, dashed in items:
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        out.append(f'<line x1="{x}" x2="{x + 22}" y1="{PAD_T + 8}" y2="{PAD_T + 8}" stroke="{color}" stroke-width="2"{dash}/>')
        out.append(f'<text x="{x + 28}" y="{PAD_T + 12}" font-size="12" fill="{INK}">{html.escape(name)}</text>')
        x += 40 + 7 * len(name)
    return "".join(out)


def line_chart(title: str, series: Sequence[Series], y_fmt: Callable[[float], str] = rupiah) -> str:
    """Several dated series on one set of axes, with a zero line."""
    pts = [p for s in series for p in s.points]
    if not pts:
        return '<p class="empty">No data to chart.</p>'
    sc = _Scale(
        min(_ord(d) for d, _ in pts),
        max(_ord(d) for d, _ in pts),
        min(0.0, min(v for _, v in pts)),
        max(0.0, max(v for _, v in pts)),
    )
    y0, y1, _ = _nice_domain(sc.y0, sc.y1)
    sc = _Scale(sc.x0, sc.x1, y0, y1)
    body = _axes(sc, y_fmt, zero_line=True)
    body += "".join(_path(sc, s.points, s.color, s.dashed) for s in series)
    body += _legend([(s.name, s.color, s.dashed) for s in series])
    ends = "; ".join(f"{s.name} ends at {y_fmt(s.points[-1][1])}" for s in series if s.points)
    return _frame(title, body, ends)


def price_chart(
    title: str,
    closes: Sequence[tuple[str, float]],
    marks: Sequence[Mark],
    window_days: int = 120,
) -> str:
    """Daily closes with buys (green up-triangle) and sells (red down-triangle); hollow = not filled."""
    if not closes and not marks:
        return '<p class="empty">No price history on file for this stock.</p>'
    last_mark = max((_ord(m.day) for m in marks), default=0)
    last_close = _ord(closes[-1][0]) if closes else 0
    x1 = max(last_mark, last_close)
    x0 = x1 - window_days
    shown = [(d, v) for d, v in closes if _ord(d) >= x0]
    ys = [v for _, v in shown] + [m.price for m in marks]
    lo, hi = min(ys), max(ys)
    y0, y1, _ = _nice_domain(lo, hi)
    sc = _Scale(x0, x1, y0, y1)
    body = _axes(sc, lambda v: f"{v:,.0f}", zero_line=False)
    body += _path(sc, shown, MUTED, False)
    for m in marks:
        if _ord(m.day) < x0:
            continue
        x, y = sc.x(m.day), sc.y(m.price)
        up = m.side == "buy"
        color = GREEN if up else RED
        if m.manual:
            tip = html.escape(m.label or f"manual {m.side} {m.price:,.0f} on {m.day}")
            dia = f"{x:.1f},{y - 8:.1f} {x + 8:.1f},{y:.1f} {x:.1f},{y + 8:.1f} {x - 8:.1f},{y:.1f}"
            body += (f'<polygon class="manual-mark" points="{dia}" fill="{color}" stroke="{INK}" '
                     f'stroke-width="2"><title>{tip}</title></polygon>')
            continue
        tri = f"{x:.1f},{y - 8:.1f} {x - 6:.1f},{y + 5:.1f} {x + 6:.1f},{y + 5:.1f}" if up else (
            f"{x:.1f},{y + 8:.1f} {x - 6:.1f},{y - 5:.1f} {x + 6:.1f},{y - 5:.1f}"
        )
        fill = color if m.filled else "#ffffff"
        tip = html.escape(m.label or f"{m.side} {m.price:,.0f} on {m.day}")
        body += f'<polygon points="{tri}" fill="{fill}" stroke="{color}" stroke-width="2"><title>{tip}</title></polygon>'
    body += _legend([("Daily close (Rp)", MUTED, False)])
    desc = f"{len(shown)} daily closes; {len(marks)} order marker(s)."
    return _frame(title, body, desc)
