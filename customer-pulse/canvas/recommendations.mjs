const escape = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function safeUrl(value) {
    try {
        const url = new URL(value);
        return url.protocol === "https:" ? url.href : null;
    } catch {
        return null;
    }
}

function itemsFor(feed, { filter = "open", query = "", accountId = null, copilot = false } = {}) {
    const text = query.trim().toLowerCase();
    return (feed?.items || []).filter(item => {
        if (accountId && item.account_id !== accountId) return false;
        if (filter === "open" && !["New", "Working", "Saved"].includes(item.status)) return false;
        if (filter === "stale" && item.status !== "Stale") return false;
        const signals = item.signals || [];
        if (copilot && !signals.some(signal => /\bcopilot\b/i.test(signal.recommended_product || ""))) return false;
        return !text || JSON.stringify([item.account_name, item.account_id, item.status, signals]).toLowerCase().includes(text);
    }).sort((a, b) => Number(a.status === "Stale") - Number(b.status === "Stale")
        || String(a.action_by || "9999").localeCompare(String(b.action_by || "9999")));
}

export function renderRecommendations(feed, options = {}) {
    const { impact = false, accountId = null, filter = "open", query = "", now = new Date() } = options;
    const title = impact ? "Salesforce Copilot recommendations" : "Salesforce recommendations";
    const start = `<section class="${impact ? "detail-section" : "panel"} recommendations" aria-labelledby="h-recommendations"><h2 id="h-recommendations">${title}</h2>`
        + "<p>Read-only Salesforce Account Recommender; separate from Pulse signals and warehouse suggested conversations.</p>";
    if (!feed || feed.status !== "available") {
        return start + `<p>Recommendations are unavailable or have not been collected; the count is unknown. ${escape(feed?.reason || "")}</p></section>`;
    }
    if (!Array.isArray(feed.items) || feed.items.some(item => !item || typeof item !== "object" || !Array.isArray(item.signals)
        || item.signals.some(signal => !signal || typeof signal !== "object" || Array.isArray(signal)))) {
        return start + "<p>Recommendation feed has an invalid shape; the count is unknown.</p></section>";
    }
    if (impact && !accountId) return start + "<p>No confirmed Salesforce account mapping; recommendations cannot be attributed to this enterprise.</p></section>";
    const items = itemsFor(feed, { filter: impact ? "all" : filter, query, accountId, copilot: impact });
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const controls = impact ? "" : `<div class="copilot-filters" role="group" aria-label="Recommendation status">`
        + [["open", "Open"], ["stale", "Stale"], ["all", "All"]].map(([key, text]) =>
            `<button type="button" class="button tiny${filter === key ? " selected" : ""}" data-filter="recommendations" data-value="${key}" aria-pressed="${filter === key}">${text}</button>`).join("")
        + `</div><label>Search recommendations <input type="search" data-search value="${escape(query)}"></label>`;
    const sourceAge = /^\d{4}-\d{2}-\d{2}$/.test(feed.source_date || "")
        ? Math.floor((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${feed.source_date}T00:00:00Z`)) / 86400000) : null;
    const age = Number.isFinite(sourceAge) && sourceAge >= 0 ? ` · Source age: ${sourceAge} day(s)` : "";
    const note = `<p>Collected ${escape(feed.collected_from || "unknown")}${feed.carried ? " · Carried forward from an earlier snapshot" : ""}${age}${feed.possibly_truncated ? " · Possible truncation: source limit reached." : ""}</p>`;
    const rows = items.map(item => {
        const url = safeUrl(item.url);
        const due = /^\d{4}-\d{2}-\d{2}$/.test(item.action_by || "") ? item.action_by : null;
        const overdue = due && due < today && ["New", "Working", "Saved"].includes(item.status);
        const signals = (item.signals || []).filter(signal => !impact || /\bcopilot\b/i.test(signal.recommended_product || ""));
        return `<li><strong>${escape(item.account_name || signals[0]?.company_name || item.account_id || "Unknown account")}</strong>`
            + `${item.in_book ? "" : " <strong>(outside owned book)</strong>"} · ${escape(item.status || "Unknown status")}`
            + ` · Action by: ${escape(due || "unknown")}${overdue ? " (overdue)" : ""}`
            + (url ? ` · <a href="${escape(url)}" target="_blank" rel="noopener noreferrer">Open Salesforce record</a>` : "")
            + signals.map(signal => `<div><p>${[signal.recommended_product, signal.revenue_play, signal.type, signal.signal_level].filter(Boolean).map(escape).join(" · ")}</p>`
                + `<p>${escape(signal.ml_driven_insights || "")}</p>`
                + (signal.recommended_outreach_sequence ? `<p>Outreach sequence: ${escape(signal.recommended_outreach_sequence)}</p>` : "")
                + "</div>").join("") + "</li>";
    }).join("");
    return start + controls + `<div data-region="recommendations">${note}` + (rows ? `<ul class="recommendation-list">${rows}</ul>` : "<p>No recommendations match this view.</p>") + "</div></section>";
}
