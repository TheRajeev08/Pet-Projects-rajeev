import {
    LANES, SOURCE_NAMES, accountPlays, accountStatus, adoptionTotals, dataCaveats, getQueue, hasSources,
    hasSurfaceSignal, integrityIssues, isNewMetric, queueByLane, riskProfile, summarize, validateModel,
} from "./insights.mjs";
import {
    ENABLEMENT_LABELS, PHASE_ORDER, adoptionFacts, enablementKnown, enterpriseRows, engagementTrend,
    impactOf, impactState, multipliers, phases, portfolioImpact, productivity, roi, surfaceAdoption,
    surfaceRows, totalEngagedUsers,
} from "./impact.mjs";

const app = document.querySelector("#app");
const embedded = new URLSearchParams(location.search).get("embed") === "1";
document.body.classList.toggle("embedded", embedded);
const notices = document.querySelector("#notices");
const navigation = document.querySelector("#navigation");
const freshness = document.querySelector("#freshness");
const refreshButton = document.querySelector("#refresh-button");
const token = document.querySelector('meta[name="csrf-token"]').content;
const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const moneyFormat = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const isNumber = value => typeof value === "number" && Number.isFinite(value);
const H = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const num = value => isNumber(value) ? numberFormat.format(value) : "&mdash;";
const money = value => isNumber(value) ? moneyFormat.format(value) : "&mdash;";
const pct = (value, decimals = 0) => isNumber(value) ? `${(value * 100).toFixed(decimals)}%` : "&mdash;";
const label = value => value === null || value === undefined || value === "" ? "&mdash;" : H(value);
const short = value => !isNumber(value) ? "&mdash;" : Math.abs(value) >= 1e6
    ? `${(value / 1e6).toFixed(1)}m` : Math.abs(value) >= 1e3
        ? `${(value / 1e3).toFixed(value < 1e4 ? 1 : 0)}k` : num(value);
const dates = value => {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return label(value);
    const parsed = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) return H(value);
    return H(new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
        .format(parsed));
};
const dayAge = day => {
    const now = new Date();
    return Math.round((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) -
        Date.parse(`${day}T00:00:00Z`)) / 86400000);
};
const STATUS_LABELS = {
    active: "Active this week",
    inactive: "Inactive this week",
    whitespace: "No Copilot signal",
    unlinked: "Usage not linked",
    sibling: "Usage on sibling",
    unknown: "Needs verification",
};
const LANE_LABELS = Object.fromEntries(LANES.map(lane => [lane.id, lane.label]));
const DESK_LIMITS = { verify: 6, now: 8, watch: 8, grow: 6 };

const state = {
    model: null, revision: null, loading: true, loadError: null, eventWarning: null,
    view: "desk", selectedId: null, previousView: "desk",
    refresh: { phase: "idle" },
    filters: { q: "", status: "all", lane: "all", health: "all", region: "all", surface: "all",
        surfaceWindow: "28d", model: "all", sort: "queue", ascending: true },
    page: 0, changeSeverity: "all", changeKind: "all", changeQ: "",
    selectedSlug: null, impactQ: "",
    pulseLicenses: null, pulseLicensesError: null,
};
let loadingModel;
let chartId = 0;
let queueCache = null;
let nameCounts = null;

function queue() {
    if (!queueCache) queueCache = getQueue(state.model);
    return queueCache;
}

function queueIndex() {
    if (!state.queueOrder) {
        state.queueOrder = new Map(queue().map((item, index) => [item.account.id, index]));
    }
    return state.queueOrder;
}

/** Salesforce legitimately holds several records with the same name; make the rows distinguishable. */
function disambiguate(account) {
    if (!nameCounts) {
        nameCounts = new Map();
        for (const item of state.model.accounts) {
            nameCounts.set(item.name, (nameCounts.get(item.name) || 0) + 1);
        }
    }
    if ((nameCounts.get(account.name) || 0) < 2) return "";
    const hint = [account.parent && `child of ${account.parent}`, account.segment, account.country]
        .filter(Boolean)[0];
    return `${hint ? `${hint} · ` : ""}ID …${String(account.id).slice(-6)}`;
}

function accountSubtitle(account) {
    const duplicate = disambiguate(account);
    const base = [account.segment, account.region].filter(Boolean).join(" / ");
    return duplicate || base || "Account detail";
}

function sourceNote(ids) {
    return [...new Set(ids)].map(id => {
        const asof = state.model.asof?.[id];
        return `${SOURCE_NAMES[id] || `Source ${id}`}${asof ? ` as of ${asof}` : ""}`;
    }).join(" · ");
}

function dataValue(account, key, ...sources) {
    return hasSources(state.model, ...sources) ? account[key] : null;
}

function metricName(key, text) {
    return H(text) + (isNewMetric(state.model, key) ? ' <span class="new-metric">New</span>' : "");
}

function adoptionFunnel(account = null) {
    const m = state.model;
    const totals = adoptionTotals(m);
    const value = (key, qid) => !hasSources(m, qid) ? null : account
        ? account[key] : totals[key].value;
    const steps = [["assigned", "Assigned", "06"], ["active_28d", "Active 28d", "03"], ["engaged_1d28", "Engaged 28d", "02"]];
    return `<div class="fact-strip fact-strip-three adoption-funnel" aria-label="Adoption funnel">${steps.map(([key, text, qid]) =>
        `<div class="fact"><small>${metricName(key, text)}</small><strong>${num(value(key, qid))}</strong>`
        + (!account ? `<span class="meta">${totals[key].n} of ${totals[key].of} reporting</span>` : "") + "</div>").join("")}</div>` +
        (account ? `<p class="meta">Installed IDE users 28d (partial telemetry): ${num(dataValue(account, "installed_1d28", "02"))}. Not a funnel step.</p>` : "") +
        (!account ? `<p class="meta">Dashboard source. Portfolio uses Pulse's dated license figures, so Licenses and Assigned can differ for a few accounts.</p>` : "") +
        `<p class="meta">Assigned &rarr; active &rarr; engaged. Source cutoffs differ; a dash means not reported.</p>`;
}

function forecastWhitespace(account) {
    const value = (key, qid) => dataValue(account, key, qid);
    const bill = value("forecast_bill", "16");
    const facts16 = [
        ["forecast_bill", "Month-end forecast", money(bill)],
        ["forecast_bill_low", "Forecast range", bill > 0 ? `${money(value("forecast_bill_low", "16"))} &ndash; ${money(value("forecast_bill_high", "16"))}` : "&mdash;"],
        ["included_pool", "Included pool", money(value("included_pool", "16"))],
        ["forecast_pool_ratio", "Forecast / pool", pct(value("forecast_pool_ratio", "16"))],
        ["forecast_above_pool", "Projected above included pool", money(value("forecast_above_pool", "16"))],
        ["entitlement_status", "Entitlement status", label(value("entitlement_status", "16"))],
        ["utilization_band", "Utilization band", label(value("utilization_band", "16"))],
        ["ubb_insight", "Insight", label(value("ubb_insight", "16"))],
        ["ubb_adoption_band", "UBB adoption band", label(value("ubb_adoption_band", "16"))],
        ["ubb_adoption_rate", "UBB adoption rate", pct(value("ubb_adoption_rate", "16"))],
        ["ubb_week_users", "Users with UBB activity", num(value("ubb_week_users", "16"))],
        ["ubb_license_users", "Licensed users", num(value("ubb_license_users", "16"))],
    ];
    const rejected = account.ws_estimate_source === "rejected_as_implausible";
    const wsValue = key => rejected ? null : value(key, "17");
    const facts17 = [
        ["ws_potential_seats", "Potential seats", num(wsValue("ws_potential_seats"))],
        ["ws_whitespace_seats", "Whitespace seats", num(wsValue("ws_whitespace_seats"))],
        ["ws_som_annual", "Annual SOM", money(wsValue("ws_som_annual"))],
        ["ws_play", "Recommended play", label(value("ws_play", "17"))],
    ];
    const rows = entries => `<dl class="facts">${entries.map(([key, text, rendered]) =>
        `<div><dt>${metricName(key, text)}</dt><dd>${rendered}</dd></div>`).join("")}</dl>`;
    return `<section class="detail-section" id="account-forecast"><h2>Forecast &amp; whitespace</h2>` +
        `<p class="meta">${H(sourceNote(["16"]))}. Projected above included pool is not invoiced overage.</p>${rows(facts16)}` +
        `<h3>Model estimate &middot; parent-level</h3><p class="meta">${H(sourceNote(["17"]))}. Direct owned-account matches only; parent-level estimates are not reconciled enterprise totals.</p>` +
        (rejected ? '<p class="inline-note warning">Implausible seat estimate withheld.</p>' : "") +
        rows(facts17) + `<p>${label(value("ws_play_reason", "17"))}</p><p>${label(value("ws_play_context", "17"))}</p></section>`;
}

function portfolioForecast() {
    const m = state.model, p = m.portfolio;
    return `<section class="detail-section"><h2>Forecast &amp; whitespace</h2>` +
        `<div class="details-grid">${["entitlement_status", "ubb_insight"].map(key =>
            `<div><h3>${metricName(key, key === "entitlement_status" ? "Entitlement status" : "Insight")}</h3>` +
            (hasSources(m, "16") ? detailTable(["Category", "Accounts"], Object.entries(p[`${key}_counts`] || {})
                .map(([text, count]) => [H(text), num(count)]), "No forecast categories reported") : "<p>&mdash;</p>") + "</div>").join("")}</div>` +
        `<h3>Model estimate &middot; parent-level</h3><p class="meta">Direct owned-account matches; sums of model estimates, not reconciled enterprise totals.</p>` +
        `<div class="fact-strip fact-strip-three">${[["ws_potential_seats", "Potential seats"], ["ws_whitespace_seats", "Whitespace seats"], ["ws_som_annual", "Annual SOM"]].map(([key, text]) =>
            `<div class="fact"><small>${metricName(key, text)}</small><strong>${(key === "ws_som_annual" ? money : num)(hasSources(m, "17") ? p[`total_${key}`] : null)}</strong></div>`).join("")}</div></section>`;
}

function empty(title, body, action = "") {
    return `<div class="empty"><strong>${H(title)}</strong><p>${H(body)}</p>${action}</div>`;
}

function laneTag(lane) {
    return `<span class="play-tag ${H(lane)}">${H(LANE_LABELS[lane] || lane)}</span>`;
}

function modelAgeMessage() {
    if (!state.model) return "";
    const age = dayAge(state.model.data_date);
    if (age < 0) return "The saved model is dated in the future; verify the refresh clock.";
    return age > 1 ? `The saved model is ${age} days old. Use Refresh live for newer source data.` : "";
}

function renderHeader() {
    const m = state.model;
    const old = m && dayAge(m.data_date) > 1;
    freshness.textContent = m ? `Snapshot ${m.data_date}${old ? " (stale)" : ""}` +
        `${m.asof?.["03"] ? ` · activity through ${m.asof["03"]}` : ""}` :
        "No readable local snapshot";
    freshness.classList.toggle("is-stale", Boolean(old));
    refreshButton.disabled = ["launching", "requested"].includes(state.refresh.phase);
    refreshButton.textContent = state.refresh.phase === "launching" ? "Requesting..." :
        state.refresh.phase === "requested" ? "Refresh requested" : "Refresh live";
    navigation.querySelectorAll("[data-view]").forEach(button => {
        if (button.dataset.view === state.view) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    });
}

/** Enterprise sources are collected incrementally; the builder reuses the last result rather than
 *  publishing a model with no enterprises. Say so, rather than showing carried data as today's. */
function staleSourceNote(model) {
    const carried = model?.stale_sources;
    if (!carried || typeof carried !== "object") return "";
    const ids = Object.keys(carried).sort();
    if (!ids.length) return "";
    const days = [...new Set(ids.map(id => carried[id]))].sort();
    const names = ids.map(id => SOURCE_NAMES[id] || id).join(", ");
    const when = days.length === 1 ? dates(days[0]) : `${dates(days[0])}–${dates(days[days.length - 1])}`;
    return `Enterprise data carried forward from ${when}: ${names} were not re-collected in today's refresh. ` +
        `Account figures are current; each enterprise panel shows its own collection date.`;
}

