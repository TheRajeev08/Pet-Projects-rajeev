// Copilot code review (CCR), coding agent (CCA) and AI units by feature, shared by Pulse and the embedded
// Copilot dashboard. Pure HTML-string rendering: every source value passes through esc(); unknown is never zero.

export const IMPACT_SOURCES = Object.freeze({
    "19": "Code review monthly (CCR)",
    "20": "Coding agent adoption & EPI (CCA)",
    "21": "Coding agent opportunity scores",
    "22": "AI units by feature, weekly",
});

const STAGES = ["No adoption", "Emerging", "Early expansion", "Expanding", "Broad adoption", "Mature adoption"];
const BANDS = { S: "High signal", A: "Strong", B: "Moderate", C: "Early stage", D: "Marginal" };
const FLAG_TEXT = {
    duplicate_month: "The source returned a month more than once; the first row was kept.",
    org_rows_disagree: "Organization rows disagree on the enterprise totals for at least one month; the largest value is shown.",
    lead_time_rows_disagree: "Lead-time rows disagree for at least one month; the largest value is shown.",
    duplicate_owner_rows: "The source returned this owner more than once; the first row was kept.",
};
const REASONS = {
    absent: "Not in this snapshot: the source was added after this model was built.",
    missing: "Not collected in the latest refresh.",
};

const esc = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const isNum = value => typeof value === "number" && Number.isFinite(value);
const DASH = '<span class="ci-na" title="Not reported">&mdash;</span>';
const NA = '<span class="ci-na">Not reported</span>';
const fmt = (value, digits = 0) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
const int = (value, empty = DASH) => isNum(value) ? fmt(value) : empty;
const compact = (value, empty = DASH) => isNum(value)
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value) : empty;
const ratioText = value => !isNum(value) ? null : value >= 0 && value <= 1 ? `${fmt(value * 100, 1)}%` : fmt(value, 2);
const ratio = (value, empty = DASH) => !isNum(value) ? empty : value >= 0 && value <= 1 ? ratioText(value)
    : `${fmt(value, 2)} <span class="ci-flag" title="Expected a 0–1 ratio">source scale unverified</span>`;
const signed = (value, empty = DASH) => !isNum(value) ? empty
    : `${value > 0 ? "+" : value < 0 ? "&minus;" : ""}${fmt(Math.abs(value * 100), 0)}%`;
const days = (value, empty = DASH) => isNum(value) ? `${fmt(value, 1)} d` : empty;
const money = value => isNum(value)
    ? `${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value)} <span class="ci-tag">Usage est.</span>` : DASH;
const text = (value, empty = DASH) => value == null || value === "" ? empty : esc(value);
const isDay = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value) && !Number.isNaN(Date.parse(value.slice(0, 10)));
const day = value => isDay(value) ? new Date(`${value.slice(0, 10)}T00:00:00Z`) : null;
const dateText = value => day(value)?.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) ?? "date not reported";
const monthText = value => day(value)?.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" }) ?? "month not reported";
const monthTick = value => day(value)?.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }) ?? "?";
const sumKnown = values => values.some(isNum) ? values.filter(isNum).reduce((a, b) => a + b, 0) : null;

/** collected | stale | missing | absent. Absent means the model predates the source, which is not the same as missing. */
export function impactSourceState(model, id) {
    if (model?.missing_sources?.includes(id)) return { state: "missing" };
    const entry = model?.sources?.[id];
    if (!entry) return { state: "absent" };
    const from = model?.stale_sources?.[id] || entry.carried_from || null;
    return { state: from ? "stale" : "collected", rows: isNum(entry.rows) ? entry.rows : null, ...(from ? { from } : {}) };
}

/** The impact subset of a Copilot dashboard model; Pulse receives this through /api/copilot-licenses. */
export function impactPayload(model) {
    if (!model || typeof model !== "object") return null;
    const ids = ["10", "14", ...Object.keys(IMPACT_SOURCES)];
    const enterprises = Array.isArray(model.enterprises) ? model.enterprises : [];
    const bySlug = new Map(enterprises.map(e => [e.slug, e]));
    const slugById = new Map(enterprises.filter(e => e.enterprise_id != null).map(e => [String(e.enterprise_id), e.slug]));
    const owner = o => ({ ...o, slug: o.enterprise ? slugById.get(String(o.owner_id)) ?? null : null });
    const accounts = {};
    for (const a of model.accounts || []) {
        if (a.ccr == null && a.cca == null && a.aiu_by_feature == null) continue;
        accounts[a.id] = {
            name: a.name ?? null,
            ccr: a.ccr ? { ...a.ccr, details: (a.ccr.enterprises || []).map(e => ({ slug: e.slug,
                ccr: bySlug.get(e.slug)?.ccr ?? { status: e.status, reason: e.reason ?? null } })) } : null,
            cca: a.cca ? { ...a.cca, owners: (a.cca.owners || []).map(owner) } : null,
            aiu_by_feature: a.aiu_by_feature ?? null,
        };
    }
    return {
        sources: Object.fromEntries(ids.map(id => [id, impactSourceState(model, id)])),
        asof: Object.fromEntries(ids.map(id => [id, model.asof?.[id] ?? null])),
        data_date: model.data_date ?? null,
        portfolio: { ccr: model.portfolio?.ccr ?? null, cca: model.portfolio?.cca ?? null,
            surface_weekly_aiu: model.portfolio?.surface_weekly_aiu ?? null },
        opportunities: model.cca_opportunities ?? null,
        accounts,
        enterprise_ccr: Object.fromEntries(enterprises.map(e => [e.slug, e.ccr ?? null])),
        enterprise_cca: Object.fromEntries(enterprises.map(e => [e.slug, e.cca ? owner(e.cca) : null])),
    };
}

