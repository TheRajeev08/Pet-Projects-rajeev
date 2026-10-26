// Pure data helpers shared by the canvas server, the extension and the browser. No DOM or Node APIs.

export const SCHEMA = 1;
export const PRIORITIES = ["High", "Medium", "Notify", "Info"];
const RANK = Object.fromEntries(PRIORITIES.map((priority, index) => [priority, index]));
export const PRODUCTS = ["ghe", "ghas", "ghcp"];
export const FAMILY_LABELS = { ghe: "GHE", ghas: "GHAS", ghcp: "Copilot", book: "Book", all: "All products" };
export const ACTION_STATES = ["done", "dismissed"];
export const SIGNAL_PRIORITIES = ["High", "Notify", "Info"];
export const SIGNAL_SOURCES = { gong: "Gong", web: "Web", zoominfo: "ZoomInfo" };
export const SOURCE_STATES = {
    ok: "Current",
    stale: "Stale",
    suspect: "Partial load held back",
    failed: "Not refreshed today",
    missing: "No data",
    not_connected: "Not connected",
};
export const VIEWS = ["overview", "copilot", "impact", "ghe", "ghas", "signals", "data"];
export const COPILOT_VIEWS = ["desk", "accounts", "trends", "changes", "notes"];
export const TYPE_LABELS = { GHSP: "Secret Protection", GHCS: "Code Security", "Visual Studio": "Visual Studio bundle" };

const DAY = /^\d{4}-\d{2}-\d{2}$/;
export const QUERY_MAX = 80;
export const isNumber = value => typeof value === "number" && Number.isFinite(value);
export const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
export const priorityRank = priority => RANK[priority] ?? PRIORITIES.length;

export function isDay(value) {
    if (typeof value !== "string" || !DAY.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function addDays(day, count) {
    const parsed = new Date(`${day}T00:00:00Z`);
    parsed.setUTCDate(parsed.getUTCDate() + count);
    return parsed.toISOString().slice(0, 10);
}

/** Whole days between a model date and the viewer's local calendar day. */
export function dayAge(day, now = new Date()) {
    if (!isDay(day)) return null;
    return Math.round((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.parse(`${day}T00:00:00Z`)) / 86400000);
}

/** Checks the structure the canvas relies on and returns the same object. Never mutates. */
export function validateModel(model) {
    if (!isObject(model)) throw new Error("the model is not a JSON object");
    if (model.schema !== SCHEMA) throw new Error(`unsupported model schema ${JSON.stringify(model.schema)} (expected ${SCHEMA})`);
    if (!isDay(model.date)) throw new Error("the model date is missing or invalid");
    for (const key of ["accounts", "events", "actions", "sources"]) {
        if (!Array.isArray(model[key])) throw new Error(`"${key}" must be a list`);
    }
    if (model.signals !== undefined && !Array.isArray(model.signals)) throw new Error('"signals" must be a list');
    if (!isObject(model.kpis)) throw new Error('"kpis" must be an object');
    const accounts = new Set();
    model.accounts.forEach((account, index) => {
        if (!isObject(account) || typeof account.id !== "string" || !account.id) throw new Error(`account ${index} has no id`);
        if (accounts.has(account.id)) throw new Error(`account ${account.id} appears twice`);
        accounts.add(account.id);
        if (!isObject(account.products) || !PRODUCTS.every(product => isObject(account.products[product]))) {
            throw new Error(`account ${account.id} has no product summary`);
        }
    });
    const actions = new Set();
    model.actions.forEach((action, index) => {
        if (!isObject(action) || typeof action.id !== "string" || !action.id) throw new Error(`action ${index} has no id`);
        if (actions.has(action.id)) throw new Error(`action ${action.id} appears twice`);
        actions.add(action.id);
        if (!Object.hasOwn(RANK, action.priority)) throw new Error(`action ${action.id} has unknown priority ${JSON.stringify(action.priority)}`);
    });
    model.events.forEach((event, index) => {
        if (!isObject(event) || typeof event.id !== "string") throw new Error(`event ${index} has no id`);
        if (!Object.hasOwn(RANK, event.priority)) throw new Error(`event ${event.id} has unknown priority ${JSON.stringify(event.priority)}`);
        if (!isDay(event.date)) throw new Error(`event ${event.id} has an invalid date`);
    });
    return model;
}

const indexes = new WeakMap();
export function accountIndex(model) {
    let index = indexes.get(model);
    if (!index) {
        index = new Map(model.accounts.map(account => [account.id, account]));
        indexes.set(model, index);
    }
    return index;
}

export const isClosed = action => ACTION_STATES.includes(action?.status ?? action?.state);

export const isVerifiedSignal = signal => signal.verified === true && signal.verification !== "needs verification";
export const isNewHighSignal = signal => signal.new === true && signal.priority === "High" && isVerifiedSignal(signal) && !isClosed(signal);

/** Signal account search uses canonical account names as well as names carried by the feed. */
export function signalsFor(model, { priority = "all", source = "all", status = "open", query = "", accountId = null, newHigh = false } = {}) {
    const search = clipQuery(query).trim().toLowerCase();
    const index = accountIndex(model);
    return (model.signals || []).filter(isObject).filter(signal =>
        (priority === "all" || signal.priority === priority)
        && (source === "all" || signal.source === source)
        && (status === "all" || (signal.status || signal.state || "open") === status)
        && (!accountId || signal.account_id === accountId)
        && (!newHigh || isNewHighSignal(signal))
        && (!search || [signal.account_id, signal.account_name, signal.name, index.get(signal.account_id)?.name]
            .some(value => String(value || "").toLowerCase().includes(search))))
        .sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))
            || priorityRank(a.priority) - priorityRank(b.priority)
            || String(a.id).localeCompare(String(b.id)));
}