function renderNotices() {
    const messages = [];
    if (state.loadError && state.model) {
        messages.push({ tone: "error", text: `Could not load the updated model; showing the prior snapshot. ${state.loadError}` });
    }
    if (state.model?.missing_sources?.length) {
        messages.push({ tone: "warning", text: `Missing sources: ${state.model.missing_sources.map(id => SOURCE_NAMES[id] || id).join(", ")}. Related totals and suggested plays are withheld.` });
    }
    const carried = staleSourceNote(state.model);
    if (carried) messages.push({ tone: "warning", text: carried });
    const ageMessage = modelAgeMessage();
    if (ageMessage) messages.push({ tone: "warning", text: ageMessage });
    if (["launching", "requested", "updated", "error"].includes(state.refresh.phase)) {
        messages.push({ tone: state.refresh.phase === "error" ? "error" :
            state.refresh.phase === "updated" ? "success" : "",
        text: state.refresh.message || "Check the Copilot refresh automation." });
    }
    if (state.eventWarning) messages.push({ tone: "warning", text: state.eventWarning });
    notices.innerHTML = messages.map(item =>
        `<div class="notice ${item.tone}"><span>${H(item.text)}</span></div>`).join("");
}

function formatAxis(value, kind) {
    return kind === "money" ? `$${short(value)}` : kind === "percent" ? pct(value) : short(value);
}

function plot(series, kind = "number", type = "line", title = "Trend", labels = null) {
    const count = Math.max(0, ...series.map(item => item.values?.length || 0));
    const values = series.flatMap(item => (item.values || []).filter(isNumber));
    if (!count || !values.length) return empty("No recorded trend", "The selected data source has no weekly values.");
    // Values arrive latest-first and are reversed to chronological order below, so a supplied
    // label list (already chronological) maps straight onto the rendered index.
    const axisLabel = index => labels
        ? (labels[index] ?? "")
        : index === count - 1 ? "now" : `-${count - 1 - index}w`;
    const W = 620, height = 215, left = 48, right = 12, top = 14, bottom = 27;
    const graphW = W - left - right, graphH = height - top - bottom;
    const maxValue = Math.max(...values.map(Math.abs), 1);
    const magnitude = Math.pow(10, Math.floor(Math.log10(maxValue)));
    const max = Math.ceil(maxValue / magnitude) * magnitude;
    const x = index => left + (type === "bar" ? (index + .5) * graphW / count :
        count > 1 ? index * graphW / (count - 1) : graphW / 2);
    const y = value => top + graphH * (1 - Math.max(0, value) / max);
    const palette = ["var(--accent)", "var(--positive)", "var(--caution)", "var(--negative)"];
    const id = `trend-${++chartId}`;
    let lines = "";
    for (let tick = 0; tick <= 3; tick++) {
        const value = max * (1 - tick / 3);
        const yy = top + graphH * tick / 3;
        lines += `<line class="grid" x1="${left}" x2="${W - right}" y1="${yy}" y2="${yy}"/>` +
            `<text x="${left - 6}" y="${yy + 3}" text-anchor="end">${formatAxis(value, kind)}</text>`;
    }
    for (let index = 0; index < count; index++) {
        if (index === 0 || index === count - 1 || index % 4 === (count - 1) % 4 || labels) {
            lines += `<text x="${x(index).toFixed(1)}" y="${height - 5}" text-anchor="middle">` +
                `${H(axisLabel(index))}</text>`;
        }
    }
    series.forEach((item, seriesIndex) => {
        const color = palette[seriesIndex % palette.length];
        const points = (item.values || []).slice().reverse();
        if (type === "bar") {
            const width = Math.max(3, graphW / count * .76 / series.length);
            points.forEach((value, index) => {
                if (!isNumber(value)) return;
                const bx = x(index) - width * series.length / 2 + seriesIndex * width;
                lines += `<rect x="${bx.toFixed(1)}" y="${y(value).toFixed(1)}" width="${width.toFixed(1)}" ` +
                    `height="${Math.max(1, y(0) - y(value)).toFixed(1)}" fill="${color}">` +
                    `<title>${H(item.name)}: ${formatAxis(value, kind)}, ${labels ? H(axisLabel(index)) : `${count - 1 - index} weeks ago`}</title></rect>`;
            });
        } else {
            let current = [];
            const segments = [];
            points.forEach((value, index) => {
                if (isNumber(value)) current.push([x(index), y(value)]);
                else if (current.length) { segments.push(current); current = []; }
            });
            if (current.length) segments.push(current);
            for (const segment of segments) {
                lines += `<polyline class="line" stroke="${color}" points="${segment.map(point =>
                    point.map(coordinate => coordinate.toFixed(1)).join(",")).join(" ")}"/>`;
            }
            points.forEach((value, index) => {
                if (!isNumber(value)) return;
                lines += `<circle cx="${x(index).toFixed(1)}" cy="${y(value).toFixed(1)}" r="2.5" fill="${color}">` +
                    `<title>${H(item.name)}: ${formatAxis(value, kind)}, ${labels ? H(axisLabel(index)) : `${count - 1 - index} weeks ago`}</title></circle>`;
            });
        }
    });
    return `<svg class="plot" role="img" aria-labelledby="${id}" viewBox="0 0 ${W} ${height}">` +
        `<title id="${id}">${H(title)} over ${count} ${labels ? "periods" : "weeks"}, oldest on the left</title>${lines}</svg>` +
        `<div class="chart-legend">${series.map((item, index) =>
            `<span><i style="background:${palette[index % palette.length]}"></i>${H(item.name)}</span>`).join("")}</div>`;
}

function chartPanel(title, note, series, sources, kind = "number", type = "line") {
    const missing = sources.filter(id => state.model.missing_sources.includes(id));
    return `<div class="chart-panel"><h3>${H(title)}</h3><div class="meta">${H(note)}</div>` +
        (missing.length ? empty("Trend unavailable", `Missing ${missing.map(id => SOURCE_NAMES[id]).join(", ")}.`) :
            plot(series, kind, type, title)) + `</div>`;
}

function jump(status, extra = {}) {
    state.selectedId = null;
    state.view = "accounts";
    state.filters = { ...state.filters, q: "", status, lane: "all", health: "all",
        region: "all", surface: "all", surfaceWindow: "28d", model: "all",
        sort: "queue", ascending: true, ...extra };
    state.page = 0;
    history.replaceState(null, "", "#/accounts");
    render();
    app.focus({ preventScroll: true });
}

function queueRow(item, index) {
    const { account, play, scenario, integrity } = item;
    const others = scenario.slice(1);
    return `<button class="queue-row" type="button" data-account="${H(account.id)}">` +
        `<span class="queue-index">${String(index + 1).padStart(2, "0")}</span><span>` +
        `<span class="queue-title"><span class="account-name">${H(account.name)}</span>` +
        `<span class="queue-reason">${H(play.title)}</span></span>` +
        (disambiguate(account) ? `<span class="account-sub">${H(disambiguate(account))}</span>` : "") +
        `<span class="queue-evidence">${H(play.evidence[0] || "")}</span>` +
        `<span class="queue-action">${H(play.nextStep)}</span>` +
        (others.length || integrity.length
            ? `<span class="signal-chips">${others.map(signal => `<span class="chip">${H(signal)}</span>`).join("")}` +
              `${integrity.length ? `<span class="chip warn">contract data conflict</span>` : ""}</span>`
            : "") +
        `</span><span class="queue-chevron" aria-hidden="true">${laneTag(play.lane)}</span></button>`;
}

function renderDesk() {
    const m = state.model, p = m.portfolio, summary = summarize(m);
    const lanes = queueByLane(m, queue());
    const weekly = hasSources(m, "03") ? p.a7?.[0] : null;
    const prior = hasSources(m, "03") ? p.a7?.[1] : null;
    const caveats = dataCaveats(m);
    const matched = count => isNumber(count) ? num(count) : "&mdash;";
    const metrics = [
        ["Owned accounts", summary.owned, "All Salesforce-owned", "all"],
        ["Consuming", summary.consuming, "Any 90-day signal", "consuming"],
        ["Active accounts", summary.active, "Users or UBB in 7d", "active"],
        ["Weekly active users", summary.weeklyActiveUsers, "Latest seven days", "active"],
        ["Users with risk flags", summary.flaggedUsers, "Not an inactivity count", "risk"],
        ["UBB gross 7d", summary.gross7d, "Usage-side list value", "spend"],
    ];
    const laneSections = lanes.filter(lane => lane.items.length).map(lane => {
        const limit = DESK_LIMITS[lane.id] || 8;
        const shown = lane.items.slice(0, limit);
        return `<section class="lane lane-${H(lane.id)}" id="lane-${H(lane.id)}"><div class="lane-head">` +
            `<div><h2>${H(lane.label)} <span class="quiet">${num(lane.items.length)}</span></h2>` +
            `<p>${H(lane.blurb)}</p></div>` +
            (lane.items.length > shown.length
                ? `<button class="link-button" type="button" data-lane-all="${H(lane.id)}">View all ${num(lane.items.length)} &rarr;</button>`
                : "") +
            `</div><div class="queue">${shown.map(queueRow).join("")}</div></section>`;
    }).join("");
    return `<div class="page-head"><div><h1>Adoption desk</h1>` +
        `<p>Grouped by what the next step is, not by a score. Every signal an account matched stays on its row.</p></div>` +
        `<div class="meta"><strong>${num(summary.urgentAccounts)}</strong> to act on &middot; ` +
        `<strong>${num(summary.verifyAccounts)}</strong> to verify &middot; <strong>${num(summary.watchAccounts)}</strong> to watch</div></div>` +
        `<div class="snapshot-ribbon"><span><strong>Snapshot ${dates(m.data_date)}</strong> &middot; ` +
        `${num(summary.owned)} owned accounts &middot; built ${H(m.generated_at || "unknown")}</span>` +
        `<span>Activity through ${H(m.asof?.["03"] || "not reported")} &middot; ` +
        `UBB through ${H(m.asof?.["07"] || "not reported")} &middot; ` +
        `health through ${H(m.asof?.["06"] || "not reported")}</span>` +
        `<button class="link-button" type="button" data-view="notes">What is included?</button></div>` +
        `<div class="metric-strip">${metrics.map(([name, value, hint, target], index) =>
            `<button class="metric ${index === 2 ? "is-key" : ""}" type="button" data-jump="${target}" ` +
            `aria-label="Explore ${H(name)}"><span class="value">${target === "spend" ? money(value) : matched(value)}</span>` +
            `<span class="label">${H(name)}</span><span class="hint">${H(hint)}</span></button>`).join("")}</div>` +
        (caveats.length ? `<div class="inline-note warning desk-caveat"><strong>Read the data carefully.</strong><ul>` +
            caveats.map(text => `<li>${H(text)}</li>`).join("") + `</ul></div>` : "") +
        adoptionFunnel() + `<div class="desk-grid"><div>${laneSections || empty("No plays to show",
            "No account matched a rule in this snapshot. Check source coverage in Ask & data notes.")}</div>` +
        `<aside class="side-stack"><section class="side-section"><h2>Portfolio pulse</h2>` +
        `<p>Weekly active users across consuming accounts</p><div class="pulse-value"><strong>${num(weekly)}</strong>` +
        `${isNumber(weekly) && isNumber(prior) ? (weekly === prior ? `<span class="delta flat">No change WoW</span>` :
            `<span class="delta ${weekly > prior ? "good" : "bad"}">${weekly > prior ? "+" : "\u2212"}${num(Math.abs(weekly - prior))} WoW</span>`) : ""}</div>` +
        (hasSources(m, "03") ? plot([{ name: "Weekly active users", values: p.a7 || [] }], "number", "line", "Portfolio weekly active users") :
            empty("Activity source missing", "The weekly active trend is withheld until the source refreshes.")) +
        `<button class="link-button" type="button" data-view="trends">Explore 13-week trends &rarr;</button></section>` +
        `<section class="side-section"><h2>Adoption path</h2><p>From owned accounts to accounts with recorded activity.</p>` +
        `<div class="funnel">` +
        [["Owned", summary.owned, "all"], ["Consuming", summary.consuming, "consuming"],
            ["Active 7d", summary.active, "active"], ["Not yet using", summary.whitespace, "whitespace"]].map(([title, value, status]) =>
            `<button type="button" data-jump="${status}"><strong>${matched(value)}</strong><span>${H(title)}</span></button>`).join("") +
        `</div><p class="meta">Consumption is a 90-day signal; active means users or UBB spend in the latest seven days.</p></section>` +
        `<section class="side-section"><h2>Copilot Impact</h2>` +
        (() => {
            const impact = portfolioImpact(m);
            if (!impact.total) return `<p>No enterprise data in this snapshot yet.</p>`;
            return `<p>Measured per GitHub enterprise, from the Copilot Impact dashboard.</p>` +
                `<div class="mini-list">` +
                `<button type="button" data-view="impact"><span>Enterprises reporting</span>` +
                `<small>${num(impact.reporting)} of ${num(impact.total)}</small></button>` +
                `<button type="button" data-view="impact"><span>Engaged developers</span>` +
                `<small>${num(impact.engagedUsers)}</small></button>` +
                `<button type="button" data-view="impact"><span>PRs merged</span>` +
                `<small>${num(impact.prsMergedTotal)}</small></button>` +
                `<button type="button" data-view="impact"><span>Cost per dev month</span>` +
                `<small>${money(impact.avgCostPerDevMonth)}</small></button>` +
                `</div>`;
        })() +
        `<button class="link-button" type="button" data-view="impact">Open Impact &rarr;</button></section></aside></div>`;
}