/** True when an account has source rows to show: observed or withheld CCR, observed CCA, AI units or an opportunity row. */
export function impactApplies(impact, accountId) {
    const a = impact?.accounts?.[accountId];
    const o = ["absent", "missing"].includes(impact?.sources?.["21"]?.state ?? "absent") ? null : impact?.opportunities;
    return ["observed", "withheld"].includes(a?.ccr?.status) || a?.cca?.status === "observed"
        || Object.keys(a?.aiu_by_feature || {}).length > 0
        || [...(o?.expansion || []), ...(o?.new_logo || [])].some(e => e.sf_id === accountId);
}

// ---------------------------------------------------------------- building blocks
const heading = (level, title, id = "") => `<h${level}${id ? ` id="${esc(id)}"` : ""}>${title}</h${level}>`;
const note = (html, tone = "") => `<p class="ci-note${tone ? ` ${tone}` : ""}">${html}</p>`;
const kpi = (label, value, sub = "") => `<div class="ci-kpi"><span class="ci-kpi-label">${esc(label)}</span>`
    + `<strong class="ci-kpi-value">${value}</strong>${sub ? `<span class="ci-kpi-sub">${sub}</span>` : ""}</div>`;
const kpis = items => {
    const shown = items.filter(Boolean);
    return `<div class="ci-kpis ci-n${shown.length}">${shown.join("")}</div>`;
};
const card = (level, title, body, { wide = false, id = "", source = "" } = {}) =>
    `<article class="ci-card${wide ? " ci-wide" : ""}"${id ? ` id="${esc(id)}"` : ""}>${heading(level, title)}${body}`
    + `${source ? `<p class="ci-source">${source}</p>` : ""}</article>`;
const details = (summary, body) => `<details class="ci-details"><summary>${esc(summary)}</summary>${body}</details>`;

function table(headers, rows, numeric = []) {
    return `<div class="ci-table"><table><thead><tr>${headers.map((h, i) =>
        `<th${numeric.includes(i) ? ' class="num"' : ""} scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>`
        + rows.map(row => `<tr>${row.map((cell, i) =>
            `<td${numeric.includes(i) ? ' class="num"' : ""} data-label="${esc(headers[i])}">${cell}</td>`).join("")}</tr>`).join("")
        + "</tbody></table></div>";
}

function bars(rows, format = value => int(value)) {
    const max = Math.max(...rows.map(r => r.value).filter(isNum), 0);
    return `<div class="ci-bars">${rows.map(r => `<div class="ci-bar"><span class="ci-bar-label">${esc(r.label)}</span>`
        + `<span class="ci-bar-track"><span class="ci-bar-fill ${r.cls || "ci-s1"}" style="width:${max > 0 && isNum(r.value) ? Math.max(1.5, r.value / max * 100).toFixed(1) : 0}%"></span></span>`
        + `<span class="ci-bar-value">${format(r.value)}</span></div>`).join("")}</div>`;
}

function niceMax(value) {
    if (!(value > 0)) return 1;
    const mag = 10 ** Math.floor(Math.log10(value));
    return [1, 2, 2.5, 5, 10].map(step => step * mag).find(step => step >= value - 1e-12) ?? value;
}

/** Line chart. Values are chronological; null breaks the line. A hollow point and dashed segment mark month to date.
 *  Lines stretch with the card (non-scaling strokes); axis text and points are HTML so they keep their size at any width. */
export function lineChart({ title, labels, series, format = value => fmt(value), mtd = -1 }) {
    const values = series.flatMap(s => s.values).filter(isNum);
    if (!values.length) return note("No values reported for this chart.");
    const min = Math.min(0, ...values), max = niceMax(Math.max(...values, 0));
    const n = labels.length;
    const x = i => n <= 1 ? 50 : i / (n - 1) * 100;
    const y = v => (max - v) / ((max - min) || 1) * 100;
    const pos = (i, v) => `left:${x(i).toFixed(2)}%;top:${y(v).toFixed(2)}%`;
    const ticks = [min, (min + max) / 2, max];
    const step = Math.max(1, Math.ceil(n / 7));
    const grid = ticks.map(t => `<line class="ci-grid" x1="0" x2="100" y1="${y(t).toFixed(2)}" y2="${y(t).toFixed(2)}" vector-effect="non-scaling-stroke"/>`).join("");
    const yLabels = ticks.map(t => `<span class="ci-tick ci-tick-y" style="top:${y(t).toFixed(2)}%">${esc(format(t))}</span>`).join("");
    const xLabels = labels.map((label, i) => (n - 1 - i) % step === 0
        ? `<span class="ci-tick ci-tick-x${(n - 1 - i) / step % 2 ? " ci-alt" : ""}" style="left:${x(i).toFixed(2)}%">${esc(label)}</span>` : "").join("");
    const lines = series.map(s => {
        let solid = "", dashed = "";
        s.values.forEach((v, i) => {
            const prev = s.values[i - 1];
            if (!isNum(v) || !isNum(prev)) return;
            const seg = `M${x(i - 1).toFixed(2)},${y(prev).toFixed(2)}L${x(i).toFixed(2)},${y(v).toFixed(2)}`;
            if (i === mtd || s.dashed) dashed += seg; else solid += seg;
        });
        return `<g class="${s.cls}">${solid ? `<path d="${solid}" vector-effect="non-scaling-stroke"/>` : ""}`
            + `${dashed ? `<path class="ci-dash" d="${dashed}" vector-effect="non-scaling-stroke"/>` : ""}</g>`;
    }).join("");
    const dots = series.filter(s => !s.dashed).map(s => s.values.map((v, i) => isNum(v)
        ? `<span class="ci-dot ${s.cls}${i === mtd ? " ci-mtd" : ""}" style="${pos(i, v)}" title="${esc(`${s.name}, ${labels[i]}${i === mtd ? " (month to date)" : ""}: ${format(v)}`)}"></span>` : "").join("")).join("");
    const keys = series.map(s => `<span class="ci-key ${s.cls}${s.dashed ? " ci-key-dash" : ""}">${esc(s.name)}</span>`).join("")
        + (mtd >= 0 ? '<span class="ci-key ci-key-mtd">Hollow point: month to date</span>' : "");
    return `<figure class="ci-chart"><div class="ci-plot" role="img" aria-label="${esc(title)}">`
        + `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">${grid}${lines}</svg>`
        + `${dots}${yLabels}${xLabels}</div><figcaption class="ci-legend">${keys}</figcaption></figure>`;
}

