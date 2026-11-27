const FEATURES = [
    { integration: "copilot-pr-reviews", surface: "Code review", label: "Copilot code review" },
    { integration: "copilot-developer", surface: "Coding agent", label: "Copilot cloud agent" },
];
const numeric = value => typeof value === "number" && Number.isFinite(value);
const escape = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const number = value => numeric(value) ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value) : "&mdash;";
const money = value => numeric(value) ? new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 2,
}).format(value) + " [Usage est.]" : "No data";
const METRICS = ["l7", "p7", "l28", "aiu28", "u7", "up7", "u28"];
function usable(row) {
    if (!row) return null;
    const values = {};
    for (const key of METRICS) {
        const value = typeof row[key] === "string" && row[key].trim() ? Number(row[key]) : row[key];
        const known = row.feature_metrics_known ? row.feature_metrics_known[key] === true : value !== 0;
        values[key] = known && numeric(value) ? value : null;
    }
    return { ...row, ...values };
}
const active = (row, window) => !!row && [row[`l${window}`], row[`u${window}`],
    ...(window === 28 ? [row.aiu28] : [])].some(value => numeric(value) && value > 0);
const zero28 = row => !!row && [row.l28, row.u28, row.aiu28].every(value => value === 0);

/** Rows represent individual enterprise or account signals, never combined user counts. */
export function featureUsage(model, enterprise = null, account = null) {
    const source = account ? "08" : "14";
    const available = !!model.sources?.[source] && !model.missing_sources?.includes(source);
    const entities = account ? [account] : enterprise ? [enterprise] : (model.enterprises || []);
    return FEATURES.map(feature => {
        const entries = entities.map(entity => {
            const matches = available && Array.isArray(entity.integrations)
                ? entity.integrations.filter(row => row.integration === feature.integration) : [];
            // A duplicate or incomplete slice is not safe to silently combine.
            const row = matches.length === 1 ? usable(matches[0]) : null;
            const policy = account ? "unknown" : entity.settings?.surfaces?.[feature.surface] || "unknown";
            return { entity, row, policy, gap: policy === "enabled" && zero28(row) };
        });
        return { ...feature, entries, available, source };
    });
}

function sum(rows, key) {
    return rows.length && rows.every(row => numeric(row[key])) ? rows.reduce((total, row) => total + row[key], 0) : null;
}

function table(headers, rows) {
    return `<div class="detail-table"><table><thead><tr>${headers.map(h => `<th>${escape(h)}</th>`).join("")}</tr></thead><tbody>` +
        rows.map(row => `<tr>${row.map((cell, i) => `<td data-label="${escape(headers[i])}">${cell}</td>`).join("")}</tr>`).join("") +
        `</tbody></table></div>`;
}

const OUTCOME_LABELS = { "19": "code review, monthly", "20": "coding agent adoption and EPI, monthly" };