function accountMatches(account, queueMap) {
    const f = state.filters, status = accountStatus(state.model, account), item = queueMap.get(account.id);
    const text = [account.name, account.parent, account.country, account.region, account.segment, account.industry,
        account.top_model, account.top_surface, ...(account.integrations || []).map(row => row.integration)]
        .filter(Boolean).join(" ").toLowerCase();
    if (f.q.trim().toLowerCase().split(/\s+/).some(part => part && !text.includes(part))) return false;
    if (f.status !== "all" && !(f.status === "consuming" ? account.consuming :
        f.status === "risk" ? hasSources(state.model, "06") && isNumber(account.at_risk) && account.at_risk > 0 :
        f.status === "spend" ? hasSources(state.model, "07") && account.gross_7d > 0 : f.status === status)) return false;
    if (f.lane !== "all" && item?.lane !== f.lane) return false;
    if (f.health !== "all" && (account.health_cat || "unscored") !== f.health) return false;
    if (f.region !== "all" && account.region !== f.region) return false;
    if (f.surface !== "all" && (!hasSources(state.model, "08") ||
        !hasSurfaceSignal(account, f.surface, f.surfaceWindow))) return false;
    if (f.model !== "all" && !(account.models || []).some(row => row.model === f.model && row.l28 > 0)) return false;
    return true;
}

function sortedAccounts(accounts) {
    const f = state.filters, order = queueIndex();
    const key = account => f.sort === "queue" ? order.get(account.id) ?? Number.MAX_SAFE_INTEGER :
        f.sort === "renewal" ? isNumber(account.renewing_in_days) && account.renewing_in_days >= 0
            ? account.renewing_in_days : null :
            f.sort === "health_cat" ? ({ Red: 3, Yellow: 2, Green: 1 })[account.health_cat] :
                ["licenses", "engaged28"].includes(f.sort) ? state.pulseLicenses?.get(account.id)?.[f.sort] : account[f.sort];
    return accounts.sort((a, b) => {
        const aa = key(a), bb = key(b);
        if (aa == null && bb == null) return a.name.localeCompare(b.name);
        if (aa == null) return 1;
        if (bb == null) return -1;
        const comparison = typeof aa === "string" ? aa.localeCompare(bb) : aa - bb;
        return (f.ascending ? comparison : -comparison) || a.name.localeCompare(b.name);
    });
}

function selectField(title, key, values, current, size = "") {
    return `<div class="field ${size}"><label for="filter-${key}">${H(title)}</label>` +
        `<select id="filter-${key}" data-select-filter="${key}">` +
        values.map(([value, text]) => `<option value="${H(value)}" ${current === value ? "selected" : ""}>${H(text)}</option>`).join("") +
        `</select></div>`;
}

function renderAccounts() {
    const m = state.model, queueMap = new Map(queue().map(item => [item.account.id, item]));
    const results = sortedAccounts(m.accounts.filter(a => accountMatches(a, queueMap)));
    const pageSize = 30;
    state.page = Math.min(state.page, Math.max(0, Math.ceil(results.length / pageSize) - 1));
    const first = state.page * pageSize;
    const subset = results.slice(first, first + pageSize);
    const regions = [...new Set(m.accounts.map(a => a.region).filter(Boolean))].sort();
    const surfaces = [...new Set(m.accounts.flatMap(a => (a.integrations || []).map(row => row.surface)))].sort();
    const models = [...new Set(m.accounts.flatMap(a => (a.models || []).map(row => row.model)))].sort();
    const options = (items, all = "All") => [["all", all], ...items.map(item => [item, item])];
    const sortButton = (name, key) => `<button type="button" data-sort="${key}" ` +
        `aria-label="Sort by ${H(name)}">${H(name)}${state.filters.sort === key ? state.filters.ascending ? " ^" : " v" : ""}</button>`;
    const reading = (account, key) => state.pulseLicenses?.get(account.id)?.[key];
    const sortState = key => state.filters.sort === key ? ` aria-sort="${state.filters.ascending ? "ascending" : "descending"}"` : "";
    return `<div class="page-head"><div><h1>All accounts</h1>` +
        `<p>All ${num(m.accounts.length)} Salesforce-owned accounts are in scope, including Copilot whitespace.</p></div>` +
        `<div class="meta">Click an account for its evidence, 13-week trajectory and suggested next steps.</div></div>` +
        `<div class="filter-bar"><div class="field search"><label for="account-search">Search account, market, integration or model</label>` +
        `<input id="account-search" data-search="accounts" type="search" placeholder="Search all owned accounts" value="${H(state.filters.q)}"></div>` +
        `${selectField("Activity", "status", [["all", "All owned"], ["consuming", "Consuming (90d)"], ["active", "Active this week"],
            ["inactive", "Inactive this week"], ["whitespace", "No Copilot signal"], ["unlinked", "Usage not linked"],
            ["sibling", "Usage on sibling"], ["risk", "Has flagged users"], ["spend", "UBB spend 7d"], ["unknown", "Needs verification"]], state.filters.status)}` +
        `${selectField("Queue lane", "lane", [["all", "Any lane"], ...LANES.map(lane => [lane.id, lane.label])], state.filters.lane)}` +
        `${selectField("Health", "health", [["all", "Any health"], ["Green", "Green"], ["Yellow", "Yellow"], ["Red", "Red"],
            ["unscored", "No score"]], state.filters.health)}` +
        `${selectField("Region", "region", options(regions, "Every region"), state.filters.region)}` +
        `${selectField("Surface (UBB)", "surface", options(surfaces, "Any surface"), state.filters.surface)}` +
        `${selectField("Surface window", "surfaceWindow", [["28d", "Last 28d"], ["7d", "Latest 7d"]], state.filters.surfaceWindow)}` +
        `${selectField("Model (UBB)", "model", options(models, "Any model"), state.filters.model)}` +
        `<div class="filter-actions"><button type="button" class="button" data-action="reset-filters">Clear filters</button></div></div>` +
        `<div class="results-line"><span><strong>${num(results.length)}</strong> of ${num(m.accounts.length)} accounts` +
        ` &middot; page ${state.page + 1} of ${Math.max(1, Math.ceil(results.length / pageSize))}</span>` +
        `<span>Surface: ${H(state.filters.surfaceWindow)} UBB; model: 28d UBB. Absence does not prove non-use.</span></div>` +
        (state.pulseLicensesError ? `<p class="meta" role="status">${H(state.pulseLicensesError)}</p>` : "") +
        (subset.length ? `<div class="table-wrap all-accounts"><table><thead><tr>` +
            `<th${sortState("name")}>${sortButton("Account", "name")}</th><th class="optional">Activity</th><th${sortState("health_cat")}>${sortButton("Health", "health_cat")}</th>` +
            `<th class="num"${sortState("licenses")}>${sortButton("Licenses", "licenses")}</th><th class="num"${sortState("assigned")}>${sortButton("Assigned", "assigned")}</th>` +
            `<th class="num"${sortState("active_28d")}>${sortButton("Active 28d", "active_28d")}</th><th class="num"${sortState("engaged28")}>${sortButton("Engaged 28d", "engaged28")}</th>` +
            `<th class="num optional"${sortState("gross_7d")}>${sortButton("UBB 7d", "gross_7d")}</th><th class="optional"${sortState("renewal")}>${sortButton("Renewal", "renewal")}</th>` +
            `<th class="queue-column"${sortState("queue")}>${sortButton("Queue", "queue")}</th></tr></thead><tbody>` +
            subset.map(a => {
                const item = queueMap.get(a.id);
                const status = accountStatus(m, a);
                const health = a.health_cat && hasSources(m, "06") ? a.health_cat : null;
                return `<tr><td><button type="button" class="account-link" data-account="${H(a.id)}">${H(a.name)}</button>` +
                    `<span class="account-sub">${H(accountSubtitle(a))}</span></td>` +
                    `<td class="optional"><span class="status ${status}">${H(STATUS_LABELS[status])}</span></td>` +
                    `<td>${health ? `<span class="health ${health}">${H(health)}</span>` : "&mdash;"}</td>` +
                    `<td class="num">${num(reading(a, "licenses"))}</td>` +
                    `<td class="num">${num(dataValue(a, "assigned", "06"))}</td>` +
                    `<td class="num">${num(dataValue(a, "active_28d", "03"))}</td>` +
                    `<td class="num">${num(reading(a, "engaged28"))}</td>` +
                    `<td class="num optional">${money(dataValue(a, "gross_7d", "07"))}</td>` +
                    `<td class="optional">${dates(a.renewal)}</td>` +
                    `<td class="queue-column">${item ? `${laneTag(item.lane)} ${H(item.play.title)}` : '<span class="quiet">No flagged play</span>'}</td></tr>`;
            }).join("") + `</tbody></table></div>` :
            empty("No accounts match these filters", "Try a broader search or clear one of the selected filters.",
                '<button type="button" class="button" data-action="reset-filters">Clear filters</button>')) +
        `<div class="pagination"><span>Showing ${subset.length ? first + 1 : 0}&ndash;${first + subset.length} of ${results.length}</span>` +
        `<div><button type="button" class="button tiny" data-action="previous-page" ${state.page === 0 ? "disabled" : ""}>Previous</button>` +
        `<button type="button" class="button tiny" data-action="next-page" ${first + pageSize >= results.length ? "disabled" : ""}>Next</button></div></div>`;
}

function renderBreakdown(entries, type) {
    const max = Math.max(0, ...entries.map(([, value]) => value));
    return entries.length ? `<div class="bar-list">${entries.slice(0, 11).map(([name, value]) =>
        `<button class="bar-row" type="button" data-${type}="${H(name)}" aria-label="Show accounts with ${H(name)} ${type === "surface" ? "7-day" : "28-day"} UBB signal">` +
        `<span>${H(name)}</span><span class="track"><span class="fill" style="width:${max > 0 ? Math.max(2, value / max * 100).toFixed(1) : 0}%"></span></span>` +
        `<span class="value">${money(value)}</span></button>`).join("")}</div>` :
        empty("No recorded breakdown", "This snapshot has no usage for this dimension.");
}

function renderTrends() {
    const m = state.model, p = m.portfolio;
    const acceptance = (p.accepted || []).map((value, index) => {
        const shown = p.shown?.[index];
        return isNumber(value) && isNumber(shown) && shown > 0 ? value / shown : null;
    });
    const surfaces = Object.entries(p.surface_weekly || {}).map(([name, values]) => [name, values?.[0] || 0])
        .sort((a, b) => b[1] - a[1]);
    const models = Object.entries(p.models || {}).map(([name, value]) => [name, value.l28 || 0])
        .sort((a, b) => b[1] - a[1]);
    const health = p.health_mix || {};
    return `<div class="page-head"><div><h1>Trends</h1>` +
        `<p>Thirteen weekly windows, newest on the right. Portfolio series sum consuming accounts only.</p></div>` +
        `<div class="meta">UBB = usage-side list value &middot; billable = beyond included pools</div></div>` +
        `<div class="chart-grid">` +
        chartPanel("Active users and assigned seats", "7-day / 28-day actives and assigned seats", [
            { name: "Weekly active", values: p.a7 }, { name: "28-day active", values: p.a28 },
            { name: "Assigned", values: p.assigned }], ["03", "06"]) +
        chartPanel("UBB spend", "Gross and billable usage-side dollars", [
            { name: "Gross", values: p.gross }, { name: "Billable", values: p.billable }], ["07"], "money", "bar") +
        chartPanel("Account momentum", "Accounts active in each 7-day window", [
            { name: "Active accounts", values: p.consuming_by_week }], ["03", "07"]) +
        chartPanel("AI units", "Weekly AI-unit consumption; not an invoice", [
            { name: "AI units", values: p.aiu }], ["07"], "number", "bar") +
        chartPanel("Suggestion acceptance", "Accepted suggestions divided by shown suggestions", [
            { name: "Acceptance rate", values: acceptance }], ["04"], "percent") +
        chartPanel("Users with risk flags", "Weekly total from the Copilot health source", [
            { name: "Flagged users", values: p.at_risk }], ["06"]) +
        `</div><div class="breakdown-grid">` +
        `<section class="breakdown"><div class="section-top"><div><h2>Where UBB is landing</h2>` +
        `<p>Surface mix, latest seven days. Select a surface for the same seven-day account window.</p></div></div>` +
        (hasSources(m, "10", "08") ? renderBreakdown(surfaces, "surface") :
            empty("Surface data unavailable", "One or more integration sources are missing.")) + `</section>` +
        `<section class="breakdown"><div class="section-top"><div><h2>Model mix</h2>` +
        `<p>UBB by model, last 28 days. Select a model to see accounts.</p></div></div>` +
        (hasSources(m, "11") ? renderBreakdown(models, "model") :
            empty("Model data unavailable", "The model source is missing.")) + `</section></div>` +
        portfolioForecast() + `<section class="section" style="margin-top:26px"><h2>Health distribution</h2>` +
        `<p class="meta">Consuming accounts only. Click a category to inspect that cohort.</p>` +
        (hasSources(m, "06") ? `<div class="health-mix">${["Green", "Yellow", "Red", "unscored"].map(key =>
            `<button type="button" data-health="${key}"><strong>${num(health[key === "unscored" ? "n/a" : key] || 0)}</strong>` +
            `${key === "unscored" ? "No score" : key}</button>`).join("")}</div>` :
            empty("Health data unavailable", "The Copilot health source is missing.")) + `</section>`;
}

