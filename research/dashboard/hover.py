"""
dashboard/hover.py — the one small script that makes every chart hoverable.

Charts (svg.py) carry their own downsampled data in a ``data-hv`` attribute; this script reads it
and draws a crosshair, a dot per series and a date / value readout (plus the gap between the two
main lines when the chart asks for one). Vanilla JS, inlined once per page, no network. Without
JavaScript the SVG ``<title>`` tooltips on the trade points still work.
"""

from __future__ import annotations

HOVER_JS = r"""
(function () {
  var NS = "http://www.w3.org/2000/svg", EPOCH = 719163;
  function iso(o) { return new Date((o - EPOCH) * 86400000).toISOString().slice(0, 10); }
  function grp(n) { return n.replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function fmt(kind, v) {
    var a = Math.abs(v), sg = v < 0 ? "-" : "";
    if (kind !== "idr") return sg + grp(a.toFixed(a >= 100 ? 0 : 2));
    if (a >= 1e6) return sg + "Rp " + (a / 1e6).toFixed(2) + "M";
    return sg + "Rp " + grp(Math.round(a).toString());
  }
  function at(p, o) {
    if (!p.length || o < p[0][0] || o > p[p.length - 1][0]) return null;
    for (var i = 1; i < p.length; i++) {
      if (o <= p[i][0]) {
        var a = p[i - 1], b = p[i];
        return b[0] === a[0] ? b[1] : a[1] + (b[1] - a[1]) * (o - a[0]) / (b[0] - a[0]);
      }
    }
    return p[p.length - 1][1];
  }
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function init(svg) {
    var d;
    try { d = JSON.parse(svg.getAttribute("data-hv")); } catch (e) { return; }
    var W = d.g[0], PL = d.g[2], PR = d.g[3], PT = d.g[4], PB = d.g[1] - d.g[5];
    // JS is running: the per-point native tooltips would fight the readout.
    Array.prototype.forEach.call(svg.querySelectorAll(".pt"), function (c) { c.style.pointerEvents = "none"; });
    var g = el("g", { "pointer-events": "none", visibility: "hidden" }, svg);
    var vline = el("line", { y1: PT, y2: PB, stroke: "var(--muted)", "stroke-dasharray": "3 3" }, g);
    var dots = d.s.map(function (s) { return el("circle", { r: 4, fill: s.c, stroke: "var(--bg)", "stroke-width": 1.5 }, g); });
    var box = el("rect", { rx: 4, fill: "var(--panel)", stroke: "var(--muted)", "fill-opacity": 0.95 }, g);
    var lines = [];
    for (var i = 0; i < d.s.length + 2; i++) lines.push(el("text", { "font-size": 12, fill: "var(--ink)" }, g));
    function y(v) { return PB - (v - d.y0) / (d.y1 - d.y0) * (PB - PT); }
    function move(ev) {
      var r = svg.getBoundingClientRect();
      var sx = (ev.clientX - r.left) / r.width * W;
      var o = Math.round(d.x0 + (sx - PL) / (W - PL - PR) * (d.x1 - d.x0));
      if (o < d.x0) o = d.x0; if (o > d.x1) o = d.x1;
      var x = PL + (o - d.x0) / (d.x1 - d.x0) * (W - PL - PR);
      var vals = d.s.map(function (s) { return at(s.p, o); });
      var rows = [iso(o)];
      vals.forEach(function (v, k) {
        rows.push(d.s[k].n + ": " + (v === null ? "-" : fmt(d.fmt, v)));
        if (v === null) { dots[k].setAttribute("visibility", "hidden"); }
        else { dots[k].setAttribute("visibility", "visible"); dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", y(v)); }
      });
      if (d.d && vals[d.d[0]] !== null && vals[d.d[1]] !== null) {
        var gap = vals[d.d[0]] - vals[d.d[1]];
        rows.push((d.dl || "Difference") + ": " + (gap >= 0 ? "+" : "") + fmt(d.fmt, gap));
      }
      var w = 14 + 6.6 * Math.max.apply(null, rows.map(function (t) { return t.length; }));
      var h = 8 + 16 * rows.length;
      var bx = x + 12 + w > W - 4 ? x - 12 - w : x + 12;
      var by = PT + 24;
      vline.setAttribute("x1", x); vline.setAttribute("x2", x);
      box.setAttribute("x", bx); box.setAttribute("y", by); box.setAttribute("width", w); box.setAttribute("height", h);
      lines.forEach(function (t, k) {
        if (k < rows.length) {
          t.setAttribute("x", bx + 7); t.setAttribute("y", by + 17 + 16 * k);
          t.setAttribute("font-weight", k === 0 || k === rows.length - 1 && d.d ? "bold" : "normal");
          t.textContent = rows[k]; t.setAttribute("visibility", "visible");
        } else t.setAttribute("visibility", "hidden");
      });
      g.setAttribute("visibility", "visible");
    }
    svg.addEventListener("mousemove", move);
    svg.addEventListener("mouseleave", function () { g.setAttribute("visibility", "hidden"); });
  }
  Array.prototype.forEach.call(document.querySelectorAll("svg[data-hv]"), init);
})();
"""


def script_tag() -> str:
    return f"<script>{HOVER_JS}</script>"
