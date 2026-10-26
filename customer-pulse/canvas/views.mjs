// Pure HTML-string rendering for the canvas. Every model value passes through esc() before reaching markup.
import {
    FAMILY_LABELS, PRIORITIES, SOURCE_STATES, TYPE_LABELS, VIEWS, accountIndex, actionsFor, attentionIndex, dayAge,
    eventsFor, hasProduct, isClosed, isDay, isNumber, isObject, priorityRank, productSeats, seriesChange, seriesPoints,
    whitespace,
} from "./model.mjs";

export const ROW_LIMIT = 50;
export const VIEW_LABELS = { overview: "Overview", copilot: "Copilot", ghe: "GHE", ghas: "GHAS", signals: "Signals", data: "Data" };
export const VIEW_FAMILY = { copilot: "ghcp", ghe: "ghe", ghas: "ghas" };
const FAMILY_VIEW = { ghcp: "copilot", ghe: "ghe", ghas: "ghas" };
const PRODUCT_TITLES = { ghe: "GitHub Enterprise", ghas: "GitHub Advanced Security", ghcp: "GitHub Copilot" };
const FEED_FAMILIES = { contracts: ["ghe", "ghas", "ghcp"], metered: ["ghe"], volume: ["ghe"], copilot: ["ghcp"], copilot_seats: ["ghcp"], ghas: ["ghas"] };
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const MINUS = "\u2212";
const DASH = "\u2013";

export const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ESC[character]);
const plural = (count, word, many = `${word}s`) => `${count === 1 ? word : many}`;
const typeLabel = type => TYPE_LABELS[type] || type;

export function fmtInt(value) {
    return isNumber(value) ? Math.round(value).toLocaleString("en-US") : DASH;
}

export function fmtMoney(value) {
    if (!isNumber(value)) return DASH;
    const abs = Math.abs(value);
    const [unit, suffix] = abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : abs >= 1e3 ? [1e3, "k"] : [1, ""];
    const scaled = abs / unit;
    const text = scaled.toFixed(unit === 1 || scaled >= 100 ? 0 : 1).replace(/\.0$/, "");
    return `${value < 0 ? MINUS : ""}$${text}${suffix}`;
}

const exactMoney = value => `${value < 0 ? MINUS : ""}$${Math.round(Math.abs(value)).toLocaleString("en-US")}`;
const money = value => isNumber(value) ? `<span class="num" title="${esc(exactMoney(value))}">${esc(fmtMoney(value))}</span>` : DASH;

export function fmtPct(value, digits = 0) {
    return isNumber(value) ? `${(value * 100).toFixed(digits)}%` : DASH;
}

/** Ratios arrive as fractions; values above 1.5 are treated as percentages, as pulse/model.py does. */
const frac = value => isNumber(value) ? (value > 1.5 ? value / 100 : value) : null;

export function fmtDay(day, { year = false } = {}) {
    const value = typeof day === "string" ? day.slice(0, 10) : "";
    if (!isDay(value)) return DASH;
    return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-US",
        { month: "short", day: "numeric", timeZone: "UTC", ...(year ? { year: "numeric" } : {}) });
}

const longDay = day => isDay(day)
    ? new Date(`${day}T00:00:00Z`).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" })
    : DASH;

export function fmtSigned(value) {
    if (!isNumber(value)) return "";
    const rounded = Math.round(value);
    if (rounded === 0) return "0";
    return `${rounded > 0 ? "+" : MINUS}${Math.abs(rounded).toLocaleString("en-US")}`;
}

/** A coloured change figure. `inline` prints "since <day>"; otherwise the day is in the tooltip. */
function delta(value, { since = null, invert = false, inline = false } = {}) {
    if (!isNumber(value)) return "";
    const tone = Math.round(value) === 0 ? "flat" : (value > 0) !== invert ? "good" : "bad";
    const title = since ? ` title="Change since ${esc(fmtDay(since, { year: true }))}"` : "";
    const tail = inline && since ? ` <span class="since">since ${esc(fmtDay(since))}</span>` : "";
    return `<span class="delta ${tone}"${title}>${esc(fmtSigned(value))}${tail}</span>`;
}

const tag = priority => `<span class="tag tag-${esc(String(priority).toLowerCase())}">${esc(priority)}</span>`;
const newTag = item => item.new ? '<span class="tag tag-new">New</span>' : "";
const familyChip = family => `<span class="family">${esc(FAMILY_LABELS[family] || family)}</span>`;
const health = category => ["Red", "Yellow", "Green"].includes(category) ? `<span class="health ${category}">${category}</span>` : DASH;

function accountLink(model, id, name) {
    const label = esc(name || id);
    return accountIndex(model).has(id)
        ? `<button type="button" class="link account-link" data-account="${esc(id)}">${label}</button>`
        : `<span class="account-name">${label}</span>`;
}

const safeUrl = value => {
    try {
        const url = new URL(String(value));
        return url.protocol === "https:" ? url.href : null;
    } catch {
        return null;
    }
};

