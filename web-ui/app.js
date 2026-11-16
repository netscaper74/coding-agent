import { lineChart, stackedBars, hbars, sparkline, fmt, actionSlot, dayLabel } from "./charts.js";

const $app = document.getElementById("app");
const TOKEN_KEY = "repoAgentAdminToken";
const PREFS_KEY = "repoAgentPrefs";
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const store = {
  get(key, fallback) { try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ } },
  del(key) { try { sessionStorage.removeItem(key); } catch { /* ignore */ } },
};
const prefs = Object.assign({ days: 14, team: "", synthetic: true }, store.get(PREFS_KEY, {}));
let token = store.get(TOKEN_KEY, null);
let me = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (res.status === 401 || res.status === 403) { logout(); throw new Error("Not authorised"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error?.message || data.error || `HTTP ${res.status}`);
  return data;
}
const qs = (extra = {}) => new URLSearchParams({ days: prefs.days, tz, team: prefs.team, synthetic: prefs.synthetic, ...extra }).toString();

// ------------------------------------------------------------------ icons
const I = {
  overview: '<path d="M3 13h7V3H3zM14 21h7V11h-7zM14 3v5h7V3zM3 21h7v-5H3z"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 010 7M18 14.6c1.8.8 3 2.6 3.5 5.4"/>',
  quality: '<path d="M12 3l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.8z"/>',
  perf: '<path d="M12 20a8 8 0 118-8"/><path d="M12 12l5-4"/><path d="M20 16v4h-4"/>',
  cost: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1.2-1.7-1.8-3-1.8-1.7 0-3 .9-3 2.2 0 3.1 6 1.6 6 4.6 0 1.3-1.3 2.3-3 2.3-1.4 0-2.6-.6-3.1-1.8M12 6v1.7M12 16.8v1.7"/>',
  knowledge: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  audit: '<path d="M8 4h11v16H5V7z"/><path d="M8 4v3H5M9 12h7M9 16h5"/>',
  play: '<path d="M4 5h16v11H8l-4 4z"/><path d="M9 10.5h6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${I[name]}</svg>`;

const PAGES = [
  ["overview", "Overview", "overview"], ["users", "Users & teams", "users"], ["quality", "Quality", "quality"],
  ["performance", "Performance", "perf"], ["cost", "Cost", "cost"], ["knowledge", "Knowledge & access", "knowledge"],
  ["audit", "Audit log", "audit"], ["playground", "Playground", "play"],
];

// ------------------------------------------------------------------ shell
function shell() {
  $app.innerHTML = `
  <div class="shell">
    <aside class="side">
      <div class="brand"><span class="brand-mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 7l-5 5 5 5M16 7l5 5-5 5"/></svg></span>
        <div>Repo Agent<small>Console</small></div></div>
      <nav class="nav">
        ${PAGES.slice(0, 7).map(([id, label, ic]) => `<a href="#/${id}" data-page="${id}">${icon(ic)}${label}</a>`).join("")}
        <div class="nav-sep">Try it</div>
        ${PAGES.slice(7).map(([id, label, ic]) => `<a href="#/${id}" data-page="${id}">${icon(ic)}${label}</a>`).join("")}
      </nav>
      <div class="side-foot">
        <div>${esc(me?.name)} · ${esc(me?.model)}</div>
        <button class="linkish" id="theme">Toggle theme</button>
        <button class="linkish" id="logout">Sign out</button>
      </div>
    </aside>
    <main id="main"></main>
  </div>`;
  document.getElementById("logout").onclick = logout;
  document.getElementById("theme").onclick = () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    store.set("repoAgentTheme", root.dataset.theme);
    route();
  };
}

function head(title, subtitle, { filters = true, team = true, extra = "" } = {}) {
  return `<div class="page-head"><div><h1>${title}</h1><p>${subtitle}</p></div>
    ${filters ? `<div class="filters">${extra}
      <div class="seg" role="group" aria-label="Date range">${[7, 14, 30, 90].map((d) =>
        `<button data-days="${d}" aria-pressed="${prefs.days === d}">${d}d</button>`).join("")}</div>
      ${team ? `<select id="team" aria-label="Team"><option value="">All teams</option></select>` : ""}
      <label class="check"><input type="checkbox" id="synthetic" ${prefs.synthetic ? "checked" : ""}> Demo data</label>
    </div>` : ""}</div>`;
}

let teamsCache = null;
async function wireFilters() {
  document.querySelectorAll("[data-days]").forEach((b) => b.onclick = () => { prefs.days = +b.dataset.days; store.set(PREFS_KEY, prefs); route(); });
  const syn = document.getElementById("synthetic");
  if (syn) syn.onchange = () => { prefs.synthetic = syn.checked; store.set(PREFS_KEY, prefs); route(); };
  const team = document.getElementById("team");
  if (team) {
    if (!teamsCache) {
      try { teamsCache = (await api(`/admin/users?${qs({ team: "", days: 90 })}`)).users.map((u) => u.team).filter(Boolean); } catch { teamsCache = []; }
      teamsCache = [...new Set(teamsCache)].sort();
    }
    team.innerHTML = `<option value="">All teams</option>` + teamsCache.map((t) => `<option ${t === prefs.team ? "selected" : ""}>${esc(t)}</option>`).join("");
    team.onchange = () => { prefs.team = team.value; store.set(PREFS_KEY, prefs); route(); };
  }
}

