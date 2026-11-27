// Account-grain seller analytics. Missing rows never imply zero, and currencies/units stay separate.
import { ACCOUNT_VIEWS, isNumber, isDay, seriesChange } from "./model.mjs";

export const ACCOUNT_TABS = ACCOUNT_VIEWS;
export const ACCOUNT_LABELS = { summary: "Summary", products: "Products & consumption", commercial: "Commercial",
    security: "Security", contacts: "Contacts (POCs)", activity: "Engagement", stack: "Tech stack", success: "Success & support" };
export const FEED_LABELS = {
    invoices: "Consumption invoices", actions: "Actions usage", protection: "Repository protection", model_cost: "Copilot model cost",
    selfserve: "Self-serve Team", mrr: "Recurring revenue", opportunities: "License opportunities", support: "Support tickets",
    services: "Services bookings", retention: "Revenue retention", risks: "Gainsight risks", sentiment: "Gainsight sentiment",
    success: "Success plans", timeline: "Gainsight timeline", contacts: "Salesforce contacts", issues: "Account-linked issues",
    gong: "Gong calls", notes: "Account notes", partners: "Partners", hierarchy: "Child accounts",
    github_accounts: "Enterprise mapping", budgets: "Enterprise budgets", emails: "Outlook email metadata", tech_stack: "Tech stack",
};
export const moneyTypes = ["ARR", "Billed", "Usage est.", "Model est.", "MRR", "Booked", "Budget", "Services est."];
export const knownSum = values => {
    const known = values.filter(isNumber);
    return known.length ? known.reduce((a, b) => a + b, 0) : null;
};
export const feedOf = (account, key) => account?.seller?.feeds?.[key] || {
    state: key === "tech_stack" ? "not_connected" : "missing", source: FEED_LABELS[key], as_of: null, records: [],
    reason: key === "tech_stack" ? "No verified SE issue or Gong stack evidence collected." : "Internal feed has not been collected.",
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
    ["ghsp", "Secret Protection", "ghas", "GHSP"], ["ghas", "GHAS (bundled)", "ghas", "Advanced Security"],
    ["copilot", "Copilot", "ghcp"], ["copilot aiu", "Copilot AI credits"], ["actions", "Actions"], ["codespaces", "Codespaces"],
    ["code_quality", "Code Quality"], ["copilot_enterprise", "Copilot Enterprise"], ["GitHub Models", "Models inference"],
    ["git_lfs", "Git LFS"], ["packages", "Packages"], ["shared_storage", "Shared storage"], ["spark", "Spark"], ["sandboxes", "Sandboxes"],
];
const productLabel = new Map(PRODUCTS.map(([key, label]) => [key, label]));
export const GHAS_BILLING_PRODUCTS = ["ghcs", "ghsp", "code_quality", "ghas"];
const normalizeName = name => name.trim().toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ");
const invoiceNames = new Map(PRODUCTS.flatMap(([key, name, , type]) =>
    [key, name, type, `GitHub ${name}`].filter(Boolean).map(alias => [normalizeName(alias), key])));
export const invoiceProduct = name => typeof name === "string" ? invoiceNames.get(normalizeName(name)) ?? name : name;
export const labelOf = key => productLabel.get(invoiceProduct(key)) || key || "Unmapped invoice product";

