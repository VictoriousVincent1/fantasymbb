const $ = (id) => document.getElementById(id);
// connection: what the visitor entered to connect, plus the server's session token.
// It lives only in this browser: localStorage when remembered, sessionStorage otherwise.
const state = { league: null, connection: null };
const CONNECTION_KEY = "coach-connection";

const esc = (value) => String(value).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function storages() {
  const found = [];
  try { found.push(localStorage, sessionStorage); } catch (e) { /* storage blocked */ }
  return found;
}

function loadConnection() {
  for (const store of storages()) {
    try {
      const saved = JSON.parse(store.getItem(CONNECTION_KEY) || "null");
      if (saved && saved.league) return { ...saved, remember: store === storages()[0] };
    } catch (e) { /* ignore unreadable storage */ }
  }
  return null;
}

function saveConnection(connection) {
  state.connection = connection;
  storages().forEach((store) => { try { store.removeItem(CONNECTION_KEY); } catch (e) { /* optional */ } });
  if (!connection) return;
  const store = storages()[connection.remember ? 0 : 1];
  const { remember, ...kept } = connection;
  try { if (store) store.setItem(CONNECTION_KEY, JSON.stringify(kept)); } catch (e) { /* kept in memory only */ }
}

const cookiesOf = (c) => ({ espn_s2: c.espn_s2 || null, swid: c.swid || null });

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.connection && state.connection.session) headers["X-League-Session"] = state.connection.session;
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || "Something went wrong. Try again.");
    error.status = response.status;
    throw error;
  }
  return body;
}

let reconnecting = null;

// The server keeps connected leagues in memory, so after a restart it asks the browser to reconnect.
function reconnect() {
  if (!reconnecting) {
    const c = state.connection;
    reconnecting = request("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ league: String(c.league), ...cookiesOf(c) }),
    }).then((result) => { saveConnection({ ...state.connection, session: result.session }); })
      .finally(() => { reconnecting = null; });
  }
  return reconnecting;
}

async function api(path, options) {
  try {
    return await request(path, options);
  } catch (error) {
    if (error.status !== 410 || !state.connection) throw error;
    try {
      await reconnect();
    } catch (failure) {
      connectionLost(failure.message);
      throw failure;
    }
    return request(path, options);
  }
}

const teamId = () => Number($("team").value);
const windowName = () => $("window").value;

function fillSelect(select, options, first) {
  select.innerHTML = (first ? `<option value="">${esc(first)}</option>` : "") +
    options.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
}

function otherTeams() {
  return state.league.teams.filter((t) => t.id !== teamId()).map((t) => ({ value: t.id, label: t.name }));
}

function statusText(p) {
  if (p.on_ir) return "IR";
  if (["ACTIVE", "NORMAL", ""].includes(p.injury_status)) return "";
  return p.injury_status.replace(/_/g, " ").toLowerCase();
}

const signed = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(2);