const PHASE_COLORS = {
    "No Cohort": "var(--muted)",
    "Phase 1": "var(--accent)",
    "Phase 2": "var(--caution)",
    "Phase 3": "var(--positive)",
};

/** Direction is authoritative: a large multiplier can still mean engaged users are worse off. */
function multiplierCard(entry) {
    const tone = entry.direction === "positive" ? "good" : entry.direction === "negative" ? "bad" : "flat";
    const verdict = entry.direction === "positive" ? "better for engaged developers"
        : entry.direction === "negative" ? "worse for engaged developers"
            : "no measurable difference";
    return `<div class="impact-card"><h3>${H(entry.label)}</h3>` +
        `<div class="impact-figure"><strong class="${tone}">${entry.multiplier !== null ? `${num(entry.multiplier)}\u00d7` : "&mdash;"}</strong>` +
        `<span class="delta ${tone}">${H(verdict)}</span></div>` +
        `<dl class="impact-compare"><div><dt>Baseline</dt><dd>${label(entry.baseline)}</dd></div>` +
        `<div><dt>Engaged</dt><dd>${label(entry.engaged)}</dd></div></dl>` +
        `<p class="meta">${H(entry.hint)}</p></div>`;
}

function phaseBar(rows, totalLabel) {
    const shown = rows.filter(row => isNumber(row.share ?? row.shareOfUsers) && (row.share ?? row.shareOfUsers) > 0);
    if (!shown.length) return empty("No cohort mix", "No engaged users were reported for this window.");
    return `<div class="phase-bar" role="img" aria-label="${H(totalLabel)}">` +
        shown.map(row => {
            const share = row.share ?? row.shareOfUsers;
            return `<span style="width:${share.toFixed(2)}%;background:${PHASE_COLORS[row.phase] || "var(--muted)"}" ` +
                `title="${H(row.phase)}: ${share.toFixed(1)}%"></span>`;
        }).join("") + `</div>` +
        `<div class="chart-legend">${shown.map(row =>
            `<span><i style="background:${PHASE_COLORS[row.phase] || "var(--muted)"}"></i>${H(row.phase)} ${(row.share ?? row.shareOfUsers).toFixed(1)}%</span>`).join("")}</div>`;
}

function matchCell(enterprise) {
    const { confidence, candidates } = enterprise.match;
    if (confidence === "confirmed") {
        return `<button class="account-link" type="button" data-account="${H(candidates[0].id)}">${H(candidates[0].name)}</button>` +
            `<span class="account-sub">Confirmed by the warehouse mapping</span>`;
    }
    if (confidence === "single") {
        return `<button class="account-link" type="button" data-account="${H(candidates[0].id)}">${H(candidates[0].name)}</button>` +
            `<span class="account-sub">Suggested by name &middot; not confirmed</span>`;
    }
    if (confidence === "ambiguous") {
        return `<span class="quiet">${num(candidates.length)} possible matches</span>` +
            `<span class="account-sub">${H(candidates.slice(0, 3).map(c => c.name).join("; "))}</span>`;
    }
    return `<span class="quiet">No name match</span><span class="account-sub">` +
        `${enterprise.match.unowned_sf_id ? "Maps to a Salesforce account you do not own" : "Not linked to a Salesforce account"}</span>`;
}

const SURFACE_STATUS_CLASS = {
    "in use": "good", "enabled, not used": "warn", "disabled by policy": "off",
    "not configured": "off", "no usage recorded": "off",
};

function surfacePanel(enterprise) {
    const rows = surfaceRows(enterprise);
    if (!rows.length) {
        return empty("No surface data", "No Copilot UBB usage or feature policy has been collected for this enterprise.");
    }
    const known = enablementKnown(enterprise);
    const gaps = rows.filter(row => row.gap);
    const max = Math.max(...rows.map(row => row.l28), 1);
    return (gaps.length
        ? `<div class="inline-note warning"><strong>Enabled but not used:</strong> ` +
          `${gaps.map(row => H(row.surface)).join(", ")}. The policy is switched on and no usage was recorded in 28 days.</div>`
        : "") +
        (known ? "" : `<div class="inline-note">Feature policies have not been collected for this enterprise, so a surface with no usage cannot be told apart from one that is switched off.</div>`) +
        `<div class="detail-table"><table><thead><tr><th>Surface</th><th>Status</th><th>Enablement</th>` +
        `<th class="num">7d $</th><th class="num">28d $</th><th class="num">AI units 28d</th>` +
        `<th class="num">Users 28d</th><th>13-week trend</th></tr></thead><tbody>` +
        rows.map(row => `<tr><td>${H(row.surface)}` +
            (row.integrations.length
                ? `<span class="account-sub">${H(row.integrations.map(i => i.integration).join(", "))}</span>` : "") +
            `</td>` +
            `<td><span class="status ${SURFACE_STATUS_CLASS[row.status] === "good" ? "active" : SURFACE_STATUS_CLASS[row.status] === "warn" ? "inactive" : "unknown"}">${H(row.status)}</span></td>` +
            `<td class="quiet">${H(ENABLEMENT_LABELS[row.enablement] || row.enablement)}</td>` +
            `<td class="num">${row.active ? money(row.l7) : "&mdash;"}</td>` +
            `<td class="num">${row.active ? money(row.l28) : "&mdash;"}</td>` +
            `<td class="num">${row.active ? num(row.aiu28) : "&mdash;"}</td>` +
            `<td class="num">${row.active ? num(row.u28) : "&mdash;"}</td>` +
            `<td>${row.weekly.some(isNumber) ? sparkline(row.weekly, row.l28 / max) : '<span class="quiet">&mdash;</span>'}</td></tr>`).join("") +
        `</tbody></table></div>` +
        `<p class="meta">Spend and AI units are summed across a surface's integrations. User counts are per-integration distinct counts, so the largest integration is shown rather than a sum, which would overcount.</p>`;
}