const pctTick = value => ratioText(value) ?? "";
const PALETTE = ["ci-s1", "ci-s2", "ci-s3", "ci-s4", "ci-s5"];
const flagNotes = flags => (flags || []).filter(f => FLAG_TEXT[f]).map(f => note(esc(FLAG_TEXT[f]), "warning")).join("");
const latestComplete = series => [...(series || [])].reverse().find(p => !p.mtd) || null;
const mtdIndex = series => (series || []).map(p => !!p.mtd).lastIndexOf(true);

function gate(impact, id, label) {
    const s = impact?.sources?.[id] || { state: "absent" };
    if (s.state === "absent" || s.state === "missing") return { blocked: note(`<strong>${esc(label)} not reported.</strong> ${esc(REASONS[s.state])}`, "warning") };
    return { stale: s.state === "stale" ? note(`Carried from ${esc(dateText(s.from))}; this source was not collected in the current snapshot.`, "warning") : "" };
}

function sourceLine(impact, id, extra = "") {
    const asof = impact?.asof?.[id];
    return `${esc(IMPACT_SOURCES[id] || `Source ${id}`)} · ${asof ? `data as of ${esc(dateText(asof))}` : "as-of date not published"}${extra ? ` · ${extra}` : ""}`;
}

const ownerLabel = o => `${esc(o.owner_type || "Owner")} ${esc(o.slug || o.owner_id || "")}`.trim();

// ---------------------------------------------------------------- code review (CCR)
function ccrRollupBody(series, { scope }) {
    const p = latestComplete(series);
    const labels = series.map(s => monthTick(s.month));
    return kpis([
        kpi("Review coverage", p ? ratio(p.coverage, NA) : NA, `CCR-reviewed ÷ reviewed PRs · ${p ? esc(monthText(p.month)) : "no complete month"}`),
        kpi("CCR-reviewed PRs", p ? compact(p.ccr_prs, NA) : NA, p ? `of ${compact(p.reviewed)} reviewed PRs` : ""),
        kpi("PRs", p ? compact(p.prs, NA) : NA, "All PRs in the month"),
        kpi("Enterprises reporting", p ? int(p.enterprises, NA) : NA, esc(scope)),
    ]) + lineChart({ title: "Copilot code review coverage by month", labels, mtd: mtdIndex(series), format: pctTick,
        series: [{ name: "Review coverage", values: series.map(s => s.coverage), cls: "ci-s1" }] })
        + details("Exact monthly values", table(["Month", "Enterprises", "PRs", "Reviewed PRs", "CCR-reviewed PRs", "Coverage"],
            series.map(s => [`${esc(monthText(s.month))}${s.mtd ? ' <span class="ci-tag">MTD</span>' : ""}`, int(s.enterprises),
                int(s.prs), int(s.reviewed), int(s.ccr_prs), ratio(s.coverage)]), [1, 2, 3, 4, 5]));
}

function leadTable(p) {
    const rows = [["CCR-reviewed PRs", "ccr"], ["General PRs", "general"]].map(([label, side]) => [esc(label),
        ...["p50", "avg", "p90"].map(stat => `${days(p.lead_days?.[side]?.[stat])}${p.lead_labels?.[side]?.[stat]
            ? `<span class="ci-cell-sub">${esc(p.lead_labels[side][stat])}</span>` : ""}`)]);
    return table(["PR lead time", "Median (p50)", "Average", "p90"], rows, [1, 2, 3])
        + `<p class="ci-meta">Days from PR open to merge as the source reports them; labels are source categories${p.label_basis?.length
            ? ` (basis: ${p.label_basis.map(esc).join(", ")})` : ""}. Shorter is not proof that review caused the difference.</p>`;
}

function ccrEnterpriseBody(c, { level }) {
    if (c.status === "withheld") return note(`<strong>Withheld.</strong> ${text(c.reason, "The source rows could not be reconciled.")}`, "warning");
    if (c.status !== "observed" || !c.series?.length) return note("The linked enterprise returned no code review rows.");
    const p = latestComplete(c.series), series = c.series, labels = series.map(s => monthTick(s.month)), mtd = mtdIndex(series);
    return kpis([
        kpi("Review coverage", p ? ratio(p.coverage, NA) : NA, `CCR-reviewed ÷ reviewed PRs · ${p ? esc(monthText(p.month)) : "no complete month"}`),
        kpi("User penetration", p ? ratio(p.penetration, NA) : NA, "CCR users ÷ active committers (source)"),
        kpi("Adoption score", p ? ratio(p.rate, NA) : NA, "Mean of coverage and penetration"),
        kpi("Industry benchmark", p ? ratio(p.benchmark, NA) : NA, p?.vs_industry ? `${esc(p.vs_industry)}${c.industry ? ` · ${esc(c.industry)}` : ""}` : "Compared with the adoption score"),
        kpi("CCR users", p ? int(p.ccr_users, NA) : NA, p ? `${int(p.ccr_prs)} CCR-reviewed of ${int(p.reviewed)} reviewed PRs` : ""),
    ]) + `<div class="ci-pair">`
        + lineChart({ title: "Review coverage and user penetration by month", labels, mtd, format: pctTick, series: [
            { name: "Review coverage", values: series.map(s => s.coverage), cls: "ci-s1" },
            { name: "User penetration", values: series.map(s => s.penetration), cls: "ci-s2" }] })
        + lineChart({ title: "Adoption score against the industry benchmark", labels, mtd, format: pctTick, series: [
            { name: "Adoption score", values: series.map(s => s.rate), cls: "ci-s3" },
            { name: "Industry benchmark", values: series.map(s => s.benchmark), cls: "ci-s5", dashed: true }] })
        + `</div><p class="ci-meta">The benchmark is compared with the adoption score (mean of coverage and penetration), not with coverage alone.</p>`
        + (p ? heading(level + 1, `PR lead time · ${esc(monthText(p.month))}`) + leadTable(p) : "")
        + (series.some(s => s.lead_conflict) ? note(esc(FLAG_TEXT.lead_time_rows_disagree), "warning") : "")
        + flagNotes((c.flags || []).filter(f => f !== "lead_time_rows_disagree"))
        + details("Exact monthly values", table(["Month", "PRs", "Reviewed", "CCR-reviewed", "CCR users", "Coverage", "Penetration", "Score", "Benchmark", "Vs industry", "Orgs"],
            series.map(s => [`${esc(monthText(s.month))}${s.mtd ? ' <span class="ci-tag">MTD</span>' : ""}`, int(s.prs), int(s.reviewed), int(s.ccr_prs),
                int(s.ccr_users), ratio(s.coverage), ratio(s.penetration), ratio(s.rate), ratio(s.benchmark), text(s.vs_industry), int(s.orgs)]),
            [1, 2, 3, 4, 5, 6, 7, 8, 10]))
        + `<p class="ci-meta">Source run ${esc(dateText(c.run_date))}. Enterprise totals repeat on every organization row upstream and are never summed.</p>`;
}