// ------------------------------------------------------------------ widgets
function delta(cur, prev, { goodUp = true, kind = "pct" } = {}) {
  if (cur == null || prev == null || (kind !== "pp" && prev === 0)) return `<span class="delta flat">no prior data</span>`;
  const diff = kind === "pp" ? (cur - prev) * 100 : ((cur - prev) / prev) * 100;
  if (Math.abs(diff) < 0.5) return `<span class="delta flat">± 0${kind === "pp" ? " pts" : "%"}</span>`;
  const up = diff > 0;
  const cls = up === goodUp ? "good" : "bad";
  return `<span class="delta ${cls}">${up ? "▲" : "▼"} ${Math.abs(diff).toFixed(kind === "pp" ? 1 : 0)}${kind === "pp" ? " pts" : "%"}</span>`;
}
const kpi = (label, value, foot = "", trend = null) =>
  `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div>
   <div class="kpi-foot"><span>${foot}</span>${trend ? sparkline(trend) : ""}</div></div>`;
const card = (span, title, sub, body, id) =>
  `<section class="card span-${span}"><h2>${title}</h2>${sub ? `<p class="sub">${sub}</p>` : ""}${id ? `<div id="${id}"></div>` : ""}${body || ""}</section>`;
const initials = (n) => (n || "?").split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
const when = (iso) => {
  if (!iso) return "–";
  const d = new Date(iso), s = (Date.now() - d) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};
const clock = (iso) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const prevLabel = () => `vs previous ${prefs.days} days`;
const actionPill = (a) => `<span class="pill"><i class="key" style="--c: var(--series-${actionSlot(a)})"></i>${esc(a || "–")}</span>`;
const statusCell = (e) => e.status === "error" ? `<span class="status err" title="${esc(e.error || "")}">error</span>` :
  e.no_context ? `<span class="status warn">no context</span>` : `<span class="status ok">ok</span>`;
const syntheticBanner = (on) => on && prefs.synthetic ? `<div class="banner">${icon("info")}<span>This period includes generated demo activity. Untick <b>Demo data</b> to see only real usage.</span></div>` : "";