/** Small inline trend for a surface row; the series is latest-first and drawn chronologically. */
function sparkline(values, weight = 1) {
    const points = values.slice(0, 13).reverse();
    const numbers = points.filter(isNumber);
    if (numbers.length < 2) return '<span class="quiet">&mdash;</span>';
    const max = Math.max(...numbers, 1);
    const W = 90, H2 = 20;
    const coords = points.map((value, index) => {
        const x = points.length > 1 ? index * W / (points.length - 1) : W / 2;
        const y = H2 - 2 - (isNumber(value) ? value : 0) / max * (H2 - 4);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return `<svg width="${W}" height="${H2}" viewBox="0 0 ${W} ${H2}" role="img" aria-label="13-week trend">` +
        `<polyline fill="none" stroke="var(--accent)" stroke-linecap="round" stroke-linejoin="round" ` +
        `stroke-width="${(1 + weight).toFixed(1)}" points="${coords.join(" ")}"/></svg>`;
}

function renderImpact() {
    const m = state.model;
    if (!Array.isArray(m.enterprises) || !m.enterprises.length) {
        return `<div class="page-head"><div><h1>Copilot Impact.</h1>` +
            `<p>Adoption phases, throughput and ROI from the Copilot Impact dashboard.</p></div></div>` +
            empty("No enterprise data in this snapshot",
                "The owned-enterprise query has not been collected yet. Run the dashboard refresh automation.",
                '<button class="button primary" type="button" data-action="refresh">Refresh live</button>');
    }
    if (state.selectedSlug) return renderEnterprise();

    const p = portfolioImpact(m);
    const rows = enterpriseRows(m).filter(row => {
        const q = state.impactQ.trim().toLowerCase();
        if (!q) return true;
        return [row.enterprise.slug, ...(row.enterprise.match.candidates || []).map(c => c.name)]
            .join(" ").toLowerCase().includes(q);
    });
    const pending = p.total - p.collected;
    const leaders = enterpriseRows(m)
        .filter(row => isNumber(row.prsMerged) && row.prsMerged > 0)
        .sort((a, b) => b.prsMerged - a.prsMerged)
        .slice(0, 8);
    const maxPrs = Math.max(...leaders.map(row => row.prsMerged), 1);

    return `<div class="page-head"><div><h1>Copilot Impact.</h1>` +
        `<p>Adoption phases, developer throughput and cost, as the Copilot Impact dashboard reports them.</p></div>` +
        `<div class="meta">${p.reportDateRange ? `${H(p.reportDateRange)} &middot; ` : ""}` +
        `${p.cohortWindowDays ? `${num(p.cohortWindowDays)}-day cohort window` : ""}</div></div>` +
        `<div class="inline-note"><strong>Measured per GitHub enterprise, not per Salesforce account.</strong> ` +
        `Impact is reported against an enterprise slug. Where the warehouse supplies a Salesforce mapping it is used and marked ` +
        `confirmed; otherwise the row falls back to a name-derived suggestion you should verify. ROI figures are modelled on an ` +
        `assumed salary of ${money(p.salary)} and are estimates, not realised savings.</div>` +
        `<div class="metric-strip">` +
        [["Enterprises reporting", `${num(p.reporting)} of ${num(p.total)}`, "Copilot Impact enabled"],
            ["Engaged developers", num(p.engagedUsers), "Across reporting enterprises"],
            ["Advanced cohorts", p.phaseMix.length
                ? pct(p.phaseMix.filter(r => ["Phase 2", "Phase 3"].includes(r.phase))
                    .reduce((sum, r) => sum + r.share, 0) / 100, 1) : "&mdash;", "Phase 2 and 3 share"],
            ["PRs merged", num(p.prsMergedTotal), `${num(p.prsMergedEnterprises)} enterprises reporting`],
            ["Cost per developer month", money(p.avgCostPerDevMonth), "Average AI spend per engaged dev"],
            ["Awaiting collection", num(pending), "Not yet fetched today"],
        ].map(([title, value, hint]) =>
            `<div class="metric"><span class="value">${value}</span>` +
            `<span class="label">${H(title)}</span><span class="hint">${H(hint)}</span></div>`).join("") + `</div>` +
        (pending > 0 ? `<div class="notice warning"><span>${num(pending)} of ${num(p.total)} enterprises have not been fetched in this snapshot, so the figures above cover only those collected. The daily refresh fills the rest.</span></div>` : "") +
        `<div class="chart-grid">` +
        `<div class="chart-panel"><h3>Adoption phases</h3>` +
        `<div class="meta">Weighted by engaged developers across ${num(p.phaseMixEnterprises)} enterprises &middot; ${num(p.phaseMixUsers)} developers</div>` +
        phaseBar(p.phaseMix, "Share of engaged developers by adoption phase") +
        (p.phaseMix.length ? `<div class="detail-table" style="margin-top:12px"><table><thead><tr><th>Phase</th>` +
            `<th class="num">Developers</th><th class="num">Share</th></tr></thead><tbody>` +
            p.phaseMix.map(row => `<tr><td>${H(row.phase)}<span class="account-sub">${H(row.note)}</span></td>` +
                `<td class="num">${num(row.users)}</td><td class="num">${row.share.toFixed(1)}%</td></tr>`).join("") +
            `</tbody></table></div>` : "") + `</div>` +
        `<div class="chart-panel"><h3>Pull requests merged</h3>` +
        `<div class="meta">Monthly total across reporting enterprises; the cohort grows as more report</div>` +
        (p.prsTrend.length
            ? plot([{ name: "PRs merged", values: p.prsTrend.map(point => point.prsMerged).reverse() }],
                "number", "bar", "Pull requests merged by month",
                p.prsTrend.map(point => point.label))
            : empty("No merge history", "No reporting enterprise returned a monthly PR series.")) + `</div></div>` +
        (leaders.length ? `<section class="section"><h2>Where the throughput is</h2>` +
            `<p class="meta">Pull requests merged in the reporting window. Select an enterprise for its full Impact view.</p>` +
            `<div class="bar-list">${leaders.map(row =>
                `<button class="bar-row" type="button" data-slug="${H(row.enterprise.slug)}">` +
                `<span>${H(row.enterprise.slug)}</span><span class="track"><span class="fill" style="width:${Math.max(2, row.prsMerged / maxPrs * 100).toFixed(1)}%"></span></span>` +
                `<span class="value">${num(row.prsMerged)}</span></button>`).join("")}</div></section>` : "") +
        (() => {
            const surfaces = surfaceAdoption(m);
            if (!surfaces.length) return "";
            const coverage = surfaces.enablementCoverage;
            const maxSpend = Math.max(...surfaces.map(s => s.l28), 1);
            const totalGaps = surfaces.reduce((sum, s) => sum + s.gapEnterprises, 0);
            return `<section class="section"><div class="section-top"><div><h2>Surfaces, CLI and agents</h2>` +
                `<p>Which Copilot surfaces your enterprises actually use, and which are switched on but idle.</p></div></div>` +
                `<div class="inline-note">Usage is 28-day UBB at enterprise grain. Enablement comes from each enterprise's ` +
                `Copilot feature policies, collected for <strong>${num(coverage.known)} of ${num(coverage.total)}</strong> enterprises; ` +
                `a surface is only called <em>enabled, not used</em> where a policy confirms it is on.` +
                `${totalGaps ? ` <strong>${num(totalGaps)}</strong> enabled-but-idle surfaces are listed below.` : ""}</div>` +
                `<div class="table-wrap"><table><thead><tr><th>Surface</th><th class="num">Using</th>` +
                `<th class="num">Enabled, not used</th><th class="num">Disabled</th><th>28-day spend</th>` +
                `<th class="num">Users</th><th>Enablement opportunities</th></tr></thead><tbody>` +
                surfaces.map(s => `<tr><td><strong>${H(s.surface)}</strong></td>` +
                    `<td class="num">${num(s.usingEnterprises)}</td>` +
                    `<td class="num">${s.gapEnterprises ? `<span class="delta bad">${num(s.gapEnterprises)}</span>` : "&mdash;"}</td>` +
                    `<td class="num">${s.disabledEnterprises ? num(s.disabledEnterprises) : "&mdash;"}</td>` +
                    `<td><span class="spend-cell"><span class="bar"><span style="width:${Math.max(1, s.l28 / maxSpend * 100).toFixed(1)}%"></span></span>` +
                    `<span class="spend-value">${money(s.l28)}</span></span></td>` +
                    `<td class="num">${s.users ? num(s.users) : "&mdash;"}</td>` +
                    `<td>${s.gaps.length
                        ? s.gaps.slice(0, 4).map(slug => `<button class="link-button" type="button" data-slug="${H(slug)}">${H(slug)}</button>`).join(", ") +
                          (s.gaps.length > 4 ? ` <span class="quiet">+${num(s.gaps.length - 4)} more</span>` : "")
                        : '<span class="quiet">&mdash;</span>'}</td></tr>`).join("") +
                `</tbody></table></div>` +
                `<p class="meta">User counts are per-integration distinct counts and are not summed across surfaces. Absence of UBB does not prove a feature is unused where enablement is unknown.</p></section>`;
        })() +
        `<section class="section"><div class="section-top"><div><h2>All enterprises</h2>` +
        `<p>${num(rows.length)} of ${num(p.total)} shown. Select one for its adoption phases, trend and ROI.</p></div></div>` +
        `<div class="filter-bar"><div class="field search"><label for="impact-search">Search enterprise or suggested account</label>` +
        `<input id="impact-search" type="search" data-search="impact" value="${H(state.impactQ)}" placeholder="Search enterprises"></div></div>` +
        (rows.length ? `<div class="table-wrap"><table><thead><tr><th>Enterprise</th><th>Suggested Salesforce account</th>` +
            `<th>Impact</th><th class="num">Engaged devs</th><th class="num">Surfaces</th><th class="num">Phase 2&ndash;3</th>` +
            `<th class="num">PRs merged</th><th>Code shipped</th><th>Time to merge</th></tr></thead><tbody>` +
            rows.map(row => {
                const mult = entry => entry && entry.multiplier !== null
                    ? `<span class="${entry.direction === "positive" ? "delta good" : entry.direction === "negative" ? "delta bad" : "quiet"}">${num(entry.multiplier)}\u00d7</span>`
                    : entry ? `<span class="quiet">no change</span>` : "&mdash;";
                return `<tr><td><button class="account-link" type="button" data-slug="${H(row.enterprise.slug)}">${H(row.enterprise.slug)}</button>` +
                    `<span class="account-sub">${H([row.enterprise.plan, row.enterprise.deployment].filter(Boolean).join(" / ") || "GitHub enterprise")}</span></td>` +
                    `<td>${matchCell(row.enterprise)}</td>` +
                    `<td><span class="status ${row.state.state === "available" ? "active" : row.state.state === "uncollected" ? "unknown" : "inactive"}">${H(row.state.label)}</span></td>` +
                    `<td class="num">${num(row.engagedUsers)}</td>` +
                    `<td class="num">${row.surfacesUsed ? num(row.surfacesUsed) : "&mdash;"}` +
                    `${row.surfaceGaps.length ? ` <span class="delta bad" title="${H(row.surfaceGaps.join(", "))}">+${num(row.surfaceGaps.length)} idle</span>` : ""}</td>` +
                    `<td class="num">${isNumber(row.advancedShare) ? `${row.advancedShare.toFixed(1)}%` : "&mdash;"}</td>` +
                    `<td class="num">${num(row.prsMerged)}</td>` +
                    `<td>${mult(row.codeShipped)}</td><td>${mult(row.timeToMerge)}</td></tr>`;
            }).join("") + `</tbody></table></div>`
            : empty("No enterprise matches", "Try a different search term.")) +
        `</section>`;
}

function renderEnterprise() {
    const m = state.model;
    const enterprise = (m.enterprises || []).find(item => item.slug === state.selectedSlug);
    if (!enterprise) return empty("Enterprise not in this snapshot", "It may have been renamed or is no longer owned.",
        '<button class="button" type="button" data-action="back-impact">Back to Impact</button>');
    const state_ = impactState(enterprise);
    const impact = impactOf(enterprise);
    const phaseRows = phases(enterprise);
    const trend = engagementTrend(enterprise);
    const roiSection = roi(enterprise);
    const mult = multipliers(enterprise);
    const engaged = totalEngagedUsers(enterprise);
    const merged = productivity(enterprise);
    const facts_ = adoptionFacts(enterprise);
    const head = `<div class="detail-head"><div>` +
        `<button class="link-button" type="button" data-action="back-impact">&larr; Back to Impact</button>` +
        `<h1>${H(enterprise.slug)}</h1><div class="meta">` +
        `${H([enterprise.plan, enterprise.deployment, enterprise.industry, enterprise.region].filter(Boolean).join(" / ") || "GitHub enterprise")}` +
        `${enterprise.impact?.reportDateRange ? ` &middot; ${H(enterprise.impact.reportDateRange)}` : ""}</div></div>` +
        `<div class="detail-actions"><span class="status ${state_.state === "available" ? "active" : state_.state === "uncollected" ? "unknown" : "inactive"}">${H(state_.label)}</span></div></div>` +
        `<nav class="detail-nav" aria-label="Enterprise sections"><a href="#enterprise-surfaces">Surfaces</a>` +
        `<a href="#enterprise-adoption">Adoption breadth</a><a href="#enterprise-phases">Phases</a>` +
        `<a href="#enterprise-trend">Trend</a><a href="#enterprise-roi">Cost &amp; ROI</a></nav>` +
        `<div class="inline-note ${["confirmed", "single"].includes(enterprise.match.confidence) ? "" : "warning"}">` +
        (enterprise.match.confidence === "confirmed"
            ? `Salesforce account: <strong>${H(enterprise.match.candidates[0].name)}</strong>. Confirmed by the warehouse mapping, not by name matching.`
            : enterprise.match.confidence === "single"
                ? `Suggested Salesforce account: <strong>${H(enterprise.match.candidates[0].name)}</strong>. Matched on name only &mdash; verify before using it commercially.`
                : enterprise.match.confidence === "ambiguous"
                    ? `This enterprise name matches ${num(enterprise.match.candidates.length)} Salesforce accounts (${H(enterprise.match.candidates.map(c => c.name).join("; "))}), so no link is assumed.`
                    : enterprise.match.unowned_sf_id
                        ? `This enterprise maps to a Salesforce account outside your owned book, so it is not linked to an account view here.`
                        : `No Salesforce account name matches this enterprise, so Impact here is not linked to account figures.`) +
        `</div>`;

    if (!impact) {
        return head +
            `<section class="detail-section" id="enterprise-surfaces"><h2>Surfaces, CLI and agents</h2>` +
            surfacePanel(enterprise) + `</section>` +
            empty(state_.label,
                state_.state === "uncollected"
                    ? "This enterprise has not been fetched in the current snapshot. The daily refresh will collect it."
                    : "The Copilot Impact dashboard returns no measurements for this enterprise, so nothing is shown rather than zeros.");
    }

    return head +
        `<div class="fact-strip">${[
            ["Engaged developers", num(engaged)],
            ["PRs merged", num(merged)],
            ["Phase 2\u20133 share", (() => {
                const advanced = phaseRows.filter(row => ["Phase 2", "Phase 3"].includes(row.phase) && isNumber(row.shareOfUsers))
                    .reduce((sum, row) => sum + row.shareOfUsers, 0);
                return phaseRows.some(row => isNumber(row.shareOfUsers)) ? `${advanced.toFixed(1)}%` : "&mdash;";
            })()],
            ["Cohort window", impact && enterprise.impact?.cohortWindowDays ? `${num(enterprise.impact.cohortWindowDays)} days` : "&mdash;"],
            ["Collected", enterprise.collected_from ? dates(String(enterprise.collected_from).slice(0, 10)) : "&mdash;"],
        ].map(([key, value]) => `<div class="fact"><small>${H(key)}</small><strong>${value}</strong></div>`).join("")}</div>` +
        (mult.length ? `<section class="detail-section"><h2>Adoption multiplier</h2>` +
            `<p class="meta">Engaged developers compared with the non-engaged baseline in the same enterprise. ` +
            `A larger multiplier is not automatically better &mdash; the direction says which way it goes.</p>` +
            `<div class="impact-grid">${mult.map(multiplierCard).join("")}</div></section>` : "") +
        `<section class="detail-section" id="enterprise-surfaces"><h2>Surfaces, CLI and agents</h2>` +
        `<p class="meta">Where Copilot spend and users actually sit, and whether each surface is switched on.</p>` +
        surfacePanel(enterprise) + `</section>` +
        (facts_ ? `<section class="detail-section" id="enterprise-adoption"><h2>Adoption breadth</h2>` +
            `<p class="meta">Licensed versus active users and how widely the product is used` +
            `${facts_.monthAsOf ? `, from the closed month ending ${H(facts_.monthAsOf)}` : ""}` +
            `${facts_.weekAsOf ? ` and the week ending ${H(facts_.weekAsOf)}` : ""}.</p>` +
            facts([
                ["Licensed users", num(facts_.licensed)],
                ["Users consuming AI units", num(facts_.ubbUsers) +
                    (isNumber(facts_.wowUbbUsers) && facts_.wowUbbUsers !== 0
                        ? ` <span class="delta ${facts_.wowUbbUsers > 0 ? "good" : "bad"}">${facts_.wowUbbUsers > 0 ? "+" : "\u2212"}${num(Math.abs(facts_.wowUbbUsers))} WoW</span>` : "")],
                ["Adoption rate", pct(facts_.adoptionRate, 1) + (facts_.band ? ` <span class="quiet small">${H(facts_.band)}</span>` : "")],
                ["Feature categories used", label(facts_.categories)],
                ["Integrations / models", `${num(facts_.integrationCount)} / ${num(facts_.modelCount)}`],
                ["Adoption maturity", label(facts_.maturity)],
                ["Product breadth", label(facts_.breadth)],
                ["Spend intensity", label(facts_.spendIntensity)],
                ["Pool utilisation (promo / standard)", `${pct(facts_.promoUtil)} / ${pct(facts_.standardUtil)}`],
                ["Entitlement outlook", label(facts_.entitlement)],
            ]) +
            (facts_.overageRisk
                ? `<div class="inline-note warning"><strong>Projected overage.</strong> ` +
                  `${H(facts_.insight || "This enterprise is projected to exceed its included pool")}` +
                  `${facts_.utilizationBand ? ` (${H(facts_.utilizationBand)})` : ""}. ` +
                  `Usage-side projection, not an invoice.</div>` : "") +
            (facts_.theme ? `<div class="inline-note"><strong>Suggested conversation:</strong> ${H(facts_.theme)} ` +
                `<span class="quiet">\u2014 the warehouse's own classification, not a recommendation from this canvas.</span></div>` : "") +
            `</section>` : "") +
        `<section class="detail-section" id="enterprise-phases"><h2>Adoption phases</h2>` +
        `<p class="meta">Where this enterprise's engaged developers sit on the Copilot adoption path.</p>` +
        phaseBar(phaseRows, "Share of engaged developers by phase") +
        `<div class="detail-table" style="margin-top:12px"><table><thead><tr><th>Phase</th><th class="num">Share</th>` +
        `<th class="num">Engaged</th><th class="num">Lines/day</th><th class="num">PRs merged</th><th>Merge time</th></tr></thead><tbody>` +
        phaseRows.map(row => `<tr><td>${H(row.phase)}<span class="account-sub">${H(row.note)}</span></td>` +
            `<td class="num">${isNumber(row.shareOfUsers) ? `${row.shareOfUsers.toFixed(1)}%` : '<span class="quiet">n/r</span>'}</td>` +
            `<td class="num">${num(row.engagedUsers)}</td>` +
            `<td class="num">${isNumber(row.locPerDay) ? num(row.locPerDay) : "&mdash;"}</td>` +
            `<td class="num">${isNumber(row.prsMerged) ? num(row.prsMerged) : "&mdash;"}</td>` +
            `<td>${label(row.mergeTime)}</td></tr>`).join("") +
        `</tbody></table></div>` +
        `<p class="meta">n/r means the phase reported no developers in this window; it is not a zero.</p></section>` +
        `<section class="detail-section" id="enterprise-trend"><h2>Engagement trend</h2>` +
        `<p class="meta">Monthly pull requests merged and how the cohort mix shifted.</p>` +
        `<div class="chart-grid"><div class="chart-panel"><h3>PRs merged by month</h3>` +
        (trend.some(point => isNumber(point.prsMerged))
            ? plot([{ name: "PRs merged", values: trend.map(point => point.prsMerged).reverse() }],
                "number", "bar", "PRs merged by month", trend.map(point => point.label))
            : empty("No merge counts", "This enterprise reports cohort mix but no merged-PR counts.")) + `</div>` +
        `<div class="chart-panel"><h3>Cohort mix by month</h3>` +
        (trend.some(point => point.cohortMix)
            ? `<div class="mix-stack">${trend.map(point => `<div class="mix-row"><span class="mix-label">${H(point.label || "")}</span>` +
                `<span class="mix-bar">${PHASE_ORDER.filter(phase => isNumber(point.cohortMix?.[phase])).map(phase =>
                    `<span style="width:${point.cohortMix[phase].toFixed(2)}%;background:${PHASE_COLORS[phase]}" title="${H(phase)}: ${point.cohortMix[phase].toFixed(1)}%"></span>`).join("")}</span>` +
                `<span class="mix-value">${isNumber(point.prsMerged) ? num(point.prsMerged) : ""}</span></div>`).join("")}</div>` +
              `<div class="chart-legend">${PHASE_ORDER.map(phase =>
                  `<span><i style="background:${PHASE_COLORS[phase]}"></i>${H(phase)}</span>`).join("")}</div>`
            : empty("No cohort mix", "This enterprise reports no monthly cohort breakdown.")) +
        `</div></div></section>` +
        (roiSection ? `<section class="detail-section" id="enterprise-roi"><h2>Cost and ROI</h2>` +
            `<p class="meta">Modelled on an assumed salary of ${money(roiSection.salary)}. These are the dashboard's estimates, ` +
            `not realised savings or invoiced amounts.</p>` +
            `<div class="detail-table"><table><thead><tr><th>Group</th><th class="num">AI credits / month</th>` +
            `<th class="num">Cost / dev month</th><th class="num">Share of payroll</th><th class="num">PRs / month</th>` +
            `<th class="num">Faster merges</th></tr></thead><tbody>` +
            roiSection.groups.map(group => `<tr><td>${H(group.label || "Group")}</td>` +
                `<td class="num">${num(group.credits)}</td>` +
                `<td class="num">${money(group.costPerDevMonth)}</td>` +
                `<td class="num">${isNumber(group.payrollPercent) ? `${group.payrollPercent.toFixed(2)}%` : "&mdash;"}</td>` +
                `<td class="num">${isNumber(group.prsPerMonth) ? num(group.prsPerMonth) : "&mdash;"}</td>` +
                `<td class="num">${isNumber(group.fasterMergesPercent)
                    ? `<span class="${group.fasterMergesPercent > 0 ? "delta good" : "delta bad"}">${group.fasterMergesPercent > 0 ? "+" : ""}${num(group.fasterMergesPercent)}%</span>`
                    : "&mdash;"}</td></tr>`).join("") +
            `</tbody></table></div>` +
            `<p class="meta">A large negative "faster merges" figure means engaged developers merged more slowly than the baseline in this window, often because very few baseline PRs were measured.</p>` +
            `</section>` : "");
}

function renderChanges() {
    const m = state.model, changes = m.changes || [];
    const kinds = [...new Set(changes.map(change => change.kind))].sort();
    const filtered = changes.filter(change =>
        (state.changeSeverity === "all" || change.severity === state.changeSeverity) &&
        (state.changeKind === "all" || change.kind === state.changeKind) &&
        `${change.account || ""} ${change.text || ""}`.toLowerCase().includes(state.changeQ.toLowerCase()));
    const owned = new Set(m.accounts.map(a => a.id));
    const severity = { bad: ["now", "Negative"], warn: ["watch", "Watch"], good: ["grow", "Positive"], info: ["grow", "Information"] };
    return `<div class="page-head"><div><h1>Changes</h1>` +
        `<p>The dashboard's change feed. Each event compares the latest weekly window with the one before it, not with yesterday.</p></div>` +
        `<div class="meta">${num(changes.length)} events in the ${H(m.data_date)} snapshot</div></div>` +
        `<div class="filter-bar"><div class="field search"><label for="change-search">Account or change</label>` +
        `<input id="change-search" type="search" data-search="changes" value="${H(state.changeQ)}" placeholder="Search the change feed"></div>` +
        `${selectField("Severity", "changeSeverity", [["all", "All changes"], ["bad", "Negative"],
            ["warn", "Watch"], ["good", "Positive"], ["info", "Information"]], state.changeSeverity)}` +
        `${selectField("Change type", "changeKind", [["all", "All types"], ...kinds.map(kind => [kind, kind.replace(/_/g, " ")])], state.changeKind)}` +
        `<div class="filter-actions"><button class="button" type="button" data-action="reset-changes">Clear filters</button></div></div>` +
        `<div class="results-line">${num(filtered.length)} of ${num(changes.length)} changes</div>` +
        (filtered.length ? `<div class="change-list">${filtered.map(change => {
            const [lane, title] = severity[change.severity] || severity.info;
            return `<div class="change-item">${laneTag(lane)}<span>` +
                (change.id && owned.has(change.id) ?
                    `<button class="account-link" type="button" data-account="${H(change.id)}">${H(change.account)}</button>` :
                    `<strong>${H(change.account || "Portfolio")}</strong><span class="account-sub">No longer owned or no linked account</span>`) +
                `</span><span class="change-text"><strong>${H(title)}: ${H(change.kind?.replace(/_/g, " ") || "Change")}</strong>` +
                `<br>${H(change.text)}</span></div>`;
        }).join("")}</div>` :
            empty("No changes match", "Try clearing the search or severity filter.")) +
        (m.missing_sources.length ? `<p class="meta">Missing sources can omit changes. See data notes for coverage.</p>` : "");
}

function facts(entries) {
    return `<dl class="facts-list">${entries.map(([title, value]) =>
        `<div><dt>${H(title)}</dt><dd>${value}</dd></div>`).join("")}</dl>`;
}

function detailTable(headers, rows, fallback) {
    if (!rows.length) return empty(fallback, "No values were recorded for this section in the saved model.");
    return `<div class="detail-table"><table><thead><tr>${headers.map(head => `<th>${H(head)}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function accountLink(id, name = "Salesforce") {
    return /^[a-zA-Z0-9]{15,18}$/.test(id || "") ?
        `<a class="button" href="https://github.lightning.force.com/lightning/r/Account/${encodeURIComponent(id)}/view" ` +
        `target="_blank" rel="noopener noreferrer">${H(name)}</a>` : "";
}

function renderAccount() {
    const m = state.model, a = m.accounts.find(account => account.id === state.selectedId);
    if (!a) return empty("Account not in this snapshot", "The selected account is no longer owned or the snapshot changed.",
        '<button class="button" type="button" data-action="back">Return to all accounts</button>');
    const plays = accountPlays(m, a), status = accountStatus(m, a);
    const integrity = integrityIssues(m, a);
    const profile = riskProfile(a);
    const integrationRows = (a.integrations || []).map(row => [
        H(row.surface), H(row.integration), money(row.l7), money(row.p7),
        money(row.l28), num(row.u28), num(row.aiu28),
    ]);
    const modelRows = (a.models || []).map(row => [H(row.model), money(row.l7), money(row.p7), money(row.l28)]);
    const series = (a.arr_series || []).map(row =>
        [label(row.date), num(row.seats), num(row.cb), num(row.ce), money(row.total_arr)]);
    const editors = hasSources(m, "04") ? Object.entries(a.editors || {})
        .map(([editor, value]) => ({ editor, accepted: value?.accepted ?? null, shown: value?.shown ?? null }))
        .filter(row => isNumber(row.accepted) || isNumber(row.shown))
        .sort((x, y) => (y.accepted || 0) - (x.accepted || 0))
        .map(row => [H(row.editor), num(row.accepted), num(row.shown),
            pct(isNumber(row.accepted) && row.shown > 0 ? row.accepted / row.shown : null, 1)]) : [];
    const retention = hasSources(m, "05") &&
        [a.mau_retained, a.mau_churned, a.mau_new].some(isNumber)
        ? { retained: a.mau_retained, churned: a.mau_churned, added: a.mau_new } : null;
    const riskRows = profile ? profile.entries.map(entry => [H(entry.label), num(entry.value)]) : [];
    const history = [
        chartPanel("Adoption trajectory", "Weekly active / 28-day active / assigned", [
            { name: "Weekly active", values: a.a7 }, { name: "28-day active", values: a.a28 },
            { name: "Assigned seats", values: a.h_assigned }], ["03", "06"]),
        chartPanel("UBB consumption", "Gross and billable, usage-side list value", [
            { name: "Gross", values: a.gross }, { name: "Billable", values: a.billable }], ["07"], "money", "bar"),
        chartPanel("Copilot health", "Health score from zero to one", [
            { name: "Health score", values: a.h_health }], ["06"], "percent"),
        chartPanel("Users with risk flags", "Weekly flagged users; not an inactivity count", [
            { name: "Flagged users", values: a.h_at_risk }], ["06"]),
        chartPanel("AI units", "Weekly AI-unit consumption", [
            { name: "AI units", values: a.aiu }], ["07"], "number", "bar"),
        chartPanel("Suggestion acceptance", "Accepted divided by shown per week", [
            { name: "Acceptance rate", values: (a.accepted || []).map((value, index) =>
                isNumber(value) && isNumber(a.shown?.[index]) && a.shown[index] > 0 ? value / a.shown[index] : null) }],
        ["04"], "percent"),
    ].join("");
    const accountChanges = (m.changes || []).filter(change => change.id === a.id);
    // Copilot Impact is enterprise-scoped; only offer it when exactly one enterprise name-matches.
    const linkedEnterprises = (m.enterprises || []).filter(enterprise =>
        enterprise.match.confidence === "single" && enterprise.match.candidates[0]?.id === a.id);
    return `<div class="detail-head"><div><button class="link-button" type="button" data-action="back">&larr; Back to ${H(LANE_LABELS[state.previousView] || (state.previousView === "desk" ? "adoption desk" : state.previousView === "trends" ? "trends" : state.previousView === "changes" ? "changes" : state.previousView === "impact" ? "impact" : "all accounts"))}</button>` +
        `<h1>${H(a.name)}</h1><div class="meta">${H([a.type, a.segment, a.region, a.country].filter(Boolean).join(" / ") || "Salesforce-owned account")}` +
        `${a.parent ? ` &middot; Parent: ${H(a.parent)}` : ""}${disambiguate(a) ? ` &middot; ${H(disambiguate(a))}` : ""}</div></div><div class="detail-actions">` +
        `<span class="status ${status}">${H(STATUS_LABELS[status])}</span>` +
        `${hasSources(m, "02") && a.mover === "Mover" ? '<span class="status active">Mover</span>' : ""}` +
        `${accountLink(a.sf_url_id || a.id, "Open in Salesforce")}</div></div>` +
        `<nav class="detail-nav" aria-label="Account sections"><a href="#account-priorities">Priorities</a>` +
        `<a href="#account-trajectory">13-week trajectory</a><a href="#account-impact">Adoption evidence</a>` +
        `<a href="#account-workflows">Workflows</a><a href="#account-commercial">Commercial context</a>` +
        `<a href="#account-evidence">Data integrity</a></nav>` +        (integrity.length ? `<div class="inline-note warning"><strong>Check the commercial data.</strong><ul>` +
            integrity.map(text => `<li>${H(text)}</li>`).join("") + `</ul></div>` : "") +
        `<div class="fact-strip">${[
            ["Active 7d", num(dataValue(a, "active_7d", "03"))],
            ["Active 28d", num(dataValue(a, "active_28d", "03"))],
            ["Assigned seats", num(dataValue(a, "assigned", "06"))],
            ["Utilization", pct(hasSources(m, "03", "06") ? a.utilization : null)],
            ["UBB gross 28d", money(dataValue(a, "gross_28d", "07"))],
        ].map(([key, value]) => `<div class="fact"><small>${H(key)}</small><strong>${value}</strong></div>`).join("")}</div>` +
        adoptionFunnel(a) + `<div class="detail-layout"><div>` +
        `<section class="detail-section" id="account-priorities"><h2>What to discuss next</h2>` +
        (plays.length ? plays.map(play => `<div class="action-card">${laneTag(play.lane)}<strong>${H(play.title)}</strong>` +
            `<ul class="evidence">${play.evidence.map(line => `<li>${H(line)}</li>`).join("")}</ul>` +
            `<p class="next-step"><strong>Suggested next step:</strong> ${H(play.nextStep)}</p>` +
            `<span class="meta">Evidence: ${H(sourceNote(play.sources))}</span></div>`).join("") :
            empty("No rule-based play flagged", "Review the account evidence below; absence of an alert is not proof that adoption is healthy.")) +
        (m.missing_sources.length ? `<div class="inline-note warning">Some plays are hidden because required sources are missing.</div>` : "") +
        `</section><section class="detail-section" id="account-trajectory"><h2>13-week trajectory</h2>` +
        `<div class="chart-grid">${history}</div></section>` +
        `<section class="detail-section" id="account-impact"><h2>Adoption evidence</h2>` +
        `<p class="meta">Engagement and interaction measures from this account's own telemetry. ` +
        `A dash means the source reports no value, not a zero.</p>` +
        `${facts([
            ["Suggestion acceptance 28d", pct(dataValue(a, "acceptance_28d", "04"), 1)],
            ["Lines of code accepted 28d", num(hasSources(m, "04")
                ? (a.loc_accepted || []).slice(0, 4).filter(isNumber).reduce((sum, v) => sum + v, 0) || null : null)],
            ["Seat activation", pct(hasSources(m, "03", "06") ? a.utilization : null)],
            ["Engaged of assigned", pct(dataValue(a, "eng_assigned", "05"), 1)],
            ["Engaged of active", pct(dataValue(a, "eng_active", "05"), 1)],
            ["MAU retention GRR / NRR", `${pct(dataValue(a, "mau_grr", "05"))} / ${pct(dataValue(a, "mau_nrr", "05"))}`],
        ])}` +
        (linkedEnterprises.length
            ? `<div class="inline-note">Copilot Impact is reported per GitHub enterprise. ` +
              `${linkedEnterprises.map(enterprise => `<button class="link-button" type="button" data-slug="${H(enterprise.slug)}">${H(enterprise.slug)}</button>`).join(", ")} ` +
              `${linkedEnterprises.length === 1 ? "matches" : "match"} this account by name &mdash; verify before relying on it.</div>`
            : `<div class="inline-note">No GitHub enterprise name-matches this account confidently, so its Copilot Impact view is not linked here. Browse them in <button class="link-button" type="button" data-view="impact">Impact</button>.</div>`) +
        (retention ? `<h3 style="margin-top:16px">Monthly active user movement</h3>` +
            detailTable(["Retained", "Churned", "New"],
                [[num(retention.retained), num(retention.churned), num(retention.added)]], "No retention detail") : "") +
        `<h3 style="margin-top:16px">Acceptance by editor</h3>` +
        (hasSources(m, "04") ? detailTable(["Editor", "Accepted", "Shown", "Rate"], editors, "No editor acceptance data")
            : empty("IDE source missing", "Acceptance is withheld.")) +
        `</section>` +
        `<section class="detail-section" id="account-workflows"><h2>Workflows and models</h2>` +
        `<p class="meta">Integration and model tables are UBB-based. No row does not prove a feature is unused.</p>` +
        `<div class="details-grid"><div><h3>Copilot surfaces / integrations</h3>` +
        (hasSources(m, "08") ? detailTable(["Surface", "Integration", "7d $", "Prior 7d $", "28d $", "28d users", "AI units 28d"],
            integrationRows, "No recorded UBB integrations") : empty("Integration source missing", "The integration breakdown is withheld.")) +
        `</div><div><h3>Models</h3>` +
        (hasSources(m, "11") ? detailTable(["Model", "7d $", "Prior 7d $", "28d $"], modelRows,
            "No recorded model UBB") : empty("Model source missing", "The model breakdown is withheld.")) +
        `</div></div></section>` +
        forecastWhitespace(a) + `<section class="detail-section" id="account-commercial"><h2>Commercial context</h2>` +
        `<p class="meta">Use these as conversation context, not a forecast or invoiced-revenue calculation.</p>` +
        `${facts([
            ["Contracted Copilot seats", num(dataValue(a, "contracted_seats", "09"))],
            ["Business / Enterprise", `${num(dataValue(a, "cb_seats", "09"))} / ${num(dataValue(a, "ce_seats", "09"))}`],
            ["GHE seats", num(dataValue(a, "ghe_seats", "02"))],
            ["Total GitHub ARR", money(dataValue(a, "total_arr", "02"))],
            ["Renewal", dates(dataValue(a, "renewal", "02"))],
            ["Open pipeline, net", money(dataValue(a, "open_pipeline_net", "02"))],
            ["Copilot billed last month / last 12m", `${money(dataValue(a, "copilot_billed_lcm", "02"))} / ${money(dataValue(a, "copilot_billed_ltm", "02"))}`],
            ["UBB gross MTD / billable MTD", `${money(dataValue(a, "mtd_gross", "07"))} / ${money(dataValue(a, "mtd_billable", "07"))}`],
            ["Projected month-end UBB", money(dataValue(a, "projected_month_spend", "07"))],
            ["Promo / standard pool utilization", `${pct(dataValue(a, "promo_util", "07"))} / ${pct(dataValue(a, "standard_util", "07"))}`],
            ["CSM / CSA", `${label(a.csm)} / ${label(a.csa)}`],
        ])}` +
        `<h3 style="margin-top:20px">Contract and seat history</h3>` +
        (hasSources(m, "09") ? detailTable(["Period", "Copilot seats", "Business", "Enterprise", "Total GitHub ARR"],
            series, "No contract history") : empty("Contract source missing", "Seat history is withheld.")) +
        `</section></div><aside><section class="side-section" id="account-evidence"><h2>Data integrity</h2>` +
        `<p>${status === "unlinked" ? "Usage on another account is shown for context only, never added to account totals." :
            status === "sibling" ? "The sibling carries usage; this billed account is not treated as inactive." :
                "Activity and attribution are based on the saved Salesforce-owned account model."}</p>` +
        (a.usage_unlinked ? `<div class="inline-note warning">No usage telemetry is attributed to this Salesforce account in the available sources.</div>` : "") +
        ((a.linked_usage || []).length ? `<h3>Unowned look-alike accounts</h3>` +
            detailTable(["Account", "UBB 7d", "UBB 28d", "Users 7d"], a.linked_usage.map(row =>
                [H(row.name), money(row.gross_7d), money(row.gross_28d), num(row.users_7d)]), "No linked usage") : "") +
        ((a.usage_on_sibling || []).length ? `<h3>Owned sibling account</h3><div class="mini-list">` +
            a.usage_on_sibling.map(row => `<button type="button" data-account="${H(row.id)}"><span>${H(row.name)}</span>` +
                `<small>${money(row.gross_28d)} 28d UBB</small></button>`).join("") + `</div>` : "") +
        (profile ? `<h3 style="margin-top:18px">Users with risk flags</h3>` +
            `<p class="meta">${num(profile.total)} flagged. These are users the health model flags for the reasons below, ` +
            `not a count of inactive users.${profile.overlapping ? " A user can carry more than one flag, so the rows sum to more than the total." : ""}</p>` +
            detailTable(["Reason", "Users"], riskRows, "No risk breakdown") : "") +
        `<h3 style="margin-top:18px">Signals and sources</h3><p>${(a.signals || []).length ? (a.signals || []).map(H).join(" / ") : "No 90-day Copilot signal."}</p>` +
        `<p class="meta">Model date ${H(m.data_date)}. Source freshness is listed in Ask &amp; data notes.</p>` +
        `</section><section class="side-section"><h2>Account changes</h2>` +
        (accountChanges.length ? `<div class="mini-list">${accountChanges.slice(0, 8).map(change =>
            `<div class="action-card">${laneTag(change.severity === "bad" ? "now" : change.severity === "warn" ? "watch" : "grow")}` +
            `<p>${H(change.text)}</p></div>`).join("")}</div>` :
            `<p>No change-feed events for this account in the saved snapshot.</p>`) +
        `</section></aside></div>`;
}

function renderNotes() {
    const m = state.model;
    const caveats = dataCaveats(m);
    const sourceRows = Object.entries(SOURCE_NAMES).map(([id, name]) => {
        const entry = m.sources[id];
        return [H(id), H(name), m.missing_sources.includes(id) ? "Missing" : num(entry?.rows),
            label(m.asof?.[id]), label(entry?.collected_from?.replace("T", " ").slice(0, 19))];
    });
    const glossary = [
        ["users with risk flags", "Users the Copilot health model flags for any reason, such as declining against their own 90-day baseline, no activity in 180 days, poor retention or low monthly active use. The reasons overlap, and the total is not a count of inactive users. The dominant reason is shown per account."],
        ["queue lanes", "Verify first means the data must be trusted before adoption is judged. Act now is urgent this week. Watch is deterioration without an emergency. Grow is expansion, whitespace, and accounts consuming Copilot on usage-based billing without licences."],
        ["signals conflict", "Weekly active users fell sharply while independent measures such as 28-day actives, UBB users or accepted suggestions did not. That pattern usually points to telemetry or attribution rather than lost adoption."],
        ...Object.entries(m.definitions),
    ];
    return `<div class="page-head"><div><h1>Data notes</h1>` +
        `<p>Definitions, source coverage and the original dashboard's offline Ask experience.</p></div>` +
        `<div class="meta">Snapshot ${H(m.data_date)} &middot; ${num(m.accounts.length)} owned accounts</div></div>` +
        `<div class="ask-card"><div><h2>Ask a question of your dashboard</h2>` +
        `<p>The original, locally generated dashboard includes a natural-language Ask box, exhaustive tables and CSV export. It uses this same data model.</p></div>` +
        `<a class="button primary" href="./classic#ask">Open Ask &amp; full dashboard</a></div>` +
        (caveats.length ? `<section class="detail-section"><h2>Read the data carefully</h2>` +
            `<ul class="notes">${caveats.map(text => `<li>${H(text)}</li>`).join("")}</ul></section>` : "") +
        `<section class="detail-section"><h2>Reading the metrics</h2>` +
        `<div class="inline-note"><strong>UBB dollars are usage-side list value, not invoiced revenue.</strong> ` +
        `Billable refers to usage beyond included pools. AI units are not the same thing as an invoice amount.</div>` +
        `<dl class="definition-list">${glossary.map(([name, description]) =>
            `<div><dt>${H(String(name).replace(/_/g, " "))}</dt><dd>${H(description)}</dd></div>`).join("")}</dl></section>` +
        `<section class="detail-section"><h2>Source coverage</h2>` +
        `<p class="meta">Freshness differs by source. A zero is shown only when the underlying metric is available; unavailable values are shown as a dash.</p>` +
        `<div class="table-wrap source-table"><table><thead><tr><th>ID</th><th>Source</th><th>Rows</th><th>Data as of</th><th>Collected</th></tr></thead>` +
        `<tbody>${sourceRows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` +
        (m.orphans?.length ? `<p class="meta">${num(m.orphans.length)} accounts attributed in warehouse sources but not owned in Salesforce are excluded from account totals.</p>` : "") +
        `</section><section class="detail-section"><h2>What this canvas does not do</h2>` +
        `<p class="meta">It does not modify Salesforce, send customer messages, infer seat-level intent, estimate business value, or claim that a missing UBB integration means a customer never used a feature. Live refresh is a separate automation started only when you request it.</p>` +
        `</section>`;
}

function render() {
    renderHeader();
    renderNotices();
    if (!state.model) {
        app.innerHTML = state.loading ? `<div class="loading-view" role="status" aria-label="Loading dashboard">` +
            `<div class="loading-line long"></div><div class="loading-line"></div>` +
            `<div class="loading-row"><div></div><div></div><div></div></div></div>` :
            empty("The local snapshot is unavailable", state.loadError || "Run the dashboard refresh automation to create the model.",
                '<button class="button primary" type="button" data-action="refresh">Refresh live</button>');
        return;
    }
    const active = document.activeElement;
    const focus = active?.dataset.search ? { type: active.dataset.search, start: active.selectionStart, end: active.selectionEnd } : null;
    chartId = 0;
    app.innerHTML = state.selectedId ? renderAccount() :
        state.view === "accounts" ? renderAccounts() :
            state.view === "trends" ? renderTrends() :
                state.view === "impact" ? renderImpact() :
                    state.view === "changes" ? renderChanges() :
                        state.view === "notes" ? renderNotes() : renderDesk();
    if (focus) {
        const input = app.querySelector(`[data-search="${focus.type}"]`);
        if (input) {
            input.focus({ preventScroll: true });
            input.setSelectionRange(focus.start, focus.end);
        }
    }
    notifyParentRoute();
}

async function loadModel() {
    if (loadingModel) return loadingModel;
    loadingModel = (async () => {
        try {
            const response = await fetch("./api/model", { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || `Dashboard request failed (${response.status}).`);
            state.model = validateModel(payload.model);
            state.revision = payload.revision;
            state.loadError = null;
            state.loading = false;
            state.eventWarning = null;
            queueCache = null;
            nameCounts = null;
            state.queueOrder = null;
            try {
                const response = await fetch("../api/copilot-licenses", { cache: "no-store" });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || `License request failed (${response.status}).`);
                state.pulseLicenses = new Map(payload.rows.map(row => [row.account_id, row]));
                state.pulseLicensesError = payload.note;
            } catch (error) {
                state.pulseLicenses = null;
                state.pulseLicensesError = `Pulse license reconciliation unavailable. ${error.message}`;
            }
        } catch (error) {
            state.loadError = error.message;
            state.loading = false;
        }
        render();
    })().finally(() => { loadingModel = null; });
    return loadingModel;
}

async function updateStatus() {
    try {
        const response = await fetch("./api/status", { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Status request failed (${response.status}).`);
        state.refresh = payload.refresh;
        renderHeader();
        renderNotices();
        if (payload.revision !== state.revision) await loadModel();
    } catch (error) {
        state.eventWarning = `Cannot check refresh status: ${error.message}`;
        renderNotices();
    }
}

async function requestRefresh() {
    if (["launching", "requested"].includes(state.refresh.phase)) return;
    state.refresh = { phase: "launching", message: "Requesting the existing Copilot refresh automation..." };
    renderHeader();
    renderNotices();
    try {
        const response = await fetch("./api/refresh", {
            method: "POST", headers: { "Content-Type": "application/json", "X-Canvas-Token": token },
            body: "{}",
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Refresh request failed (${response.status}).`);
        state.refresh = payload.refresh;
    } catch (error) {
        state.refresh = { phase: "error", message: error.message };
    }
    renderHeader();
    renderNotices();
}

function resetFilters() {
    state.filters = { q: "", status: "all", lane: "all", health: "all", region: "all",
        surface: "all", surfaceWindow: "28d", model: "all", sort: "queue", ascending: true };
    state.page = 0;
    render();
}

function openAccount(id) {
    if (!state.model.accounts.some(account => account.id === id)) return;
    state.previousView = state.view;
    state.selectedId = id;
    state.selectedSlug = null;
    history.pushState(null, "", `#/account/${encodeURIComponent(id)}`);
    render();
    app.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
}

function openEnterprise(slug) {
    if (!(state.model.enterprises || []).some(enterprise => enterprise.slug === slug)) return;
    state.selectedId = null;
    state.selectedSlug = slug;
    state.view = "impact";
    history.pushState(null, "", `#/enterprise/${encodeURIComponent(slug)}`);
    render();
    app.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
}

function changeView(view) {
    if (embedded && view === "impact" && location.hash !== "#/impact") {
        parent.postMessage({ type: "pulse:navigate", view: "impact" }, location.origin);
        return;
    }
    state.selectedId = null;
    state.selectedSlug = null;
    state.view = view;
    history.replaceState(null, "", `#/${view}`);
    render();
    app.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
}

const VIEWS = ["desk", "accounts", "trends", "impact", "changes", "notes"];

function notifyParentRoute() {
    if (!embedded || state.view === "impact") return;
    parent.postMessage({ type: "pulse:route", view: state.view, account: state.selectedId }, location.origin);
}

if (embedded) {
    window.addEventListener("message", event => {
        if (event.origin !== location.origin || event.source !== parent || event.data?.type !== "pulse:theme") return;
        if (!["light", "dark"].includes(event.data.mode)) return;
        document.documentElement.dataset.colorMode = event.data.mode;
        for (const key of ["paper", "ink", "muted", "line", "accent", "accent-ink", "positive", "negative", "caution",
            "focus", "font", "surface", "surface-strong", "wash", "hover-row"]) {
            const value = event.data.tokens?.[key];
            if (typeof value === "string" && value.length < 500) document.documentElement.style.setProperty(`--${key}`, value);
        }
    });
    let lastHeight = 0;
    new ResizeObserver(() => {
        const height = Math.ceil(document.querySelector(".shell").getBoundingClientRect().height);
        if (height !== lastHeight) {
            lastHeight = height;
            parent.postMessage({ type: "pulse:height", height }, location.origin);
        }
    }).observe(document.querySelector(".shell"));
}

document.addEventListener("click", event => {
    const target = event.target;
    const section = target.closest('.detail-nav a[href^="#"]');
    if (section) {
        event.preventDefault();
        document.querySelector(section.getAttribute("href"))?.scrollIntoView({ behavior: "smooth" });
        return;
    }
    const account = target.closest("[data-account]");
    if (account) { openAccount(account.dataset.account); return; }
    const slug = target.closest("[data-slug]");
    if (slug) { openEnterprise(slug.dataset.slug); return; }
    const view = target.closest("[data-view]");
    if (view) { changeView(view.dataset.view); return; }
    const laneAll = target.closest("[data-lane-all]");
    if (laneAll) { jump("all", { lane: laneAll.dataset.laneAll }); return; }
    const health = target.closest("[data-health]");
    if (health) { jump("consuming", { health: health.dataset.health }); return; }
    const surface = target.closest("[data-surface]");
    if (surface) { jump("all", { surface: surface.dataset.surface, surfaceWindow: "7d" }); return; }
    const model = target.closest("[data-model]");
    if (model) { jump("all", { model: model.dataset.model }); return; }
    const jumpTo = target.closest("[data-jump]");
    if (jumpTo) { jump(jumpTo.dataset.jump, jumpTo.dataset.jump === "spend" ? { sort: "gross_7d", ascending: false } : {}); return; }
    const sort = target.closest("[data-sort]");
    if (sort) {
        const key = sort.dataset.sort;
        state.filters.ascending = state.filters.sort === key ? !state.filters.ascending
            : ["name", "renewal", "queue"].includes(key);
        state.filters.sort = key;
        render();
        return;
    }
    const action = target.closest("[data-action]")?.dataset.action;
    if (action === "reload") { loadModel(); updateStatus(); }
    if (action === "refresh") requestRefresh();
    if (action === "reset-filters") resetFilters();
    if (action === "reset-changes") {
        state.changeSeverity = "all"; state.changeKind = "all"; state.changeQ = ""; render();
    }
    if (action === "previous-page" || action === "next-page") {
        state.page += action === "next-page" ? 1 : -1;
        state.page = Math.max(0, state.page);
        render();
    }
    if (action === "back") changeView(state.previousView);
    if (action === "back-impact") changeView("impact");
});

document.addEventListener("change", event => {
    const key = event.target.dataset.selectFilter;
    if (!key) return;
    if (key === "changeSeverity" || key === "changeKind") state[key] = event.target.value;
    else { state.filters[key] = event.target.value; state.page = 0; }
    render();
});

document.addEventListener("input", event => {
    if (event.target.dataset.search === "accounts") {
        state.filters.q = event.target.value; state.page = 0; render();
    }
    if (event.target.dataset.search === "changes") {
        state.changeQ = event.target.value; render();
    }
    if (event.target.dataset.search === "impact") {
        state.impactQ = event.target.value; render();
    }
});

document.addEventListener("keydown", event => {
    if (event.key === "Escape" && state.selectedSlug) { changeView("impact"); return; }
    if (event.key === "Escape" && state.selectedId) { changeView(state.previousView); return; }
    if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
        event.preventDefault();
        if (state.view !== "accounts" || state.selectedId) changeView("accounts");
        document.querySelector("#account-search")?.focus();
    }
});

window.addEventListener("popstate", () => {
    const account = location.hash.match(/^#\/account\/(.+)$/);
    const enterprise = location.hash.match(/^#\/enterprise\/(.+)$/);
    state.selectedId = null;
    state.selectedSlug = null;
    if (account && state.model?.accounts.some(a => a.id === decodeURIComponent(account[1]))) {
        state.selectedId = decodeURIComponent(account[1]);
    } else if (enterprise) {
        state.selectedSlug = decodeURIComponent(enterprise[1]);
        state.view = "impact";
    } else {
        state.view = VIEWS.find(view => location.hash === `#/${view}`) || "desk";
    }
    render();
});

const initialAccount = location.hash.match(/^#\/account\/(.+)$/);
const initialEnterprise = location.hash.match(/^#\/enterprise\/(.+)$/);
if (initialAccount) {
    try {
        state.selectedId = decodeURIComponent(initialAccount[1]);
        const from = new URLSearchParams(location.search).get("view");
        state.view = VIEWS.includes(from) && from !== "impact" ? from : "accounts";
        state.previousView = state.view;
    }
    catch { state.selectedId = null; }
} else if (initialEnterprise) {
    try { state.selectedSlug = decodeURIComponent(initialEnterprise[1]); state.view = "impact"; }
    catch { state.selectedSlug = null; }
} else {
    state.view = VIEWS.find(view => location.hash === `#/${view}`) || "desk";
}
await Promise.all([loadModel(), updateStatus()]);
const events = new EventSource("./events");
events.addEventListener("model", () => { void loadModel(); });
events.addEventListener("status", event => {
    const payload = JSON.parse(event.data);
    state.refresh = payload.refresh;
    renderHeader();
    renderNotices();
    if (payload.revision !== state.revision) void loadModel();
});
events.addEventListener("error", event => {
    state.eventWarning = event.data ? JSON.parse(event.data).error :
        "Live updates disconnected; checking the saved model periodically.";
    renderNotices();
});
events.addEventListener("open", () => { state.eventWarning = null; renderNotices(); });
setInterval(() => { void updateStatus(); }, 12000);