/** Product presence, matching the engine's KPI definitions. */
export function hasProduct(account, family) {
    const product = account?.products?.[family];
    if (!product) return false;
    if (family === "ghe") return (product.contracted || 0) > 0 || Boolean(product.metered) || Boolean(product.ubu);
    if (family === "ghas") return (product.contracted || 0) > 0 || Boolean(product.committers);
    if (family === "ghcp") return (product.contracted || 0) > 0 || Boolean(product.assigned);
    return false;
}

/** The seat figure that best represents a product for an account, with what it measures. */
export function productSeats(account, family) {
    const product = account?.products?.[family] || {};
    const pick = (value, basis) => ({ value, basis });
    if (family === "ghe") {
        if ((product.contracted || 0) > 0) return pick(product.contracted, "contracted");
        if (isNumber(product.metered)) return pick(product.metered, "metered");
        if (isNumber(product.ubu)) return pick(product.ubu, "billable");
    } else if (family === "ghas") {
        if ((product.contracted || 0) > 0) return pick(product.contracted, "contracted");
        if (isNumber(product.committers) && product.committers > 0) return pick(product.committers, "committers");
    } else if (family === "ghcp") {
        if ((product.contracted || 0) > 0) return pick(product.contracted, "contracted");
        if (isNumber(product.assigned) && product.assigned > 0) return pick(product.assigned, "assigned");
    }
    return pick(null, null);
}

export function sortActions(actions) {
    return [...actions].sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority)
        || Number(Boolean(b.new)) - Number(Boolean(a.new))
        || Number(a.kind !== "risk") - Number(b.kind !== "risk")
        || (b.arr || 0) - (a.arr || 0)
        || String(a.account_name || "").localeCompare(String(b.account_name || ""))
        || String(a.id).localeCompare(String(b.id)));
}

/** Actions for a product tab include account-wide ("all") actions for accounts that use the product. */
export function actionsFor(model, { family = null, accountId = null, kind = null, includeClosed = false } = {}) {
    const index = accountIndex(model);
    return sortActions(model.actions.filter(action =>
        (includeClosed || !isClosed(action))
        && (!accountId || action.account_id === accountId)
        && (!kind || action.kind === kind)
        && (!family || action.family === family
            || (action.family === "all" && hasProduct(index.get(action.account_id), family)))));
}

export function eventsFor(model, { family = null, accountId = null, recentOnly = false, includeReverted = true, minPriority = null } = {}) {
    const limit = minPriority ? priorityRank(minPriority) : Infinity;
    return model.events.filter(event =>
        (!family || event.family === family)
        && (!accountId || event.account_id === accountId)
        && (!recentOnly || event.recent)
        && (includeReverted || !event.reverted)
        && priorityRank(event.priority) <= limit)
        .sort((a, b) => b.date.localeCompare(a.date)
            || priorityRank(a.priority) - priorityRank(b.priority)
            || Math.abs(b.delta || 0) - Math.abs(a.delta || 0));
}

