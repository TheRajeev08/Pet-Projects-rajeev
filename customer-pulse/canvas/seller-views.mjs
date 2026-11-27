import {
    ACCOUNT_TABS, ACCOUNT_LABELS, FEED_LABELS, activitySummary, allAccounts, billedLtm, billedSeries, bookConsumption, bookFeedCoverage, contactCount, contactDirectory, currentRows, dayOf,
    feedOf, GHAS_BILLING_PRODUCTS, invoiceProduct, knownSum, labelOf, latestRows, moneyTypes, monthlyChange, observedCount, openRows, productRows,
    portfolioSeries, rankAccounts, renewalBuckets, renewalRisk, rowsOf, stackGroups, STACK_STATUS, usageTrend, openSeCount,
} from "./seller-data.mjs";
import { codeImpactAccountHtml, impactApplies } from "./copilot/code-impact.mjs";
import { ENABLEMENT_LABELS } from "./copilot/impact.mjs";
import { SOURCE_STATES, addDays, hasProduct, isDay, isNumber, seriesPoints } from "./model.mjs";
import { chartPaths } from "./chart-paths.mjs";

export function createSellerViews({ esc, fmtInt, fmtMoney, fmtPct, fmtDay, sparkline, table, actionsFor, renderRecommendations,
    actionsPanel, changesPanel, productCard, newSignalsPanel }) {
    const noData = '<span class="none">No data</span>';
    const text = value => value == null || value === "" ? noData : esc(value);
    const integer = value => value == null ? noData : fmtInt(value);
    const percent = value => value == null ? noData : fmtPct(value, 1);
    const date = value => dayOf(value) ? esc(fmtDay(dayOf(value), { year: true })) : noData;
    const money = (value, type = "ARR") => {
        if (!moneyTypes.includes(type)) throw new Error("Unknown monetary type");
        return value == null ? noData : `<span class="num">${esc(fmtMoney(value))} <small>[${esc(type)}]</small></span>`;
    };
    const budgetMoney = (value, type, currency) => currency === "USD" ? money(value, type)
        : value == null ? noData : `${text(value)} <small>[${esc(type)}] · ${currency ? esc(currency) : "currency not supplied"}</small>`;
    const link = (url, label) => {
        try {
            const parsed = new URL(url);
            if (parsed.protocol === "https:") return `<a href="${esc(parsed.href)}" target="_blank" rel="noopener noreferrer">${text(label)}</a>`;
        } catch { /* Missing or unsafe evidence URLs remain plain text. */ }
        return text(label);
    };
    const accountLink = a => `<a class="link" data-focus-key="account360:${esc(a.id)}" href="#/account360/summary/account/${encodeURIComponent(a.id)}">${esc(a.name || a.id)}</a>`;
    const header = (title, note) => `<section class="page-head"><div><h1>${esc(title)}</h1><p class="lede">${esc(note)}</p></div></section>`;
    const states = { ok: "Has data", no_rows: "No rows (not zero)", missing: "No data", stale: "Stale",
        not_connected: "Not connected", not_authorized: "Not authorized", failed: "Collection failed", partial: "Partial" };
    function source(account, feed) {
        const info = feedOf(account, feed);
        const state = feed === "contacts" && info.state === "no_rows" ? "No contacts returned" : states[info.state] || "No data";
        return `<p class="seller-source"><strong>${esc(state)}</strong> · ${esc(FEED_LABELS[feed])} · Collected ${date(info.collected_at)}`
            + `${info.reason ? `<br>${esc(info.reason)}` : ""}</p>`
            + `<details class="source-detail"><summary>Source dates & coverage</summary><p>${esc(info.source || FEED_LABELS[feed])} · Data date: ${date(info.as_of)}</p>`
            + (info.coverage ? Object.entries(info.coverage).map(([key, value]) =>
                `<p><strong>${esc(key)}</strong>: ${esc(value.state)} · ${esc(value.reason || "")} · Discovered ${integer(value.discovered)} · Hydrated ${integer(value.hydrated)} · Deferred ${integer(value.deferred)}</p>`
                + (value.errors?.length ? `<p>${value.errors.map(esc).join("; ")}</p>` : "")).join("") : "") + "</details>";
    }
    function bookSources(model, keys) {
        return `<p class="seller-source">${keys.map(key => {
            const f = bookFeedCoverage(model, key);
            return `${esc(FEED_LABELS[key])}: data dates ${date(f.first)}${f.first !== f.as_of ? ` – ${date(f.as_of)}` : ""}; last collected ${date(f.collected_at)}; ${f.reporting}/${f.total} accounts with current rows`;
        }).join("<br>")}</p>`;
    }
    function readySources(model) {
        const dates = keys => {
            const values = model.accounts.flatMap(a => keys.map(key => dayOf(key === "account" ? a.as_of : a.seller?.ready?.[key]))).filter(Boolean).sort();
            return `${date(values[0])}${values[0] !== values.at(-1) ? ` – ${date(values.at(-1))}` : ""}`;
        };
        return `<p class="seller-source">Ready now data dates · Pulse account context: ${dates(["account"])} · Copilot q02/q09: ${dates(["q02_as_of", "q09_as_of"])} · Forecast q16: ${dates(["q16_as_of"])}</p>`;
    }
    const panel = (title, content, meta = "") => `<section class="panel seller-panel"><div class="panel-head"><h2>${esc(title)}</h2></div>${meta}${content}</section>`;
    function simpleTable(headings, rows, empty = "No data") {
        if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
        return `<div class="table-wrap"><table><thead><tr>${headings.map(h => `<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>`
            + rows.map(row => `<tr>${row.map((value, i) => i === 0 ? `<th scope="row">${value}</th>` : `<td data-label="${esc(headings[i])}">${value}</td>`).join("")}</tr>`).join("")
            + "</tbody></table></div>";
    }
    const exact = value => value == null ? noData : esc(value.toLocaleString("en-US", { maximumFractionDigits: 6 }));
    const compactMoney = (value, kind = "ARR") => value == null ? noData
        : `<span class="num" aria-label="${esc(value.toLocaleString("en-US"))} USD ${esc(kind)}">${esc(fmtMoney(value))}</span>`;
    const healthBadge = category => `<span class="health ${["Red", "Yellow", "Green"].includes(category) ? category : ""}">${text(category)}</span>`;
    const health = a => healthBadge(a.products?.ghcp?.health?.category);
    const disclosure = (label, body, meta = "") => `<details class="panel"><summary>${esc(label)}</summary>${meta}${body}</details>`;
    function select(ui, key, label, options, fallback = options[0]?.[0]) {
        const value = options.some(([k]) => k === ui.sellerOptions?.[key]) ? ui.sellerOptions[key] : fallback;
        return `<label class="workspace-select">${esc(label)}<select data-seller-option="${key}" data-focus-key="seller-option:${key}">${options.map(([k, text]) =>
            `<option value="${esc(k)}"${k === value ? " selected" : ""}>${esc(text)}</option>`).join("")}</select></label>`;
    }
    function metrics(items) {
        return `<dl class="workspace-metrics">${items.map(([label, value, detail]) =>
            `<div><dt>${esc(label)}</dt><dd>${value}</dd><dd class="metric-note">${detail || ""}</dd></div>`).join("")}</dl>`;
    }
    function lineChart(series, label, unit = "Billed USD", dateLabel = "Date") {
        const points = (series?.points ?? seriesPoints(series)).filter(([day]) => dayOf(day));
        const known = points.filter(([, value]) => typeof value === "number" && Number.isFinite(value));
        if (!known.length) return `<p class="empty">No reported ${esc(unit)} in this window.</p>`;
        const min = Math.min(0, ...known.map(p => p[1])), max = Math.max(0, ...known.map(p => p[1]));
        const low = Date.parse(points[0][0]), span = Date.parse(points.at(-1)[0]) - low || 1;
        const y = value => 165 - (value - min) / (max - min || 1) * 142;
        const x = day => 72 + (Date.parse(day) - low) / span * 580;
        const paths = chartPaths(points.map(p => p[1]), (value, i) => `${x(points[i][0]).toFixed(1)} ${y(value).toFixed(1)}`);
        const dots = known.map(([day, value]) => `<circle cx="${x(day)}" cy="${y(value)}" r="3"><title>${esc(day)}: ${esc(value)} ${esc(unit)}</title></circle>`).join("");
        const axis = [...new Set([max, (max + min) / 2, min])].map(value => `<line class="grid-line" x1="72" x2="652" y1="${y(value)}" y2="${y(value)}"/><text x="64" y="${y(value) + 4}" text-anchor="end">${esc(unit.includes("USD") ? fmtMoney(value) : fmtInt(value))}</text>`).join("");
        const indices = [...new Set([0, Math.floor(points.length / 2), points.length - 1])];
        const gapNote = paths.gap ? " · Dashed lines span unreported periods." : "";
        return `<figure class="history-chart"><figcaption>${esc(label)} <span>${esc(unit)} · ${esc(points[0][0])} to ${esc(points.at(-1)[0])}${gapNote}</span></figcaption>`
            + `<svg viewBox="0 0 680 205" role="img" aria-label="${esc(label)}. ${esc(unit)}. Exact values follow.">${axis}<path class="history-line" d="${paths.solid}"/>`
            + (paths.gap ? `<path class="history-line" stroke-dasharray="5 4" d="${paths.gap}"/>` : "") + dots
            + indices.map(i => `<text x="${x(points[i][0])}" y="192" text-anchor="${i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}">${esc(dateLabel === "Week ending" ? fmtDay(points[i][0]) : points[i][0].slice(0, 7))}</text>`).join("") + "</svg></figure>"
            + `<details><summary>Exact values & reporting coverage</summary>${simpleTable([dateLabel, unit, ...(series.coverage ? ["Reporting accounts"] : [])],
                points.map(([day, value], i) => [date(day), exact(value), ...(series.coverage ? [`${series.coverage[i]}/${series.total}`] : [])]))}</details>`;
    }
    function bars(rows, label, unit) {
        const known = rows.filter(r => r.value != null);
        const scale = Math.max(1, ...known.map(r => Math.abs(r.value)));
        return `<figure class="bar-chart"><figcaption>${esc(label)} <span>${esc(unit)}</span></figcaption>`
            + known.map(r => `<div class="bar-row"><span>${esc(r.name)}</span><span class="bar-track"><i class="${r.value < 0 ? "negative-bar" : ""}" style="width:${Math.abs(r.value) / scale * 100}%"></i></span><strong>${compactMoney(r.value, unit)}</strong></div>`).join("")
            + (!known.length ? '<p class="empty">No reported values.</p>' : "") + "</figure>"
            + `<details><summary>Exact values, including unknown products</summary>${simpleTable(["Product", unit], rows.map(r => [text(r.name), exact(r.value)]))}</details>`;
    }
    function accountTrend(a, model, ui) {
        const products = productRows(a, model.date);
        const chosen = ui.sellerOptions?.product || "all";
        const product = products.some(p => p.key === chosen) ? chosen : null;
        const measure = ui.sellerOptions?.measure || "billed";
        const telemetry = { ghe: [a.products?.ghe?.series?.metered, "GHE account-wide metered users", "Users"],
            copilot: [a.products?.ghcp?.series?.assigned, "Copilot assigned seats", "Seats"],
            security: [a.products?.ghas?.series?.committers, "Security active committers", "Committers"] };
        const [data, label, unit] = telemetry[measure] || [billedSeries(a, model.date, product), `${product ? labelOf(product) : "All products"} · closed-month billing`, "Billed USD"];
        const count = ui.sellerOptions?.period === "6" ? 6 : 13;
        return panel("Product consumption", `<div class="workspace-toolbar">${select(ui, "measure", "Measure", [["billed", "Invoice billing"], ["ghe", "GHE metered users"], ["copilot", "Copilot assigned seats"], ["security", "Security committers"]])}`
            + (measure === "billed" ? select(ui, "product", "Product", [["all", "All products"], ...products.map(p => [p.key, p.name])])
                + select(ui, "period", "Closed months", [["13", "13 months"], ["6", "6 months"]]) : "") + "</div>"
            + lineChart(measure === "billed" ? { ...data, points: data.points.slice(-count) } : data, label, unit),
        measure === "billed" ? source(a, "invoices") : `<p class="seller-source">Daily observations in the stored telemetry window; not added across dates. Account source date ${date(a.as_of)}.</p>`);
    }
    function change(value) {
        if (!value) return noData;
        return `${money(value.delta, "Billed")}<span class="sub">${value.fraction === null ? "Percent change unavailable (zero baseline)" : percent(value.fraction)}</span>`;
    }
    function trend(series, label) {
        return sparkline(series, { label, width: 180, height: 36 }) || noData;
    }
    function seriesTable(series, type = "Billed") {
        return simpleTable(["Month", type], series.points.map(([day, amount]) => [date(day), money(amount, type)]));
    }
    function recordPanel(account, feed, columns) {
        const info = feedOf(account, feed);
        // Retained rows stay inspectable during failures, but never contribute to current aggregates.
        const records = rowsOf(account, feed);
        const warning = info.state !== "ok" && records.length ? '<p class="notice warning">Retained evidence, not current reporting.</p>' : "";
        return panel(FEED_LABELS[feed], warning + simpleTable(columns.map(c => c[0]), records.map(r => columns.map(([, field, format]) =>
            format ? format(r[field], r) : text(r[field])))), source(account, feed));
    }
    const recordColumns = [["Record", "title", (v, r) => link(r.url, v || r.name || r.id)], ["Date", "date", date],
        ["Status", "status"], ["Details", "description"]];
    function productTable(a, model) {
        return panel("Products & consumption", simpleTable(
            ["Product", "Status", "Seats", "Used %", "ARR", "Billed LTM", "Billed last month (security split)", "1-month change", "3-month change", "Data date"],
            productRows(a, model.date).map(p => [text(p.name), text(p.status), integer(p.seats), percent(p.utilization), money(p.arr),
                `${money(p.billed, "Billed")}<span class="sub">${p.billingSource === "p06" ? "p06 collected LTM aggregate" : `${p.months}/12 months with invoice rows`}</span>`, money(p.lcm, "Billed"),
                change(monthlyChange(p.trend, 1)), `${trend(p.trend, `${p.name} closed-month billing`)}${change(monthlyChange(p.trend, 3))}`, date(p.as_of)])),
            `<p class="seller-source">Ready now: p02 contracts / p04 seats / p06 security split. Account data: ${date(a.as_of)}. `
            + "Invoices compare closed calendar months (1/3 months), not rolling daily spend. Missing product rows do not prove whitespace. GHE Cloud usage is not inferred from account-wide GHE usage.</p>" + source(a, "invoices"));
    }
    function readyPanel(a) {
        const r = a.seller?.ready || {};
        const ghe = a.products?.ghe || {};
        return panel("Collected commercial context", simpleTable(["Measure", "Value", "Data date"], [
            ["Copilot AI credits, last month", money(r.copilot_aiu_billed_lcm, "Billed"), date(r.q02_as_of)],
            ["Copilot AI credits, last 12 months", money(r.copilot_aiu_billed_ltm, "Billed"), date(r.q02_as_of)],
            ["Copilot month forecast (not billed MTD)", money(r.forecast_bill, "Usage est."), date(r.q16_as_of)],
            ["GHE potential seats (parent model)", integer(r.ghe_potential_seats), date(r.q17_as_of)],
            ["GHE annual SOM (parent model)", money(r.ghe_som_annual, "Model est."), date(r.q17_as_of)],
            ["Security potential seats (parent model)", integer(r.ghas_potential_seats), date(r.q17_as_of)],
            ["Security annual SOM (parent model)", money(r.ghas_som_annual, "Model est."), date(r.q17_as_of)],
            ["Deployment", text(r.deployment), date(r.q02_as_of)],
            ["Metered cloud users", integer(ghe.metered_detail?.cloud), date(ghe.metered_as_of)],
            ["Metered server users", integer(ghe.metered_detail?.server), date(ghe.metered_as_of)],
        ]), '<p class="seller-source"><span class="chip">Ready now</span> Copilot q02/q16/q17 and Pulse p03. Parent model estimates repeat across children: never sum them. Residency is shown only when present in deployment metadata.</p>');
    }
    function whyNow(a, model) {
        const actions = actionsFor(model, { accountId: a.id });
        const rows = actions.map(r => [text(r.priority), text(r.title), text(r.why)]);
        for (const r of openRows(a, "risks")) rows.push([text(r.priority), text(r.title), text(r.reason)]);
        for (const r of openRows(a, "support").filter(r => ["urgent", "high", "p1", "p2"].includes(String(r.priority).toLowerCase())))
            rows.push([text(r.priority), text(r.title), "Review the open support escalation before renewal."]);
        return panel("Why talk now & next-best actions", rows.length ? `<ul class="brief-actions">${rows.map(([priority, title, evidence]) =>
            `<li><span class="chip">${priority}</span><h3>${title}</h3><p>${evidence}</p></li>`).join("")}</ul>`
            : '<p class="empty">No evidence-backed action in the current snapshot.</p>',
            `<p class="seller-source">Pulse rules and current internal risk/support records · ${date(model.date)}</p>`);
    }
    function consumptionDetails(a, model) {
        const products = productRows(a, model.date).filter(p => p.months > 0);
        const history = panel("13-month billed history by product", products.length ? products.map(p =>
            `<details><summary>${esc(p.name)} ${trend(p.trend, `${p.name} [Billed] USD`)}</summary>${seriesTable(p.trend)}</details>`).join("") : '<p class="empty">No data</p>',
        source(a, "invoices"));
        const units = recordPanel(a, "invoices", [["Month", "date", date], ["Product", "product", v => text(labelOf(v))],
            ["Billing channel", "billing_source"], ["Amount USD", "amount", v => money(v, "Billed")],
            ["Units", "units", integer], ["Unit of measure", "uom"]]);
        const actions = rowsOf(a, "actions");
        const actionSeries = key => ({ points: actions.map(r => [dayOf(r.date), r[key] ?? null]).filter(p => p[0]) });
        const actionTrends = panel("Actions runner / job trends", simpleTable(["Measure (28-day rolling)", "Past 90 days"],
            [["All minutes", trend(actionSeries("minutes"), "Actions minutes")], ["Jobs", trend(actionSeries("jobs"), "Actions jobs")],
                ["Hosted minutes", trend(actionSeries("hosted_minutes"), "GitHub-hosted minutes")],
                ["Self-hosted minutes", trend(actionSeries("self_hosted_minutes"), "Self-hosted minutes")]]),
        source(a, "actions") + "<p>Rolling observations are not added across dates. Minutes, jobs and active days are separate units.</p>");
        return history + units + actionTrends + recordPanel(a, "actions", [["Date", "date", date], ["Minutes / 28d", "minutes", integer],
            ["Hosted minutes", "hosted_minutes", integer], ["Self-hosted minutes", "self_hosted_minutes", integer],
            ["Jobs / 28d", "jobs", integer], ["Hosted jobs", "hosted_jobs", integer], ["Self-hosted jobs", "self_hosted_jobs", integer],
            ["Active days / 28d", "active_days", integer], ["Minutes latest 14d", "minutes_14d", integer],
            ["Minutes prior 14d", "minutes_prior14", integer], ["Jobs latest 14d", "jobs_14d", integer], ["Jobs prior 14d", "jobs_prior14", integer]])
            + recordPanel(a, "model_cost", [["Month", "date", date], ["Enterprise", "enterprise_id"], ["Model", "model"], ["Integration", "integration"],
                ["Usage-side net USD", "amount", v => money(v, "Usage est.")], ["AI units", "units", integer],
                ["Distinct actors in this slice only", "actors", integer]]);
    }
    function commercial(a, model) {
        const contracts = Object.values(a.products || {}).flatMap(p => Object.entries(p.by_type || {}).map(([type, r]) =>
            [text(type), text((r.skus || []).join(", ")), integer(r.seats), money(r.arr), date(r.since)]));
        const mrr = latestRows(a, "mrr")[0];
        const mrrRows = mrr ? Object.entries(mrr).filter(([key]) => key.endsWith("_mrr")).map(([key, value]) =>
            [text(key.replaceAll("_", " ")), money(value, "MRR"), date(mrr.date)]) : [];
        return panel("ARR by contracted product / SKU", simpleTable(["Product", "SKUs", "Seats", "ARR", "Since"], contracts),
            `<p class="seller-source">Ready now · p02 contract intervals · ${date(a.products?.ghe?.as_of)}</p>`)
            + panel("License vs metered MRR", simpleTable(["Component", "MRR (not billed)", "Month"], mrrRows), source(a, "mrr"))
            + recordPanel(a, "opportunities", [["Opportunity", "title"], ["Close date", "date", date], ["Stage", "status"], ["Type", "type"],
                ["Expected ARR", "expected_arr", v => money(v)], ["New ARR", "new_arr", v => money(v)], ["Upsell ARR", "upsell_arr", v => money(v)],
                ["Contraction ARR", "contraction_arr", v => money(v)], ["Churn ARR", "churn_arr", v => money(v)]])
            + recordPanel(a, "hierarchy", [["Child account", "name"], ["Parent ID", "parent_id"]])
            + recordPanel(a, "budgets", [["Scope", "scope"], ["Product", "product"], ["Target", "target", (v, r) => budgetMoney(v, "Budget", r.currency)],
                ["Spend", "spend", (v, r) => budgetMoney(v, "Usage est.", r.currency)], ["Limit type", "limit_type"]])
            + recordPanel(a, "selfserve", [["Month", "date", date], ["Billing type", "billing_type"], ["Team seats", "seats", integer],
                ["Team ARR", "arr", v => money(v)], ["Team MRR", "mrr", v => money(v, "MRR")]])
            + panel("Team to Enterprise candidates", "<p>Account-mapped self-serve Team seats are upgrade signals, not organization-level proof. Org names are No data in the account-grain source.</p>", source(a, "selfserve"))
            + readyPanel(a) + consumptionDetails(a, model);
    }
    function security(a) {
        const cq = a.products?.ghas?.code_quality;
        const latest = latestRows(a, "protection")[0];
        const ratio = key => latest && typeof latest.private_repos === "number" && latest.private_repos > 0
            && typeof latest[key] === "number" && latest[key] >= 0 && latest[key] <= latest.private_repos ? latest[key] / latest.private_repos : null;
        return metrics([["Code protection", percent(ratio("code_repos")), "Share of reported private repositories"],
            ["Secret protection", percent(ratio("secret_repos")), "Separate, overlapping population"],
            ["Active committers", integer(a.products?.ghas?.committers), `Account snapshot ${date(a.as_of)}`]])
            + `<div class="workspace-columns">${["code_repos", "secret_repos"].map((key, i) => panel(i ? "Secret protection history" : "Code protection history",
                lineChart({ points: currentRows(a, "protection").map(r => [r.date, r[key]]).sort(([a], [b]) => a.localeCompare(b)) },
                    i ? "Secret-protected repositories" : "Code-protected repositories", "Repositories"), source(a, "protection"))).join("")}</div>`
            + disclosure("Exact protection populations", recordPanel(a, "protection", [["Date", "date", date], ["Private repositories", "private_repos", integer], ["Code protected repos", "code_repos", integer],
            ["Secret protected repos", "secret_repos", integer], ["All commits", "commits", integer], ["Code protected commits", "code_commits", integer],
            ["Secret protected commits", "secret_commits", integer]]))
            + panel("Code Security / Secret Protection billing", simpleTable(["Product", "Latest complete month"], [
                ["Code Security", money(a.billing_lcm?.ghcs, "Billed")], ["Secret Protection", money(a.billing_lcm?.ghsp, "Billed")]]),
            `<p class="seller-source">Ready now · p06 C360 account attributes · ${date(a.as_of)}</p>`)
            + panel("Code Quality", cq ? simpleTable(["Measure", "Value"], [
                ["Active repositories", integer(cq.repos_active)], ["Committers", integer(cq.committers)],
                ["Autofixes / 30d", integer(cq.autofixes_l30d)], ["PRs fixed / 30d", integer(cq.prs_fixed_l30d)]]) : '<p class="empty">No data</p>',
            `<p class="seller-source">Ready now · p05 Code Quality · ${date(cq?.date)}</p>`);
    }
    function success(a) {
        return recordPanel(a, "risks", [["Risk", "title"], ["Opened", "date", date], ["Status", "status"], ["Risk level", "priority"],
            ["Reason", "reason"], ["Product", "product"], ["CSM ARR at risk (forecast)", "arr_at_risk", v => money(v)],
            ["Renewal ARR at risk (forecast)", "renewal_arr_at_risk", v => money(v)]])
            + recordPanel(a, "sentiment", [["Date", "date", date], ["Measure", "name"], ["Rating", "label"], ["Score", "score", integer], ["Trend", "trend"]])
            + recordPanel(a, "success", [["Objective", "objective"], ["Status", "status"], ["Review due", "due", date],
                ["GHE goals", "ghe_goals"], ["Security goals", "security_goals"], ["Copilot goals", "copilot_goals"], ["Actions goals", "actions_goals"]])
            + recordPanel(a, "support", [["Ticket", "title"], ["Created", "date", date], ["Status", "status"], ["Priority", "priority"],
                ["First response (minutes)", "first_response_minutes", integer], ["Resolution (calendar minutes)", "resolution_minutes", integer],
                ["Handle time (hours)", "handle_hours", integer], ["CSAT / 5", "csat", integer]])
            + recordPanel(a, "services", [["Service", "title"], ["Close date", "date", date], ["Services booked (not ARR)", "booked", v => money(v, "Booked")],
                ["GitHub expected services revenue", "expected", v => money(v, "Services est.")]])
            + recordPanel(a, "retention", [["Period end", "date", date], ["Period start", "start", date], ["ARR type", "arr_type"], ["Segment", "arr_segment"],
                ["NDRR (revenue, not MAU)", "ndrr", percent], ["GDRR (revenue, not MAU)", "gdrr", percent]]);
    }
    function contacts(a, ui = {}, preview = false) {
        const directory = contactDirectory(a, preview ? "" : ui.sellerOptions?.contactsQuery || "");
        const rows = preview ? directory.slice(0, 4) : directory;
        return panel("Contacts (POCs)", (preview ? "" : `<label class="workspace-search">Find a contact<input type="search" data-seller-search="contactsQuery" data-focus-key="contacts-search" value="${esc(ui.sellerOptions?.contactsQuery || "")}" placeholder="Name, role or email"></label>`)
            + simpleTable(["Contact", "Role", "Email", "CRM activity", "Observed interaction"], rows.map(p =>
                [text(p.name || p.id), text(p.role), text(p.email), date(p.last_activity), p.interaction
                    ? `${date(p.interaction.last)}<span class="sub">Exact email match · ${text(p.interaction.sources.join(", "))}</span>`
                    : '<span class="none">No matched email / Gong interaction</span>']),
            feedOf(a, "contacts").state === "no_rows" ? "No contacts returned" : "No contacts available in this snapshot.")
            + (preview ? `<a href="#/account360/contacts/account/${encodeURIComponent(a.id)}">${contactCount(a) == null ? "View contact directory" : `View all ${integer(contactCount(a))} recorded contacts`}</a>` : "")
            + '<p class="note">Every stable-ID Salesforce Contact is included. CRM activity is not proof of a conversation.</p>', source(a, "contacts"));
    }
    function stack(a, preview = false) {
        const groups = stackGroups(a);
        const rows = preview ? groups.slice(0, 4) : groups;
        return panel("Reported tech stack", rows.length ? `<div class="stack-list">${rows.map(g =>
            `<article><div class="stack-heading"><h3>${esc(g.technology)}</h3><span class="chip">${esc(g.category)}</span></div>`
            + `<p><strong>${esc(STACK_STATUS[g.status])}</strong> · as of ${date(g.as_of)}</p>`
            + (g.evidence.some(r => r.source_state === "retained") ? '<p class="note">Includes retained evidence; collection is incomplete.</p>' : "")
            + `<details><summary>${g.evidence.length} supporting ${g.evidence.length === 1 ? "source" : "sources"}</summary>${g.evidence.map(r =>
                `<div class="evidence"><p>${link(r.source_url, r.source_type === "gong" ? "Gong / CRM source" : "SE issue source")} · ${date(r.observed_at)} · ${esc(STACK_STATUS[r.usage_status] || "Mention only")}</p>`
                + `<blockquote>${esc(r.evidence_quote)}</blockquote><p class="note">${r.source_current === false ? "Superseded source version" : text(r.verification_state)} · ${r.source_state === "retained" ? "Retained from last successful read" : "Collector-verified source version"} · Collected ${date(r.collected_at)}${r.reason ? ` · ${esc(r.reason)}` : ""}</p></div>`).join("")}</details></article>`).join("")}</div>`
            + (preview ? `<a href="#/account360/stack/account/${encodeURIComponent(a.id)}">Inspect all stack evidence</a>` : "")
            : '<p class="empty">No verified SE / Gong stack evidence. Missing evidence is not an empty technology stack.</p>',
        source(a, "tech_stack"));
    }
    function activity(a, model, ui = {}) {
        const summary = activitySummary(a);
        const emails = summary.emails;
        const threads = new Map();
        for (const r of emails) {
            const key = r.thread || r.id;
            const t = threads.get(key) || { title: r.title, url: r.url, count: 0, dates: [], people: new Set() };
            t.count++;
            if (dayOf(r.date)) t.dates.push(dayOf(r.date));
            for (const p of r.people || []) if (p.email) t.people.add(p.email);
            threads.set(key, t);
        }
        const months = Array.from({ length: 6 }, (_, i) => {
            const day = new Date(`${model.date.slice(0, 7)}-01T00:00:00Z`);
            day.setUTCMonth(day.getUTCMonth() - 5 + i);
            return day.toISOString().slice(0, 7);
        });
        const sourceFilter = ui.sellerOptions?.engagementSource || "all", issueFilter = ui.sellerOptions?.issueState || "all";
        const issueRows = rowsOf(a, "issues").filter(r => issueFilter === "all" || (issueFilter === "closed"
            ? String(r.status || "").toLowerCase().startsWith("closed") : String(r.status || "").toLowerCase() === "open"));
        const timeline = [["emails", rowsOf(a, "emails")], ["gong", rowsOf(a, "gong")], ["issues", issueRows]]
            .filter(([key]) => sourceFilter === "all" || sourceFilter === key)
            .flatMap(([key, rows]) => rows.map(r => ({ ...r, feed: key }))).sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
        const volume = { points: months.map(month => [`${month}-01`, timeline.length ? timeline.filter(r => dayOf(r.date)?.startsWith(month)).length : null]) };
        return panel("Engagement timeline", `<div class="workspace-toolbar">${select(ui, "engagementSource", "Source", [["all", "All sources"], ["emails", "Email"], ["gong", "Gong"], ["issues", "Account-linked issues"]])}`
            + select(ui, "issueState", "Issue state", [["all", "All states"], ["open", "Open"], ["closed", "Closed"]]) + "</div>"
            + `<p class="note">Observed records only, not total account engagement. Retained rows remain visible; see each source's freshness below.</p>`
            + `<ol class="engagement-timeline">${timeline.map(r => `<li><time>${date(r.date)}</time><div><span class="chip">${esc(FEED_LABELS[r.feed])}</span> <span>${text(r.status)}</span><h3>${link(r.url, r.title || r.id)}</h3>`
                + (r.description ? `<details><summary>Source summary</summary><p class="evidence-text">${esc(r.description)}</p></details>` : "")
                + `<span class="sub">${esc(states[feedOf(a, r.feed).state] || "No data")}</span></div></li>`).join("")}</ol>`
            + (!timeline.length ? '<p class="empty">No observed engagement for these filters.</p>' : "")
            + `<details><summary>Observed monthly volume</summary>${lineChart(volume, "Selected engagement records", "Records")}</details>`,
        ["emails", "gong", "issues"].map(key => source(a, key)).join(""))
            + disclosure("Email threads & correspondents", simpleTable(["Thread", "Messages", "Latest", "Matched Salesforce contacts"], [...threads.values()].map(t =>
                [link(t.url, t.title), integer(t.count), date(t.dates.sort().at(-1)), text([...t.people].join(", "))])), source(a, "emails"))
            + disclosure("Contacts with matched interactions", simpleTable(["Contact", "Role", "Email", "Evidence sources", "Last contact"],
                summary.spoken.map(p => [text(p.name), text(p.role), text(p.email), text(p.sources.join(", ")), date(p.last)])),
            source(a, "contacts") + "<p>Only exact email matches to Salesforce customer contacts count here. The contact list below alone does not prove a conversation.</p>")
            + disclosure("Success timeline, notes & partners", recordPanel(a, "timeline", [["Activity", "title"], ["Date", "date", date], ["Type", "type"], ["Attendees (source text)", "attendees"],
                ["Products discussed (not proof of stack)", "products"], ["Notes", "notes"]])
            + recordPanel(a, "notes", recordColumns) + recordPanel(a, "partners", recordColumns))
            + panel("Gong & web signals", simpleTable(["Signal", "Source", "Date"], (model.signals || []).filter(s => s.account_id === a.id && ["gong", "web"].includes(s.source))
                .map(s => [link(s.url, s.title), text(s.source), date(s.date)])), `<p class="seller-source">Evidence-backed signals · ${date(model.date)}</p>`)
            ;
    }
    function codeImpact(a, ui, showCoverage = false) {
        if (ui.copilotLicensesLoading) return panel("Copilot code review, coding agent & AI units", '<p class="seller-source" role="status">Loading Copilot outcome metrics…</p>');
        const impact = ui.copilotLicenses?.impact;
        if (!impact || !(showCoverage || hasProduct(a, "ghcp") || impactApplies(impact, a.id))) return "";
        return `<section class="panel seller-panel copilot-impact"><div class="panel-head"><h2>Copilot code review, coding agent &amp; AI units</h2></div>`
            + codeImpactAccountHtml(impact, a.id, { level: 3, title: false, showCoverage }) + "</section>";
    }
    function copilotSource(sources, id) {
        const s = sources?.[id];
        if (!s || ["missing", "absent"].includes(s.state)) return `q${id} · Not available`;
        return `q${id} · ${s.cutoff ? `Through ${date(s.cutoff)}` : `Cutoff not reported${s.collected ? ` · Collected ${date(s.collected)}` : ""}`}`
            + (s.state === "stale" ? ` · Carried from ${date(s.from)}` : "");
    }
    function accountCopilot(a, model, ui, detail = false) {
        const cp = a.products.ghcp, metricsData = ui.copilotLicenses?.accountMetrics;
        const reading = metricsData && Object.hasOwn(metricsData.readings, a.id) ? metricsData.readings[a.id] : null;
        const sources = metricsData?.sources;
        const unavailable = '<span class="none">Not available</span>';
        const asOf = day => dayOf(day) ? date(day) : "Date not reported";
        const pulse = (feed, day) => {
            const state = model.sources.find(s => s.feed === feed)?.state;
            return `Pulse · ${asOf(day)}${state && state !== "ok" ? ` · ${esc(SOURCE_STATES[state] || state)}` : ""}`;
        };
        const origin = q => copilotSource(sources, q);
        const pick = (label, ...choices) => {
            const [value, source] = choices.find(([n]) => isNumber(n)) || [null, choices[0][1]];
            return [label, value === null ? unavailable : integer(value), source];
        };
        const [week, prior] = reading?.weekly || [];
        const change = isNumber(week) && isNumber(prior) ? week - prior : null;
        const encoded = encodeURIComponent(a.id);
        const status = ui.copilotLicensesLoading ? "Loading Copilot dashboard metrics…"
            : ui.copilotLicensesError || (!metricsData ? ui.copilotLicenses?.note || "Copilot dashboard not available. Showing Pulse readings only."
                : !reading ? "No matching Salesforce ID in the Copilot dashboard. Showing Pulse readings only." : null);
        const healthValue = cp.health?.category ?? reading?.health;
        const summary = panel("Copilot adoption", metrics([
            pick("Assigned seats", [cp.assigned, pulse("copilot", cp.as_of)], [reading?.assigned, origin("06")], [reading?.snapshotAssigned, origin("02")]),
            pick("Active users · 28 days", [cp.funnel?.active_1d28, pulse("attributes", a.as_of)], [reading?.active28, origin("03")], [reading?.snapshotActive, origin("02")]),
            pick("Engaged users · 28 days", [cp.funnel?.engaged_1d28, pulse("attributes", a.as_of)], [reading?.engaged28, origin("02")]),
            ["Active users · 7 days", isNumber(week) ? integer(week) : unavailable,
                `${change === null ? "WoW not available" : `${change > 0 ? "+" : ""}${integer(change)} ${Math.abs(change) === 1 ? "user" : "users"} WoW`} · ${origin("03")}`],
        ]) + `<p class="seller-source">Seats under contract: <strong>${isNumber(cp.contracted) ? integer(cp.contracted) : unavailable}</strong> · ${pulse("copilot_seats", cp.contracted_as_of)}`
            + ` &nbsp; | &nbsp; Copilot health: ${healthValue ? healthBadge(healthValue) : unavailable} · ${cp.health?.category ? pulse("copilot", cp.health.date) : origin("06")}</p>`
            + (status ? `<p class="seller-source" role="status">${esc(status)}</p>` : "")
            + `<div class="workspace-toolbar">${detail ? "" : `<a class="button" href="#/account360/copilot/account/${encoded}">View Copilot metrics</a>`}`
            + (reading ? `<a href="#/copilot/desk/account/${encoded}">Open in Adoption desk</a>` : "") + "</div>");
        if (!detail || !reading) return summary;
        const cutoff = sources["07"].cutoff;
        const trend = (key, title, units) => isDay(cutoff) && reading[key].some(isNumber)
            ? lineChart({ points: Array.from({ length: 13 }, (_, i) => [addDays(cutoff, -7 * (12 - i)), reading[key][12 - i] ?? null]) },
                title, units, "Week ending") : `<p class="empty">${esc(title)}: not available for a dated window.</p>`;
        const value = n => isNumber(n) ? exact(n) : unavailable;
        const usage = panel("Weekly Copilot consumption", `<div class="workspace-columns"><div>${trend("aiu", "AI units consumed", "AI units")}</div>`
            + `<div>${trend("gross", "Gross usage value", "Usage est. USD")}</div></div>`,
        `<p class="seller-source">13 seven-day windows · ${origin("07")}. Gross usage value is before credits, not an invoice.</p>`);
        const total = reading.models.every(r => isNumber(r.gross)) ? knownSum(reading.models.map(r => r.gross)) : null;
        const mix = `<div class="workspace-columns">${panel("Surface usage · 28 days", simpleTable(["Metered surface", "AI units", "Gross USD"],
            reading.surfaces.map(r => [text(r.name), value(r.aiu), value(r.gross)]), "Not available: no reported surface rows."),
        `<p class="seller-source">${origin("08")}. Metered activity only; an unlisted surface is unknown.</p>`)}`
            + panel("Model mix · 28 days", simpleTable(["Model", "Gross USD", "Share of value"],
                reading.models.map(r => [text(r.name), value(r.gross), total > 0 ? percent(r.gross / total) : unavailable]), "Not available: no reported model rows."),
            `<p class="seller-source">${origin("11")}. Share of reported positive usage value, not requests.</p>`) + "</div>";
        const enterprise = panel("Enterprise impact & settings", simpleTable(["Confirmed enterprise", "Impact", "Feature settings"], reading.enterprises.map(e => {
            const policies = Object.entries(e.policies || {});
            return [text(e.slug), `${text(e.state)} · ${asOf(e.impactDate)}<br>PRs merged: ${value(e.merged)}`
                + (isNumber(e.cohortDays) ? ` · ${integer(e.cohortDays)}-day cohort` : ""),
            policies.length ? `${policies.map(([name, policy]) => `${esc(name)}: ${esc(ENABLEMENT_LABELS[policy] || "Unknown")}`).join("<br>")}<br>Collected ${asOf(e.settingsDate)}` : unavailable];
        }), "Not available: no confirmed warehouse-linked enterprise with mapping coverage."),
        `<p class="seller-source">Shown per enterprise, never added across slugs. Mapping: ${origin("13")} / ${origin("15")}.</p>`);
        return summary + usage + mix + enterprise + codeImpact(a, ui, true);
    }
    function account360(model, ui) {
        const a = model.accounts.find(a => a.id === ui.account);
        if (!a) return top50(model, ui, true);
        const tab = ACCOUNT_TABS.includes(ui.subview) ? ui.subview : "summary";
        const content = tab === "commercial" ? commercial(a, model) : tab === "products" ? accountTrend(a, model, ui) + productTable(a, model) + codeImpact(a, ui) + disclosure("Invoice quantities, runners & model costs", consumptionDetails(a, model))
            : tab === "copilot" ? accountCopilot(a, model, ui, true) : tab === "security" ? security(a) : tab === "success" ? success(a) : tab === "activity" ? activity(a, model, ui)
                : tab === "contacts" ? contacts(a, ui) : tab === "stack" ? stack(a)
                    : accountCopilot(a, model, ui) + `<div class="workspace-columns">${accountTrend(a, model, ui)}${whyNow(a, model)}</div>`
                        + `<div class="workspace-columns">${contacts(a, ui, true)}${stack(a, true)}</div>`
                        + panel("Recent engagement", simpleTable(["Source", "Last observed", "Open SE issues"],
                            [["Email", date(activitySummary(a).lastEmail), integer(openSeCount(a))],
                                ["Gong", date(activitySummary(a).lastMeeting), `<a href="#/account360/activity/account/${encodeURIComponent(a.id)}">View engagement</a>`]]))
                        + renderRecommendations(model.recommendations, { accountId: a.id, now: ui.now });
        const billing = billedLtm(a, model.date);
        return `<div class="account-toolbar"><a class="button" href="${esc(ui.returnHash || "#/account360")}">Back to accounts</a>`
            + `<label class="workspace-select">Switch account<select data-account-switch data-focus-key="account-switch">${allAccounts(model).map(r =>
                `<option value="${esc(r.id)}"${r.id === a.id ? " selected" : ""}>${esc(r.name || r.id)}</option>`).join("")}</select></label></div>`
            + header(a.name || a.id, `Account 360 / ${ACCOUNT_LABELS[tab]}`)
            + `<div class="account-identity">${link(a.sfdc_url, "Salesforce account")}${a.parent ? ` · ${esc(a.parent)}` : ""}`
            + `${["summary", "copilot"].includes(tab) ? "" : ` · Copilot health ${health(a)}`} · Snapshot ${date(a.as_of)}</div>`
            + metrics([["Account ARR · USD", compactMoney(a.arr), "Recurring revenue"],
                ["Observed billed LTM · USD", compactMoney(billing.value, "Billed"), billing.months == null ? "Collected LTM aggregate" : `${billing.months}/12 months reporting`],
                ["Renewal", date(a.renewal), `${integer(a.renewing_in_days)} days`],
                ["Recorded contacts (POCs)", integer(contactCount(a)), esc(states[feedOf(a, "contacts").state] || "No data")]]) + content;
    }
    function top50(model, ui, directory = false) {
        const basis = ["arr", "billed", "renewal"].includes(ui.sellerRank) ? ui.sellerRank : "arr";
        const ranked = directory ? allAccounts(model, ui.query || "") : rankAccounts(model, basis, ui.query || "");
        const cols = [
            { key: "name", label: "Account", value: a => a.name, cell: a => `${directory ? "" : `<span class="rank">${a.rank}</span>`}${accountLink(a)}` },
            { key: "arr", label: "ARR · USD", numeric: true, value: a => a.arr, cell: a => compactMoney(a.arr) },
            { key: "billed", label: "Billed LTM · USD", numeric: true, value: a => billedLtm(a, model.date).value, cell: a =>
                `${compactMoney(billedLtm(a, model.date).value, "Billed")}<span class="row-trend">${trend(billedSeries(a, model.date), "13 closed months [Billed] USD")}</span>` },
            { key: "renewal", label: "Renewal", numeric: true, value: a => a.renewing_in_days, cell: a => `${date(a.renewal)}<span class="sub">${a.renewing_in_days == null ? "Unknown date" : `${integer(a.renewing_in_days)} days`}</span>` },
            { key: "health", label: "Copilot health", value: a => a.products?.ghcp?.health?.category, cell: health },
            { key: "contacts", label: "Contacts", numeric: true, value: contactCount, cell: a => `${integer(contactCount(a))}<span class="sub">${esc(states[feedOf(a, "contacts").state] || "Uncollected")}</span>` },
            { key: "email", label: "Last engagement", value: a => [activitySummary(a).lastEmail, activitySummary(a).lastMeeting].filter(Boolean).sort().at(-1),
                cell: a => `${date([activitySummary(a).lastEmail, activitySummary(a).lastMeeting].filter(Boolean).sort().at(-1))}<span class="sub">Email / Gong</span>` },
        ];
        if (ui.sellerOptions?.issuesColumn) cols.push({ key: "issues", label: "Open SE", numeric: true, value: openSeCount, cell: a => integer(openSeCount(a)) });
        if (ui.sellerOptions?.stackColumn) cols.push({ key: "stack", label: "Stack evidence", value: a => stackGroups(a).length, cell: a => `${stackGroups(a).length} tools with evidence` });
        if (ui.sellerOptions?.productsColumn) cols.push({ key: "products", label: "Product footprint", value: a => a.name, cell: a => text(productRows(a, model.date).filter(p => ["Owned", "Metered"].includes(p.status)).map(p => p.name).join(", ")) });
        return header(directory ? "All accounts" : "Top 50 accounts", directory ? "Your complete owned book. Search any account, then open its briefing." : "Your ranked shortlist. Scan the book, then open an account briefing.")
            + `<section class="panel seller-panel compact-accounts" aria-labelledby="h-top50"><div class="panel-head"><h2 id="h-top50">${directory ? "All accounts" : "My top 50"} <span class="count-text">${ranked.length}</span></h2>`
            + `<label class="search">Search <input type="search" data-search data-focus-key="seller-search" value="${esc(ui.query || "")}" maxlength="80"></label></div>`
            + `<div class="workspace-toolbar">${directory ? '<a href="#/top50">View ranked Top 50</a>' : `<div class="seller-ranks" role="group" aria-label="Rank basis">${[["arr", "Rank by ARR"], ["billed", "Rank by billed LTM"], ["renewal", "Rank by renewal"]]
                .map(([key, label]) => `<button class="button tiny${key === basis ? " selected" : ""}" data-filter="seller-rank" data-value="${key}" data-focus-key="seller-rank:${key}" aria-pressed="${key === basis}">${label}</button>`).join("")}</div>`}`
            + `<details class="column-picker"><summary>Optional columns</summary>${[["issuesColumn", "Open SE issues"], ["stackColumn", "Stack availability"], ["productsColumn", "Product footprint"]].map(([key, label]) =>
                `<label><input type="checkbox" data-seller-column="${key}" data-focus-key="${key}"${ui.sellerOptions?.[key] ? " checked" : ""}>${label}</label>`).join("")}</details></div>`
            + `<p class="note">${directory ? "All owned accounts are searchable." : "Ranking selects the 50 before search; column sorting changes their order, not membership."} Missing values stay unknown. Billing history: closed months.</p>`
            + `<div data-region="top50">${table(ui, { id: directory ? "directory" : "top50", columns: cols, rows: ranked, defaultSort: basis, defaultDir: basis === "renewal" ? 1 : -1, empty: "No accounts match this list." })}</div></section>`;
    }
    function renewalTable(model, horizon = 120, selected = null) {
        const accounts = (selected || allAccounts(model).filter(a => a.renewing_in_days != null && a.renewing_in_days >= 0 && a.renewing_in_days <= horizon))
            .sort((a, b) => a.renewing_in_days - b.renewing_in_days);
        return simpleTable(["Account", "Renewal", "Days", "Expected renewal ARR", "Copilot health", "30d seat-usage change", "Open Gainsight risks", "Support escalations"],
            accounts.map(a => [accountLink(a), date(a.renewal), integer(a.renewing_in_days), money(a.open_renewal_arr), text(a.products?.ghcp?.health?.category),
                integer(usageTrend(a)?.delta), integer(observedCount(a, "risks", openRows(a, "risks"))),
                integer(observedCount(a, "support", openRows(a, "support").filter(r => ["urgent", "high", "p1", "p2"].includes(String(r.priority).toLowerCase()))))]));
    }
    function radar(model, ui) {
        const buckets = renewalBuckets(model);
        const selected = buckets.find(b => b.key === ui.sellerOptions?.renewalBucket) || buckets[0];
        return panel("Renewal radar", `<div class="renewal-buckets">${buckets.map(b =>
            `<div><strong>${b.accounts.length}</strong><span>${esc(b.label)}</span></div>`).join("")}</div>`
            + select(ui, "renewalBucket", "Inspect bucket", buckets.map(b => [b.key, b.label]))
            + simpleTable(["Account", "Renewal", "Expected renewal ARR · USD"], selected.accounts.map(a =>
                [accountLink(a), date(a.renewal), compactMoney(a.open_renewal_arr)]), "No accounts in this bucket.")
            + '<p class="note">Disjoint date buckets. Unknown and overdue dates are separate; expected renewal ARR is not invoiced revenue.</p>');
    }
    function overview(model, ui = {}) {
        const accounts = model.accounts.filter(a => a.in_book !== false);
        const billed = knownSum(accounts.map(a => billedLtm(a, model.date).value));
        const renewing = accounts.filter(a => a.renewing_in_days != null && a.renewing_in_days >= 0 && a.renewing_in_days <= 90);
        const reporting = accounts.filter(a => billedLtm(a, model.date).value != null);
        const incomplete = reporting.filter(a => billedLtm(a, model.date).months !== 12).length;
        const stats = [
            ["Book ARR · USD", compactMoney(knownSum(accounts.map(a => a.arr))), `${accounts.filter(a => a.arr != null).length}/${accounts.length} accounts reporting`],
            ["Observed billed LTM · USD", compactMoney(billed, "Billed"), `${reporting.length}/${accounts.length} accounts · ${incomplete} partial / aggregate histories`],
            ["Open pipeline net ARR · USD", compactMoney(knownSum(accounts.map(a => a.open_pipeline_net_arr))), "Expected recurring revenue, not invoices"],
            ["Renewals within 90 days", integer(accounts.some(a => a.renewing_in_days != null) ? renewing.length : null), `${renewing.filter(renewalRisk).length} with known risk signals · known dates only`],
        ];
        const mix = new Map();
        for (const a of accounts) for (const p of productRows(a, model.date)) {
            const rows = mix.get(p.key) || []; rows.push(p); mix.set(p.key, rows);
        }
        const measure = ui.sellerOptions?.contribution === "arr" ? "arr" : "billed";
        const contributions = [...mix].map(([key, rows]) => ({ name: labelOf(key), value: knownSum(rows.map(r => r[measure])) }));
        const aiu = ui.copilotLicenses?.aiuTrend;
        return header("Your book", `${accounts.length} owned accounts · Snapshot ${model.date}`)
            + `<section class="product-cards overview-products" aria-label="Product overview">${["ghe", "ghas", "ghcp"].map(family => productCard(model, family)).join("")}`
            + `<section class="product-trend"><div class="card-head"><h2>Copilot AIU trend</h2><span class="muted">13 weeks</span></div>`
            + (aiu ? lineChart(aiu, "Weekly AI units consumed", "AI units", "Week ending")
                : `<p class="empty">${ui.copilotLicensesLoading ? "Loading AI unit history…" : "Weekly AI unit history is unavailable for this snapshot."}</p>`)
            + (aiu ? `<p class="note">Latest week: ${integer(aiu.coverage.at(-1))}/${integer(aiu.total)} owned accounts reporting.</p>` : "") + "</section></section>"
            + metrics(stats)
            + `<div class="workspace-columns">${panel("Product contribution", select(ui, "contribution", "Measure", [["billed", "Observed billed LTM"], ["arr", "Contract ARR"]])
                + bars(contributions, measure === "arr" ? "Contract ARR by product" : "Observed billed LTM by product", measure === "arr" ? "ARR USD" : "Billed USD")
                + '<p class="note">Observed product subtotals, not a share of book total. Unallocated family ARR is never repeated across products.</p>')}`
            + `<div class="attention-column">${actionsPanel(model, ui, { key: "overview", limit: 8 })}`
            + newSignalsPanel(model, ui)
            + '<div class="queue-links"><a href="#/signals">All signals</a><a href="#/recommendations">Recommendations</a></div></div></div>'
            + `<div class="workspace-columns">${panel("Consumption movement", lineChart(portfolioSeries(model), "Closed-month invoiced history")
                + '<p class="note">Reporting cohorts change. Unmatched total movement is not growth. See matched comparisons in Consumption.</p>'
                + '<a href="#/consumption">Explore consumption</a>', bookSources(model, ["invoices"]))}${radar(model, ui)}</div>`
            + changesPanel(model, ui, { key: "overview", limit: 8 })
            + `<div class="workspace-toolbar"><a class="button" href="#/account360">View all accounts</a><a href="#/data">Coverage & freshness</a></div>`
            + disclosure("Metric definitions & source dates", '<p>ARR, invoiced billing, pipeline and usage forecasts are different measures and are never added together. LTM includes observed months, not imputed zeros.</p>' + readySources(model) + bookSources(model, ["invoices"]));
    }
    const billingModes = [["metered", "Metered"], ["volume", "Volume"], ["both", "Both"]];
    const notAvailable = '<span class="none" title="No volume billing source exists for this product">Not available</span>';
    // GHE volume evidence (license MRR, contract model) is family-level and may cover GHE Server, so qualify it wherever the count shows.
    const VOLUME_QUALIFIER = { ghec: "GHE licensing (family-level)" };
    const modeCount = (p, key) => p.modes[key] === null ? notAvailable : integer(p.modes[key].length);
    const qualifiedCount = (p, key) => modeCount(p, key)
        + (key === "volume" && p.modes[key] !== null && VOLUME_QUALIFIER[p.product] ? ` <span class="qualifier">${esc(VOLUME_QUALIFIER[p.product])}</span>` : "");
    const modeTitle = (p, key, label) => key === "volume" && VOLUME_QUALIFIER[p.product] ? `${label} · ${VOLUME_QUALIFIER[p.product]}` : label;
    const billingRules = "<p><strong>Metered:</strong> a positive consumption invoice for the product in the last closed month on any billing channel (for example Zuora or Azure), positive metered MRR at the exact SKU grain, or positive GHE metered users under a metered or mixed GHE contract model.</p>"
        + "<p><strong>Volume:</strong> positive license MRR at the exact SKU grain (GHE, the GHAS bundle, Code Security, Secret Protection), a volume or mixed GHE contract model, or a Volume GHAS licensing model with positive GHAS seats or ARR. GHE evidence is family-level GHE licensing. GHAS bundle evidence is never allocated to Code Security or Secret Protection.</p>"
        + "<p>Copilot, Copilot AI credits, Code Quality, Actions, storage, Packages, Git LFS, Codespaces and other consumption products have no volume source, so Volume and Both show Not available rather than 0. Accounts with Copilot MRR but no Copilot invoice in 13 months (an invoice feed collected with rows or with no rows) are listed separately and are not counted as volume. Stale, partial, failed or missing invoice feeds never qualify.</p>"
        + "<p>Each source keeps its own date. Recurring revenue is the latest MRR month and licensing models are account snapshots; neither is a closed-month figure. Both counts once. Missing, zero, negative and credit-only evidence never establish a paying customer, and stale or partial feeds are excluded. One account can buy several products.</p>";
    function evidenceCaption(basis) {
        const unique = [...new Map(basis.map(b => [b.source, b])).values()];
        return `<ul class="billing-basis" aria-label="Evidence dates">${unique.map(b => `<li>${esc(b.label)} <strong>${esc(b.date ?? "not reported")}</strong>${b.date ? ` <span class="sub">${esc(b.note)}</span>` : ""}</li>`).join("")}</ul>`;
    }
    function productHistory(model, ui, products) {
        const scope = ui.view;
        const selection = ui.sellerOptions?.[`${scope}Product`];
        const selected = products.find(p => p.product === selection) ?? (scope === "consumption" ? null : products[0]);
        const buttons = (label, items) => items.length ? `<div class="product-picker"><strong>${esc(label)}</strong><div role="group" aria-label="${esc(label)}" class="segment-control">${items.map(([key, name]) =>
            `<button type="button" data-filter="billing-product" data-value="${esc(key)}" data-focus-key="billing:${esc(key)}" aria-pressed="${key === (selected?.product ?? "all")}">${esc(name)}</button>`).join("")}</div></div>` : "";
        const group = security => products.filter(p => GHAS_BILLING_PRODUCTS.includes(p.product) === security).map(p => [p.product, p.name]);
        const picker = buttons("Products", [...(scope === "consumption" ? [["all", "All products"]] : []), ...group(false)]) + buttons("GHAS", group(true));
        const name = selected?.name ?? "All products";
        const observed = p => billingModes.reduce((n, [key]) => n + (p.modes[key]?.length ?? 0), 0) + (p.mrrOnly?.length ?? 0);
        const modeLabel = Object.fromEntries([...billingModes, ["mrr_only", "Copilot MRR, no invoice in 13 months"]]);
        const evidence = row => row.evidence.map(e => `<span class="evidence-item">${esc(e.label)} <span class="sub">${esc(e.date ?? "undated")}</span></span>`).join("");
        const detail = selected && observed(selected)
            ? `<details class="billing-evidence"><summary>View customers and evidence</summary>${simpleTable(["Billing model", "Account", "Evidence"], selected.details.map(row =>
                [esc(modeTitle(selected, row.mode, modeLabel[row.mode])), accountLink(row.account), evidence(row)]))}</details>` : "";
        const copilot = products.find(p => p.mrrOnly);
        const mrrOnlyNote = !selected && copilot?.mrrOnly.length
            ? `<p class="note">Copilot: ${integer(copilot.mrrOnly.length)} accounts have Copilot MRR but no Copilot invoice in 13 months. Not counted as volume.</p>` : "";
        const split = !selected ? simpleTable(["Product", ...billingModes.map(([, label]) => label)], products.map(p =>
            [text(p.name), ...billingModes.map(([key]) => observed(p) || p.modes[key] === null ? qualifiedCount(p, key) : noData)])) + mrrOnlyNote
            : observed(selected) ? `<dl class="billing-counts">${billingModes.map(([key, label]) =>
                `<div><dt>${esc(modeTitle(selected, key, label))}</dt><dd>${modeCount(selected, key)}</dd></div>`).join("")}${selected.mrrOnly
                ? `<div class="billing-other"><dt>Copilot MRR, no Copilot invoice in 13 months</dt><dd>${integer(selected.mrrOnly.length)}</dd></div>` : ""}</dl>`
                + (VOLUME_QUALIFIER[selected.product] && selected.modes.volume?.length
                    ? '<p class="note billing-qualifier">Volume rests on family-level GHE licensing (license MRR or contract model), which may include GHE Server. It is not confirmed as GHE Cloud.</p>' : "")
                : `<p class="empty">No positive billing-model evidence for ${esc(name)}.</p>`;
        const basis = selected ? selected.basis : products.flatMap(p => p.basis);
        return { selected: selected?.product ?? null, html: `<section class="panel seller-panel product-billing"><div class="panel-head"><h2>Product consumption</h2></div>${picker}`
            + `<div class="workspace-columns"><div><h3>${esc(name)} metered consumption billed</h3>${lineChart(selected?.series ?? portfolioSeries(model), "13 closed months")}`
            + '<p class="note">Consumption invoices only. Volume license billing is not in this feed, so this is not total product billing.</p></div>'
            + `<div><h3>Metered vs. volume customers</h3>${evidenceCaption(basis)}<p class="note">Unique owned accounts per product</p>${split}${detail}`
            + `<details class="billing-rules"><summary>Billing sources & counting rules</summary>${billingRules}${bookSources(model, ["invoices", "mrr"])}</details></div></div></section>` };
    }
    function familyHistory(model, ui) {
        const keys = ui.view === "ghas" ? GHAS_BILLING_PRODUCTS : ui.view === "ghe" ? ["ghec"] : ["copilot", "copilot aiu", "copilot_enterprise"];
        return productHistory(model, ui, bookConsumption(model, keys)).html;
    }
    function consumption(model, ui = {}) {
        const products = bookConsumption(model);
        const { selected, html } = productHistory(model, ui, products);
        const book = allAccounts(model);
        const movers = book.map(a => ({ a, delta: monthlyChange(billedSeries(a, model.date, selected)) }))
            .filter(r => r.delta).sort((a, b) => b.delta.delta - a.delta.delta);
        const units = book.flatMap(a => currentRows(a, "invoices").filter(r => !selected || invoiceProduct(r.product) === selected).map(r => ({ ...r, account: a })));
        return header("Consumption", "Closed-month invoiced USD only. Missing account-months stay unknown. MoM and 3-month changes use matched reporting accounts, not unmatched totals.")
            + html + panel("Billed by product", simpleTable(["Product", "Last complete month", "MoM (matched)", "3-month change (matched)", "13-month trend", "Reporting accounts"],
                products.filter(p => !selected || p.product === selected).map(p => [text(p.name), money(p.latest, "Billed"), change(p.mom), change(p.quarter),
                    trend(p.series, `${p.name} [Billed] USD`), `${p.reporting}/${book.length}`])),
            '<p class="seller-source">New internal feed · rev_source consumption invoices. No projected categories or synthetic zero rows.</p>' + bookSources(model, ["invoices"]))
            + panel("Top growers / decliners", simpleTable(["Account", "MoM billed change"], [
                ...movers.filter(r => r.delta.delta > 0).slice(0, 5),
                ...movers.filter(r => r.delta.delta < 0).slice(-5).reverse(),
            ].map(r => [accountLink(r.a), change(r.delta)])), bookSources(model, ["invoices"]))
            + disclosure("Billed quantity by unit (never combined)", simpleTable(["Account", "Month", "Product", "UOM", "Billed units", "Billed USD"], units.map(r =>
                [accountLink(r.account), date(r.date), text(labelOf(r.product)), text(r.uom), integer(r.units), money(r.amount, "Billed")])) + bookSources(model, ["invoices"]))
            + (!selected || selected === "actions" ? disclosure("Actions latest usage", simpleTable(["Account", "Date", "Hosted minutes / 28d", "Self-hosted minutes / 28d", "Jobs / 28d", "Active days / 28d"],
                book.flatMap(a => latestRows(a, "actions").map(r => [accountLink(a), date(r.date), integer(r.hosted_minutes),
                    integer(r.self_hosted_minutes), integer(r.jobs), integer(r.active_days)]))), '<p class="seller-source">New internal feed · c360.fact_actions_metrics (experimental). Each account links to runner/job history.</p>') : "");
    }
    function pipeline(model, ui = {}) {
        const months = new Map();
        for (const a of model.accounts) if (a.renewal) {
            const month = a.renewal.slice(0, 7);
            const values = months.get(month) || []; values.push(a); months.set(month, values);
        }
        const opportunities = model.accounts.flatMap(a => currentRows(a, "opportunities").map(r => ({ ...r, account: a })));
        const opportunityColumns = [{ key: "account", label: "Account", value: r => r.account.name, cell: r => accountLink(r.account) },
            ...[["title", "Opportunity"], ["date", "Close"], ["status", "Stage"], ["type", "Type"]].map(([key, label]) =>
                ({ key, label, value: r => r[key], cell: r => key === "date" ? date(r[key]) : text(r[key]) })),
            ...[["expected_arr", "Expected ARR"], ["new_arr", "New ARR"], ["upsell_arr", "Upsell ARR"], ["contraction_arr", "Contraction ARR"], ["churn_arr", "Churn ARR"]]
                .map(([key, label]) => ({ key, label, numeric: true, value: r => r[key], cell: r => money(r[key]) }))];
        return header("Pipeline & renewals", "Expected license ARR is pipeline, not billed revenue. Revenue retention is distinct from Copilot MAU retention.")
            + radar(model, ui)
            + disclosure("Renewals by calendar month", simpleTable(["Month", "Accounts", "Known expected renewal ARR", "Known at-risk accounts"], [...months].sort(([a], [b]) => a.localeCompare(b))
                .map(([month, accs]) => [text(month), integer(accs.length), money(knownSum(accs.map(a => a.open_renewal_arr))), integer(accs.filter(renewalRisk).length)])),
            `<p class="seller-source">Ready now · p06 account renewal dates · ${date(model.date)}</p>`)
            + panel("Renewals with health, usage and risk", renewalTable(model), readySources(model) + bookSources(model, ["risks", "support"]))
            + panel("License opportunities", table(ui, { id: "opportunities", columns: opportunityColumns, rows: opportunities, defaultSort: "date", defaultDir: 1, empty: "No current license opportunities." }),
            '<p class="seller-source">New internal feed · rev_source license opportunity fact + opportunity_dim. Current stages, not historical stage snapshots.</p>' + bookSources(model, ["opportunities"]))
            + panel("Revenue retention", simpleTable(["Account", "Period end", "ARR type", "Segment", "NDRR", "GDRR", "Source date"],
                model.accounts.flatMap(a => currentRows(a, "retention").map(r => [accountLink(a), date(r.date), text(r.arr_type), text(r.arr_segment),
                    percent(r.ndrr), percent(r.gdrr), date(r.source_date)]))), '<p class="seller-source">c360.fact_arr_retention_metrics (experimental). Ratios are not summed or averaged across accounts.</p>')
            + panel("Enterprise budgets", simpleTable(["Account", "Scope", "Target", "Spend", "Currency"], model.accounts.flatMap(a => currentRows(a, "budgets")
                .map(r => [accountLink(a), text(r.scope), budgetMoney(r.target, "Budget", r.currency),
                    budgetMoney(r.spend, "Usage est.", r.currency), text(r.currency)]))),
            '<p class="seller-source">Revenue MCP get_budgets. Missing enterprise mapping or seller access remains No data. Per-user budgets are excluded; currency is not inferred.</p>' + bookSources(model, ["budgets"]));
    }
    function coverage(model, ui = {}) {
        const entries = allAccounts(model, ui.query || "").flatMap(a => Object.keys(FEED_LABELS).map(key => ({ a, key, f: feedOf(a, key) })));
        const filtered = entries.filter(({ key, f }) => (!ui.sellerOptions?.coverageSource || ui.sellerOptions.coverageSource === "all" || key === ui.sellerOptions.coverageSource)
            && (!ui.sellerOptions?.coverageState || ui.sellerOptions.coverageState === "all" || f.state === ui.sellerOptions.coverageState));
        return header("Coverage & freshness", "Collection freshness is separate from the date of the source evidence.")
            + metrics([["Account / source pairs", integer(entries.length), "Internal sources only"],
                ["Current rows", integer(entries.filter(r => r.f.state === "ok").length), "Not proof of complete discovery"],
                ["Partial or stale", integer(entries.filter(r => ["partial", "stale"].includes(r.f.state)).length), "Retained evidence remains inspectable"],
                ["Failed reads", integer(entries.filter(r => r.f.state === "failed").length), "Unknown, not empty"]])
            + panel("Account / source matrix", `<div class="workspace-toolbar"><label class="workspace-search">Account<input type="search" data-search data-focus-key="coverage-search" value="${esc(ui.query || "")}"></label>`
                + select(ui, "coverageSource", "Source", [["all", "All sources"], ...Object.entries(FEED_LABELS)])
                + select(ui, "coverageState", "State", [["all", "All states"], ...Object.entries(states)]) + "</div>"
                + simpleTable(["Account", "Source", "State", "Dates & details"], filtered.map(({ a, key, f }) =>
                    [accountLink(a), esc(FEED_LABELS[key]), esc(states[f.state] || "No data"), `<details><summary>Data ${date(f.as_of)} · Collected ${date(f.collected_at)}</summary>${source(a, key)}</details>`]))
                + '<p class="note">No rows does not mean zero. Failed or stale sources do not contribute to current rollups. Gainsight gaps may reflect parent/sibling attribution.</p>');
    }
    function productExtras(model, family) {
        return panel(family === "ghe" ? "GHE whitespace & deployment" : "Security billing & whitespace", simpleTable(
            family === "ghe" ? ["Account", "Potential seats (parent)", "Annual SOM (parent)", "Deployment", "Source date"]
                : ["Account", "Code Security last month", "Secret Protection last month", "Potential seats (parent)", "Annual SOM (parent)", "Source date"],
            model.accounts.map(a => {
                const r = a.seller?.ready || {};
                return family === "ghe" ? [accountLink(a), integer(r.ghe_potential_seats), money(r.ghe_som_annual, "Model est."), text(r.deployment), date(r.q17_as_of)]
                    : [accountLink(a), money(a.billing_lcm?.ghcs, "Billed"), money(a.billing_lcm?.ghsp, "Billed"),
                        integer(r.ghas_potential_seats), money(r.ghas_som_annual, "Model est."), date(r.q17_as_of)];
            })), '<p class="seller-source">Ready now · q17 parent TAM/SOM and p06 billing split. Parent estimates must not be summed across children. Open Account 360 for protection coverage, Team upgrade signals and product history.</p>');
    }
    function copilotExtras(model) {
        return panel("Copilot AI credits billed", simpleTable(["Account", "Last complete month", "Last 12 months", "Source date"], model.accounts.map(a => {
            const r = a.seller?.ready || {};
            return [accountLink(a), money(r.copilot_aiu_billed_lcm, "Billed"), money(r.copilot_aiu_billed_ltm, "Billed"), date(r.q02_as_of)];
        })), '<p class="seller-source">Ready now · Copilot dashboard q02. AI credits are kept separate from license billing.</p>')
            + panel("Closed-month model costs", simpleTable(["Account", "Month", "Enterprise", "Model", "Integration", "Usage-side net USD", "Distinct actors (slice only)"], model.accounts
                .flatMap(a => currentRows(a, "model_cost").map(r => [accountLink(a), date(r.date), text(r.enterprise_id), text(r.model), text(r.integration),
                    money(r.amount, "Usage est."), integer(r.actors)]))),
            '<p class="seller-source">New internal feed · enterprise_account_monthly_ubb_model_cost_fact. Usage-side net is not final invoice billing. Distinct actors are never added across model, integration or enterprise slices.</p>');
    }
    return { account360, top50, overview, consumption, familyHistory, pipeline, coverage, productExtras, copilotExtras };
}
