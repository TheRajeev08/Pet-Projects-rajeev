// Account-grain seller analytics. Missing rows never imply zero, and currencies/units stay separate.
import { ACCOUNT_VIEWS, isNumber, isDay, seriesChange } from "./model.mjs";

export const ACCOUNT_TABS = ACCOUNT_VIEWS;
export const FEED_LABELS = {
    invoices: "Consumption invoices", actions: "Actions usage", protection: "Repository protection", model_cost: "Copilot model cost",
    selfserve: "Self-serve Team", mrr: "Recurring revenue", opportunities: "License opportunities", support: "Support tickets",
    services: "Services bookings", retention: "Revenue retention", risks: "Gainsight risks", sentiment: "Gainsight sentiment",
    success: "Success plans", timeline: "Gainsight timeline", contacts: "Salesforce contacts", issues: "SE linked issues",
    pocs: "Proofs of concept", gong: "Gong calls", notes: "Account notes", partners: "Partners", hierarchy: "Child accounts",
    github_accounts: "Enterprise mapping", budgets: "Enterprise budgets", emails: "Outlook email metadata", tech_stack: "Tech stack",
};
export const moneyTypes = ["ARR", "Billed", "Usage est.", "Model est.", "MRR", "Booked", "Budget", "Services est."];
export const knownSum = values => {
    const known = values.filter(isNumber);
    return known.length ? known.reduce((a, b) => a + b, 0) : null;
};
export const feedOf = (account, key) => account?.seller?.feeds?.[key] || {
    state: key === "tech_stack" ? "not_connected" : "missing", source: FEED_LABELS[key], as_of: null, records: [],
    reason: key === "tech_stack" ? "ZoomInfo is not connected. No verified internal tech-stack evidence." : "Internal feed has not been collected.",
};
export const rowsOf = (account, key) => (feedOf(account, key).records || []).filter(r => r && typeof r === "object");
export const currentRows = (account, key) => feedOf(account, key).state === "ok" ? rowsOf(account, key) : [];
export const dayOf = value => isDay(String(value || "").slice(0, 10)) ? String(value).slice(0, 10) : null;
export const monthOf = value => dayOf(value)?.slice(0, 7) || null;
export function bookFeedCoverage(model, key) {
    const feeds = model.accounts.filter(a => a.in_book !== false).map(a => feedOf(a, key));
    const dates = feeds.map(f => dayOf(f.as_of)).filter(Boolean).sort();
    const collected = feeds.map(f => dayOf(f.collected_at)).filter(Boolean).sort();
    const states = [...new Set(feeds.map(f => f.state))];
    return { state: states.length === 1 ? states[0] : states.length ? "partial" : "missing",
        first: dates[0] || null, as_of: dates.at(-1) || null, collected_at: collected.at(-1) || null,
        reporting: feeds.filter(f => f.state === "ok").length, total: feeds.length };
}
export function monthOffset(day, offset) {
    const date = new Date(`${day.slice(0, 7)}-01T00:00:00Z`);
    date.setUTCMonth(date.getUTCMonth() + offset);
    return date.toISOString().slice(0, 7);
}
export function latestRows(account, key, dimensions = []) {
    const latest = new Map();
    for (const row of currentRows(account, key)) {
        const id = JSON.stringify(dimensions.map(k => row[k]));
        if (!latest.has(id) || String(row.date || "") > String(latest.get(id).date || "")) latest.set(id, row);
    }
    return [...latest.values()];
}

export const PRODUCTS = [
    ["ghec", "GHE Cloud", "ghe", "GitHub Enterprise Cloud"], ["ghcs", "Code Security", "ghas", "GHCS"],
    ["ghsp", "Secret Protection", "ghas", "GHSP"], ["ghas", "Advanced Security (bundled)", "ghas", "Advanced Security"],
    ["copilot", "Copilot", "ghcp"], ["copilot aiu", "Copilot AI credits"], ["actions", "Actions"], ["codespaces", "Codespaces"],
    ["code_quality", "Code Quality"], ["copilot_enterprise", "Copilot Enterprise"], ["GitHub Models", "Models inference"],
    ["git_lfs", "Git LFS"], ["packages", "Packages"], ["shared_storage", "Shared storage"], ["spark", "Spark"], ["sandboxes", "Sandboxes"],
];
const productLabel = new Map(PRODUCTS.map(([key, label]) => [key, label]));
export const labelOf = key => productLabel.get(key) || key || "Unmapped invoice product";

