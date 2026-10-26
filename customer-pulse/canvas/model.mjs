// Pure data helpers shared by the canvas server, the extension and the browser. No DOM or Node APIs.

export const SCHEMA = 1;
export const PRIORITIES = ["High", "Medium", "Notify", "Info"];
const RANK = Object.fromEntries(PRIORITIES.map((priority, index) => [priority, index]));
export const PRODUCTS = ["ghe", "ghas", "ghcp"];
export const FAMILY_LABELS = { ghe: "GHE", ghas: "GHAS", ghcp: "Copilot", book: "Book", all: "All products" };
export const ACTION_STATES = ["done", "dismissed"];
export const SOURCE_STATES = {
    ok: "Current",
    stale: "Stale",
    suspect: "Partial load held back",
    failed: "Not refreshed today",
    missing: "No data",
};
export const VIEWS = ["overview", "copilot", "ghe", "ghas", "signals", "data"];
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

export const isClosed = action => ACTION_STATES.includes(action?.state);

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
export function hashFor({ view = "overview", account = null, query = "" } = {}) {
    const tab = VIEWS.includes(view) ? view : "overview";
    const search = clipQuery(query);
    return `#/${tab}${account ? `/account/${encodeURIComponent(account)}` : ""}${search ? `?q=${encodeURIComponent(search)}` : ""}`;
}

/** Inverse of hashFor. Unknown views fall back to the overview, and malformed parts are dropped. */
export function parseHash(hash) {
    const raw = String(hash ?? "").replace(/^#\/?/, "");
    const mark = raw.indexOf("?");
    const parts = (mark < 0 ? raw : raw.slice(0, mark)).split("/");
    const view = VIEWS.includes(parts[0]) ? parts[0] : "overview";
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
    return { view, account, query };
}

/** Applies locally recorded done/dismissed decisions without mutating the model. */
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
    };
}

const kpiOf = kpi => isObject(kpi)
    ? { value: kpi.value ?? null, as_of: kpi.as_of ?? null, change: kpi.d7 ?? null, change_since: kpi.d7_since ?? null }
    : null;

/** Compact, JSON-safe digest of the model for the agent-facing get_summary action. */
export function summarize(model, { limit = 8 } = {}) {
    const kpis = model.kpis || {};
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
            product: FAMILY_LABELS[event.family] || event.family, change: event.text, new: Boolean(event.new),
        })),
        sources_needing_attention: model.sources.filter(source => source.state !== "ok").map(source => ({
            source: source.label, state: SOURCE_STATES[source.state] || source.state, as_of: source.as_of ?? null,
            notes: source.messages || [],
        })),
        signals: (model.signals || []).length,
    };
}