/** Inline SVG trend line. Positions follow calendar days, so sparse monthly points are spaced honestly. */
export function sparkline(series, { label = "Trend", width = 120, height = 32 } = {}) {
    const points = seriesPoints(series);
    const known = points.filter(([, value]) => value !== null);
    if (known.length < 2) return "";
    const values = known.map(([, value]) => value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const first = Date.parse(`${points[0][0]}T00:00:00Z`);
    const span = Date.parse(`${points[points.length - 1][0]}T00:00:00Z`) - first || 1;
    let path = "";
    let drawing = false;
    for (const [day, value] of points) {
        if (value === null) {
            drawing = false;
            continue;
        }
        const x = ((Date.parse(`${day}T00:00:00Z`) - first) / span) * width;
        const y = max === min ? height / 2 : height - 3 - ((value - min) / (max - min)) * (height - 6);
        path += `${drawing ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
        drawing = true;
    }
    const summary = `${label}: ${fmtInt(values[0])} to ${fmtInt(values[values.length - 1])} between ${fmtDay(known[0][0])} and ${fmtDay(known[known.length - 1][0])}`;
    return `<svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${esc(summary)}"><title>${esc(summary)}</title><path d="${path}"/></svg>`;
}

function notice(tone, html, extra = "") {
    return `<div class="notice ${tone}" role="${tone === "error" ? "alert" : "status"}"><p>${html}</p>${extra}</div>`;
}

export function newHighCount(model) {
    return actionsFor(model).filter(action => action.new && action.priority === "High").length
        + model.events.filter(event => event.new && event.priority === "High").length;
}

function freshness(model) {
    const built = model.generated_at ? new Date(model.generated_at) : null;
    const time = built && !Number.isNaN(built.getTime()) ? ` · built ${built.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "";
    return `Snapshot ${fmtDay(model.date, { year: true })}${time}`;
}

export function renderTopbar(model, ui = {}) {
    const busy = ["launching", "requested"].includes(ui.refresh?.phase);
    let middle = "";
    if (model) {
        const issues = model.sources.filter(source => source.state !== "ok");
        middle += issues.length
            ? `<a class="pill warn" href="#/data" data-view="data">${issues.length} ${plural(issues.length, "source")} need${issues.length === 1 ? "s" : ""} attention</a>`
            : '<a class="pill ok" href="#/data" data-view="data">All sources current</a>';
        const high = newHighCount(model);
        if (high) middle += `<span class="pill hot" title="New High-priority actions and seat changes since the last digest">${high} new High</span>`;
    }
    return `<div class="brand"><span class="mark" aria-hidden="true">P</span><div class="brand-text"><span class="product-name">Customer Pulse</span>`
        + `<span class="freshness">${model ? esc(freshness(model)) : ui.loading ? "Loading the snapshot…" : "No snapshot loaded"}</span></div></div>`
        + `<div class="top-actions">${middle}<button type="button" class="button subtle" data-command="reload">Reload</button>`
        + `<button type="button" class="button primary" data-command="refresh"${busy ? ' aria-disabled="true"' : ""}>${busy ? "Refreshing…" : "Refresh"}</button></div>`;
}

function tabCount(model, view) {
    const family = VIEW_FAMILY[view];
    if (family) {
        const open = actionsFor(model, { family });
        if (!open.length) return "";
        const high = open.filter(action => action.priority === "High").length;
        return `<span class="count${high ? " high" : ""}" title="${open.length} open ${plural(open.length, "action")}${high ? `, ${high} High` : ""}">${open.length}</span>`;
    }
    if (view === "signals") {
        const shown = Array.isArray(model.signals) ? model.signals.filter(isObject).length : 0;
        return shown ? `<span class="count">${shown}</span>` : "";
    }
    return "";
}

export function renderNav(model, ui = {}) {
    return VIEWS.map(view => `<a href="#/${view}" data-view="${view}"${ui.view === view ? ' aria-current="page"' : ""}>`
        + `${VIEW_LABELS[view]}${model ? tabCount(model, view) : ""}</a>`).join("");
}

export function renderNotices(model, ui = {}) {
    const out = [];
    const refresh = ui.refresh;
    if (refresh && refresh.phase !== "idle" && refresh.message && !ui.refreshDismissed) {
        const tone = refresh.phase === "error" ? "error" : refresh.phase === "updated" ? "success" : "info";
        const close = ["error", "updated"].includes(refresh.phase)
            ? '<button type="button" class="button tiny subtle" data-command="dismiss-refresh">Dismiss</button>' : "";
        out.push(notice(tone, esc(refresh.message), close));
    }
    if (ui.error) out.push(notice("error", esc(ui.error)));
    if (!model) return out.join("");
    const build = ui.build;
    if (build?.build_error && build.generated_at && (!model.generated_at || Date.parse(build.generated_at) > Date.parse(model.generated_at))) {
        out.push(notice("error", `The latest refresh could not rebuild the snapshot: ${esc(build.build_error.slice(0, 300))} Showing the snapshot from ${esc(fmtDay(model.date, { year: true }))}.`));
    }
    const age = dayAge(model.date, ui.now || new Date());
    if (age !== null && age > 1) {
        out.push(notice("warning", `This snapshot is from ${esc(fmtDay(model.date, { year: true }))}, ${age} days ago. Use Refresh to rebuild it.`));
    }
    const family = VIEW_FAMILY[ui.view];
    const issues = model.sources.filter(source => source.state !== "ok"
        && (ui.view === "overview" || (family && (FEED_FAMILIES[source.feed] || []).includes(family))));
    if (issues.length) {
        const groups = new Map();
        for (const source of issues) {
            const state = SOURCE_STATES[source.state] || source.state;
            groups.set(state, [...(groups.get(state) || []), source.label]);
        }
        const list = [...groups].map(([state, labels]) => `${esc(state)}: ${labels.map(esc).join(", ")}.`).join(" ");
        out.push(notice("warning", `Some data is not current. ${list} <a href="#/data" data-view="data">See data sources</a>`));
    }
    if (model.baseline && ["overview", "ghe", "ghas", "copilot"].includes(ui.view)) {
        out.push(notice("info", "First snapshot: only each source's latest-day changes are marked New. Earlier changes are shown for context."));
    }
    return out.join("");
}

function segmentControl(name, value, options, label) {
    return `<div class="segment-control" role="group" aria-label="${esc(label)}">${options.map(([option, text]) =>
        `<button type="button" data-filter="${esc(name)}" data-value="${esc(option)}" aria-pressed="${option === value}">${esc(text)}</button>`).join("")}</div>`;
}

function actionRow(model, action, { showFamily = true, showAccount = true, showReasons = false } = {}) {
    const closed = isClosed(action);
    const id = esc(action.id);
    const controls = closed
        ? `<span class="state">${action.state === "done" ? "Done" : "Dismissed"}${action.state_at ? ` ${esc(fmtDay(action.state_at))}` : ""}</span>`
            + `<button type="button" class="button tiny subtle" data-action="reopen" data-id="${id}" data-focus-key="reopen:${id}">Reopen</button>`
        : `<button type="button" class="button tiny" data-action="done" data-id="${id}" data-focus-key="done:${id}">Done</button>`
            + `<button type="button" class="button tiny subtle" data-action="dismiss" data-id="${id}" data-focus-key="dismiss:${id}">Dismiss</button>`;
    const capability = action.tag ? `<span class="chip">${esc(action.tag === "New" ? "New capability" : action.tag)}</span>` : "";
    // The engine's "why" already inlines the first reasons, so a full list replaces it rather than repeating it.
    const listed = showReasons && Array.isArray(action.reasons) && action.reasons.length > 1;
    const reasons = listed ? `<ul class="reasons">${action.reasons.map(reason => `<li>${esc(reason)}</li>`).join("")}</ul>` : "";
    const renews = listed ? fmtDay(action.metrics?.renewal, { year: true }) : DASH;
    const why = renews !== DASH ? `Renews ${renews}.` : action.why;
    return `<li class="action-row priority-${esc(String(action.priority).toLowerCase())}${closed ? " closed" : ""}">`
        + `<div class="action-body"><div class="meta">${tag(action.priority)}${newTag(action)}`
        + `<span class="kind ${action.kind === "risk" ? "risk" : "opportunity"}">${action.kind === "risk" ? "Risk" : "Opportunity"}</span>`
        + `${showFamily ? familyChip(action.family) : ""}${capability}</div>`
        + `<p class="title">${showAccount ? `${accountLink(model, action.account_id, action.account_name)}<span class="sep" aria-hidden="true">·</span>` : ""}${esc(action.title)}</p>`
        + `<p class="why">${esc(why)}</p>${reasons}</div><div class="action-controls">${controls}</div></li>`;
}

/** "Show all" toggle; it stays in place as "Show fewer" so keyboard focus survives expanding a list. */
function moreButton(key, total, limit, expanded, noun = "") {
    if (total <= limit) return "";
    const label = expanded ? "Show fewer" : `Show all ${total}${noun ? ` ${noun}` : ""}`;
    return `<button type="button" class="button subtle more" data-more="${esc(key)}" data-focus-key="more:${esc(key)}" aria-expanded="${expanded}">${esc(label)}</button>`;
}

function emptyActions(kind, family) {
    const what = kind === "risk" ? "risks" : kind === "opportunity" ? "opportunities" : "actions";
    return `No open ${what}${family ? ` for ${esc(FAMILY_LABELS[family])}` : ""}.`;
}

function actionsPanel(model, ui, { family = null, key, limit = 8, title = "Needs attention" }) {
    const kind = ui.kind?.[key] || "all";
    const showClosed = Boolean(ui.showClosed);
    const all = actionsFor(model, { family, kind: kind === "all" ? null : kind, includeClosed: showClosed });
    const expanded = Boolean(ui.expanded?.has(`actions:${key}`));
    const rows = expanded ? all : all.slice(0, limit);
    const body = rows.length
        ? `<ol class="action-list">${rows.map(action => actionRow(model, action, { showFamily: !family || action.family !== family })).join("")}</ol>`
        : `<p class="empty">${emptyActions(kind, family)}</p>`;
    const more = moreButton(`actions:${key}`, all.length, limit, expanded);
    return `<section class="panel" aria-labelledby="h-actions-${esc(key)}"><div class="panel-head">`
        + `<h2 id="h-actions-${esc(key)}">${esc(title)} <span class="count-text">${all.length}</span></h2><div class="panel-tools">`
        + segmentControl(`kind:${key}`, kind, [["all", "All"], ["risk", "Risks"], ["opportunity", "Opportunities"]], "Action type")
        + `<label class="toggle"><input type="checkbox" data-toggle="closed" data-focus-key="closed:${esc(key)}"${showClosed ? " checked" : ""}> Show done</label>`
        + `</div></div>${body}${more}</section>`;
}

/** The model appends " since <day>" after a reporting gap; show that as a date range, as the email does. */
function changeWhen(event) {
    const text = String(event.text || "");
    const suffix = isDay(event.since) ? ` since ${event.since}` : "";
    if (suffix && text.endsWith(suffix)) {
        return { text: text.slice(0, -suffix.length), when: `${fmtDay(event.since)} – ${fmtDay(event.date)}`,
            title: `Previous value reported ${fmtDay(event.since, { year: true })}` };
    }
    return { text, when: fmtDay(event.date), title: "" };
}

function changeRow(model, event, { showFamily = true, showAccount = true } = {}) {
    const { text, when, title } = changeWhen(event);
    return `<li class="change-row${event.reverted ? " reverted" : ""}"><div class="meta">${tag(event.priority)}${newTag(event)}`
        + `${showFamily ? familyChip(event.family) : ""}<time datetime="${esc(event.date)}"${title ? ` title="${esc(title)}"` : ""}>${esc(when)}</time>`
        + `${event.reverted ? '<span class="chip">Reverted</span>' : ""}</div>`
        + `${showAccount ? `<p class="who">${accountLink(model, event.account_id, event.account_name)}</p>` : ""}`
        + `<p class="change-text">${esc(text)}</p></li>`;
}

function changesPanel(model, ui, { family = null, key, limit = 10 }) {
    const windowDays = model.changes_window_days || 7;
    const events = eventsFor(model, { family, recentOnly: true, includeReverted: Boolean(family), minPriority: family ? null : "Notify" });
    const expanded = Boolean(ui.expanded?.has(`changes:${key}`));
    const rows = expanded ? events : events.slice(0, limit);
    const body = rows.length
        ? `<ul class="change-list">${rows.map(event => changeRow(model, event, { showFamily: !family })).join("")}</ul>`
        : `<p class="empty">No seat changes in the last ${windowDays} days.</p>`;
    const more = moreButton(`changes:${key}`, events.length, limit, expanded);
    return `<section class="panel" aria-labelledby="h-changes-${esc(key)}"><div class="panel-head"><h2 id="h-changes-${esc(key)}">`
        + `Seat changes <span class="muted">last ${windowDays} days</span></h2></div>${body}${more}</section>`;
}

function renewalsPanel(model, ui) {
    const horizon = model.thresholds?.renewal_days ?? 120;
    const soon = model.accounts.filter(account => isNumber(account.renewing_in_days) && account.renewing_in_days >= 0 && account.renewing_in_days <= horizon)
        .sort((a, b) => a.renewing_in_days - b.renewing_in_days);
    const attention = attentionIndex(model);
    const expanded = Boolean(ui.expanded?.has("renewals"));
    const rows = expanded ? soon : soon.slice(0, 6);
    const body = rows.length ? `<ul class="renewal-list">${rows.map(account => {
        const top = attention.get(account.id)?.top;
        return `<li><div>${accountLink(model, account.id, account.name)}<span class="sub">${esc(fmtDay(account.renewal, { year: true }))} · in ${account.renewing_in_days} ${plural(account.renewing_in_days, "day")}</span></div>`
            + `<div class="right">${top && priorityRank(top) <= 1 ? tag(top) : ""}${money(account.open_renewal_arr ?? account.arr)}</div></li>`;
    }).join("")}</ul>` : `<p class="empty">No renewals in the next ${horizon} days.</p>`;
    const more = moreButton("renewals", soon.length, 6, expanded);
    return `<section class="panel" aria-labelledby="h-renewals"><div class="panel-head"><h2 id="h-renewals">Renewals <span class="muted">next ${horizon} days</span></h2></div>${body}${more}</section>`;
}

function compareValues(a, b, dir) {
    const missing = value => value === null || value === undefined || (typeof value === "number" && Number.isNaN(value));
    if (missing(a) || missing(b)) return missing(a) === missing(b) ? 0 : missing(a) ? 1 : -1;
    if (Array.isArray(a)) {
        for (let index = 0; index < a.length; index += 1) {
            const result = compareValues(a[index], b[index], 1);
            if (result) return result * dir;
        }
        return 0;
    }
    if (typeof a === "string" || typeof b === "string") return String(a).localeCompare(String(b)) * dir;
    return (a < b ? -1 : a > b ? 1 : 0) * dir;
}

/** Sortable table. Columns: {key, label, numeric, value(row), cell(row), title}. The first column is the row header. */
function table(ui, { id, columns, rows, defaultSort = "attention", defaultDir = 1, noun = "accounts", empty }) {
    if (!rows.length) return `<p class="empty">${empty}</p>`;
    const sort = ui.sort?.[id] || { key: defaultSort, dir: defaultDir };
    const column = columns.find(item => item.key === sort.key) || columns[0];
    const sorted = [...rows].sort((a, b) => compareValues(column.value(a), column.value(b), sort.dir)
        || String(a.name || "").localeCompare(String(b.name || "")));
    const expanded = Boolean(ui.expanded?.has(`table:${id}`));
    const visible = expanded ? sorted : sorted.slice(0, ROW_LIMIT);
    const head = columns.map(item => {
        const active = item.key === column.key;
        const aria = active ? ` aria-sort="${sort.dir === 1 ? "ascending" : "descending"}"` : "";
        return `<th scope="col" class="${item.numeric ? "num" : ""}"${aria}${item.title ? ` title="${esc(item.title)}"` : ""}>`
            + `<button type="button" data-sort="${esc(item.key)}" data-table="${esc(id)}" data-focus-key="sort:${esc(id)}:${esc(item.key)}">${esc(item.label)}`
            + `${active ? `<span class="arrow" aria-hidden="true">${sort.dir === 1 ? "↑" : "↓"}</span>` : ""}</button></th>`;
    }).join("");
    const body = visible.map(row => `<tr>${columns.map((item, index) => index === 0
        ? `<th scope="row">${item.cell(row)}</th>`
        : `<td class="${item.numeric ? "num" : ""}">${item.cell(row)}</td>`).join("")}</tr>`).join("");
    const more = moreButton(`table:${id}`, sorted.length, ROW_LIMIT, expanded, noun);
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
}

function matches(account, query) {
    const text = String(query || "").trim().toLowerCase();
    if (!text) return true;
    return [account.name, account.parent, account.id, account.industry, account.country, account.segment]
        .some(value => typeof value === "string" && value.toLowerCase().includes(text));
}

function searchBox(ui, key) {
    return `<label class="search"><span class="visually-hidden">Search accounts</span><input type="search" data-search placeholder="Search accounts  /"`
        + ` value="${esc(ui.query || "")}" data-focus-key="search:${esc(key)}" maxlength="80" autocomplete="off" spellcheck="false"></label>`;
}

function accountCell(model, account) {
    const sub = [account.segment, account.industry, account.country].filter(value => typeof value === "string" && value).join(" · ");
    return `${accountLink(model, account.id, account.name)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`;
}

function attentionColumn(model) {
    const index = attentionIndex(model);
    return {
        key: "attention", label: "Attention", title: "Most urgent open action or recent seat change",
        value: account => {
            const entry = index.get(account.id);
            return [entry ? entry.rank : PRIORITIES.length, -(account.arr || 0)];
        },
        cell: account => {
            const entry = index.get(account.id);
            if (!entry || !entry.top) return `<span class="none">${DASH}</span>`;
            return `${tag(entry.top)}${entry.actions ? `<span class="sub">${entry.actions} ${plural(entry.actions, "action")}</span>` : ""}`;
        },
    };
}

const BASIS_SERIES = { contracted: "contracted", metered: "metered", billable: "ubu", committers: "committers", assigned: "assigned" };

function seatCell(account, family, windowDays) {
    const { value, basis } = productSeats(account, family);
    if (value === null) return `<span class="none">${DASH}</span>`;
    const change = seriesChange(account.products[family]?.series?.[BASIS_SERIES[basis]], windowDays);
    const note = basis !== "contracted" ? ` <span class="basis">${esc(basis)}</span>` : "";
    return `${fmtInt(value)}${note}${change && Math.round(change.delta) ? ` ${delta(change.delta, { since: change.since })}` : ""}`;
}

function measureCell(product, measure, windowDays, { invert = false } = {}) {
    const value = product?.[measure];
    if (!isNumber(value)) return `<span class="none">${DASH}</span>`;
    const change = seriesChange(product.series?.[measure], windowDays);
    return `${fmtInt(value)}${change && Math.round(change.delta) ? ` ${delta(change.delta, { since: change.since, invert })}` : ""}`;
}

const nameColumn = model => ({ key: "name", label: "Account", value: account => String(account.name || "").toLowerCase(), cell: account => accountCell(model, account) });
const arrColumn = { key: "arr", label: "ARR", numeric: true, value: account => account.arr, cell: account => money(account.arr) };
const renewalColumn = {
    key: "renewal", label: "Renewal", numeric: true, value: account => account.renewing_in_days ?? null,
    cell: account => account.renewal ? `${esc(fmtDay(account.renewal, { year: true }))}${isNumber(account.renewing_in_days) && account.renewing_in_days >= 0 ? `<span class="sub">in ${account.renewing_in_days} ${plural(account.renewing_in_days, "day")}</span>` : ""}` : DASH,
};

function accountsPanel(model, ui) {
    const windowDays = model.changes_window_days || 7;
    const rows = model.accounts.filter(account => matches(account, ui.query));
    const seat = family => ({
        key: family, label: FAMILY_LABELS[family], numeric: true, title: `${FAMILY_LABELS[family]} seats (contracted, or usage when there is no contract)`,
        value: account => productSeats(account, family).value, cell: account => seatCell(account, family, windowDays),
    });
    const html = table(ui, {
        id: "accounts", rows, columns: [nameColumn(model), arrColumn, renewalColumn, seat("ghe"), seat("ghas"), seat("ghcp"), attentionColumn(model)],
        empty: ui.query ? `No accounts match “${esc(ui.query)}”.` : "No accounts in your book.",
    });
    return `<section class="panel" aria-labelledby="h-accounts"><div class="panel-head"><h2 id="h-accounts">Accounts <span class="count-text">${rows.length}</span></h2>`
        + `<div class="panel-tools">${searchBox(ui, "accounts")}</div></div><div data-region="accounts">${html}</div></section>`;
}

function kpiTile(label, kpi, { invert = false, title = "", modelDate = null } = {}) {
    const value = isObject(kpi) ? kpi.value : kpi;
    const change = isObject(kpi) && isNumber(kpi.d7) ? delta(kpi.d7, { since: kpi.d7_since, invert, inline: true }) : "";
    const asOf = isObject(kpi) && kpi.as_of && kpi.as_of !== modelDate ? `<span class="as-of">as of ${esc(fmtDay(kpi.as_of))}</span>` : "";
    return `<div class="kpi"${title ? ` title="${esc(title)}"` : ""}><span class="kpi-label">${esc(label)}</span>`
        + `<strong class="kpi-value">${typeof value === "string" ? value : fmtInt(value)}</strong><span class="kpi-sub">${change}${asOf}</span></div>`;
}

function healthChips(categories) {
    const counts = isObject(categories) ? categories : {};
    const chips = ["Red", "Yellow", "Green"].filter(category => counts[category]).map(category =>
        `<span class="health ${category}" title="${counts[category]} ${plural(counts[category], "account")} with ${category} Copilot health">${counts[category]} ${category}</span>`).join("");
    return chips || DASH;
}

function productCard(model, family) {
    const kpis = model.kpis?.[family] || {};
    const view = FAMILY_VIEW[family];
    let facts;
    let series;
    let seriesLabel;
    if (family === "ghe") {
        facts = [["Metered users", kpiFact(kpis.metered)], ["Billable users", kpiFact(kpis.ubu)], ["Adoption", fmtPct(kpis.adoption)]];
        series = kpis.contracted?.series;
        seriesLabel = "GHE contracted seats";
    } else if (family === "ghas") {
        facts = [["Active committers", kpiFact(kpis.committers)], ["Utilization", fmtPct(kpis.utilization)], ["Code Quality accounts", fmtInt(kpis.code_quality_accounts)]];
        series = kpis.committers?.series;
        seriesLabel = "GHAS active committers";
    } else {
        facts = [["Assigned seats", kpiFact(kpis.assigned)], ["Users at risk", fmtInt(kpis.at_risk)], ["Health", healthChips(kpis.categories)]];
        series = kpis.assigned?.series;
        seriesLabel = "Copilot assigned seats";
    }
    const contracted = kpis.contracted || {};
    const open = actionsFor(model, { family });
    const high = open.filter(action => action.priority === "High").length;
    const asOf = contracted.as_of && contracted.as_of !== model.date ? ` <span class="as-of">as of ${esc(fmtDay(contracted.as_of))}</span>` : "";
    return `<a class="product-card" href="#/${view}" data-view="${view}"><div class="card-head"><h2>${esc(FAMILY_LABELS[family])}</h2>`
        + `<span class="muted">${fmtInt(kpis.accounts)} ${plural(kpis.accounts, "account")}</span></div>`
        + `<p class="figure"><span class="figure-value">${fmtInt(contracted.value)}</span><span class="figure-label">contracted seats${asOf}</span>`
        + `${isNumber(contracted.d7) ? delta(contracted.d7, { since: contracted.d7_since, inline: true }) : ""}</p>`
        + `${sparkline(series, { label: seriesLabel })}<dl class="card-facts">${facts.map(([term, value]) => `<div><dt>${esc(term)}</dt><dd>${value}</dd></div>`).join("")}</dl>`
        + `<p class="card-foot"><span>${open.length ? `${open.length} open ${plural(open.length, "action")}${high ? ` · <span class="hot">${high} High</span>` : ""}` : "No open actions"}</span>`
        + '<span class="go" aria-hidden="true">→</span></p></a>';
}

function kpiFact(kpi) {
    if (!isObject(kpi) || !isNumber(kpi.value)) return DASH;
    return `${fmtInt(kpi.value)}${isNumber(kpi.d7) && Math.round(kpi.d7) ? ` ${delta(kpi.d7, { since: kpi.d7_since })}` : ""}`;
}

function renderOverview(model, ui) {
    const overall = model.kpis?.overall || {};
    const open = actionsFor(model);
    const high = open.filter(action => action.priority === "High").length;
    const recent = eventsFor(model, { recentOnly: true, includeReverted: false, minPriority: "Notify" }).length;
    const stats = [["High priority", high, high ? "hot" : ""], ["Open actions", open.length, ""], ["New since last digest", newCount(model), ""],
        [`Seat changes, ${model.changes_window_days || 7} days`, recent, ""]];
    return `<section class="page-head"><div><p class="eyebrow">${esc(longDay(model.date))}</p><h1>Your accounts today</h1>`
        + `<p class="lede">${fmtInt(overall.accounts ?? model.accounts.length)} accounts · ${money(overall.arr)} ARR · ${fmtInt(overall.renewing_120)} renewing within ${model.thresholds?.renewal_days ?? 120} days</p></div>`
        + `<dl class="headline-stats">${stats.map(([term, value, tone]) => `<div class="${tone}"><dt>${esc(term)}</dt><dd>${fmtInt(value)}</dd></div>`).join("")}</dl></section>`
        + `<section class="product-cards" aria-label="Products">${productCard(model, "ghe")}${productCard(model, "ghas")}${productCard(model, "ghcp")}</section>`
        + `<div class="columns"><div class="col-main">${actionsPanel(model, ui, { key: "overview" })}</div>`
        + `<div class="col-side">${changesPanel(model, ui, { key: "overview", limit: 8 })}${renewalsPanel(model, ui)}</div></div>`
        + accountsPanel(model, ui);
}

function newCount(model) {
    return actionsFor(model).filter(action => action.new).length + model.events.filter(event => event.new).length;
}

function kpiStrip(model, family) {
    const kpis = model.kpis?.[family] || {};
    const date = model.date;
    let tiles;
    if (family === "ghe") {
        tiles = [kpiTile("Contracted seats", kpis.contracted, { modelDate: date }), kpiTile("Metered users", kpis.metered, { modelDate: date, title: "Consumed GHE licenses on metered billing" }),
            kpiTile("Billable users", kpis.ubu, { modelDate: date, title: "Unique billable users on volume licensing (monthly)" }),
            kpiTile("Adoption", fmtPct(kpis.adoption), { title: "Billable users ÷ contracted seats, volume accounts" }), kpiTile("Accounts", kpis.accounts)];
    } else if (family === "ghas") {
        tiles = [kpiTile("Contracted seats", kpis.contracted, { modelDate: date }), kpiTile("Active committers", kpis.committers, { modelDate: date }),
            kpiTile("Utilization", fmtPct(kpis.utilization), { title: "Active committers ÷ contracted seats" }),
            kpiTile("Code Quality accounts", kpis.code_quality_accounts), kpiTile("Accounts", kpis.accounts)];
    } else {
        tiles = [kpiTile("Contracted seats", kpis.contracted, { modelDate: date, title: "Contracted Copilot seats (monthly)" }),
            kpiTile("Assigned seats", kpis.assigned, { modelDate: date }), kpiTile("Users at risk", kpis.at_risk, { title: "Assigned users flagged at risk by Copilot health" }),
            kpiTile("Health", healthChips(kpis.categories), { title: "Accounts by Copilot health category" }), kpiTile("Accounts", kpis.accounts)];
    }
    return `<section class="kpi-strip" aria-label="${esc(FAMILY_LABELS[family])} totals">${tiles.join("")}</section>`;
}

function gheTable(model, ui, rows, windowDays) {
    const ghe = account => account.products.ghe;
    return table(ui, {
        id: "ghe", rows, empty: emptyTable(ui, "GHE"), columns: [
            nameColumn(model),
            { key: "billing", label: "Billing", value: account => ghe(account).billing, cell: account => esc(ghe(account).billing || DASH) },
            { key: "contracted", label: "Contracted", numeric: true, value: account => ghe(account).contracted, cell: account => measureCell(ghe(account), "contracted", windowDays) },
            { key: "metered", label: "Metered", numeric: true, title: "Consumed licenses on metered billing", value: account => ghe(account).metered, cell: account => measureCell(ghe(account), "metered", windowDays) },
            { key: "ubu", label: "Billable", numeric: true, title: "Unique billable users (volume, monthly)", value: account => ghe(account).ubu, cell: account => measureCell(ghe(account), "ubu", windowDays) },
            { key: "adoption", label: "Adoption", numeric: true, title: "Billable users ÷ contracted seats", value: account => ghe(account).adoption, cell: account => fmtPct(ghe(account).adoption) },
            renewalColumn, attentionColumn(model),
        ],
    });
}

function ghasTable(model, ui, rows, windowDays) {
    const ghas = account => account.products.ghas;
    const utilization = account => (ghas(account).contracted || 0) > 0 && isNumber(ghas(account).committers) ? ghas(account).committers / ghas(account).contracted : null;
    return table(ui, {
        id: "ghas", rows, empty: emptyTable(ui, "GHAS"), columns: [
            nameColumn(model),
            { key: "contracted", label: "Contracted", numeric: true, value: account => ghas(account).contracted, cell: account => measureCell(ghas(account), "contracted", windowDays) },
            { key: "committers", label: "Committers", numeric: true, title: "Active committers", value: account => ghas(account).committers, cell: account => measureCell(ghas(account), "committers", windowDays) },
            { key: "utilization", label: "Utilization", numeric: true, value: utilization, cell: account => fmtPct(utilization(account)) },
            { key: "code_scanning", label: "Code scanning", numeric: true, title: "Active committers covered by code scanning", value: account => ghas(account).code_scanning, cell: account => fmtInt(ghas(account).code_scanning) },
            { key: "secret_scanning", label: "Secret scanning", numeric: true, title: "Active committers covered by secret scanning", value: account => ghas(account).secret_scanning, cell: account => fmtInt(ghas(account).secret_scanning) },
            { key: "licensing", label: "Licensing", value: account => ghas(account).licensing_model, cell: account => esc(ghas(account).licensing_model || DASH) },
            { key: "code_quality", label: "Code Quality", numeric: true, title: "Active repositories using Code Quality", value: account => ghas(account).code_quality?.repos_active ?? null, cell: account => isObject(ghas(account).code_quality) ? `${fmtInt(ghas(account).code_quality.repos_active)} <span class="basis">repos</span>` : `<span class="none">${DASH}</span>` },
            attentionColumn(model),
        ],
    });
}

function copilotTable(model, ui, rows, windowDays) {
    const cp = account => account.products.ghcp;
    const plan = account => {
        const parts = [["Business", cp(account).business], ["Enterprise", cp(account).enterprise], ["Standalone", cp(account).standalone]].filter(([, value]) => value);
        return parts.length ? parts.map(([name, value]) => `${esc(name)} ${fmtInt(value)}`).join("<br>") : DASH;
    };
    return table(ui, {
        id: "ghcp", rows, empty: emptyTable(ui, "Copilot"), columns: [
            nameColumn(model),
            { key: "plan", label: "Plan", value: account => cp(account).enterprise ? "Enterprise" : cp(account).business ? "Business" : null, cell: plan },
            { key: "contracted", label: "Contracted", numeric: true, value: account => cp(account).contracted, cell: account => measureCell(cp(account), "contracted", windowDays) },
            { key: "assigned", label: "Assigned", numeric: true, value: account => cp(account).assigned, cell: account => measureCell(cp(account), "assigned", windowDays) },
            { key: "at_risk", label: "At risk", numeric: true, title: "Assigned users flagged at risk", value: account => cp(account).at_risk, cell: account => measureCell(cp(account), "at_risk", windowDays, { invert: true }) },
            { key: "category", label: "Health", value: account => ({ Red: 0, Yellow: 1, Green: 2 })[cp(account).health?.category] ?? null, cell: account => health(cp(account).health?.category) },
            { key: "grr", label: "GRR 30d", numeric: true, title: "Gross revenue retention, 30 days", value: account => frac(cp(account).health?.grr_30d), cell: account => fmtPct(frac(cp(account).health?.grr_30d)) },
            { key: "engaged", label: "Engaged", numeric: true, title: "Users engaged at least one day in 28", value: account => cp(account).funnel?.engaged_1d28 ?? null, cell: account => fmtInt(cp(account).funnel?.engaged_1d28) },
            { key: "billed", label: "Billed LTM", numeric: true, value: account => cp(account).billed_ltm ?? null, cell: account => money(cp(account).billed_ltm) },
            attentionColumn(model),
        ],
    });
}

const emptyTable = (ui, product) => ui.query ? `No ${esc(product)} accounts match “${esc(ui.query)}”.` : `No accounts with ${esc(product)} yet.`;

function copilotMix(model) {
    const cp = model.accounts.map(account => account.products.ghcp).filter(product => product && ((product.contracted || 0) > 0 || product.assigned));
    const counts = model.kpis?.ghcp?.categories || {};
    const total = ["Red", "Yellow", "Green"].reduce((sum, category) => sum + (counts[category] || 0), 0);
    const bar = total
        ? `<div class="mix-bar" role="img" aria-label="${["Red", "Yellow", "Green"].map(category => `${counts[category] || 0} ${category}`).join(", ")}">`
            + ["Red", "Yellow", "Green"].filter(category => counts[category]).map(category =>
                `<span class="seg ${category}" style="width:${((counts[category] / total) * 100).toFixed(2)}%"></span>`).join("") + "</div>"
            + `<p class="legend">${["Red", "Yellow", "Green"].map(category => `<span class="health ${category}">${category} ${counts[category] || 0}</span>`).join("")}</p>`
        : '<p class="empty">No Copilot health scores yet.</p>';
    const steps = [["assigned", "Assigned"], ["active_1d28", "Active 28d"], ["engaged_1d28", "Engaged 28d"]];
    // A step no account reports stays blank: missing data must never read as zero users.
    const reporting = Object.fromEntries(steps.map(([key]) => [key, cp.filter(product => isNumber(product.funnel?.[key]))]));
    const sums = Object.fromEntries(steps.map(([key]) => [key, reporting[key].length ? reporting[key].reduce((sum, product) => sum + product.funnel[key], 0) : null]));
    const top = sums.assigned || Math.max(0, ...Object.values(sums).filter(isNumber));
    const funnel = top
        ? `<ol class="funnel">${steps.map(([key, label]) => {
            const value = sums[key];
            const share = isNumber(value) ? Math.max(0, Math.min(100, (value / top) * 100)) : 0;
            const partial = isNumber(value) && reporting[key].length < cp.length ? ` title="Reported by ${reporting[key].length} of ${cp.length} accounts"` : "";
            return `<li${partial}><span class="funnel-label">${label}</span><span class="funnel-bar"><span style="width:${share.toFixed(2)}%"></span></span>`
                + `<span class="funnel-value">${fmtInt(value)}<span class="sub">${key === "assigned" || !isNumber(value) ? "" : `${share.toFixed(0)}%`}</span></span></li>`;
        }).join("")}</ol><p class="note">Users with at least one day of activity in the last 28 days, summed across accounts. A dash means not reported.</p>`
        : '<p class="empty">No Copilot usage funnel yet.</p>';
    return `<div class="columns even"><section class="panel" aria-labelledby="h-mix"><div class="panel-head"><h2 id="h-mix">Health mix</h2></div>${bar}</section>`
        + `<section class="panel" aria-labelledby="h-funnel"><div class="panel-head"><h2 id="h-funnel">Usage funnel</h2></div>${funnel}</section></div>`;
}

function whitespacePanel(model, ui, family) {
    const list = whitespace(model, family);
    const title = family === "ghe" ? "Accounts without GHE" : `GHE accounts without ${FAMILY_LABELS[family]}`;
    const columns = [nameColumn(model), arrColumn,
        ...(family === "ghe" ? [] : [{ key: "ghe", label: "GHE seats", numeric: true, value: account => productSeats(account, "ghe").value, cell: account => fmtInt(productSeats(account, "ghe").value) }]),
        renewalColumn];
    const html = table(ui, { id: `ws-${family}`, rows: list, columns, defaultSort: "arr", defaultDir: -1, empty: "Every account already has it." });
    return `<section class="panel" aria-labelledby="h-ws-${family}"><div class="panel-head"><h2 id="h-ws-${family}">${esc(title)} <span class="count-text">${list.length}</span></h2></div>${html}</section>`;
}

function renderProduct(model, ui, family) {
    const windowDays = model.changes_window_days || 7;
    const rows = model.accounts.filter(account => hasProduct(account, family) && matches(account, ui.query));
    const key = FAMILY_VIEW[family];
    const tableHtml = family === "ghe" ? gheTable(model, ui, rows, windowDays) : family === "ghas" ? ghasTable(model, ui, rows, windowDays) : copilotTable(model, ui, rows, windowDays);
    const note = family === "ghcp"
        ? '<p class="note">For editor, model and premium-request detail, open the Copilot adoption canvas.</p>' : "";
    return `<section class="page-head"><div><p class="eyebrow">${esc(longDay(model.date))}</p><h1>${esc(PRODUCT_TITLES[family])}</h1>`
        + `<p class="lede">${fmtInt(model.kpis?.[family]?.accounts)} ${plural(model.kpis?.[family]?.accounts, "account")} with ${esc(FAMILY_LABELS[family])}</p></div></section>`
        + kpiStrip(model, family) + (family === "ghcp" ? copilotMix(model) : "")
        + `<div class="columns"><div class="col-main">${actionsPanel(model, ui, { family, key, title: "Actions" })}</div>`
        + `<div class="col-side">${changesPanel(model, ui, { family, key })}</div></div>`
        + `<section class="panel" aria-labelledby="h-table-${key}"><div class="panel-head"><h2 id="h-table-${key}">${esc(FAMILY_LABELS[family])} accounts <span class="count-text">${rows.length}</span></h2>`
        + `<div class="panel-tools">${searchBox(ui, key)}</div></div><div data-region="${esc(family)}">${tableHtml}</div></section>`
        + whitespacePanel(model, ui, family) + note;
}

function renderSignals(model) {
    const signals = (Array.isArray(model.signals) ? model.signals : []).filter(isObject)
        .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")) || priorityRank(a.priority) - priorityRank(b.priority));
    const head = '<section class="page-head"><div><h1>Signals</h1><p class="lede">Conversations, news and public posts that point to engineering or business priorities.</p></div></section>';
    if (!signals.length) {
        return `${head}<section class="panel empty-state"><h2>No signals yet</h2><p>This tab will gather:</p><ul>`
            + "<li>Gong call themes that mention competitors or GitHub</li><li>ZoomInfo intent topics and company news</li>"
            + "<li>Public web posts, such as engineering blogs and press, about developer platforms and AI in the SDLC</li></ul>"
            + "<p>Competitor mentions and priorities that GitHub solves will be marked High.</p>"
            + '<p class="note">LinkedIn is not monitored, because its terms do not allow automated reading of member posts.</p></section>';
    }
    return `${head}<section class="panel"><ul class="signal-list">${signals.map(signal => {
        const url = safeUrl(signal.url);
        const tags = Array.isArray(signal.tags) ? signal.tags.filter(value => typeof value === "string").map(value => `<span class="chip">${esc(value)}</span>`).join("") : "";
        return `<li class="signal"><div class="meta">${PRIORITIES.includes(signal.priority) ? tag(signal.priority) : ""}${newTag(signal)}`
            + `${signal.source ? `<span class="family">${esc(signal.source)}</span>` : ""}<time>${esc(fmtDay(signal.date))}</time>${tags}</div>`
            + `<p class="who">${signal.account_id ? accountLink(model, signal.account_id, signal.account_name) : esc(signal.account_name || "")}</p>`
            + `<p class="title">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(signal.title || url)}</a>` : esc(signal.title || "")}</p>`
            + `${signal.summary ? `<p class="why">${esc(signal.summary)}</p>` : ""}</li>`;
    }).join("")}</ul></section>`;
}

function pct(value) {
    return isNumber(value) ? `${Math.round(value * 100)}%` : "?";
}

function renderData(model) {
    const th = model.thresholds || {};
    const sources = `<div class="table-wrap"><table><thead><tr><th scope="col">Source</th><th scope="col">Query</th><th scope="col">State</th>`
        + '<th scope="col">Data as of</th><th scope="col">Collected</th><th scope="col" class="num">Rows</th><th scope="col">Via</th><th scope="col">Notes</th></tr></thead><tbody>'
        + model.sources.map(source => `<tr><th scope="row">${esc(source.label)}</th><td>${esc(source.qid)}</td>`
            + `<td><span class="state-chip ${esc(source.state)}">${esc(SOURCE_STATES[source.state] || source.state)}</span></td>`
            + `<td class="nowrap">${esc(fmtDay(source.as_of, { year: true }))}</td><td class="nowrap">${esc(fmtDay(source.raw_date, { year: true }))}</td>`
            + `<td class="num">${fmtInt(source.rows)}</td><td class="nowrap">${esc(source.via || DASH)}</td>`
            + `<td>${[...(source.messages || []), ...(source.held?.length ? [`held: ${source.held.map(day => fmtDay(day)).join(", ")}`] : [])].map(esc).join("<br>") || DASH}</td></tr>`).join("")
        + "</tbody></table></div>";
    const changes = [
        ["Contracted seats", `GHE, GHAS and Copilot seats on contract, compared day to day at product level. Any change is reported: a drop of at least ${th.contracted_high_abs ?? "?"} seats, or ${pct(th.contracted_high_pct)} and at least ${th.contracted_high_min_abs ?? "?"} seats, is High, a smaller drop Medium, a product going to zero High (Medium under ${th.contracted_high_min_abs ?? "?"} seats), and an increase or new product Notify. Seats moving between product types that net to about zero are a migration (Info).`],
        ["Usage measures", `Metered users, billable users, GHAS active committers and Copilot assigned seats. A daily change is reported when it is at least ${th.consumed_abs ?? "?"} users and ${pct(th.consumed_pct)}; a drop of at least ${th.consumed_high_abs ?? "?"} users and ${pct(th.consumed_high_pct)} is High. A missing value means not reported, never zero.`],
        ["Weekly trend", `A net change of at least ${th.consumed_7d_abs ?? "?"} users and ${pct(th.consumed_7d_pct)} over 7 days without a matching daily change: up is Notify, down Medium.`],
        ["Reverted", `A change undone within ${th.revert_days ?? "?"} days is shown struck through and is not notified again.`],
        ["Partial loads", `Days whose row count or total looks incomplete (row count under ${pct(th.healthy_rows_ratio)} of normal, or totals off by more than ${pct(th.breaker_drop_pct)}) are held back until confirmed, so a bad load cannot invent churn.`],
        ["New", `Not yet in an email digest. Open actions are flagged New again after ${th.renotify_after_days ?? "?"} days.`],
    ];
    const rules = [
        ["Renewal with risk signals", "Risk", "High", `Renews within ${th.renewal_days ?? "?"} days and has another risk below, or a contracted drop in the last 30 days. Replaces the individual risks.`],
        ["Low GHE seat adoption", "Risk", "Medium", `At least ${th.ghe_adoption_min_seats ?? "?"} contracted seats and billable users under ${pct(th.ghe_adoption_low)} of them. Titled "No GHE usage reported" when no billable users are reported, which often means GHE Server without license sync.`],
        ["GHAS seats under-used", "Risk", "Medium", `At least ${th.ghas_min_seats ?? "?"} seats and active committers under ${pct(th.ghas_committers_low)} of them.`],
        ["Copilot adoption at risk", "Risk", "Medium", `Copilot health is Red, or at least ${pct(th.copilot_risk_share)} of assigned users are at risk.`],
        ["GHE usage above contract", "Opportunity", "High", `Volume or mixed billing and billable users above contract by at least ${th.overage_abs ?? "?"} and ${pct(th.overage_pct)}.`],
        ["GHAS committers above seats", "Opportunity", "High", "Active committers above contracted GHAS seats by the same margin."],
        ["GHAS whitespace", "Opportunity", "Medium", `At least ${th.whitespace_min_seats ?? "?"} GHE seats and no GHAS.`],
        ["Copilot expansion or whitespace", "Opportunity", "Medium", `At least ${th.copilot_penetration_min_ghe ?? "?"} GHE seats and Copilot on under ${pct(th.copilot_penetration_low)} of them.`],
        ["Code Quality in use", "Opportunity", "Medium", "Code Quality has committers or active repositories (a new capability)."],
        ["Move legacy GHAS", "Opportunity", "Medium", `Legacy Advanced Security seats renewing within ${th.renewal_days ?? "?"} days: move to Secret Protection and Code Security.`],
    ];
    return '<section class="page-head"><div><h1>Data</h1><p class="lede">Where the numbers come from and how changes and actions are decided.</p></div></section>'
        + `<section class="panel" aria-labelledby="h-sources"><div class="panel-head"><h2 id="h-sources">Sources</h2></div>${sources}</section>`
        + `<section class="panel" aria-labelledby="h-defs"><div class="panel-head"><h2 id="h-defs">How seat changes are detected</h2></div><dl class="definitions">`
        + changes.map(([term, text]) => `<div><dt>${esc(term)}</dt><dd>${esc(text)}</dd></div>`).join("") + "</dl></section>"
        + `<section class="panel" aria-labelledby="h-rules"><div class="panel-head"><h2 id="h-rules">Action rules</h2></div><div class="table-wrap"><table>`
        + '<thead><tr><th scope="col">Action</th><th scope="col">Type</th><th scope="col">Priority</th><th scope="col">When</th></tr></thead><tbody>'
        + rules.map(([name, kind, priority, when]) => `<tr><th scope="row">${esc(name)}</th><td>${esc(kind)}</td><td>${tag(priority)}</td><td>${esc(when)}</td></tr>`).join("")
        + "</tbody></table></div></section>"
        + `<details class="panel thresholds"><summary>All thresholds</summary><dl class="facts-list">${Object.keys(th).sort().map(key =>
            `<div><dt>${esc(key)}</dt><dd>${esc(JSON.stringify(th[key]))}</dd></div>`).join("")}</dl></details>`;
}

export function renderView(model, ui = {}) {
    if (ui.view === "copilot") {
        const nav = `<nav class="copilot-subnav" aria-label="Copilot views"><a href="#/copilot"${ui.subview !== "seats" ? ' aria-current="page"' : ""}>Adoption dashboard</a>`
            + `<a href="#/copilot/seats"${ui.subview === "seats" ? ' aria-current="page"' : ""}>Seats &amp; actions</a>`
            + '<a href="copilot/" target="_blank" rel="noopener noreferrer">Open Copilot dashboard in its own tab</a></nav>';
        return nav + (ui.subview === "seats" ? renderProduct(model, ui, "ghcp")
            : `<iframe class="copilot-frame" name="copilot-dashboard" title="Copilot adoption dashboard" src="copilot/?embed=1${ui.copilotAccount ? `#/account/${encodeURIComponent(ui.copilotAccount)}` : ""}"></iframe>`);
    }
    if (VIEW_FAMILY[ui.view]) return renderProduct(model, ui, VIEW_FAMILY[ui.view]);
    if (ui.view === "signals") return renderSignals(model);
    if (ui.view === "data") return renderData(model);
    return renderOverview(model, ui);
}

export function renderUnavailable(error) {
    const missing = error?.code === "dashboard_missing";
    return `<section class="panel empty-state"><h1>${missing ? "No snapshot yet" : "The snapshot is unavailable"}</h1><p>${esc(error?.message || "Unknown error.")}</p>`
        + `${missing ? "<p>Use Refresh to run the daily automation, or run it from the Automations view.</p>" : ""}</section>`;
}

// Drawer -------------------------------------------------------------------------------------------

function facts(pairs) {
    const shown = pairs.filter(([, value]) => value !== null && value !== undefined && value !== "" && value !== DASH);
    return shown.length ? `<dl class="facts-list">${shown.map(([term, value]) => `<div><dt>${esc(term)}</dt><dd>${value}</dd></div>`).join("")}</dl>` : "";
}

function byType(types) {
    if (!isObject(types) || !Object.keys(types).length) return null;
    return Object.entries(types).sort((a, b) => (b[1]?.seats || 0) - (a[1]?.seats || 0)).map(([type, info]) =>
        `${esc(typeLabel(type))} ${fmtInt(info?.seats)}${isNumber(info?.arr) ? ` · ${money(info.arr)}` : ""}${info?.since ? ` <span class="sub">since ${esc(fmtDay(info.since, { year: true }))}</span>` : ""}`).join("<br>");
}

function asOf(value, day) {
    if (!isNumber(value)) return null;
    return `${fmtInt(value)}${day ? ` <span class="sub">as of ${esc(fmtDay(day))}</span>` : ""}`;
}

function withChange(product, measure, windowDays, day, options) {
    if (!isNumber(product?.[measure])) return null;
    const change = seriesChange(product.series?.[measure], windowDays);
    return `${asOf(product[measure], day)}${change && Math.round(change.delta) ? ` ${delta(change.delta, { since: change.since, ...options })}` : ""}`;
}

function lastReported(last) {
    return Array.isArray(last) && isNumber(last[1]) ? `${fmtInt(last[1])} <span class="sub">last reported ${esc(fmtDay(last[0]))}</span>` : null;
}

function sparks(pairs) {
    const html = pairs.map(([label, series]) => {
        const svg = sparkline(series, { label });
        return svg ? `<figure class="spark-figure"><figcaption>${esc(label)}</figcaption>${svg}</figure>` : "";
    }).join("");
    return html ? `<div class="spark-row">${html}</div>` : "";
}

function productPanel(title, present, body, missing) {
    if (!present) return `<section class="product-panel absent"><h3>${esc(title)}</h3><p class="empty">${esc(missing)}</p></section>`;
    return `<section class="product-panel"><h3>${esc(title)}</h3>${body}</section>`;
}

function ghePanel(account, windowDays) {
    const ghe = account.products.ghe;
    const detail = isObject(ghe.metered_detail) ? ghe.metered_detail : {};
    const split = [["Cloud", detail.cloud], ["Server only", detail.server], ["Users", detail.users]].filter(([, value]) => isNumber(value))
        .map(([label, value]) => `${label} ${fmtInt(value)}`).join(" · ");
    const body = facts([
        ["Contracted seats", withChange(ghe, "contracted", windowDays, ghe.as_of)],
        ["Billing", ghe.billing ? esc(ghe.billing) : null],
        ["Contract ARR", isNumber(ghe.arr) ? money(ghe.arr) : null],
        ["By product", byType(ghe.by_type)],
        ["Metered licenses", withChange(ghe, "metered", windowDays, ghe.metered_as_of) ?? lastReported(ghe.metered_last)],
        ["Metered split", split || null],
        ["Billable users", withChange(ghe, "ubu", windowDays, ghe.ubu_as_of) ?? lastReported(ghe.ubu_last)],
        ["Adoption", isNumber(ghe.adoption) ? `${fmtPct(ghe.adoption)} <span class="sub">billable ÷ contracted</span>` : null],
        ["Monthly engaged users", asOf(ghe.meu, ghe.engagement_as_of)],
        ["Monthly active users", asOf(ghe.mau, ghe.engagement_as_of)],
    ]) + sparks([["Contracted seats", ghe.series?.contracted], ["Metered licenses", ghe.series?.metered], ["Billable users", ghe.series?.ubu]]);
    return productPanel("GitHub Enterprise", hasProduct(account, "ghe"), body, "No GHE on record.");
}

function ghasPanel(account, windowDays) {
    const ghas = account.products.ghas;
    const cq = isObject(ghas.code_quality) ? ghas.code_quality : null;
    const utilization = (ghas.contracted || 0) > 0 && isNumber(ghas.committers) ? ghas.committers / ghas.contracted : null;
    const body = facts([
        ["Contracted seats", withChange(ghas, "contracted", windowDays, ghas.as_of)],
        ["By product", byType(ghas.by_type)],
        ["Active committers", withChange(ghas, "committers", windowDays, ghas.committers_as_of) ?? lastReported(ghas.committers_last)],
        ["Utilization", isNumber(utilization) ? fmtPct(utilization) : null],
        ["Code scanning committers", isNumber(ghas.code_scanning) ? fmtInt(ghas.code_scanning) : null],
        ["Secret scanning committers", isNumber(ghas.secret_scanning) ? fmtInt(ghas.secret_scanning) : null],
        ["Plan", ghas.plan ? esc(ghas.plan) : null],
        ["Licensing", ghas.licensing_model ? esc(ghas.licensing_model) : null],
        ["Code Quality", cq ? `${fmtInt(cq.committers)} committers · ${fmtInt(cq.repos_active)} of ${fmtInt(cq.total_repos)} repos active${cq.date ? ` <span class="sub">as of ${esc(fmtDay(cq.date))}</span>` : ""}` : null],
        ["Code Quality, 30 days", cq ? `${fmtInt(cq.autofixes_l30d)} autofixes · ${fmtInt(cq.prs_fixed_autofix_l30d)} PRs fixed by autofix · ${fmtInt(cq.prs_fixed_l30d)} fixed · ${fmtInt(cq.prs_not_fixed_l30d)} not fixed` : null],
    ]) + sparks([["Contracted seats", ghas.series?.contracted], ["Active committers", ghas.series?.committers]]);
    return productPanel("GitHub Advanced Security", hasProduct(account, "ghas"), body, "No GHAS on record.");
}

function copilotPanel(account, windowDays) {
    const cp = account.products.ghcp;
    const healthInfo = isObject(cp.health) ? cp.health : {};
    const funnel = isObject(cp.funnel) ? cp.funnel : null;
    const plan = [["Business", cp.business], ["Enterprise", cp.enterprise], ["Standalone", cp.standalone]].filter(([, value]) => value)
        .map(([name, value]) => `${name} ${fmtInt(value)}`).join(" · ");
    const scores = [["Product", healthInfo.product_health], ["Commercial", healthInfo.commercial_health], ["Copilot", healthInfo.copilot_health]]
        .filter(([, value]) => isNumber(value)).map(([name, value]) => `${name} ${Math.round(value)}`).join(" · ");
    const risks = [["MAU", healthInfo.mau_risk], ["Intracohort", healthInfo.intracohort_risk], ["No activity 180d", healthInfo.no_activity_180d_risk], ["PRU/MAU retention", healthInfo.pru_mau_retention_risk]]
        .filter(([, value]) => isNumber(value) && value).map(([name, value]) => `${name} ${fmtInt(value)}`).join(" · ");
    const share = isNumber(cp.at_risk) && cp.assigned ? ` <span class="sub">${fmtPct(cp.at_risk / cp.assigned)} of assigned</span>` : "";
    const funnelHtml = funnel && isNumber(funnel.assigned) && funnel.assigned > 0
        ? `<ol class="funnel compact">${[["assigned", "Assigned"], ["active_1d28", "Active 28d"], ["engaged_1d28", "Engaged 28d"]].map(([key, label]) => {
            const value = isNumber(funnel[key]) ? funnel[key] : null;
            const width = isNumber(value) ? Math.max(0, Math.min(100, (value / funnel.assigned) * 100)) : 0;
            return `<li><span class="funnel-label">${label}</span><span class="funnel-bar"><span style="width:${width.toFixed(2)}%"></span></span><span class="funnel-value">${fmtInt(value)}</span></li>`;
        }).join("")}</ol>` : "";
    const body = facts([
        ["Contracted seats", withChange(cp, "contracted", windowDays, cp.contracted_as_of)],
        ["Plan mix", plan || null],
        ["Assigned seats", withChange(cp, "assigned", windowDays, cp.as_of) ?? lastReported(cp.assigned_last)],
        ["Installed IDE users 28d (partial)", `<span>${fmtInt(funnel?.installed_1d28)}</span>`],
        ["Users at risk", isNumber(cp.at_risk) ? `${fmtInt(cp.at_risk)}${share}` : null],
        ["At-risk reasons", risks || null],
        ["Health", healthInfo.category ? `${health(healthInfo.category)}${scores ? ` <span class="sub">${esc(scores)}</span>` : ""}` : null],
        ["GRR, 30 days", isNumber(healthInfo.grr_30d) ? fmtPct(frac(healthInfo.grr_30d)) : null],
        ["Retention vs 180-day peak", isNumber(healthInfo.retention_180d_hwm) ? `${fmtPct(frac(healthInfo.retention_180d_hwm))}${isNumber(healthInfo.hwm_assigned_180d) ? ` <span class="sub">peak ${fmtInt(healthInfo.hwm_assigned_180d)} assigned</span>` : ""}` : null],
        ["Billed, last 12 months", isNumber(cp.billed_ltm) ? money(cp.billed_ltm) : null],
    ]) + (funnelHtml ? `<h4>Usage, 28 days</h4>${funnelHtml}` : "")
        + sparks([["Assigned seats", cp.series?.assigned], ["Users at risk", cp.series?.at_risk], ["Contracted seats", cp.series?.contracted]]);
    return productPanel("GitHub Copilot", hasProduct(account, "ghcp"), body, "No Copilot on record.");
}

const FLAG_LABELS = {
    g2k: "Global 2000", has_actions: "Uses Actions", has_codespaces: "Uses Codespaces", has_ghec_metered: "Metered GHEC",
    has_cloud_orgs: "Cloud orgs", has_server_orgs: "Server orgs", ghas_enabled: "GHAS enabled",
};

export function renderDrawer(model, ui = {}) {
    const account = accountIndex(model).get(ui.account);
    const close = '<button type="button" class="button subtle icon close" data-close aria-label="Close account details">×</button>';
    if (!account) {
        return `<div class="backdrop" data-close></div><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">`
            + `<header class="drawer-head"><h2 id="drawer-title">Account not found</h2>${close}</header>`
            + '<div class="drawer-body"><p class="empty">This account is not in the current snapshot.</p></div></aside>';
    }
    const windowDays = model.changes_window_days || 7;
    const sub = [account.parent && account.parent !== account.name ? `Part of ${account.parent}` : null, account.segment, account.industry, account.country]
        .filter(value => typeof value === "string" && value).map(esc).join(" · ");
    const sfdc = safeUrl(account.sfdc_url);
    const strip = [
        ["ARR", money(account.arr)],
        ["Renewal", account.renewal ? `${esc(fmtDay(account.renewal, { year: true }))}${isNumber(account.renewing_in_days) && account.renewing_in_days >= 0 ? `<span class="sub">in ${account.renewing_in_days} ${plural(account.renewing_in_days, "day")}</span>` : ""}` : DASH],
        ["Open renewal ARR", money(account.open_renewal_arr)],
        ["Open pipeline", money(account.open_pipeline_net_arr)],
    ];
    const actions = actionsFor(model, { accountId: account.id, includeClosed: true });
    const events = eventsFor(model, { accountId: account.id });
    const other = byType(account.products.other?.by_type);
    const flags = Object.entries(FLAG_LABELS).filter(([key]) => account.flags?.[key]).map(([, label]) => `<span class="chip">${esc(label)}</span>`).join("");
    const billing = isObject(account.billing_ltm) ? [["GHEC", account.billing_ltm.ghec], ["GHAS", account.billing_ltm.ghas], ["Copilot", account.billing_ltm.copilot], ["Actions", account.billing_ltm.actions]]
        .filter(([, value]) => isNumber(value) && value).map(([label, value]) => `${label} ${money(value)}`).join(" · ") : "";
    const support = isObject(account.support) ? [isNumber(account.support.tickets_28d) ? `${fmtInt(account.support.tickets_28d)} tickets in 28 days` : null,
        isNumber(account.support.csat_ltm) ? `CSAT ${account.support.csat_ltm <= 1 ? fmtPct(account.support.csat_ltm) : esc(account.support.csat_ltm)} (12 months)` : null].filter(Boolean).join(" · ") : "";
    return `<div class="backdrop" data-close></div><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">`
        + `<header class="drawer-head"><div><h2 id="drawer-title">${esc(account.name || account.id)}</h2>${sub ? `<p class="sub">${sub}</p>` : ""}</div>`
        + `<div class="drawer-tools">${sfdc ? `<a class="button subtle tiny" href="${esc(sfdc)}" target="_blank" rel="noopener noreferrer">Open in Salesforce</a>` : ""}${close}</div></header>`
        + `<div class="drawer-body"><p><a href="copilot/?embed=1#/account/${encodeURIComponent(account.id)}" data-copilot-account="${esc(account.id)}" target="copilot-dashboard">Open in Copilot dashboard</a></p><dl class="fact-strip">${strip.map(([term, value]) => `<div><dt>${esc(term)}</dt><dd>${value}</dd></div>`).join("")}</dl>`
        + `<section class="drawer-section"><h3>Actions <span class="count-text">${actions.filter(action => !isClosed(action)).length}</span></h3>`
        + `${actions.length ? `<ol class="action-list">${actions.map(action => actionRow(model, action, { showAccount: false, showReasons: true })).join("")}</ol>` : '<p class="empty">No actions for this account.</p>'}</section>`
        + ghePanel(account, windowDays) + ghasPanel(account, windowDays) + copilotPanel(account, windowDays)
        + (other ? `<section class="product-panel"><h3>Other products</h3>${facts([["Contracted", other]])}</section>` : "")
        + `<section class="drawer-section"><h3>Seat changes <span class="muted">last ${esc(model.thresholds?.history_days ?? 35)} days</span></h3>`
        + `${events.length ? `<ul class="change-list timeline">${events.map(event => changeRow(model, event, { showAccount: false })).join("")}</ul>` : '<p class="empty">No seat changes recorded.</p>'}</section>`
        + `${flags || billing || support ? `<section class="drawer-section"><h3>Account context</h3>${flags ? `<p class="chips">${flags}</p>` : ""}${facts([["Billed, last 12 months", billing || null], ["Support", support ? esc(support) : null]])}</section>` : ""}`
        + "</div></aside>";
}
