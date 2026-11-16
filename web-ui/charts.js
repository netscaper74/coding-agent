// Small dependency-free SVG charts (works offline / on-prem).
// Specs: 2px lines, <=24px bars with 4px rounded data-end, 2px surface gap between
// stacked segments, hairline grid, legend for >=2 series, hover tooltip, table view.

const NS = "http://www.w3.org/2000/svg";

// Categorical slots in fixed order. Color follows the entity (an action keeps its slot).
export const SLOT_COUNT = 8;
const ACTION_SLOTS = { explain: 1, chat: 2, edit: 3, tests: 4, apply: 5, "mcp:search_code": 6,
  "mcp:get_file": 7, "mcp:search_standards": 7, "mcp:list_repos": 7 };
export const actionSlot = (a) => ACTION_SLOTS[a] || 8;

const fmtCompact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
export const fmt = {
  int: (v) => (v == null ? "–" : Math.abs(v) >= 10000 ? fmtCompact.format(v) : Math.round(v).toLocaleString()),
  pct: (v) => { if (v == null) return "–"; const x = Math.round(v * 1000) / 10; return `${x.toFixed(Number.isInteger(x) ? 0 : 1)}%`; },
  ms: (v) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`),
  usd: (v) => (v == null ? "–" : v >= 100 ? `$${Math.round(v).toLocaleString()}` : v >= 0.1 || v === 0 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`),
};

export function dayLabel(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

const TICKS = 4;
// Axis max that gives TICKS clean steps (1, 2, 2.5, 5 × 10^n).
function niceMax(max) {
  if (!(max > 0)) return TICKS;
  const raw = max / TICKS;
  const exp = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * exp >= raw) return m * exp * TICKS;
  return 10 * exp * TICKS;
}

function legend(container, series) {
  if (series.length < 2) return;
  const lg = document.createElement("div");
  lg.className = "legend";
  for (const s of series) {
    const item = document.createElement("span");
    item.className = "legend-item";
    item.innerHTML = `<i class="key ${s.kind === "line" ? "key-line" : ""}" style="--c: var(--series-${s.slot})"></i>`;
    item.append(document.createTextNode(s.name));
    lg.appendChild(item);
  }
  container.appendChild(lg);
}

function tableView(container, labels, series, format, labelFmt = dayLabel) {
  const det = document.createElement("details");
  det.className = "table-view";
  const sum = document.createElement("summary");
  sum.textContent = "View as table";
  det.appendChild(sum);
  const t = document.createElement("table");
  const head = t.createTHead().insertRow();
  head.insertCell().textContent = "";
  for (const s of series) head.insertCell().textContent = s.name;
  const body = t.createTBody();
  labels.forEach((l, i) => {
    const r = body.insertRow();
    r.insertCell().textContent = labelFmt(l);
    for (const s of series) r.insertCell().textContent = format(s.values[i]);
  });
  det.appendChild(t);
  container.appendChild(det);
}

function tooltip(container) {
  let tip = container.querySelector(".tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tip";
    tip.setAttribute("role", "status");
    container.appendChild(tip);
  }
  return {
    show(x, y, html) {
      tip.innerHTML = html;
      tip.style.display = "block";
      const w = tip.offsetWidth;
      const cw = container.clientWidth;
      tip.style.left = `${Math.min(Math.max(x - w / 2, 0), cw - w)}px`;
      tip.style.top = `${Math.max(y - tip.offsetHeight - 10, 0)}px`;
    },
    hide() { tip.style.display = "none"; },
  };
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function frame(container, opts) {
  container.innerHTML = "";
  container.classList.add("chart");
  const plot = document.createElement("div");
  plot.className = "plot";
  container.appendChild(plot);
  const width = Math.max(plot.clientWidth || container.clientWidth || 600, 280);
  const height = opts.height || 220;
  const m = { top: 12, right: 16, bottom: 26, left: 52 };
  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": opts.title || "chart" });
  plot.appendChild(svg);
  return { plot, svg, width, height, m, iw: width - m.left - m.right, ih: height - m.top - m.bottom };
}

function yAxis(svg, f, max, format) {
  const ticks = TICKS;
  for (let i = 0; i <= ticks; i++) {
    const v = (max / ticks) * i;
    const y = f.m.top + f.ih - (f.ih * i) / ticks;
    el("line", { x1: f.m.left, x2: f.m.left + f.iw, y1: y, y2: y, class: i === 0 ? "baseline" : "grid" }, svg);
    const t = el("text", { x: f.m.left - 8, y: y + 4, "text-anchor": "end", class: "tick" }, svg);
    t.textContent = format(v);
  }
}

function xLabels(svg, f, labels, xAt, labelFmt) {
  const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(f.iw / 64))));
  labels.forEach((l, i) => {
    if (i % every !== 0 && i !== labels.length - 1) return;
    if (i !== labels.length - 1 && labels.length - 1 - i < every) return; // keep the last label clear
    const t = el("text", { x: xAt(i), y: f.height - 6, "text-anchor": "middle", class: "tick" }, svg);
    t.textContent = labelFmt(l);
  });
}