function ccrPortfolioCard(impact, level) {
    const g = gate(impact, "19", "Code review (CCR)");
    const data = impact.portfolio?.ccr;
    let body = g.blocked || g.stale;
    if (!g.blocked) {
        if (!data?.series?.length) body += note("No owned enterprise returned code review rows.");
        else {
            const s = data.summary || {}, lead = s.median_lead || {};
            body += ccrRollupBody(data.series, { scope: "Owned GitHub enterprises" })
                + `<div class="ci-pair">`
                + `<div>${heading(level + 1, "Adoption against industry")}${Object.keys(s.vs_industry || {}).length
                    ? bars(Object.entries(s.vs_industry).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value, cls: "ci-s3" })))
                    : note("No benchmark labels reported.")}<p class="ci-meta">Enterprises by source label, latest complete month. Labels compare the adoption score with the industry benchmark.</p></div>`
                + `<div>${heading(level + 1, "Median PR lead time")}${lead.compared
                    ? bars([{ label: "CCR-reviewed faster", value: lead.faster, cls: "ci-s3" }, { label: "CCR-reviewed slower", value: lead.slower, cls: "ci-s4" },
                        { label: "Same", value: lead.same, cls: "ci-s5" }])
                    : note("No enterprise reported both median lead times.")}<p class="ci-meta">${int(lead.compared, "0")} enterprises reported both medians. Observational, not causal.</p></div>`
                + `</div>`
                + (data.withheld ? note(`${int(data.withheld)} ${data.withheld === 1 ? "enterprise is" : "enterprises are"} withheld because the source rows could not be reconciled.`, "warning") : "");
        }
    }
    return card(level, "Copilot code review", body, { source: sourceLine(impact, "19", "the current month is month to date") });
}

function ccrAccountCard(impact, account, level) {
    const g = gate(impact, "19", "Code review (CCR)");
    const c = account?.ccr;
    let body = g.blocked || g.stale;
    if (!g.blocked) {
        if (!c) body += note("Code review was not reported for this account.");
        else if (c.status === "no_enterprise") body += note("No confirmed GitHub enterprise link for this account, so code review is not attributed to it.");
        else if (c.status === "no_rows") body += note("The linked enterprise returned no code review rows.");
        else {
            const details_ = c.details || [];
            const observed = details_.filter(d => d.ccr?.status === "observed");
            body += (observed.length === 1 && details_.length === 1
                ? ccrEnterpriseBody(observed[0].ccr, { level: level + 1 })
                : (c.series?.length ? ccrRollupBody(c.series, { scope: "Confirmed enterprises" }) : note("No observed code review months."))
                    + (details_.length ? table(["Enterprise", "Status", "Coverage", "Score", "Benchmark", "Vs industry", "Median lead (CCR / general)"],
                        details_.map(d => {
                            const p = d.ccr?.status === "observed" ? latestComplete(d.ccr.series) : null;
                            return [text(d.slug), d.ccr?.status === "withheld" ? `Withheld <span class="ci-cell-sub">${text(d.ccr.reason, "")}</span>` : text(d.ccr?.status),
                                p ? ratio(p.coverage) : DASH, p ? ratio(p.rate) : DASH, p ? ratio(p.benchmark) : DASH, p ? text(p.vs_industry) : DASH,
                                p ? `${days(p.lead_days?.ccr?.p50)} / ${days(p.lead_days?.general?.p50)}` : DASH];
                        }), [2, 3, 4]) : ""));
            if (c.status === "withheld" && !observed.length) body = (g.stale || "") + note("Code review is withheld for every linked enterprise because the source rows could not be reconciled.", "warning")
                + table(["Enterprise", "Reason"], details_.map(d => [text(d.slug), text(d.ccr?.reason)]));
        }
    }
    return card(level, "Copilot code review", body, { source: sourceLine(impact, "19", "confirmed enterprise links only") });
}