function renderTeam(report) {
  const labelClass = { strength: "strength", weakness: "weakness", "likely punt": "punt" };
  const punts = report.punts.length
    ? `Likely punts: <strong>${report.punts.map(esc).join(", ")}</strong>. You're far behind here, so it may be cheaper to give up on them and trade for other categories.`
    : "No clear punts: you aren't far behind the league in any category.";
  const zCategories = report.categories.map((c) => c.category);
  $("team-view").innerHTML = `
    <div class="stat">${report.expected_wins.toFixed(2)} <span class="muted" style="font-size:15px;font-weight:400">of ${report.category_count} categories</span></div>
    <p class="muted" style="margin-top:2px">Expected category wins in a matchup with an average opponent. Record: ${esc(report.team.record)}.</p>
    <p>${punts}</p>
    <h2>Categories</h2>
    <div class="scroll"><table>
      <thead><tr><th>Category</th><th>Your team</th><th>League avg</th><th>Rank of ${report.team_count}</th><th>Status</th><th class="wrap">What it means</th></tr></thead>
      <tbody>${report.categories.map((c) => `<tr>
        <td><strong>${esc(c.category)}</strong></td><td>${esc(c.value)}</td><td>${esc(c.league_average)}</td><td>${c.rank}</td>
        <td><span class="chip ${labelClass[c.label] || ""}">${esc(c.label)}</span></td>
        <td class="wrap">${esc(c.help)}</td></tr>`).join("")}</tbody>
    </table></div>
    <h2>Roster</h2>
    <p class="muted">Value adds up how far each player is above or below the average rostered player, by category. Zero is average.</p>
    <div class="scroll"><table>
      <thead><tr><th>Player</th><th>Pos</th><th>GP</th><th>Value</th>${zCategories.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${report.roster.map((p) => `<tr>
        <td>${esc(p.name)} ${statusText(p) ? `<span class="chip">${esc(statusText(p))}</span>` : ""}</td>
        <td>${esc(p.position)}</td><td>${p.games}</td><td><strong>${p.value.toFixed(2)}</strong></td>
        ${zCategories.map((c) => `<td>${p.z[c].toFixed(1)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>`;
}

function dataTable(table) {
  const tone = (cell) => (/^\+\d*\.?\d*[1-9]/.test(cell) ? "up" : /^[−-]\d*\.?\d*[1-9]/.test(cell) ? "down" : "");
  return `<details><summary>${esc(table.title)}</summary><div class="scroll"><table>
    <thead><tr>${table.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${table.rows.map((row) => `<tr>${row.map((cell, i) => `<td class="${i === row.length - 1 ? tone(String(cell)) : ""}">${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div></details>`;
}

function playerList(players) {
  const label = state.league.scoring_mode === "points" ? "pts/game" : "value";
  const digits = state.league.scoring_mode === "points" ? 1 : 2;
  return `<ul>${players.map((p) => `<li>${esc(p.name)} <span class="muted">${esc(p.position)}, ${label} ${p.value.toFixed(digits)}${statusText(p) ? ", " + esc(statusText(p)) : ""}</span></li>`).join("")}</ul>`;
}

function tradeCard(t, header) {
  const change = t.headline.change;
  return `<article class="card${header ? " " + header.className : ""}">
    ${header ? header.html : `<h3>${esc(t.verdict)} <span class="muted">with ${esc(t.opponent.name)}</span></h3>`}
    <div class="deal">
      <div><h4>YOU GIVE</h4>${playerList(t.give)}</div>
      <div><h4>YOU GET</h4>${playerList(t.get)}</div>
    </div>
    <p><strong class="${change > 0 ? "up" : change < 0 ? "down" : ""}">${esc(t.headline.text)}</strong> ${esc(t.summary)}</p>
    <p>${esc(t.why_you)} ${esc(t.cost)}</p>
    <p><strong>Why they might accept:</strong> ${esc(t.why_them)}</p>
    <div class="lesson"><strong>Lesson: ${esc(t.lesson.title)}</strong>${esc(t.lesson.body)} ${esc(t.lesson.detail)}</div>
    ${t.risks.length ? `<strong>Risks</strong><ul class="risks">${t.risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    ${t.tables.map(dataTable).join("")}
  </article>`;
}

let teamRequest = 0;
let ideasRequest = 0;

function renderPointsTeam(r) {
  const trend = { hot: "hot", cold: "cold" };
  $("team-view").innerHTML = `
    <div class="stat">${r.weekly.toLocaleString()} <span class="muted" style="font-size:15px;font-weight:400">projected points per week, rank ${r.rank} of ${r.team_count}</span></div>
    <p class="muted" style="margin-top:2px">Counts only players likely to fill your ${r.starting_slots} starting slots each day. League average: ${r.league_average.toLocaleString()}. Record: ${esc(r.team.record)}.</p>
    <h2>Your league's scoring</h2>
    <p>${r.rules.map(esc).join(" · ")}</p>
    <p class="muted">${esc(r.rules_note)}</p>
    <h2>Where your points come from</h2>
    <div class="scroll"><table>
      <thead><tr><th>Stat</th><th>Points each</th><th>Your team</th><th>League avg</th><th>Rank of ${r.team_count}</th></tr></thead>
      <tbody>${r.sources.map((s) => `<tr><td>${esc(s.stat)}</td><td>${esc(s.points_each)}</td><td>${esc(s.yours)}</td><td>${esc(s.league_average)}</td><td>${s.rank}</td></tr>`).join("")}</tbody>
    </table></div>
    <h2>Roster</h2>
    <p class="muted">Above replacement compares each player with a typical free agent, who averages about ${r.replacement.toFixed(1)} points per game in this league.</p>
    <div class="scroll"><table>
      <thead><tr><th>Player</th><th>Pos</th><th>GP</th><th>Pts/game</th><th>Season</th><th>Last 30 days</th><th>Trend</th><th>Above replacement</th></tr></thead>
      <tbody>${r.roster.map((p) => `<tr>
        <td>${esc(p.name)} ${statusText(p) ? `<span class="chip">${esc(statusText(p))}</span>` : ""}</td>
        <td>${esc(p.position)}</td><td>${p.games}</td><td><strong>${p.value.toFixed(1)}</strong></td>
        <td>${p.season_fppg === null ? "–" : p.season_fppg.toFixed(1)}</td>
        <td>${p.last30_fppg === null ? "–" : p.last30_fppg.toFixed(1)}</td>
        <td>${p.trend ? `<span class="chip ${p.trend === "hot" ? "strength" : "weakness"}">${trend[p.trend]}</span>` : ""}</td>
        <td>${p.above_replacement.toFixed(1)}</td></tr>`).join("")}</tbody>
    </table></div>`;
}

const when = (ms) => new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function offerCard(o) {
  const title = o.incoming ? `Offer from ${esc(o.from_team)}` : `Your offer to ${esc(o.analysis ? o.analysis.opponent.name : "another team")}`;
  const times = `Proposed ${when(o.proposed_ms)}${o.expires_ms ? ` · expires ${when(o.expires_ms)}` : ""}`;
  const notes = o.notes.map((n) => `<p class="muted">${esc(n)}</p>`).join("");
  if (!o.analysis) {
    return `<article class="card offer"><h3>${title}</h3><p class="muted">${times}</p>${notes}<p>${esc(o.problem)}</p></article>`;
  }
  const html = `<div class="offer-head"><div><h3>${title}</h3><span class="muted">${times}</span></div>
    <span class="advice ${o.advice.key}">${esc(o.advice.text)}</span></div>${notes}`;
  return tradeCard(o.analysis, { html, className: `offer ${o.advice.key}` });
}

let offersRequest = 0;

async function loadOffers() {
  const request = ++offersRequest;
  $("offers").innerHTML = '<p class="muted" role="status">Checking offers...</p>';
  try {
    const result = await api(`/api/offers?team_id=${teamId()}&window=${windowName()}`);
    if (request !== offersRequest) return;
    $("offers-updated").textContent = result.source === "demo"
      ? "These are example offers in the demo league."
      : `Offers as of ${new Date(result.fetched_at).toLocaleString()}; click Update from ESPN for the latest.`;
    const section = (title, items, empty) => `<h2>${title}</h2>` +
      (items.length ? items.map(offerCard).join("") : `<div class="empty">${empty}</div>`);
    $("offers").innerHTML = (result.error ? `<p class="error" role="alert">${esc(result.error)}</p>` : "") +
      section("Offers to you", result.incoming, "No pending offers to your team.") +
      section("Offers you sent", result.sent, "You have no pending offers out.");
  } catch (error) {
    if (request !== offersRequest) return;
    $("offers").innerHTML = `<p class="error" role="alert">${esc(error.message)}</p>`;
  }
}

async function loadTeam() {
  const request = ++teamRequest;
  $("team-view").innerHTML = '<p class="muted" role="status">Loading your team...</p>';
  try {
    const report = await api(`/api/team/${teamId()}?window=${windowName()}`);
    if (request !== teamRequest) return;
    if (report.mode === "points") renderPointsTeam(report); else renderTeam(report);
  } catch (error) {
    if (request !== teamRequest) return;
    $("team-view").innerHTML = `<p class="error" role="alert">${esc(error.message)}</p>`;
  }
}

async function loadIdeas() {
  const request = ++ideasRequest;
  $("ideas").innerHTML = "";
  $("ideas-count").textContent = "Searching trades across the league. This can take a few seconds...";
  const params = new URLSearchParams({ team_id: teamId(), window: windowName() });
  if ($("opponent").value) params.set("opponent_id", $("opponent").value);
  if ($("need").value && state.league.scoring_mode !== "points") params.set("need", $("need").value);
  try {
    const result = await api(`/api/trades?${params}`);
    if (request !== ideasRequest) return; // a newer search has replaced this one
    const shown = result.trades.length;
    $("ideas-count").textContent = shown
      ? `Showing ${shown} of ${result.matching} matching ideas, best for you first.`
      : "";
    $("ideas").innerHTML = shown
      ? result.trades.map(tradeCard).join("")
      : '<div class="empty">No trades here clearly help both teams. Try a different opponent or category.</div>';
  } catch (error) {
    if (request !== ideasRequest) return;
    $("ideas-count").textContent = "";
    $("ideas").innerHTML = `<p class="error" role="alert">${esc(error.message)}</p>`;
  }
}

function picker(container, players, name) {
  container.innerHTML = players.map((p) => `<label><input type="checkbox" name="${name}" value="${p.id}">
    ${esc(p.name)} <span class="muted">${esc(p.position)}</span></label>`).join("");
}

function setupCheck() {
  const me = state.league.teams.find((t) => t.id === teamId());
  fillSelect($("check-opponent"), otherTeams());
  picker($("check-give"), me.players, "give");
  updateCheckOpponent();
}

function updateCheckOpponent() {
  const them = state.league.teams.find((t) => t.id === Number($("check-opponent").value));
  picker($("check-get"), them ? them.players : [], "get");
  $("check-result").innerHTML = "";
  $("check-error").hidden = true;
}

const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => Number(i.value));

async function submitCheck(event) {
  event.preventDefault();
  $("check-error").hidden = true;
  $("check-result").innerHTML = "";
  try {
    const result = await api("/api/trade-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: teamId(), opponent_id: Number($("check-opponent").value),
        give: checked("give"), get: checked("get"), window: windowName(),
      }),
    });
    $("check-result").innerHTML = tradeCard(result);
  } catch (error) {
    $("check-error").textContent = error.message;
    $("check-error").hidden = false;
  }
}

function activeTab() {
  return document.querySelector('nav button[aria-selected="true"]').dataset.tab;
}

function refreshView() {
  if (activeTab() === "team-view") loadTeam();
  if (activeTab() === "ideas-view") loadIdeas();
  if (activeTab() === "offers-view") loadOffers();
}

function showTab(tab) {
  document.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  ["team-view", "ideas-view", "offers-view", "check-view"].forEach((id) => { $(id).hidden = id !== tab; });
  refreshView();
}

// Remembered per league, so switching from the demo to a real league doesn't carry a pick over.
const teamKey = () => `coach-team:${state.league.source}:${state.league.name}:${state.league.year}`;

function teamChanged() {
  try { localStorage.setItem(teamKey(), $("team").value); } catch (e) { /* storage is optional */ }
  fillSelect($("opponent"), otherTeams(), "All teams");
  setupCheck();
  refreshView();
}

function updatedText(league) {
  if (league.source === "demo") {
    return league.public
      ? "You're exploring a demo league of made-up players. Click Connect your league to coach your real team."
      : league.note;
  }
  return `Data from ${new Date(league.fetched_at).toLocaleString()}.`;
}

// Connecting a league
function openConnect(message, openPrivate) {
  const c = state.connection;
  $("connect-league").value = c ? c.league : "";
  $("connect-s2").value = c && c.espn_s2 ? c.espn_s2 : "";
  $("connect-swid").value = c && c.swid ? c.swid : "";
  $("connect-remember").checked = c ? c.remember !== false : true;
  $("private-fields").open = Boolean(openPrivate || (c && c.espn_s2));
  $("connect-error").textContent = message || "";
  $("connect-error").hidden = !message;
  $("connect-dialog").showModal();
  $("connect-league").focus();
}

async function submitConnect(event) {
  event.preventDefault();
  const connection = {
    league: $("connect-league").value.trim(),
    espn_s2: $("connect-s2").value.trim(),
    swid: $("connect-swid").value.trim(),
    remember: $("connect-remember").checked,
  };
  if (!connection.league) {
    $("connect-error").textContent = "Enter your league ID or league page address.";
    $("connect-error").hidden = false;
    return;
  }
  $("connect-submit").disabled = true;
  $("connect-submit").textContent = "Connecting...";
  $("connect-error").hidden = true;
  try {
    const result = await request("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ league: connection.league, ...cookiesOf(connection) }),
    });
    saveConnection({ ...connection, session: result.session });
    $("connect-dialog").close();
    await $("connect").addEventListener("click", () => openConnect());
$("disconnect").addEventListener("click", disconnect);
$("connect-form").addEventListener("submit", submitConnect);
$("connect-cancel").addEventListener("click", () => $("connect-dialog").close());

state.connection = loadConnection();
if (state.connection && !state.connection.session) state.connection.session = "expired"; // forces a reconnect
init();
  } catch (error) {
    $("connect-error").textContent = error.message;
    $("connect-error").hidden = false;
    if (error.status === 403) $("private-fields").open = true;
  } finally {
    $("connect-submit").disabled = false;
    $("connect-submit").textContent = "Connect";
  }
}

// Reconnecting failed, usually because ESPN cookies expired: fall back to the starting league.
function connectionLost(message) {
  const kept = state.connection;
  state.connection = kept ? { ...kept, session: null } : null;
  init().then(() => openConnect(`Couldn't reconnect your league. ${message}`, true));
}

async function disconnect() {
  const old = state.connection;
  if (old && old.session) {
    await request("/api/disconnect", { method: "POST" }).catch(() => {});
  }
  saveConnection(null);
  await $("connect").addEventListener("click", () => openConnect());
$("disconnect").addEventListener("click", disconnect);
$("connect-form").addEventListener("submit", submitConnect);
$("connect-cancel").addEventListener("click", () => $("connect-dialog").close());

state.connection = loadConnection();
if (state.connection && !state.connection.session) state.connection.session = "expired"; // forces a reconnect
init();
}

async function init() {
  try {
    const league = await api("/api/league");
    state.league = league;
    const leagueName = String(league.name) === String(league.year) ? "ESPN league" : league.name;
    $("league-name").textContent = `${leagueName}, ${league.year - 1}-${String(league.year).slice(2)} season`;
    const notices = [league.warning, updatedText(league)].filter(Boolean);
    $("banner").textContent = notices.join(" ");
    $("banner").hidden = !notices.length;
    $("refresh").hidden = !league.can_refresh;
    $("connect").textContent = league.connected ? "Change league" : "Connect your league";
    $("connect").classList.toggle("primary", !league.connected);
    $("disconnect").hidden = !league.connected;
    fillSelect($("team"), league.teams.map((t) => ({ value: t.id, label: t.name })));
    fillSelect($("need"), league.categories.map((c) => ({ value: c, label: c })), "Any");
    $("need").closest("label").hidden = league.scoring_mode === "points";
    let saved = null;
    try { saved = localStorage.getItem(teamKey()); } catch (e) { /* storage is optional */ }
    const preferred = [saved, league.default_team_id].find((id) => id && league.teams.some((t) => String(t.id) === String(id)));
    if (preferred) $("team").value = preferred;
    teamChanged();
  } catch (error) {
    $("team-view").innerHTML = `<p class="error" role="alert">${esc(error.message)}</p>`;
  }
}

$("team").addEventListener("change", teamChanged);
$("window").addEventListener("change", refreshView);
$("opponent").addEventListener("change", loadIdeas);
$("need").addEventListener("change", loadIdeas);
$("check-opponent").addEventListener("change", updateCheckOpponent);
$("check-form").addEventListener("submit", submitCheck);
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("refresh").addEventListener("click", async () => {
  $("refresh").disabled = true;
  try {
    const options = { method: "POST" };
    if (state.connection && state.connection.session) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(cookiesOf(state.connection));
    }
    await api("/api/refresh", options);
    await $("connect").addEventListener("click", () => openConnect());
$("disconnect").addEventListener("click", disconnect);
$("connect-form").addEventListener("submit", submitConnect);
$("connect-cancel").addEventListener("click", () => $("connect-dialog").close());

state.connection = loadConnection();
if (state.connection && !state.connection.session) state.connection.session = "expired"; // forces a reconnect
init();
  } catch (error) {
    $("banner").textContent = error.message;
    $("banner").hidden = false;
  }
  $("refresh").disabled = false;
});
$("connect").addEventListener("click", () => openConnect());
$("disconnect").addEventListener("click", disconnect);
$("connect-form").addEventListener("submit", submitConnect);
$("connect-cancel").addEventListener("click", () => $("connect-dialog").close());

state.connection = loadConnection();
if (state.connection && !state.connection.session) state.connection.session = "expired"; // forces a reconnect
init();