export function billedSeries(account, today, product = null) {
    const totals = new Map();
    for (const row of currentRows(account, "invoices")) {
        if (!isNumber(row.amount) || (product !== null && invoiceProduct(row.product) !== product)) continue;
        const month = monthOf(row.date);
        totals.set(month, (totals.get(month) ?? 0) + row.amount);
    }
    return { points: Array.from({ length: 13 }, (_, i) => {
        const month = monthOffset(today, i - 13);
        return [`${month}-01`, totals.get(month) ?? null];
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
    const extra = [...new Set(invoices.map(r => invoiceProduct(r.product)).filter(Boolean))].filter(p => !productLabel.has(p)).map(p => [p, p]);
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
        const metered = invoices.some(r => invoiceProduct(r.product) === key && isNumber(r.amount));
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
export function openSeCount(account) {
    const rows = currentRows(account, "issues");
    if (!rows.length || rows.some(r => typeof r.se_issue !== "boolean")) return null;
    const se = rows.filter(r => r.se_issue);
    return se.every(r => r.status) ? se.filter(r => String(r.status).toLowerCase() === "open").length : null;
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
/** Stable contact identities, including retained/no-email records; interaction is a separate attribute. */
export function contactDirectory(account, query = "") {
    const people = new Map(activitySummary(account).spoken.map(p => [p.email.toLowerCase(), p]));
    const contacts = new Map(rowsOf(account, "contacts").filter(p => typeof p.id === "string" && p.id).map(p => [p.id, p]));
    return [...contacts.values()].map(p => ({ ...p, interaction: typeof p.email === "string" ? people.get(p.email.toLowerCase()) || null : null }))
        .filter(p => [p.name, p.role, p.email, p.id].some(v => String(v || "").toLowerCase().includes(query.trim().toLowerCase())))
        .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)) || a.id.localeCompare(b.id));
}
export function contactCount(account) {
    const info = feedOf(account, "contacts");
    const count = contactDirectory(account).length;
    return count || ["ok", "no_rows"].includes(info.state) ? count : null;
}
export function allAccounts(model, query = "") {
    return model.accounts.filter(a => a.in_book !== false).filter(a =>
        [a.name, a.id, a.parent, a.industry, a.country, a.segment].some(v => String(v || "").toLowerCase().includes(query.trim().toLowerCase())));
}
export function renewalBuckets(model) {
    const accounts = allAccounts(model);
    return [
        ["0-30", "0–30 days", n => n >= 0 && n <= 30],
        ["31-60", "31–60 days", n => n > 30 && n <= 60],
        ["61-90", "61–90 days", n => n > 60 && n <= 90],
        ["91-120", "91–120 days", n => n > 90 && n <= 120],
        ["overdue", "Overdue", n => n < 0], ["later", "Later", n => n > 120],
        ["unknown", "Unknown date", n => !isNumber(n)],
    ].map(([key, label, accept]) => ({ key, label, accounts: accounts.filter(a =>
        (key === "unknown" || isNumber(a.renewing_in_days)) && accept(a.renewing_in_days)) }));
}
export function portfolioSeries(model, product = null) {
    const accounts = allAccounts(model).map(a => billedSeries(a, model.date, product));
    return { points: Array.from({ length: 13 }, (_, i) => [`${monthOffset(model.date, i - 13)}-01`,
        knownSum(accounts.map(s => s.points[i][1]))]),
    coverage: Array.from({ length: 13 }, (_, i) => accounts.filter(s => isNumber(s.points[i][1])).length), total: accounts.length };
}
export const STACK_STATUS = { reported_in_use: "Reported in use", evaluating: "Evaluating", planned: "Planned",
    historical: "Historical / discontinued", mention_only: "Mention only", conflicting: "Conflicting evidence" };
export function stackGroups(account) {
    const groups = new Map();
    for (const r of rowsOf(account, "tech_stack").filter(r => r.schema === 1 && r.technology && r.source_url && r.evidence_quote)) {
        const key = `${r.category}:${r.technology.toLowerCase()}`;
        const group = groups.get(key) || { technology: r.technology, category: r.category, evidence: [] };
        group.evidence.push(r);
        groups.set(key, group);
    }
    return [...groups.values()].map(g => {
        const current = g.evidence.filter(r => r.verification_state === "quote_verified" && r.source_current !== false);
        const states = new Set(current.map(r => r.usage_status).filter(s => s !== "mention_only"));
        const conflict = states.has("reported_in_use") && states.size > 1;
        return { ...g, status: conflict ? "conflicting" : [...states][0] || "mention_only",
            as_of: current.map(r => dayOf(r.observed_at)).filter(Boolean).sort().at(-1) || null };
    }).sort((a, b) => (a.status === "reported_in_use" ? -1 : 1) - (b.status === "reported_in_use" ? -1 : 1)
        || a.category.localeCompare(b.category) || a.technology.localeCompare(b.technology));
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
// Split MRR exists at this exact SKU grain. Family-level GHAS/GHE MRR and
// Copilot plan tiers cannot establish the billing model of a component.
function customerBillingMode(account, product, month) {
    const invoices = currentRows(account, "invoices").filter(row => monthOf(row.date) === month
        && invoiceProduct(row.product) === product && isNumber(row.amount) && row.amount > 0);
    const sources = new Set(invoices.map(r => String(r.billing_source || "").trim().toLowerCase()));
    const mrr = ["ghcs", "ghsp"].includes(product)
        ? currentRows(account, "mrr").filter(r => monthOf(r.date) === month) : [];
    const positive = key => mrr.some(row => isNumber(row[key]) && row[key] > 0);
    const metered = sources.has("metered") || positive(`${product}_metered_mrr`);
    const volume = sources.has("volume") || positive(`${product}_license_mrr`);
    return metered && volume ? "both" : metered ? "metered" : volume ? "volume" : invoices.length ? "unknown" : null;
}
export function bookConsumption(model, only = null) {
    const book = allAccounts(model);
    const products = only ?? [...new Set(["ghec", "copilot", ...GHAS_BILLING_PRODUCTS,
        ...book.flatMap(a => currentRows(a, "invoices").map(r => invoiceProduct(r.product)).filter(Boolean))])];
    const month = monthOffset(model.date, -1);
    return products.map(product => {
        const modes = { metered: [], volume: [], both: [], unknown: [] };
        const accounts = book.map(a => {
            const mode = customerBillingMode(a, product, month);
            if (mode) modes[mode].push(a);
            return { series: billedSeries(a, model.date, product) };
        });
        const point = index => knownSum(accounts.map(a => a.series.points[index][1]));
        const series = { points: Array.from({ length: 13 }, (_, i) => [`${monthOffset(model.date, i - 13)}-01`, point(i)]),
            coverage: Array.from({ length: 13 }, (_, i) => accounts.filter(a => isNumber(a.series.points[i][1])).length), total: accounts.length };
        const comparable = months => {
            const pairs = accounts.filter(a => isNumber(a.series.points[12][1]) && isNumber(a.series.points[12 - months][1]));
            if (!pairs.length) return null;
            const current = knownSum(pairs.map(a => a.series.points[12][1]));
            const prior = knownSum(pairs.map(a => a.series.points[12 - months][1]));
            return { delta: current - prior, fraction: prior === 0 ? null : (current - prior) / Math.abs(prior), accounts: pairs.length };
        };
        return { product, name: labelOf(product), series, modes, latest: point(12), mom: comparable(1), quarter: comparable(3),
            reporting: series.coverage[12] };
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