export function featureUsagePanel(model, enterprise = null, account = null, now = new Date()) {
    const data = featureUsage(model, enterprise, account);
    const source = account ? "08" : "14";
    const cutoff = model.asof?.[source];
    const age = cutoff ? (Date.parse(now.toISOString().slice(0, 10)) - Date.parse(cutoff)) / 86400000 : NaN;
    const carried = model.stale_sources?.[source];
    const scope = account ? "Salesforce account" : enterprise ? "GitHub enterprise" : "GitHub enterprise portfolio";
    const collected = id => !!model.sources?.[id] && !model.missing_sources?.includes(id);
    const outcomes = ["19", "20"].filter(collected);
    const weeklyAiu = account ? collected("22")
        : (enterprise ? [enterprise] : model.enterprises || []).some(e => (e.integrations || []).some(r => Array.isArray(r.weekly_aiu)));
    let html = `<h2>Code review &amp; cloud agent usage</h2>` +
        `<p class="meta">${scope}: metered UBB usage indicators, not every interaction. Usage value is gross list value, not an invoice. ` +
        `User signals are peak daily ${account ? "sums of model-slice counts and may double-count people" : "counts from a single model slice"}, ` +
        `not deduplicated unique users over the period. ` +
        `They are not summed across enterprises or features.</p>` +
        (outcomes.length
            ? `<div class="inline-note">PR outcomes come from separate sources and appear in the Copilot code review and coding agent sections` +
              ` (${outcomes.map(id => escape(OUTCOME_LABELS[id])).join(", ")}). They are not derived from, or attributed to, the UBB usage below.</div>`
            : `<div class="inline-note">Outcome metrics are not available: PRs created by cloud agent, PRs reviewed by Copilot, ` +
              `merges and review comments acted on are not supplied by this dashboard's feeds. ` +
              `Overall Impact throughput, merge time and ROI are not attributed to these features.</div>`) +
        `<p class="meta">Source cutoff: ${escape(cutoff || "not published")}` +
        `${carried ? ` &middot; carried from ${escape(carried)}` : ""}. ${weeklyAiu
            ? "Weekly AI units by feature are in the AI units section; the weekly trend here is usage value."
            : "AI units are available for 28 days only; weekly trend is usage value."}</p>` +
        (!Number.isFinite(age) ? `<div class="inline-note warning">The source cutoff is unknown; freshness cannot be established.</div>` :
            age > 3 ? `<div class="inline-note warning">The source cutoff is more than 3 days old; these are retained readings.</div>` :
                age < 0 ? `<div class="inline-note warning">The source cutoff is in the future; verify the refresh clock.</div>` : "") +
        (carried ? `<div class="inline-note warning">This source was carried forward and was not collected in the current snapshot.</div>` : "");
    if (!data[0].available) return html + `<div class="inline-note warning">Integration source unavailable; usage is unknown.</div>`;
    for (const feature of data) {
        const entries = feature.entries, rows = entries.map(e => e.row).filter(Boolean);
        const counts = policy => entries.filter(e => e.policy === policy).length;
        const unknownPolicy = entries.length - counts("enabled") - counts("disabled");
        const gaps = entries.filter(e => e.gap);
        html += `<h3>${escape(feature.label)}</h3>`;
        if (!account && !enterprise) {
            html += `<p class="meta">Observed integration rows: ${number(rows.length)} of ${number(entries.length)} enterprises. ` +
                `At least using (7d / 28d): ${number(rows.filter(row => active(row, 7)).length)} / ${number(rows.filter(row => active(row, 28)).length)}. ` +
                `Policy enabled / disabled / other or unknown: ${number(counts("enabled"))} / ${number(counts("disabled"))} / ${number(unknownPolicy)}.</p>`;
        } else if (enterprise) {
            html += `<p class="meta">Policy: ${escape(entries[0].policy)}; collected ${escape(enterprise.settings?.collected_from || "not recorded")}. ` +
                `${rows.length ? "Integration row observed." : "No usable integration row; usage is unknown."}</p>`;
        } else {
            html += `<p class="meta">${rows.length ? "Integration row observed." : "No usable integration row; usage is unknown."} Account-level policy is not available.</p>`;
        }
        html += table(["7d value", "Prior 7d value", "28d value", "AI units 28d",
            ...((enterprise || account) ? ["Peak daily user signal 7d", "Peak daily user signal prior 7d", "Peak daily user signal 28d"] : [])],
            [[money(sum(rows, "l7")), money(sum(rows, "p7")), money(sum(rows, "l28")), number(sum(rows, "aiu28")),
                ...((enterprise || account) ? [number(rows[0]?.u7), number(rows[0]?.up7), number(rows[0]?.u28)] : [])]]);
        if (!account && gaps.length) {
            html += `<p class="meta">Enabled with an observed zero 28d UBB signal: ` +
                gaps.map(entry => `<button class="link-button" type="button" data-slug="${escape(entry.entity.slug)}">${escape(entry.entity.slug)}</button>`).join(", ") +
                `. This does not rule out unmetered interactions.</p>`;
        } else if (enterprise && gaps.length) {
            html += `<p class="meta">Enabled with an observed zero 28d UBB signal; unmetered interactions may still occur.</p>`;
        }
        if (!account && rows.some(row => Array.isArray(row.weekly) && row.weekly.some(numeric))) {
            const weeks = Array.from({ length: 13 }, (_, index) => {
                const values = rows.map(row => row.weekly?.[index]).filter(numeric);
                return values.length ? `Week ${index + 1}: ${money(values.reduce((a, b) => a + b, 0))} (${number(values.length)} observed)` : null;
            }).filter(Boolean);
            html += `<p class="meta">Weekly usage value, newest first; coverage varies: ${weeks.join(" &middot; ")}.</p>`;
        }
    }
    return html + `<p class="meta">Totals include observed rows only; policy snapshots may have different collection dates. A missing row or policy does not prove no usage. ` +
        `Older snapshots did not retain metric completeness, so reported zeros are unknown until a new snapshot is built. ` +
        `Weekly AI units and feature-specific outcomes are not supplied by this source.</p>`;
}