const EVENT_MEASURES = {
    contracted: ["seats under contract", "seat"],
    metered: ["metered users", "user"],
    ubu: ["billable users", "user"],
    committers: ["active committers", "committer"],
    assigned: ["assigned seats", "seat"],
};
const eventNumber = value => Math.round(value).toLocaleString("en-US");

/** Presentation only: structured events override saved copy, without reclassifying changes or missing reporting. */
export function formatSeatEvent(event) {
    const fallback = String(event.text || "");
    const measure = Object.hasOwn(EVENT_MEASURES, event.measure) ? EVENT_MEASURES[event.measure] : null;
    if (!measure || typeof event.family !== "string" || !event.family) return fallback;
    const family = FAMILY_LABELS[event.family] || event.family;
    const product = event.product_type ? TYPE_LABELS[event.product_type] || event.product_type : null;
    const what = product ? `${product} ${measure[0]}` : `${family} ${measure[0]}`;
    const a = event.from;
    const b = event.to;
    let text;
    if (["added", "dropped", "new_product", "product_to_zero", "trend_up", "trend_down"].includes(event.kind)) {
        if (!isNumber(a) || !isNumber(b) || !isNumber(event.delta)) return fallback;
        const n = Math.abs(event.delta);
        const units = `${measure[1]}${n === 1 ? "" : "s"}`;
        const movement = event.delta === 0 ? "remained unchanged"
            : `${event.delta > 0 ? "increased" : "decreased"} by ${eventNumber(n)} ${units}`;
        const trend = ["trend_up", "trend_down"].includes(event.kind) ? " over 7+ days" : "";
        text = `${what} ${movement}${trend} (from ${eventNumber(a)} to ${eventNumber(b)})`;
    } else if (event.kind === "stopped" && isNumber(a) && b == null) {
        text = `${what} stopped reporting (last ${eventNumber(a)})`;
    } else if (event.kind === "started" && a == null && isNumber(b)) {
        text = `${what} started reporting at ${eventNumber(b)}`;
    } else if (event.kind === "resumed" && isNumber(a) && isNumber(b)) {
        text = `${what} reporting again (from ${eventNumber(a)} to ${eventNumber(b)})`;
    } else {
        // Migration details and unknown/incomplete events retain the producer's wording verbatim.
        return fallback;
    }
    const suffix = isDay(event.since) ? ` since ${event.since}` : "";
    const trend = ["trend_up", "trend_down"].includes(event.kind);
    if (!trend && suffix && (event.gap_days > 1 || fallback.endsWith(suffix))) text += suffix;
    return text;
}

/** Normalizes the engine's two series shapes ({start, values} daily, or sparse {points}) to [day, value] pairs. */
export function seriesPoints(series) {
    if (!isObject(series)) return [];
    if (Array.isArray(series.points)) {
        return series.points.filter(point => Array.isArray(point) && isDay(point[0]))
            .map(([day, value]) => [day, isNumber(value) ? value : null]);
    }
    if (isDay(series.start) && Array.isArray(series.values)) {
        return series.values.map((value, index) => [addDays(series.start, index), isNumber(value) ? value : null]);
    }
    return [];
}

/** Change between the latest value and the latest value at least `days` earlier. */
export function seriesChange(series, days) {
    const points = seriesPoints(series).filter(([, value]) => value !== null);
    if (points.length < 2) return null;
    const [lastDay, lastValue] = points[points.length - 1];
    const target = addDays(lastDay, -days);
    const base = [...points].reverse().find(([day]) => day <= target);
    return base ? { delta: lastValue - base[1], since: base[0], value: lastValue, as_of: lastDay } : null;
}

/** Accounts that could adopt a product: GHE customers without it (or, for GHE, book accounts without GHE). */
export function whitespace(model, family) {
    return model.accounts.filter(account => account.in_book !== false && !hasProduct(account, family)
        && (family === "ghe" || hasProduct(account, "ghe")))
        .sort((a, b) => (b.arr || 0) - (a.arr || 0) || String(a.name).localeCompare(String(b.name)));
}