/** Line chart: labels on x, one or more series {name, values, slot}. */
export function lineChart(container, { labels, series, format = fmt.int, max, title, height, labelFmt = dayLabel }) {
  const draw = () => {
    const f = frame(container, { title, height });
    const vals = series.flatMap((s) => s.values).filter((v) => v != null);
    const yMax = max ?? niceMax(Math.max(...vals, 0));
    const xAt = (i) => f.m.left + (labels.length <= 1 ? f.iw / 2 : (f.iw * i) / (labels.length - 1));
    const yAt = (v) => f.m.top + f.ih - (f.ih * v) / yMax;
    yAxis(f.svg, f, yMax, format);
    xLabels(f.svg, f, labels, xAt, labelFmt);
    for (const s of series) {
      let d = "";
      let pen = false;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`;
        pen = true;
      });
      el("path", { d, class: "line", style: `stroke: var(--series-${s.slot})` }, f.svg);
      const last = s.values.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
      if (last) el("circle", { cx: xAt(last[1]), cy: yAt(last[0]), r: 4, class: "dot", style: `fill: var(--series-${s.slot})` }, f.svg);
    }
    // crosshair + tooltip
    const cross = el("line", { y1: f.m.top, y2: f.m.top + f.ih, class: "crosshair", visibility: "hidden" }, f.svg);
    const dots = series.map((s) => el("circle", { r: 4, class: "dot", style: `fill: var(--series-${s.slot})`, visibility: "hidden" }, f.svg));
    const hit = el("rect", { x: f.m.left, y: f.m.top, width: f.iw, height: f.ih, fill: "transparent" }, f.svg);
    const tip = tooltip(f.plot);
    hit.addEventListener("pointermove", (e) => {
      const r = f.svg.getBoundingClientRect();
      const x = e.clientX - r.left;
      const i = Math.round(((x - f.m.left) / f.iw) * (labels.length - 1));
      const idx = Math.max(0, Math.min(labels.length - 1, i));
      cross.setAttribute("x1", xAt(idx)); cross.setAttribute("x2", xAt(idx)); cross.setAttribute("visibility", "visible");
      series.forEach((s, k) => {
        const v = s.values[idx];
        if (v == null) { dots[k].setAttribute("visibility", "hidden"); return; }
        dots[k].setAttribute("cx", xAt(idx)); dots[k].setAttribute("cy", yAt(v)); dots[k].setAttribute("visibility", "visible");
      });
      const rows = series.map((s) => `<div class="tip-row"><i class="key key-line" style="--c: var(--series-${s.slot})"></i>${esc(s.name)}<b>${format(s.values[idx])}</b></div>`).join("");
      tip.show(xAt(idx), f.m.top + 10, `<div class="tip-title">${esc(labelFmt(labels[idx]))}</div>${rows}`);
    });
    hit.addEventListener("pointerleave", () => {
      cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); tip.hide();
    });
    legend(container, series.map((s) => ({ ...s, kind: "line" })));
    tableView(container, labels, series, format, labelFmt);
  };
  observe(container, draw);
}

/** Stacked column chart over time (or single series columns). */
export function stackedBars(container, { labels, series, format = fmt.int, title, height, labelFmt = dayLabel }) {
  const draw = () => {
    const f = frame(container, { title, height });
    const totals = labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));
    const yMax = niceMax(Math.max(...totals, 0));
    const band = f.iw / labels.length;
    const bw = Math.min(24, Math.max(4, band * 0.62));
    const xAt = (i) => f.m.left + band * i + band / 2;
    const yAt = (v) => f.m.top + f.ih - (f.ih * v) / yMax;
    yAxis(f.svg, f, yMax, format);
    xLabels(f.svg, f, labels, xAt, labelFmt);
    const tip = tooltip(f.plot);
    labels.forEach((l, i) => {
      let acc = 0;
      const segs = series.map((s) => ({ s, v: s.values[i] || 0 })).filter((x) => x.v > 0);
      segs.forEach((seg, k) => {
        const y0 = yAt(acc), y1 = yAt(acc + seg.v);
        acc += seg.v;
        const top = k === segs.length - 1;
        const gap = k > 0 ? 2 : 0; // 2px surface gap between segments
        const h = Math.max(0, y0 - y1 - gap);
        if (h <= 0) return;
        el("path", { d: barPath(xAt(i) - bw / 2, y1, bw, h, top ? Math.min(4, h) : 0), style: `fill: var(--series-${seg.s.slot})` }, f.svg);
      });
      const hit = el("rect", { x: f.m.left + band * i, y: f.m.top, width: band, height: f.ih, fill: "transparent" }, f.svg);
      hit.addEventListener("pointerenter", () => {
        const rows = series.filter((s) => s.values[i]).map((s) => `<div class="tip-row"><i class="key" style="--c: var(--series-${s.slot})"></i>${esc(s.name)}<b>${format(s.values[i])}</b></div>`).join("");
        tip.show(xAt(i), yAt(totals[i]), `<div class="tip-title">${esc(labelFmt(l))} · ${format(totals[i])}</div>${rows}`);
      });
      hit.addEventListener("pointerleave", () => tip.hide());
    });
    legend(container, series);
    tableView(container, labels, series, format, labelFmt);
  };
  observe(container, draw);
}

/** Horizontal bars for a ranked list: rows [{label, value, sub}] */
export function hbars(container, { rows, format = fmt.int, slot = 1, title }) {
  container.innerHTML = "";
  container.classList.add("hbars");
  if (!rows.length) { container.innerHTML = `<p class="empty">No data in this period.</p>`; return; }
  const max = Math.max(...rows.map((r) => r.value), 0) || 1;
  const list = document.createElement("div");
  list.setAttribute("role", "list");
  list.setAttribute("aria-label", title || "");
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "hbar-row";
    row.setAttribute("role", "listitem");
    row.innerHTML = `<span class="hbar-label">${esc(r.label)}${r.sub ? `<small>${esc(r.sub)}</small>` : ""}</span>
      <span class="hbar-track"><span class="hbar-fill" style="width:${Math.max(1, (r.value / max) * 100)}%; background: var(--series-${slot})"></span></span>
      <span class="hbar-value">${format(r.value)}</span>`;
    list.appendChild(row);
  }
  container.appendChild(list);
}

/** Small trend line for stat tiles (de-emphasised, last point accented). */
export function sparkline(values) {
  const vals = values.map((v) => v ?? 0);
  const w = 96, h = 28, max = Math.max(...vals), min = Math.min(...vals), span = max - min || 1;
  const pts = vals.map((v, i) => [(w * i) / Math.max(vals.length - 1, 1), h - 3 - ((h - 6) * (v - min)) / span]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const last = pts[pts.length - 1] || [0, h];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <path d="${d}" class="spark-line"/><circle cx="${last[0]}" cy="${last[1]}" r="3" class="spark-dot"/></svg>`;
}

function barPath(x, y, w, h, r) {
  // rounded data-end (top), square at the baseline
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

const observers = new WeakMap();
function observe(container, draw) {
  draw();
  observers.get(container)?.disconnect();
  let lastW = container.clientWidth;
  const ro = new ResizeObserver(() => {
    if (Math.abs(container.clientWidth - lastW) > 8) { lastW = container.clientWidth; draw(); }
  });
  ro.observe(container);
  observers.set(container, ro);
}