// ---------------------------------------------------------------- coding agent (CCA)
function epiBlock(epi, level) {
    if (!epi?.tiers?.length) return note("No engineering productivity (EPI) comparison was reported for the latest month.");
    const lift = t => t.comparison ? signed(t.lift?.avg) : `<span class="ci-na">No comparison</span><span class="ci-cell-sub">${!t.has_cca ? "No CCA PRs" : !t.has_non_cca ? "No non-CCA PRs" : "Not compared"}</span>`;
    const guidance = epi.tiers.filter(t => t.guidance?.summary || t.guidance?.action);
    return heading(level, `Engineering productivity by PR complexity · ${esc(monthText(epi.month))}`)
        + table(["Complexity", "CCA PRs", "Non-CCA PRs", "EPI median (CCA / non-CCA)", "Lift (average)", "Lift (median)"],
            epi.tiers.map(t => [text(t.tier), int(t.cca_prs), int(t.non_cca_prs),
                `${isNum(t.epi?.cca?.p50) ? fmt(t.epi.cca.p50, 2) : DASH} / ${isNum(t.epi?.non_cca?.p50) ? fmt(t.epi.non_cca.p50, 2) : DASH}`,
                lift(t), t.comparison ? signed(t.lift?.p50) : DASH]), [1, 2, 4, 5])
        + `<p class="ci-meta">EPI combines cycle time and work units and is percent-ranked within a peer pool. Lift compares CCA with non-CCA PRs of the same complexity. Observational, not causal.</p>`
        + (guidance.length ? `<div class="ci-guidance"><strong>Source guidance</strong><ul>${guidance.map(t =>
            `<li><span class="ci-tag">${text(t.tier)}</span> ${text(t.guidance.summary, "")}${t.guidance.action ? ` <em>${esc(t.guidance.action)}</em>` : ""}</li>`).join("")}</ul></div>` : "");
}

function ccaOwnerBody(o, level) {
    if (o.status !== "observed") return note("The linked owner returned no coding agent rows.");
    const series = o.series || [], p = series.at(-1), labels = series.map(s => monthTick(s.month));
    return kpis([
        kpi("Adoption rate", p ? ratio(p.rate, NA) : NA, "Cube root of share × penetration × frequency"),
        kpi("Stage", p ? text(p.stage, NA) : NA, p ? esc(monthText(p.month)) : "No month reported"),
        kpi("PR share", p ? ratio(p.share, NA) : NA, p ? `${int(p.cca_prs)} of ${int(p.prs)} PRs` : ""),
        kpi("Repo penetration", p ? ratio(p.penetration, NA) : NA, p ? `${int(p.cca_repos)} of ${int(p.repos)} repos` : ""),
        kpi("Active-day frequency", p ? ratio(p.frequency, NA) : NA, p ? `${int(p.cca_days)} of ${int(p.days)} days` : ""),
    ]) + (series.length > 1 ? lineChart({ title: "Coding agent PR share, repo penetration and frequency by month", labels, format: pctTick, series: [
        { name: "PR share", values: series.map(s => s.share), cls: "ci-s1" },
        { name: "Repo penetration", values: series.map(s => s.penetration), cls: "ci-s2" },
        { name: "Active-day frequency", values: series.map(s => s.frequency), cls: "ci-s3" }] }) : "")
        + (o.interpretation ? `<div class="ci-guidance"><strong>Source interpretation</strong><p>${esc(o.interpretation)}</p></div>` : "")
        + epiBlock(o.epi, level)
        + flagNotes(o.flags)
        + details("Exact monthly values", table(["Month", "CCA PRs", "PRs", "Share", "CCA repos", "Repos", "Penetration", "CCA days", "Days", "Frequency", "Rate", "Stage"],
            series.map(s => [esc(monthText(s.month)), int(s.cca_prs), int(s.prs), ratio(s.share), int(s.cca_repos), int(s.repos), ratio(s.penetration),
                int(s.cca_days), int(s.days), ratio(s.frequency), ratio(s.rate), text(s.stage)]), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]));
}

function ccaRollupChart(series) {
    return lineChart({ title: "Coding agent PR share and repo penetration by month", labels: series.map(s => monthTick(s.month)), format: pctTick, series: [
        { name: "PR share", values: series.map(s => s.share), cls: "ci-s1" },
        { name: "Repo penetration", values: series.map(s => s.penetration), cls: "ci-s2" }] });
}

function ccaPortfolioCard(impact, level) {
    const g = gate(impact, "20", "Coding agent (CCA)");
    const data = impact.portfolio?.cca;
    let body = g.blocked || g.stale;
    if (!g.blocked) {
        if (!data?.series?.length) body += note("No owned enterprise or organization returned coding agent rows.");
        else {
            const s = data.summary || {}, p = data.series.at(-1);
            const stages = Object.entries(s.stages || {}).sort((a, b) =>
                (STAGES.indexOf(a[0]) + 1 || 99) - (STAGES.indexOf(b[0]) + 1 || 99) || a[0].localeCompare(b[0]));
            body += kpis([
                kpi("PR share", ratio(p.share, NA), `${compact(p.cca_prs)} of ${compact(p.prs)} PRs · ${esc(monthText(p.month))}`),
                kpi("Repo penetration", ratio(p.penetration, NA), `${compact(p.cca_repos)} of ${compact(p.repos)} repos`),
                kpi("Owners reporting", int(s.owners, NA), "Enterprises and organizations"),
                kpi("Mature or broad", int(sumKnown(stages.filter(([k]) => ["Broad adoption", "Mature adoption"].includes(k)).map(([, v]) => v)) ?? 0), "Owners in the latest month"),
            ]) + ccaRollupChart(data.series)
                + `<div class="ci-pair"><div>${heading(level + 1, "Adoption stage")}${stages.length
                    ? bars(stages.map(([label, value], i) => ({ label, value, cls: PALETTE[Math.min(i, 4)] })))
                    : note("No stages reported.")}<p class="ci-meta">Owners by source stage, ${esc(monthText(s.latest_month))}. Share and penetration are recomputed from summed counts; rates are never averaged.</p></div>`
                + `<div>${heading(level + 1, "EPI lift by complexity")}${(s.epi_tiers || []).length
                    ? table(["Complexity", "Positive", "Negative", "Flat", "No comparison"], s.epi_tiers.map(t =>
                        [text(t.tier), int(t.positive), int(t.negative), int(t.flat), int(t.no_comparison)]), [1, 2, 3, 4])
                    : note("No EPI comparisons reported.")}<p class="ci-meta">Owners whose CCA PRs ranked above or below non-CCA PRs on average. Observational, not causal.</p></div></div>`;
        }
    }
    return card(level, "Copilot coding agent", body, { source: sourceLine(impact, "20", "completed months") });
}