/** Most urgent open item per account, used to rank accounts by attention. */
export function attentionIndex(model) {
    const index = new Map();
    const note = (id, priority, isAction) => {
        const current = index.get(id) || { rank: PRIORITIES.length, actions: 0, top: null };
        if (isAction) current.actions += 1;
        if (priorityRank(priority) < current.rank) {
            current.rank = priorityRank(priority);
            current.top = priority;
        }
        index.set(id, current);
    };
    for (const action of model.actions) if (!isClosed(action)) note(action.account_id, action.priority, true);
    for (const event of model.events) if (event.recent && !event.reverted && event.priority !== "Info") note(event.account_id, event.priority, false);
    return index;
}

/** Resolves a Salesforce id, an exact name or a unique partial name to an account id. */
export function resolveAccount(model, text) {
    const query = String(text || "").trim().toLowerCase();
    if (!query) return null;
    const byId = model.accounts.find(account => account.id.toLowerCase() === query);
    if (byId) return byId.id;
    const exact = model.accounts.filter(account => String(account.name || "").toLowerCase() === query);
    if (exact.length === 1) return exact[0].id;
    const partial = model.accounts.filter(account => String(account.name || "").toLowerCase().includes(query));
    return partial.length === 1 ? partial[0].id : null;
}

/** Trims a search to QUERY_MAX characters without splitting a character, so it always URL-encodes. */
export function clipQuery(text) {
    const value = String(text ?? "");
    const whole = typeof value.toWellFormed === "function" ? value.toWellFormed() : value;
    return Array.from(whole).slice(0, QUERY_MAX).join("");
}

/** Canvas location: "#/<view>[/account/<id>][?q=<search>]". */
export function hashFor({ view = "overview", subview = null, account = null, query = "" } = {}) {
    const tab = VIEWS.includes(view) ? view : "overview";
    const search = clipQuery(query);
    return `#/${tab}${tab === "copilot" && COPILOT_VIEWS.includes(subview) ? `/${subview}` : ""}${account ? `/account/${encodeURIComponent(account)}` : ""}${search ? `?q=${encodeURIComponent(search)}` : ""}`;
}