function sortableTable(container, columns, rows, { onRow, empty = "No data in this period.", initial } = {}) {
  let sortKey = initial?.key ?? null, dir = initial?.dir ?? -1;
  const render = () => {
    const sorted = [...rows];
    if (sortKey) {
      const col = columns.find((c) => c.key === sortKey);
      const val = col.sort || ((r) => r[sortKey]);
      sorted.sort((a, b) => { const x = val(a), y = val(b); return (x == null) - (y == null) || (x > y ? 1 : x < y ? -1 : 0) * dir; });
    }
    container.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr>${columns.map((c) =>
      `<th class="${c.num ? "num " : ""}${c.key ? "sortable" : ""}" data-key="${c.key || ""}">${c.label}${sortKey === c.key ? (dir > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
      <tbody>${sorted.map((r, i) => `<tr data-i="${rows.indexOf(r)}" class="${onRow ? "clickable" : ""}">${columns.map((c) =>
        `<td class="${c.num ? "num" : ""}">${c.render ? c.render(r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">${empty}</p>`;
    container.querySelectorAll("th.sortable").forEach((th) => th.onclick = () => {
      const k = th.dataset.key; if (!k) return;
      dir = sortKey === k ? -dir : -1; sortKey = k; render();
    });
    if (onRow) container.querySelectorAll("tbody tr").forEach((tr) => tr.onclick = () => onRow(rows[+tr.dataset.i]));
  };
  render();
}

function actionSeries(byAction, days) {
  return Object.keys(byAction).sort((a, b) => actionSlot(a) - actionSlot(b) || a.localeCompare(b))
    .map((a) => ({ name: a, slot: actionSlot(a), values: Array.isArray(byAction[a]) ? byAction[a] : days.map((d) => byAction[a][d] || 0) }));
}

// ------------------------------------------------------------------ pages
async function overview(main) {
  const d = await api(`/admin/overview?${qs()}`);
  const t = d.tiles;
  const total = d.days.map((_, i) => Object.values(d.requests_by_action).reduce((a, s) => a + (s[d.days[i]] || 0), 0));
  main.insertAdjacentHTML("beforeend", `${syntheticBanner(d.has_synthetic)}
    <div class="kpis">
      ${kpi("Active developers", fmt.int(t.active_users.value), `${delta(t.active_users.value, t.active_users.prev)} · DAU ${t.dau} · WAU ${t.wau}`, d.daily_active_users)}
      ${kpi("Requests", fmt.int(t.requests.value), `${delta(t.requests.value, t.requests.prev)} ${prevLabel()}`, total)}
      ${kpi("Suggestion acceptance", fmt.pct(t.acceptance.value), t.acceptance.value == null ? "no outcomes yet" : `${delta(t.acceptance.value, t.acceptance.prev, { kind: "pp" })} · ${fmt.int(t.acceptance.accepted)} accepted`)}
      ${kpi("Grounded answers", fmt.pct(t.grounded.value), `${delta(t.grounded.value, t.grounded.prev, { kind: "pp" })} cited repo sources`)}
      ${kpi("Latency p95", fmt.ms(t.latency_p95), `p50 ${fmt.ms(t.latency_p50)}`)}
      ${kpi("Spend", fmt.usd(t.cost_usd.value), `${delta(t.cost_usd.value, t.cost_usd.prev, { goodUp: false })} · errors ${fmt.pct(t.error_rate.value)}`)}
    </div>
    <div class="grid">
      ${card(8, "Requests by action", "Every request the agent served, per day", "", "c-actions")}
      ${card(4, "Teams", "Requests in this period", "", "c-teams")}
      ${card(6, "Daily active developers", "Distinct developers with at least one request", "", "c-dau")}
      ${card(6, "Where usage comes from", "Requests per team, active developers and spend", "", "t-teams")}
    </div>`);
  stackedBars(document.getElementById("c-actions"), { labels: d.days, series: actionSeries(d.requests_by_action, d.days), title: "Requests by action" });
  hbars(document.getElementById("c-teams"), { rows: d.teams.map((x) => ({ label: x.team, value: x.requests, sub: `${x.users} dev` })), title: "Requests by team" });
  lineChart(document.getElementById("c-dau"), { labels: d.days, series: [{ name: "Active developers", slot: 1, values: d.daily_active_users }], title: "Daily active developers" });
  sortableTable(document.getElementById("t-teams"), [
    { key: "team", label: "Team" }, { key: "users", label: "Developers", num: true },
    { key: "requests", label: "Requests", num: true, render: (r) => fmt.int(r.requests) },
    { key: "errors", label: "Errors", num: true }, { key: "cost_usd", label: "Spend", num: true, render: (r) => fmt.usd(r.cost_usd) },
  ], d.teams, { initial: { key: "requests" } });
}

async function users(main) {
  const d = await api(`/admin/users?${qs()}`);
  main.insertAdjacentHTML("beforeend", `<div class="grid">${card(12, "Developers", "Click a row for the activity drill-down. Per-developer views are admin-only.", "", "t-users")}</div>`);
  sortableTable(document.getElementById("t-users"), [
    { key: "name", label: "Developer", render: (r) => `<div class="who"><span class="avatar">${initials(r.name)}</span><div>${esc(r.name)}<small>${esc(r.user)}</small></div></div>` },
    { key: "team", label: "Team" }, { key: "ide", label: "IDE", render: (r) => esc(r.ide || "–") },
    { key: "last_active", label: "Last active", render: (r) => when(r.last_active) },
    { key: "requests", label: "Requests", num: true, render: (r) => fmt.int(r.requests) },
    { key: "top_action", label: "Top action", render: (r) => r.top_action ? actionPill(r.top_action) : "–" },
    { key: "grounded_rate", label: "Grounded", num: true, render: (r) => fmt.pct(r.grounded_rate) },
    { key: "acceptance", label: "Acceptance", num: true, render: (r) => fmt.pct(r.acceptance) },
    { key: "avg_latency_ms", label: "Avg latency", num: true, render: (r) => fmt.ms(r.avg_latency_ms) },
    { key: "error_rate", label: "Errors", num: true, render: (r) => fmt.pct(r.error_rate) },
    { key: "cost_usd", label: "Spend", num: true, render: (r) => fmt.usd(r.cost_usd) },
  ], d.users, { onRow: (r) => { location.hash = `#/users/${encodeURIComponent(r.user)}`; }, initial: { key: "requests" } });
}

async function userDetail(main, user) {
  const d = await api(`/admin/users/${encodeURIComponent(user)}?${qs()}`);
  const s = d.summary;
  main.innerHTML = head(`<a href="#/users" class="muted" style="text-decoration:none">Users /</a> ${esc(user)}`, `Activity over the last ${prefs.days} days`, { team: false });
  wireFilters();
  main.insertAdjacentHTML("beforeend", `
    <div class="kpis">
      ${kpi("Requests", fmt.int(s.requests))}
      ${kpi("Grounded answers", fmt.pct(s.requests ? s.with_sources / s.requests : null))}
      ${kpi("Acceptance", fmt.pct(d.acceptance.rate), `${d.acceptance.accepted} accepted · ${d.acceptance.rejected} rejected`)}
      ${kpi("Avg latency", fmt.ms(s.requests ? s.latency_sum / s.requests : null))}
      ${kpi("Spend", fmt.usd(s.cost_usd))}
      ${kpi("Hidden by permissions", fmt.int(s.filtered_by_acl), "relevant chunks outside their access")}
    </div>
    <div class="grid">
      ${card(8, "Daily activity", "Requests per day by action", "", "c-daily")}
      ${card(4, "Repositories", "Requests grounded in each repo", "", "c-repos")}
      ${card(12, "Recent requests", "Prompt text is not stored unless LOG_PROMPTS is enabled", "", "t-recent")}
    </div>`);
  stackedBars(document.getElementById("c-daily"), { labels: d.days, series: actionSeries(d.daily, d.days) });
  hbars(document.getElementById("c-repos"), { rows: d.repos.map((r) => ({ label: r.repo, value: r.requests })) });
  eventsTable(document.getElementById("t-recent"), d.recent, { user: false });
}

function eventsTable(container, rows, { user = true } = {}) {
  sortableTable(container, [
    { key: "ts", label: "Time", render: (r) => `<span title="${esc(r.ts)}">${clock(r.ts)}</span>` },
    ...(user ? [{ key: "user", label: "Developer", render: (r) => `${esc(r.user)} <span class="muted">${esc(r.team || "")}</span>` }] : []),
    { key: "action", label: "Action", render: (r) => actionPill(r.action) },
    { key: "channel", label: "Via", render: (r) => esc(r.channel === "ide" ? r.ide : r.channel) },
    { key: "repo", label: "Repo", render: (r) => esc(r.repo || "–") },
    { key: "status", label: "Status", render: statusCell },
    { key: "sources", label: "Sources", num: true },
    { key: "cited", label: "Cited", num: true },
    { key: "filtered_by_acl", label: "Hidden", num: true },
    { key: "latency_ms", label: "Latency", num: true, render: (r) => fmt.ms(r.latency_ms) },
    { key: "cost_usd", label: "Cost", num: true, render: (r) => fmt.usd(r.cost_usd) },
    ...(rows.some((r) => r.query) ? [{ key: "query", label: "Query", render: (r) => `<span class="muted" title="${esc(r.query)}">${esc((r.query || "").slice(0, 60))}</span>` }] : []),
  ], rows, { empty: "No requests in this period." });
}

async function quality(main) {
  const d = await api(`/admin/quality?${qs()}`);
  const latest = d.latest_eval;
  const avg = (xs) => { const v = xs.filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const fb = d.feedback, fbTotal = fb.helpful + fb.not_helpful;
  const acc = d.acceptance_by_event.reduce((a, r) => ({ ok: a.ok + r.accepted, n: a.n + r.total }), { ok: 0, n: 0 });
  main.insertAdjacentHTML("beforeend", `
    <div class="kpis">
      ${kpi("Grounded answers", fmt.pct(avg(d.grounded_rate)), "answers with retrieved sources", d.grounded_rate)}
      ${kpi("Answers citing sources", fmt.pct(avg(d.cited_rate)), "used [S#] labels", d.cited_rate)}
      ${kpi("No-context rate", fmt.pct(avg(d.no_context_rate)), "nothing relevant was accessible", d.no_context_rate)}
      ${kpi("Edit acceptance", fmt.pct(acc.n ? acc.ok / acc.n : null), `${fmt.int(acc.ok)} of ${fmt.int(acc.n)} outcomes`)}
      ${kpi("Helpful feedback", fmt.pct(fbTotal ? fb.helpful / fbTotal : null), `${fbTotal} ratings`)}
      ${kpi("Latest eval", latest ? fmt.pct(latest.pass_rate) : "–", latest ? `${latest.questions} questions · ${when(latest.started_at)}` : "not run yet")}
    </div>
    <div class="grid">
      ${card(7, "Grounding over time", "Share of IDE answers that had repository sources, and that cited them", "", "c-ground")}
      ${card(5, "No-context rate by team", "High values point at repos or docs that are missing from the index", "", "c-noctx")}
      ${card(6, "Quality by action", "", "", "t-action")}
      ${card(6, "Outcomes from the IDE", "Continue dev-data events (editOutcome etc.) and explicit feedback", "", "t-outcomes")}
      <section class="card span-12"><div class="page-head" style="margin:0 0 10px"><div><h2>Evaluation runs</h2>
        <p class="sub" style="margin:0">Fixed question set in <code>eval/questions.yaml</code>, run through the real agent: retrieval hit, citation accuracy, standards, permission checks</p></div>
        <button class="btn primary" id="run-eval">Run eval now</button></div><div id="t-evals"></div><div id="t-eval-latest" style="margin-top:14px"></div></section>
      ${d.prompt_logging ? card(12, "Recent no-context questions", "Shown because LOG_PROMPTS is enabled", "", "t-noctxq") : ""}
    </div>`);
  lineChart(document.getElementById("c-ground"), { labels: d.days, max: 1, format: fmt.pct, series: [
    { name: "Grounded", slot: 1, values: d.grounded_rate }, { name: "Cited sources", slot: 2, values: d.cited_rate }] });
  hbars(document.getElementById("c-noctx"), { rows: d.no_context_by_team.map((r) => ({ label: r.team, value: r.rate || 0, sub: `${r.no_context}/${r.requests}` })), format: fmt.pct, slot: 2 });
  sortableTable(document.getElementById("t-action"), [
    { key: "action", label: "Action", render: (r) => actionPill(r.action) }, { key: "requests", label: "Requests", num: true, render: (r) => fmt.int(r.requests) },
    { key: "grounded_rate", label: "Grounded", num: true, render: (r) => fmt.pct(r.grounded_rate) },
    { key: "cited_rate", label: "Cited", num: true, render: (r) => fmt.pct(r.cited_rate) },
    { key: "invalid_citation_rate", label: "Invalid citations", num: true, render: (r) => fmt.pct(r.invalid_citation_rate) },
  ], d.by_action, { initial: { key: "requests" } });
  sortableTable(document.getElementById("t-outcomes"), [
    { key: "event", label: "Event" }, { key: "total", label: "Outcomes", num: true },
    { key: "accepted", label: "Accepted", num: true }, { key: "rate", label: "Rate", num: true, render: (r) => fmt.pct(r.rate) },
  ], d.acceptance_by_event, { empty: "No outcome events yet. Add the data block from continue/config.yaml to Continue." });
  sortableTable(document.getElementById("t-evals"), [
    { key: "started_at", label: "Run", render: (r) => clock(r.started_at) }, { key: "model", label: "Model" },
    { key: "questions", label: "Questions", num: true }, { key: "pass_rate", label: "Pass", num: true, render: (r) => fmt.pct(r.pass_rate) },
    { key: "retrieval_hit_rate", label: "Retrieval hit", num: true, render: (r) => fmt.pct(r.retrieval_hit_rate) },
    { key: "citation_accuracy", label: "Citation accuracy", num: true, render: (r) => fmt.pct(r.citation_accuracy) },
    { key: "standards_hit_rate", label: "Standards", num: true, render: (r) => fmt.pct(r.standards_hit_rate) },
    { key: "acl_pass_rate", label: "Permissions", num: true, render: (r) => fmt.pct(r.acl_pass_rate) },
    { key: "latency_p50_ms", label: "p50", num: true, render: (r) => fmt.ms(r.latency_p50_ms) },
    { key: "cost_usd", label: "Cost", num: true, render: (r) => fmt.usd(r.cost_usd) },
  ], d.eval_runs, { empty: "No eval runs yet. Click “Run eval now” or run: docker compose run --rm eval" });
  if (latest) {
    const checks = (c) => Object.entries(c || {}).map(([k, v]) => `<span class="status ${v ? "ok" : "err"}">${esc(k.replace(/_/g, " "))}</span>`).join(" &nbsp; ");
    sortableTable(document.getElementById("t-eval-latest"), [
      { key: "id", label: "Latest run: question" }, { key: "as_user", label: "As" },
      { key: "passed", label: "Result", render: (r) => r.error ? `<span class="status err" title="${esc(r.error)}">error</span>` : `<span class="status ${r.passed ? "ok" : "err"}">${r.passed ? "pass" : "fail"}</span>` },
      { label: "Checks", render: (r) => checks(r.checks) },
      { key: "latency_ms", label: "Latency", num: true, render: (r) => fmt.ms(r.latency_ms) },
    ], latest.results);
  }
  if (d.prompt_logging) eventsTable(document.getElementById("t-noctxq"), d.no_context_queries);
  const btn = document.getElementById("run-eval");
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = "Running… (about a minute)";
    try {
      await api("/admin/eval/run", { method: "POST" });
      const before = d.eval_runs[0]?.id;
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 5000));
        const q = await api(`/admin/quality?${qs()}`);
        if (q.eval_runs[0] && q.eval_runs[0].id !== before) { route(); return; }
      }
    } catch (e) { btn.textContent = `Failed: ${e.message}`; return; }
    btn.disabled = false; btn.textContent = "Run eval now";
  };
}

async function performance(main) {
  const d = await api(`/admin/performance?${qs()}`);
  const o = d.overall;
  main.insertAdjacentHTML("beforeend", `
    <div class="kpis">
      ${kpi("End-to-end p50", fmt.ms(o.total.p50), `p95 ${fmt.ms(o.total.p95)}`)}
      ${kpi("Time to first token p50", fmt.ms(o.ttft.p50), `p95 ${fmt.ms(o.ttft.p95)}`)}
      ${kpi("Retrieval p95", fmt.ms((o.embed.p95 || 0) + (o.search.p95 || 0) + (o.rerank.p95 || 0)), "embed + search + rerank")}
      ${kpi("LLM p95", fmt.ms(o.llm.p95), `p50 ${fmt.ms(o.llm.p50)}`)}
      ${kpi("Error rate", fmt.pct(d.requests.reduce((a, n, i) => a + n * (d.error_rate[i] || 0), 0) / Math.max(1, d.requests.reduce((a, b) => a + b, 0))), "requests that failed")}
    </div>
    <div class="grid">
      ${card(8, "End-to-end latency", "Per day, all channels except eval", "", "c-lat")}
      ${card(4, "Where the time goes", "p95 per stage across the period", "", "c-stage")}
      ${card(6, "Error rate", "Share of requests per day that failed", "", "c-err")}
      ${card(6, "Retrieval stages", "p95 per day: Voyage embed, Atlas search, Voyage rerank", "", "c-ret")}
      ${card(12, "Slowest requests", "", "", "t-slow")}
      ${card(12, "Recent errors", "", "", "t-errors")}
    </div>`);
  lineChart(document.getElementById("c-lat"), { labels: d.days, format: fmt.ms, series: [
    { name: "p50", slot: 1, values: d.p50.total }, { name: "p95", slot: 2, values: d.p95.total }] });
  hbars(document.getElementById("c-stage"), { format: fmt.ms, rows: [
    ["LLM (OpenAI)", o.llm.p95], ["Atlas search", o.search.p95], ["Voyage embed", o.embed.p95], ["Voyage rerank", o.rerank.p95]]
    .map(([label, value]) => ({ label, value: value || 0 })) });
  lineChart(document.getElementById("c-err"), { labels: d.days, format: fmt.pct, series: [{ name: "Error rate", slot: 8, values: d.error_rate }] });
  lineChart(document.getElementById("c-ret"), { labels: d.days, format: fmt.ms, series: [
    { name: "Embed", slot: 1, values: d.p95.embed }, { name: "Search", slot: 2, values: d.p95.search }, { name: "Rerank", slot: 3, values: d.p95.rerank }] });
  eventsTable(document.getElementById("t-slow"), d.slowest);
  eventsTable(document.getElementById("t-errors"), d.recent_errors);
}

async function cost(main) {
  const d = await api(`/admin/cost?${qs()}`);
  const t = d.totals;
  main.insertAdjacentHTML("beforeend", `
    <div class="kpis">
      ${kpi("Total spend", fmt.usd(t.cost_usd + t.indexing), `${fmt.int(t.requests)} requests`)}
      ${kpi("OpenAI", fmt.usd(t.openai), `${fmt.int(t.openai_in)} in · ${fmt.int(t.openai_out)} out tokens`)}
      ${kpi("Voyage (queries)", fmt.usd(t.voyage_queries), `${fmt.int(t.voyage_tokens)} tokens`)}
      ${kpi("Voyage (indexing)", fmt.usd(t.indexing), `${fmt.int(t.index_tokens)} tokens embedded`)}
      ${kpi("Per accepted suggestion", fmt.usd(t.per_accepted), `per request ${fmt.usd(t.per_request)}`)}
      ${kpi("Per active developer", fmt.usd(t.per_active_user), `over ${prefs.days} days`)}
    </div>
    <div class="grid">
      ${card(8, "Daily spend", `Query-time cost by provider · model ${esc(d.model)}`, "", "c-cost")}
      ${card(4, "By team", "", "", "c-team")}
      ${card(7, "Top developers by spend", "", "", "t-users")}
      ${card(5, "Price table", "From config/pricing.yaml (USD per 1M tokens) — check against current price lists", `<pre class="mono">${esc(JSON.stringify(d.pricing, null, 2))}</pre>`)}
    </div>`);
  stackedBars(document.getElementById("c-cost"), { labels: d.days, format: fmt.usd, series: [
    { name: "OpenAI", slot: 1, values: d.daily.openai }, { name: "Voyage", slot: 3, values: d.daily.voyage }] });
  hbars(document.getElementById("c-team"), { format: fmt.usd, rows: d.by_team.map((x) => ({ label: x.team, value: x.cost_usd, sub: `${x.users} dev` })) });
  sortableTable(document.getElementById("t-users"), [
    { key: "user", label: "Developer" }, { key: "team", label: "Team" },
    { key: "requests", label: "Requests", num: true, render: (r) => fmt.int(r.requests) },
    { key: "openai_in", label: "Tokens in", num: true, render: (r) => fmt.int(r.openai_in) },
    { key: "openai_out", label: "Tokens out", num: true, render: (r) => fmt.int(r.openai_out) },
    { key: "cost_usd", label: "Spend", num: true, render: (r) => fmt.usd(r.cost_usd) },
  ], d.by_user, { initial: { key: "cost_usd" } });
}

async function knowledge(main) {
  const d = await api(`/admin/knowledge?${qs()}`);
  const code = d.repos.filter((r) => r.kind !== "standard");
  const lastRun = d.ingest_runs[0];
  main.insertAdjacentHTML("beforeend", `
    <div class="kpis">
      ${kpi("Repositories indexed", fmt.int(code.length), `${d.repos.length - code.length} standards document(s)`)}
      ${kpi("Chunks in Atlas", fmt.int(d.total_chunks), "functions, classes and clauses")}
      ${kpi("Files", fmt.int(d.repos.reduce((a, r) => a + r.files, 0)))}
      ${kpi("Last ingest", lastRun ? when(lastRun.started_at) : "–", lastRun ? (lastRun.ok ? `<span class="status ok">ok</span> · ${lastRun.files_changed} files changed` : `<span class="status err">errors</span>`) : "ingest has not run")}
      ${kpi("Relevant results hidden", fmt.int(d.acl.reduce((a, r) => a + r.hidden, 0)), "chunks outside the asker's access, this period")}
    </div>
    <div class="grid">
      ${card(12, "Indexed sources", "What the agent can retrieve from, and who can see it", "", "t-repos")}
      ${card(7, "Ingest runs", "Incremental: only changed files are re-chunked and re-embedded", "", "t-runs")}
      ${card(5, "Permission filtering", "Developers whose questions matched content outside their access scopes. Nothing hidden is shown to them.", "", "t-acl")}
    </div>`);
  sortableTable(document.getElementById("t-repos"), [
    { key: "repo", label: "Source", render: (r) => `<b>${esc(r.repo)}</b> ${r.kind === "standard" ? '<span class="pill">standard</span>' : ""}` },
    { key: "files", label: "Files", num: true }, { key: "chunks", label: "Chunks", num: true },
    { label: "Languages", render: (r) => esc(r.languages.join(", ")) },
    { label: "Access scope", render: (r) => r.access_scope.map((s) => `<span class="pill">${esc(s)}</span>`).join(" ") },
    { key: "commit", label: "Commit", render: (r) => `<span class="mono">${esc((r.commit || "–").slice(0, 10))}</span>` },
    { key: "minutes_since_check", label: "Checked", render: (r) => when(r.last_checked) },
    { key: "last_change_indexed", label: "Last change indexed", render: (r) => when(r.last_change_indexed) },
    { key: "requests", label: "Requests", num: true },
    { label: "Status", render: (r) => r.errors.length ? `<span class="status err" title="${esc(r.errors.join("\n"))}">${r.errors.length} error(s)</span>` : `<span class="status ok">ok</span>` },
  ], d.repos, { empty: "Nothing indexed yet. Check the ingest service logs: docker compose logs ingest" });
  sortableTable(document.getElementById("t-runs"), [
    { key: "started_at", label: "Started", render: (r) => clock(r.started_at) },
    { key: "seconds", label: "Duration", num: true, render: (r) => `${r.seconds}s` },
    { key: "files_changed", label: "Files changed", num: true }, { key: "chunks_written", label: "Chunks written", num: true },
    { key: "embed_tokens", label: "Tokens", num: true, render: (r) => fmt.int(r.embed_tokens) },
    { label: "Status", render: (r) => r.ok ? `<span class="status ok">ok</span>` : `<span class="status err" title="${esc(r.errors.join("\n"))}">errors</span>` },
  ], d.ingest_runs);
  sortableTable(document.getElementById("t-acl"), [
    { key: "user", label: "Developer" }, { key: "team", label: "Team" },
    { key: "requests", label: "Affected requests", num: true }, { key: "hidden", label: "Chunks hidden", num: true },
  ], d.acl, { empty: "No permission filtering in this period.", initial: { key: "hidden" } });
}

async function audit(main) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const f = { user: params.get("user") || "", action: params.get("action") || "", status: params.get("status") || "", channel: params.get("channel") || "" };
  main.insertAdjacentHTML("beforeend", `
    <div class="card" style="margin-bottom:16px"><form id="af" class="pg-row">
      <input type="text" name="user" placeholder="Developer (e.g. alice)" value="${esc(f.user)}">
      <select name="action"><option value="">Any action</option>${["explain", "chat", "edit", "tests", "apply", "mcp:search_code", "mcp:search_standards", "mcp:get_file", "mcp:list_repos"].map((a) => `<option ${a === f.action ? "selected" : ""}>${a}</option>`).join("")}</select>
      <select name="channel"><option value="">Any channel</option>${["ide", "mcp", "playground"].map((a) => `<option ${a === f.channel ? "selected" : ""}>${a}</option>`).join("")}</select>
      <select name="status"><option value="">Any status</option>${["ok", "error"].map((a) => `<option ${a === f.status ? "selected" : ""}>${a}</option>`).join("")}</select>
      <button class="btn primary">Apply</button></form></div>
    <div class="grid">${card(12, "Requests", "One row per agent request. Code and prompts are not stored.", "", "t-ev")}
      <div class="span-12" style="text-align:center"><button class="btn" id="more" hidden>Load older</button></div>
      ${card(12, "Admin actions", "e.g. playground questions asked on behalf of another developer", "", "t-admin")}</div>`);
  document.getElementById("af").onsubmit = (e) => {
    e.preventDefault();
    const p = new URLSearchParams(Object.entries(Object.fromEntries(new FormData(e.target))).filter(([, v]) => v));
    location.hash = `#/audit?${p}`;
  };
  let rows = [];
  const load = async (before) => {
    const d = await api(`/admin/audit?${qs({ ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)), limit: 100, ...(before ? { before } : {}) })}`);
    rows = rows.concat(d.events);
    eventsTable(document.getElementById("t-ev"), rows);
    const more = document.getElementById("more");
    more.hidden = !d.next_before;
    more.onclick = () => load(d.next_before);
    if (!before) sortableTable(document.getElementById("t-admin"), [
      { key: "ts", label: "Time", render: (r) => clock(r.ts) }, { key: "actor", label: "Admin" }, { key: "action", label: "Action" },
      { key: "as_user", label: "On behalf of", render: (r) => esc(r.as_user || "–") }, { key: "repo", label: "Repo", render: (r) => esc(r.repo || "–") },
    ], d.admin_actions, { empty: "No admin actions in this period." });
  };
  await load();
}