function ccaAccountCard(impact, account, level) {
    const g = gate(impact, "20", "Coding agent (CCA)");
    const c = account?.cca;
    let body = g.blocked || g.stale;
    if (!g.blocked) {
        if (!c) body += note("Coding agent was not reported for this account.");
        else if (c.status !== "observed") body += note("No coding agent owner carries this Salesforce account in the latest collection.");
        else {
            const owners = c.owners || [];
            if (owners.length === 1) body += `<p class="ci-meta">${ownerLabel(owners[0])}</p>` + ccaOwnerBody(owners[0], level + 1);
            else {
                const p = c.series?.at(-1);
                body += kpis([
                    kpi("PR share", p ? ratio(p.share, NA) : NA, p ? `${compact(p.cca_prs)} of ${compact(p.prs)} PRs · ${esc(monthText(p.month))}` : ""),
                    kpi("Repo penetration", p ? ratio(p.penetration, NA) : NA, p ? `${compact(p.cca_repos)} of ${compact(p.repos)} repos` : ""),
                    kpi("Owners", int(owners.length), "Enterprises and organizations"),
                ]) + (c.series?.length > 1 ? ccaRollupChart(c.series) : "")
                    + table(["Owner", "Latest month", "Stage", "Rate", "PR share", "Repo penetration"], owners.map(o => {
                        const q = o.series?.at(-1);
                        return [ownerLabel(o), q ? esc(monthText(q.month)) : DASH, q ? text(q.stage) : DASH, q ? ratio(q.rate) : DASH, q ? ratio(q.share) : DASH, q ? ratio(q.penetration) : DASH];
                    }), [3, 4, 5])
                    + owners.filter(o => o.status === "observed").map(o => details(`${o.owner_type || "Owner"} ${o.slug || o.owner_id}: detail`, ccaOwnerBody(o, level + 1))).join("");
            }
        }
    }
    return card(level, "Copilot coding agent", body, { source: sourceLine(impact, "20", "owners carrying this Salesforce ID") });
}

// ---------------------------------------------------------------- AI units by feature
function weekLabels(asof, n) {
    const end = day(asof);
    return Array.from({ length: n }, (_, i) => {
        if (!end) return `wk ${i - n + 1}`;
        const d = new Date(end.getTime() - (n - 1 - i) * 7 * 86400000);
        return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
    });
}

/** features: {name: {aiu: [latest-first], gross?: [latest-first]}}. */
function aiuBody(features, asof, { level, scope }) {
    const entries = Object.entries(features).map(([name, f]) => ({ name, aiu: f.aiu || [], gross: f.gross || null,
        total: sumKnown(f.aiu || []), grossTotal: f.gross ? sumKnown(f.gross) : null }))
        .sort((a, b) => (b.total ?? -1) - (a.total ?? -1) || a.name.localeCompare(b.name));
    const total = sumKnown(entries.map(e => e.total));
    const n = Math.max(...entries.map(e => e.aiu.length), 0);
    const top = entries.slice(0, 5);
    const latestTotal = sumKnown(entries.map(e => e.aiu[0]));
    const hasGross = entries.some(e => e.gross);
    return kpis([
        kpi("AI units, 13 weeks", compact(total, NA), esc(scope)),
        kpi("Latest week", compact(latestTotal, NA), asof ? `Week ending ${esc(dateText(asof))}` : "Week end not published"),
        kpi("Top feature", top[0] ? esc(top[0].name) : NA, top[0] && total ? `${ratioText(top[0].total / total)} of AI units` : ""),
    ]) + lineChart({ title: "Weekly AI units by Copilot feature", labels: weekLabels(asof, n), format: v => compact(v, ""),
        series: top.map((e, i) => ({ name: e.name, values: [...e.aiu].reverse(), cls: PALETTE[i] })) })
        + (entries.length > top.length ? `<p class="ci-meta">The chart shows the top ${top.length} features; the table lists all ${entries.length}.</p>` : "")
        + table(["Feature", "AI units, 13 wk", "Latest week", "Share", ...(hasGross ? ["Usage value, 13 wk"] : [])],
            entries.map(e => [esc(e.name), int(e.total), int(e.aiu[0]), total && isNum(e.total) ? ratio(e.total / total) : DASH,
                ...(hasGross ? [money(e.grossTotal)] : [])]), [1, 2, 3, 4])
        + `<p class="ci-meta">AI units are Copilot's metered usage unit, not usage value and not a billable amount. Usage value is gross list value, not an invoice.</p>`;
}

function aiuCard(impact, features, { level, id, scope, asofId, source, emptyText }) {
    const g = gate(impact, id, "AI units by feature");
    let body = g.blocked || g.stale;
    if (!g.blocked) {
        if (features == null) body += note("Weekly AI units were not reported for this scope.");
        else if (!Object.keys(features).length) body += note(esc(emptyText));
        else body += aiuBody(features, impact.asof?.[asofId], { level, scope });
    }
    return card(level, "AI units by feature", body, { wide: true, source: source || sourceLine(impact, id) });
}