/** Inverse of hashFor. Unknown views fall back to the overview, and malformed parts are dropped. */
export function parseHash(hash) {
    const raw = String(hash ?? "").replace(/^#\/?/, "");
    const mark = raw.indexOf("?");
    const parts = (mark < 0 ? raw : raw.slice(0, mark)).split("/");
    const view = VIEWS.includes(parts[0]) ? parts[0] : "overview";
    const subview = view === "copilot" && COPILOT_VIEWS.includes(parts[1]) ? parts[1] : null;
    if (subview || (view === "copilot" && parts[1] === "seats")) parts.splice(1, 1);
    let account = null;
    if (parts[1] === "account" && parts.length > 2) {
        try {
            account = decodeURIComponent(parts.slice(2).join("/")) || null;
        } catch {
            account = null;
        }
    }
    let query = "";
    if (mark >= 0) {
        try {
            query = clipQuery(new URLSearchParams(raw.slice(mark + 1)).get("q") || "");
        } catch {
            query = "";
        }
    }
    return { view, account, query, ...(subview ? { subview } : {}) };
}

const copilotMetrics = ["licenses", "business", "enterprise", "assigned", "active28", "engaged28"];
const numberOrNull = value => isNumber(value) ? value : null;

/** Null-preserving totals, with independent coverage for every measure. */
export function copilotRowTotals(rows) {
    const totals = {};
    const metrics = {};
    for (const key of copilotMetrics) {
        const known = rows.filter(row => isNumber(row[key]));
        totals[key] = known.length ? known.reduce((sum, row) => sum + row[key], 0) : null;
        metrics[key] = { n: known.length, of: rows.length };
    }
    totals.reporting = { n: rows.filter(row => isNumber(row.active28) && isNumber(row.engaged28)).length, of: rows.length, metrics };
    return totals;
}

/** Pulse is authoritative for contracted seats and adoption. The second model only reconciles licenses and slugs. */
export function copilotLicenses(pulseModel, copilotModel = null) {
    const dashboard = new Map((copilotModel?.accounts || []).map(account => [account.id, account]));
    const slugs = new Map();
    for (const enterprise of copilotModel?.enterprises || []) {
        const match = enterprise.match;
        if (!["confirmed", "single"].includes(match?.confidence) || match.candidates?.length !== 1 || !enterprise.slug) continue;
        const id = match.candidates[0].id;
        slugs.set(id, [...new Set([...(slugs.get(id) || []), enterprise.slug])]);
    }
    const usageDate = pulseModel.sources?.find(source => source.feed === "attributes")?.as_of ?? null;
    const attention = attentionIndex(pulseModel);
    const rows = pulseModel.accounts.filter(account => hasProduct(account, "ghcp")
        || (!copilotModel?.missing_sources?.includes("09") && dashboard.get(account.id)?.contracted_seats > 0 && isNumber(account.products.ghcp.contracted)
            && account.products.ghcp.contracted !== dashboard.get(account.id).contracted_seats)).map(account => {
        const cp = account.products.ghcp;
        const other = dashboard.get(account.id);
        const licenses = numberOrNull(cp.contracted);
        const assigned = numberOrNull(cp.assigned);
        const active28 = numberOrNull(cp.funnel?.active_1d28);
        const engaged28 = numberOrNull(cp.funnel?.engaged_1d28);
        const dashboardLicenses = copilotModel?.missing_sources?.includes("09") ? null : numberOrNull(other?.contracted_seats);
        const flags = {
            unassigned: licenses !== null && assigned !== null && assigned < licenses,
            over_assigned: licenses !== null && assigned !== null && assigned > licenses,
            engaged_lt_50: licenses > 0 && engaged28 !== null && engaged28 / licenses < .5,
            renewing_90: isNumber(account.renewing_in_days) && account.renewing_in_days >= 0 && account.renewing_in_days <= 90,
            source_mismatch: licenses !== null && dashboardLicenses !== null && licenses !== dashboardLicenses,
        };
        return {
            account_id: account.id, name: account.name, slug: (slugs.get(account.id) || []).sort().join(", ") || null,
            licenses, business: numberOrNull(cp.business), enterprise: numberOrNull(cp.enterprise), assigned, active28, engaged28, flags,
            reconciliationOnly: !hasProduct(account, "ghcp"),
            dashboardLicenses, dashboardAsOf: copilotModel?.asof?.["09"] ?? copilotModel?.data_date ?? null,
            licensesAsOf: cp.contracted_as_of ?? null, assignedAsOf: cp.as_of ?? null, usageAsOf: usageDate,
            needsAttention: (attention.get(account.id)?.rank ?? PRIORITIES.length) < PRIORITIES.length
                || cp.health?.category === "Red" || flags.source_mismatch,
        };
    });
    return { rows, totals: copilotRowTotals(rows),
        flags: Object.fromEntries(["unassigned", "over_assigned", "engaged_lt_50", "renewing_90", "source_mismatch"]
            .map(flag => [flag, rows.filter(row => row.flags[flag]).length])),
        reconciliation: copilotModel ? "available" : "unavailable",
        note: copilotModel ? null : "Copilot dashboard reconciliation unavailable. Showing Pulse figures only." };
}

export function copilotTotals(model) {
    return copilotLicenses(model).totals;
}

export function copilotFilterCounts(rows) {
    return { all: rows.length, attention: rows.filter(row => row.needsAttention).length,
        unassigned: rows.filter(row => row.flags.unassigned).length,
        engaged_lt_50: rows.filter(row => row.flags.engaged_lt_50).length,
        renewing_90: rows.filter(row => row.flags.renewing_90).length };
}

export function copilotRowsFor(rows, filter = "all", query = "") {
    const text = clipQuery(query).trim().toLowerCase();
    return rows.filter(row => (!text || [row.name, row.account_id, row.slug].some(value => String(value || "").toLowerCase().includes(text)))
        && (filter === "all" || (filter === "attention" ? row.needsAttention : Boolean(row.flags[filter]))));
}

/** The local decision store is authoritative for actions and signals, including reopened items. Never mutates. */
export function withActionStates(model, items) {
    if (!isObject(items)) return model;
    return {
        ...model,
        actions: model.actions.map(action => {
            const entry = items[action.id];
            const state = isObject(entry) && ACTION_STATES.includes(entry.state) ? entry.state : null;
            if (state === (action.state ?? null) && (!state || entry.at === action.state_at)) return action;
            return { ...action, state, state_at: state ? entry.at ?? null : null, new: state ? false : action.new };
        }),
        ...(model.signals ? { signals: model.signals.map(signal => {
            if (!isObject(signal)) return signal;
            const entry = items[signal.id];
            const status = isObject(entry) && ACTION_STATES.includes(entry.state) ? entry.state : "open";
            return { ...signal, status, state_at: status === "open" ? null : entry.at ?? null,
                new: status === "open" ? signal.new : false };
        }) } : {}),
    };
}

const kpiOf = kpi => isObject(kpi)
    ? { value: kpi.value ?? null, as_of: kpi.as_of ?? null, change: kpi.d7 ?? null, change_since: kpi.d7_since ?? null }
    : null;

/** Compact, JSON-safe digest of the model for the agent-facing get_summary action. */
export function summarize(model, { limit = 8 } = {}) {
    const kpis = model.kpis || {};
    const copilot = copilotTotals(model);
    const open = actionsFor(model);
    const counts = Object.fromEntries(PRIORITIES.slice(0, 3).map(priority => [priority, open.filter(a => a.priority === priority).length]));
    return {
        date: model.date,
        generated_at: model.generated_at ?? null,
        owner: model.owner ?? null,
        data_status: model.status ?? null,
        accounts: kpis.overall?.accounts ?? model.accounts.length,
        arr: kpis.overall?.arr ?? null,
        renewing_within_120_days: kpis.overall?.renewing_120 ?? null,
        products: {
            ghe: { accounts: kpis.ghe?.accounts ?? null, contracted_seats: kpiOf(kpis.ghe?.contracted),
                metered_users: kpiOf(kpis.ghe?.metered), billable_users: kpiOf(kpis.ghe?.ubu), adoption: kpis.ghe?.adoption ?? null },
            ghas: { accounts: kpis.ghas?.accounts ?? null, contracted_seats: kpiOf(kpis.ghas?.contracted),
                active_committers: kpiOf(kpis.ghas?.committers), utilization: kpis.ghas?.utilization ?? null },
            copilot: { accounts: kpis.ghcp?.accounts ?? null, contracted_seats: kpiOf(kpis.ghcp?.contracted),
                licenses_total: copilot.licenses, active_devs_28d: copilot.active28, engaged_devs_28d: copilot.engaged28,
                assigned_seats: kpiOf(kpis.ghcp?.assigned), users_at_risk: kpis.ghcp?.at_risk ?? null,
                health_categories: kpis.ghcp?.categories ?? {} },
        },
        open_actions: counts,
        top_actions: open.slice(0, limit).map(action => ({
            id: action.id, priority: action.priority, kind: action.kind, account: action.account_name ?? action.account_id,
            product: FAMILY_LABELS[action.family] || action.family, title: action.title, why: action.why, new: Boolean(action.new),
        })),
        recent_changes: eventsFor(model, { recentOnly: true, includeReverted: false, minPriority: "Notify" }).slice(0, limit).map(event => ({
            date: event.date, priority: event.priority, account: event.account_name ?? event.account_id,
            product: FAMILY_LABELS[event.family] || event.family, change: formatSeatEvent(event), new: Boolean(event.new),
        })),
        sources_needing_attention: model.sources.filter(source => source.state !== "ok").map(source => ({
            source: source.label, state: SOURCE_STATES[source.state] || source.state, as_of: source.as_of ?? null,
            notes: source.messages || [],
        })),
        signals: (model.signals || []).filter(isObject).length,
        open_signals: Object.fromEntries(SIGNAL_PRIORITIES.map(priority => [priority, signalsFor(model, { priority }).length])),
        new_verified_high_signals: signalsFor(model, { newHigh: true }).length,
        top_signals: signalsFor(model).slice(0, limit).map(signal => ({
            id: signal.id, priority: signal.priority, account: signal.account_name ?? signal.name ?? signal.account_id,
            source: signal.source, date: signal.date, title: signal.title, evidence: signal.evidence, url: signal.url,
            competitors: signal.competitors || [], themes: signal.themes || [], play: signal.play, reason: signal.reason,
            confidence: signal.confidence, verified: isVerifiedSignal(signal),
            verification: isVerifiedSignal(signal) ? "verified" : "needs verification", new: Boolean(signal.new), status: "open",
        })),
    };
}