function markdown(src) {
  const blocks = [];
  let text = esc(src).replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => { blocks.push(`<pre><code>${code}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  text = text.replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/^#{1,4} (.*)$/gm, "<h3>$1</h3>")
    .replace(/^---$/gm, "<hr>")
    .replace(/(^|\n)((?:[-*] .*(?:\n|$))+)/g, (_, pre, list) => `${pre}<ul>${list.trim().split("\n").map((l) => `<li>${l.replace(/^[-*] /, "")}</li>`).join("")}</ul>`)
    .split(/\n{2,}/).map((p) => /^\s*<(h3|ul|hr)|\u0000/.test(p) ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

async function playground(main) {
  const [u, k] = await Promise.all([api(`/admin/users?${qs({ days: 90, team: "", synthetic: false })}`), api(`/admin/knowledge?${qs()}`)]);
  const people = u.users.filter((x) => x.role);
  const repos = k.repos.filter((r) => r.kind !== "standard").map((r) => r.repo);
  const examples = ["Where are journal entries validated before posting, and which rules are enforced?",
    "Review the ledger repository for SQL that violates our coding standard and propose a fix.",
    "Is the receipt preview in the payment form safe against XSS?",
    "Write unit tests for convert() in the FX module."];
  main.insertAdjacentHTML("beforeend", `<div class="grid">
    <section class="card span-12"><form class="pg-form" id="pg">
      <div class="pg-row"><label>Ask as <select name="as_user">${people.map((p) => `<option value="${esc(p.user)}">${esc(p.name)} (${esc(p.team)})</option>`).join("")}</select></label>
        <label>Repo <select name="repo"><option value="">All accessible</option>${repos.map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
        <span class="muted">Answers use the same retrieval, permissions and prompt as the IDE.</span></div>
      <textarea name="question" placeholder="Ask about the indexed code…" required>${esc(examples[0])}</textarea>
      <div class="pg-row"><button class="btn primary" id="ask">Ask</button>
        ${examples.slice(1).map((e) => `<button type="button" class="btn ex">${esc(e.slice(0, 48))}…</button>`).join("")}</div>
    </form></section>
    <div id="pg-out" class="span-12 grid" style="grid-template-columns: inherit"></div></div>`);
  main.querySelectorAll(".ex").forEach((b, i) => b.onclick = () => { main.querySelector("textarea").value = examples[i + 1]; });
  document.getElementById("pg").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    const out = document.getElementById("pg-out");
    const btn = document.getElementById("ask");
    btn.disabled = true; btn.textContent = "Thinking…";
    out.innerHTML = `<section class="card span-12"><p class="loading">Retrieving from Atlas and asking ${esc(me?.model)}…</p></section>`;
    try {
      const r = await api("/admin/playground", { method: "POST", body: JSON.stringify(body) });
      const lat = r.latency_ms;
      out.innerHTML = `
        <section class="card span-7"><h2>Answer</h2>
          <div class="chips"><span class="pill">${esc(r.as_user.user)} · ${r.as_user.scopes.map(esc).join(", ")}</span>${actionPill(r.action)}
            <span class="pill">total ${fmt.ms(lat.total)}</span><span class="pill">retrieval ${fmt.ms(lat.embed + lat.search + lat.rerank)}</span>
            <span class="pill">${fmt.int(r.tokens.openai_in)} → ${fmt.int(r.tokens.openai_out)} tokens</span><span class="pill">${fmt.usd(r.cost_usd)}</span>
            <span class="pill">${r.cited} cited${r.invalid_citations ? ` · ${r.invalid_citations} invalid` : ""}</span>
            ${r.filtered_by_acl ? `<span class="pill">${r.filtered_by_acl} relevant chunk(s) hidden by permissions</span>` : ""}</div>
          ${r.retrieval_error ? `<p class="error">${esc(r.retrieval_error)}</p>` : ""}
          <div class="answer">${markdown(r.answer)}</div></section>
        <section class="card span-5"><h2>Retrieved sources</h2><p class="sub">Permission-filtered hybrid search (vector + keyword), reranked</p>
          ${r.sources.length ? r.sources.map((s) => `<div class="source"><div class="source-head"><span class="pill">${s.label}</span>
            <b>${esc(s.kind === "standard" ? `${s.repo} ${s.clause}` : `${s.repo}/${s.path}:${s.start_line}-${s.end_line}`)}</b>
            <span class="muted">${esc(s.kind === "standard" ? s.title : s.symbol)}${s.score != null ? ` · ${Number(s.score).toFixed(3)}` : ""}</span></div>
            <pre>${esc(s.preview)}</pre></div>`).join("") : `<p class="empty">No sources this developer can access matched.</p>`}</section>`;
    } catch (err) {
      out.innerHTML = `<section class="card span-12"><p class="error">${esc(err.message)}</p></section>`;
    } finally { btn.disabled = false; btn.textContent = "Ask"; }
  };
}

const ROUTES = {
  overview: ["Overview", "Adoption, value and health of the coding agent", overview],
  users: ["Users & teams", "Who uses the agent, how, and with what results", users],
  quality: ["Quality", "Is the agent grounded, cited and accepted by developers?", quality],
  performance: ["Performance", "Latency by stage and reliability", performance],
  cost: ["Cost", "OpenAI and Voyage AI spend, per team and per outcome", cost],
  knowledge: ["Knowledge & access", "What is indexed in Atlas, how fresh it is, and permission filtering", knowledge],
  audit: ["Audit log", "Searchable record of agent requests", audit],
  playground: ["Playground", "Ask the agent as any developer and inspect what it retrieved", playground],
};

async function route() {
  if (!token) return login();
  if (!me) {
    try { me = await api("/admin/me"); } catch { return; }
    shell();
  }
  const [path] = location.hash.replace(/^#\/?/, "").split("?");
  const [page, arg] = path.split("/");
  const key = ROUTES[page] ? page : "overview";
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.page === key) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const main = document.getElementById("main");
  const [title, sub, fn] = ROUTES[key];
  main.innerHTML = `<div class="loading">Loading…</div>`;
  try {
    if (key === "users" && arg) return await userDetail(main, decodeURIComponent(arg));
    main.innerHTML = head(title, sub, { filters: key !== "playground", team: !["knowledge", "audit"].includes(key) });
    wireFilters();
    await fn(main);
  } catch (err) {
    main.insertAdjacentHTML("beforeend", `<div class="card"><p class="error">Could not load this page: ${esc(err.message)}</p>
      <p class="muted">Check that agent-api can reach Atlas (MONGODB_URI, Network Access list): docker compose logs agent-api</p></div>`);
  }
}

function login(message = "") {
  $app.innerHTML = `<div class="login"><section class="card">
    <h1>Repo Agent Console</h1><p>Sign in with an admin token from <code>config/users.yaml</code>.</p>
    <form id="lf"><input type="password" name="token" placeholder="Admin token" autocomplete="current-password" required autofocus>
      <button class="btn primary">Sign in</button><p class="error" id="lerr">${esc(message)}</p></form></section></div>`;
  document.getElementById("lf").onsubmit = async (e) => {
    e.preventDefault();
    token = new FormData(e.target).get("token").trim();
    try {
      const res = await fetch("/api/admin/me", { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(res.status === 403 ? "That token is not an admin token." : "Invalid token.");
      store.set(TOKEN_KEY, token);
      me = await res.json();
      shell();
      route();
    } catch (err) { token = null; document.getElementById("lerr").textContent = err.message; }
  };
}

function logout() { store.del(TOKEN_KEY); token = null; me = null; login(); }

const savedTheme = store.get("repoAgentTheme", null);
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
window.addEventListener("hashchange", route);
route();