// ---------------------------------------------------------------- opportunities
function opportunitiesCard(impact, { level, accountId = null, accountLink, limit = 10, showCoverage = false }) {
    const g = gate(impact, "21", "Coding agent opportunity scores");
    if (g.blocked) return accountId && !showCoverage ? "" : card(level, "Coding agent opportunities", g.blocked, { wide: true });
    const o = impact.opportunities;
    const expansion = (o?.expansion || []).filter(e => !accountId || e.sf_id === accountId);
    const newLogo = (o?.new_logo || []).filter(e => !accountId || e.sf_id === accountId);
    if (accountId && !expansion.length && !newLogo.length) return showCoverage
        ? card(level, "Coding agent opportunities", g.stale + note("No opportunity row for this account."), { wide: true, source: sourceLine(impact, "21") }) : "";
    const link = e => accountLink(e.sf_id, e.account || e.sf_id || "Unlinked owner");
    const bandCounts = Object.keys(BANDS).map(letter => [letter, expansion.filter(e => e.band_letter === letter).length]);
    const top = rows => rows.slice(0, accountId ? rows.length : limit);
    const more = (rows, label) => rows.length > limit && !accountId ? `<p class="ci-meta">Showing the top ${limit} of ${rows.length} ${label} by score.</p>` : "";
    const guidance = rows => rows.filter(e => e.guidance?.action || e.guidance?.lift_summary || e.guidance?.opportunity_summary);
    let body = g.stale + `<p class="ci-meta">Scores and recommended actions are source guidance from the coding agent opportunity model, not commitments or forecasts. Owners are GitHub enterprises or organizations.</p>`;
    body += heading(level + 1, `Expansion · ${esc(monthText(o?.expansion_month))}`);
    body += expansion.length
        ? (!accountId ? `<div class="ci-bands">${bandCounts.map(([letter, count]) =>
            `<span class="ci-band ci-band-${letter}"><strong>${letter}</strong> ${esc(BANDS[letter])} <span>${int(count, "0")}</span></span>`).join("")}</div>` : "")
            + table([...(accountId ? [] : ["Account"]), "Owner", "Band", "Score", "Stage", "PR share", "Untapped repos", "EPI lift", "Speed"],
                top(expansion).map(e => [...(accountId ? [] : [link(e)]), ownerLabel(e),
                    e.band_letter ? `<span class="ci-band ci-band-${e.band_letter}" title="${esc(e.band)}"><strong>${e.band_letter}</strong></span>` : text(e.band),
                    isNum(e.score) ? fmt(e.score, 1) : DASH, text(e.stage), ratio(e.share), int(e.untapped_repos), signed(e.lift),
                    isNum(e.speed_multiplier) ? `${fmt(e.speed_multiplier, 1)}×` : DASH]), accountId ? [2, 4, 5, 6, 7] : [3, 5, 6, 7, 8])
            + more(expansion, "expansion owners")
            + (guidance(top(expansion)).length ? details("Source guidance", `<ul class="ci-guidance-list">${guidance(top(expansion)).map(e =>
                `<li><strong>${accountId ? ownerLabel(e) : `${esc(e.account || e.sf_id || "Unlinked")} · ${ownerLabel(e)}`}</strong> ${text(e.guidance.lift_summary, "")} ${text(e.guidance.untapped_summary, "")}`
                + `${e.guidance.action ? ` <em>${esc(e.guidance.action)}</em>` : ""}</li>`).join("")}</ul>`) : "")
        : note("No expansion rows reported.");
    body += heading(level + 1, `New logo readiness · ${esc(monthText(o?.readiness_month))}`);
    body += newLogo.length
        ? table([...(accountId ? [] : ["Account"]), "Owner", "Readiness", "PR volume", "Lead time", "Repos score", "Repos", "Non-CCA PRs"],
            top(newLogo).map(e => [...(accountId ? [] : [link(e)]), ownerLabel(e), isNum(e.score) ? fmt(e.score, 1) : DASH,
                ratio(e.components?.pr_volume), ratio(e.components?.lead_time), ratio(e.components?.repo), int(e.repos), int(e.non_cca_prs)]),
            accountId ? [1, 2, 3, 4, 5, 6] : [2, 3, 4, 5, 6, 7])
            + more(newLogo, "readiness owners")
            + `<p class="ci-meta">Readiness is the product of the PR volume, lead time and repository components × 100, for owners not yet using the coding agent.</p>`
        : note("No new logo readiness rows reported.");
    return card(level, "Coding agent opportunities", body, { wide: true, source: sourceLine(impact, "21") });
}

// ---------------------------------------------------------------- public renderers
const plainLink = (id, name) => esc(name || id || "");
const intro = scope => `<p class="ci-intro">Copilot code review (CCR) and coding agent (CCA) outcomes plus weekly AI units by feature, `
    + `from the warehouse tables behind the Copilot AIU, CCR and CCA reports. ${esc(scope)} Figures are observational and do not attribute outcomes to Copilot.</p>`;

export function codeImpactPortfolioHtml(impact, { level = 2, accountLink = plainLink, id = "code-impact" } = {}) {
    if (!impact) return "";
    const weekly = impact.portfolio?.surface_weekly_aiu;
    const features = weekly ? Object.fromEntries(Object.entries(weekly).map(([name, aiu]) => [name, { aiu }])) : null;
    const aiuGate = impact.sources?.["10"]?.state;
    return `<div class="ci-panel" id="${esc(id)}">${heading(level, "Code review &amp; coding agent impact")}${intro("Measured per GitHub enterprise or organization and rolled up from counts.")}`
        + `<div class="ci-grid">${ccrPortfolioCard(impact, level + 1)}${ccaPortfolioCard(impact, level + 1)}</div>`
        + aiuCard(impact, aiuGate === "collected" || aiuGate === "stale" ? features : null, { level: level + 1, id: "10", asofId: "10", scope: "Owned book, all surfaces",
            emptyText: "No AI units reported in the 13-week window.",
            source: `Surface history · ${impact.asof?.["10"] ? `weeks ending ${esc(dateText(impact.asof["10"]))}` : "as-of date not published"}` })
        + opportunitiesCard(impact, { level: level + 1, accountLink }) + "</div>";
}