export function billedSeries(account, today, product = null) {
    const rows = currentRows(account, "invoices");
    return { points: Array.from({ length: 13 }, (_, i) => {
        const month = monthOffset(today, i - 13);
        const values = rows.filter(r => monthOf(r.date) === month && (product === null || r.product === product)).map(r => r.amount);
        return [`${month}-01`, knownSum(values)];
    }) };
}
export function billedLtm(account, today, product = null) {
    const points = billedSeries(account, today, product).points.slice(-12);
    const value = knownSum(points.map(p => p[1]));
    if (value !== null) return { value, months: points.filter(p => p[1] !== null).length, source: "invoices" };
    const collected = product === null ? knownSum(Object.values(account.billing_ltm || {})) : account.billing_ltm?.[product];
    return isNumber(collected) ? { value: collected, months: null, source: "p06" } : { value: null, months: 0, source: "invoices" };
}
export function monthlyChange(series, months = 1) {
    const points = series.points;
    const current = points.at(-1);
    const previous = points.at(-1 - months);
    if (!current || !previous || !isNumber(current[1]) || !isNumber(previous[1])) return null;
    return { delta: current[1] - previous[1], fraction: previous[1] === 0 ? null : (current[1] - previous[1]) / Math.abs(previous[1]),
        since: previous[0], as_of: current[0] };
}
export function productRows(account, today) {
    const invoices = currentRows(account, "invoices");
    const extra = [...new Set(invoices.map(r => r.product).filter(Boolean))].filter(p => !productLabel.has(p)).map(p => [p, p]);
    return [...PRODUCTS, ...extra].map(([key, name, family, type]) => {
        const p = account.products?.[family] || {};
        const contract = type ? p.by_type?.[type] : null;
        let seats = contract?.seats ?? null;
        let used = null;
        if (key === "copilot") { seats = p.contracted ?? null; used = p.funnel?.active_1d28 ?? null; }
        if (key === "copilot_enterprise") seats = account.products?.ghcp?.enterprise ?? null;
        if (key === "ghcs") used = p.code_scanning ?? null;
        if (key === "ghsp") used = p.secret_scanning ?? null;
        if (key === "ghas") used = p.committers ?? null;
        // GHE usage is account-wide, not safely attributable to the Cloud-only SKU.
        const billing = billedLtm(account, today, key);
        const owned = (seats ?? 0) > 0;
        const metered = invoices.some(r => r.product === key && isNumber(r.amount));
        const explicitAbsent = isNumber(seats) && seats === 0;
        return { key, name, seats, used, utilization: seats > 0 && isNumber(used) ? used / seats : null,
            arr: key === "copilot" ? account.seller?.ready?.copilot_arr ?? null : contract?.arr ?? null,
            billed: billing.value, months: billing.months, billingSource: billing.source,
            lcm: key === "ghcs" || key === "ghsp" ? account.billing_lcm?.[key] ?? null : null,
            trend: billedSeries(account, today, key), status: owned ? "Owned" : metered ? "Metered" : explicitAbsent ? "Whitespace" : "No data",
            as_of: contract ? p.as_of : key === "copilot" || key === "copilot_enterprise" ? account.products?.ghcp?.contracted_as_of : null };
    });
}
export function rankAccounts(model, basis = "arr", query = "") {
    const value = a => basis === "billed" ? billedLtm(a, model.date).value : basis === "renewal" ? a.renewing_in_days : a.arr;
    const dir = basis === "renewal" ? 1 : -1;
    return model.accounts.filter(a => a.in_book !== false).sort((a, b) => {
        const x = value(a), y = value(b);
        return (isNumber(x) !== isNumber(y) ? isNumber(x) ? -1 : 1 : isNumber(x) && x !== y ? (x - y) * dir : 0)
            || String(a.name || "").localeCompare(String(b.name || "")) || a.id.localeCompare(b.id);
    }).slice(0, 50).map((a, i) => ({ ...a, rank: i + 1 })).filter(a =>
        [a.name, a.id, a.parent].some(v => String(v || "").toLowerCase().includes(query.trim().toLowerCase())));
}
export const openRows = (account, key) => currentRows(account, key).filter(r => {
    const status = String(r.status || "").toLowerCase();
    return status && !status.startsWith("closed") && !["solved", "completed", "done", "cancelled"].includes(status);
});
export function observedCount(account, key, rows = currentRows(account, key)) {
    const all = currentRows(account, key);
    return all.length && (rows.length || all.every(r => r.status)) ? rows.length : null;
}
export function activitySummary(account) {
    const emails = currentRows(account, "emails");
    const calls = currentRows(account, "gong");
    const people = new Map();
    for (const [kind, rows] of [["emails", emails], ["gong", calls]]) {
        for (const r of rows) for (const p of r.people || r.participants || []) {
            if (!p.email || p.affiliation === "internal") continue;
            const key = p.email.toLowerCase();
            const item = people.get(key) || { ...p, sources: new Set(), last: null };
            item.sources.add(kind);
            item.last = [item.last, dayOf(r.date)].filter(Boolean).sort().at(-1) || null;
            people.set(key, item);
        }
    }
    const contacts = new Map(currentRows(account, "contacts").filter(p => p.email).map(p => [p.email.toLowerCase(), p]));
    // Known Salesforce contacts are customer POCs; unrelated/internal correspondents are not counted.
    const spoken = [...people].filter(([email]) => contacts.has(email)).map(([email, p]) => ({
        ...p, name: contacts.get(email).name || p.name, role: contacts.get(email).role, sources: [...p.sources],
    }));
    const recency = rows => rows.map(r => dayOf(r.date)).filter(Boolean).sort().at(-1) || null;
    return { emails, spoken, lastEmail: recency(emails), lastMeeting: recency(calls),
        threads: emails.length ? new Set(emails.map(r => r.thread).filter(Boolean)).size || null : null };
}
export function renewalRisk(account) {
    const health = account.products?.ghcp?.health?.category;
    return health === "Red" || openRows(account, "risks").length > 0 || openRows(account, "support")
        .some(r => ["urgent", "high", "p1", "p2"].includes(String(r.priority || "").toLowerCase()));
}
export function usageTrend(account) {
    const s = account.products?.ghcp?.series?.assigned || account.products?.ghe?.series?.metered;
    return seriesChange(s, 30);
}
export function bookConsumption(model) {
    const products = [...new Set(model.accounts.flatMap(a => currentRows(a, "invoices").map(r => r.product)))];
    return products.map(product => {
        const accounts = model.accounts.map(a => ({ account: a, series: billedSeries(a, model.date, product) }));
        const point = index => knownSum(accounts.map(a => a.series.points[index][1]));
        const series = { points: Array.from({ length: 13 }, (_, i) => [`${monthOffset(model.date, i - 13)}-01`, point(i)]) };
        const comparable = months => {
            const pairs = accounts.filter(a => isNumber(a.series.points[12][1]) && isNumber(a.series.points[12 - months][1]));
            if (!pairs.length) return null;
            const current = knownSum(pairs.map(a => a.series.points[12][1]));
            const prior = knownSum(pairs.map(a => a.series.points[12 - months][1]));
            return { delta: current - prior, fraction: prior === 0 ? null : (current - prior) / Math.abs(prior), accounts: pairs.length };
        };
        return { product, name: labelOf(product), series, latest: point(12), mom: comparable(1), quarter: comparable(3),
            reporting: accounts.filter(a => isNumber(a.series.points[12][1])).length };
    });
}

/** Only exact evidence matches collapse. Do not use fuzzy product similarity to hide distinct risks. */
export function dedupeSignals(signals, recommendations) {
    const normalized = value => String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
    const evidence = new Set((recommendations?.items || []).flatMap(item =>
        (item.signals || []).map(signal => `${item.account_id}|${normalized(signal.ml_driven_insights)}`)));
    const seen = new Set();
    return signals.filter(signal => {
        const key = `${signal.account_id}|${normalized(signal.evidence || signal.title)}`;
        if (evidence.has(key) || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}