/** enterprise: an enterprise row from the Copilot dashboard model (integrations carry weekly_aiu). */
export function codeImpactEnterpriseHtml(impact, enterprise, { level = 2 } = {}) {
    if (!impact || !enterprise) return "";
    const ccrGate = gate(impact, "19", "Code review (CCR)"), ccaGate = gate(impact, "20", "Coding agent (CCA)");
    const ccr = enterprise.ccr ?? impact.enterprise_ccr?.[enterprise.slug] ?? null;
    const cca = enterprise.cca ?? impact.enterprise_cca?.[enterprise.slug] ?? null;
    const ccrBody = ccrGate.blocked || ccrGate.stale + (ccr ? ccrEnterpriseBody(ccr, { level: level + 2 }) : note("No code review row for this enterprise in the latest collection."));
    const ccaBody = ccaGate.blocked || ccaGate.stale + (cca ? ccaOwnerBody(cca, level + 2) : note("No enterprise-level coding agent row in the latest collection. Organization-level rows appear on the linked Salesforce account."));
    const rows = Array.isArray(enterprise.integrations) ? enterprise.integrations : [];
    let features = null;
    if (rows.length && rows.every(r => Array.isArray(r.weekly_aiu))) {
        features = {};
        for (const r of rows) {
            const f = features[r.surface || "Other"] ??= { aiu: Array(r.weekly_aiu.length).fill(0), gross: Array(r.weekly_aiu.length).fill(0) };
            r.weekly_aiu.forEach((v, i) => { f.aiu[i] = isNum(v) ? (f.aiu[i] ?? 0) + v : f.aiu[i]; });
            (r.weekly || []).forEach((v, i) => { f.gross[i] = isNum(v) ? (f.gross[i] ?? 0) + v : f.gross[i]; });
        }
    } else if (!rows.length && impact.sources?.["14"]?.state === "collected") features = {};
    return `<div class="ci-panel">`
        + card(level, "Copilot code review", ccrBody, { wide: true, id: "enterprise-code-review", source: sourceLine(impact, "19", "the current month is month to date") })
        + card(level, "Copilot coding agent", ccaBody, { wide: true, id: "enterprise-coding-agent", source: sourceLine(impact, "20", "completed months") })
        + aiuCard(impact, features, { level, id: "14", asofId: "14", scope: "This enterprise, all surfaces",
            emptyText: "No feature usage reported in the 13-week window.",
            source: `Enterprise surfaces · ${impact.asof?.["14"] ? `weeks ending ${esc(dateText(impact.asof["14"]))}` : "as-of date not published"}${
                rows.length && !features ? " · weekly AI units need a newer collection" : ""}` })
        + "</div>";
}

export function codeImpactAccountHtml(impact, accountId, { level = 2, accountLink = plainLink, title = true, showCoverage = false } = {}) {
    if (!impact) return "";
    const account = impact.accounts?.[accountId] ?? null;
    const aiu = account ? account.aiu_by_feature : impact.sources?.["22"]?.state === "collected" ? {} : null;
    return `<div class="ci-panel">${title ? heading(level, "Code review &amp; coding agent impact") + intro("Attributed only through confirmed enterprise links and owners carrying this Salesforce ID.") : ""}`
        + `<div class="ci-grid">${ccrAccountCard(impact, account, level + 1)}${ccaAccountCard(impact, account, level + 1)}</div>`
        + aiuCard(impact, aiu, { level: level + 1, id: "22", asofId: "22", scope: "This Salesforce account",
            emptyText: "No feature usage reported in the 13-week window." })
        + opportunitiesCard(impact, { level: level + 1, accountId, accountLink, showCoverage }) + "</div>";
}

/** A compact strip for drawers: the latest headline per source, with the reason when it is not reported. */
export function codeImpactSummaryHtml(impact, accountId, { level = 3, href = "" } = {}) {
    if (!impact) return "";
    const account = impact.accounts?.[accountId] ?? null;
    const state = id => impact.sources?.[id]?.state || "absent";
    const reason = id => ["absent", "missing"].includes(state(id)) ? REASONS[state(id)] : null;
    const ccr = account?.ccr, cca = account?.cca, aiu = account?.aiu_by_feature;
    const ccrPoint = ccr?.status === "observed" ? latestComplete(ccr.series) : null;
    const ccaPoint = cca?.status === "observed" ? (cca.owners?.length === 1 ? cca.owners[0].series?.at(-1) : cca.series?.at(-1)) : null;
    const aiuTotal = aiu ? sumKnown(Object.values(aiu).map(f => sumKnown(f.aiu || []))) : null;
    const why = (id, value) => reason(id) ?? (value?.status === "no_enterprise" ? "No confirmed enterprise link" : value?.status === "withheld" ? "Withheld"
        : value?.status === "no_rows" ? "No rows for this account" : "Not reported");
    return `<section class="ci-panel ci-summary">${heading(level, "Code review &amp; coding agent")}`
        + kpis([
            kpi("Review coverage", ccrPoint ? ratio(ccrPoint.coverage, NA) : NA, ccrPoint ? esc(monthText(ccrPoint.month)) : esc(why("19", ccr))),
            kpi("Coding agent PR share", ccaPoint ? ratio(ccaPoint.share, NA) : NA, ccaPoint ? `${ccaPoint.stage ? `${esc(ccaPoint.stage)} · ` : ""}${esc(monthText(ccaPoint.month))}` : esc(why("20", cca))),
            kpi("AI units, 13 weeks", aiu && Object.keys(aiu).length ? compact(aiuTotal, NA) : NA, aiu ? Object.keys(aiu).length ? `${Object.keys(aiu).length} features` : "No feature usage reported" : esc(reason("22") ?? "Not reported")),
        ]) + (href ? `<p class="ci-meta"><a href="${esc(href)}">Full code review, coding agent and AI unit detail</a></p>` : "") + "</section>";
}
