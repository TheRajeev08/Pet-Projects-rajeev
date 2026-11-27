import {
    ACCOUNT_TABS, FEED_LABELS, activitySummary, billedLtm, billedSeries, bookConsumption, bookFeedCoverage, currentRows, dayOf,
    feedOf, knownSum, labelOf, latestRows, moneyTypes, monthlyChange, observedCount, openRows, productRows,
    rankAccounts, renewalRisk, rowsOf, usageTrend,
} from "./seller-data.mjs";

export function createSellerViews({ esc, fmtInt, fmtMoney, fmtPct, fmtDay, sparkline, table, actionsFor, renderRecommendations }) {
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
    const accountLink = a => `<a class="link" href="#/account360/summary/account/${encodeURIComponent(a.id)}">${esc(a.name || a.id)}</a>`;
    const header = (title, note) => `<section class="page-head"><div><h1>${esc(title)}</h1><p class="lede">${esc(note)}</p></div></section>`;
    const states = { ok: "Has data", no_rows: "No rows (not zero)", missing: "No data", stale: "Stale",
        not_connected: "Not connected", not_authorized: "Not authorized", failed: "Collection failed", partial: "Partial" };
    function source(account, feed) {
        const info = feedOf(account, feed);
        return `<p class="seller-source"><span class="chip">New internal feed</span> ${esc(info.source || FEED_LABELS[feed])}`
            + ` · Data date: ${date(info.as_of)} · Collected: ${date(info.collected_at)} · <strong>${esc(states[info.state] || "No data")}</strong>`
            + `${info.reason ? `<br>${esc(info.reason)}` : ""}</p>`;
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
            + rows.map(row => `<tr>${row.map((value, i) => i === 0 ? `<th scope="row">${value}</th>` : `<td>${value}</td>`).join("")}</tr>`).join("")
            + "</tbody></table></div>";
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
            ["Product", "Status", "Seats", "Used %", "ARR", "Billed LTM", "Billed last month (security split)", "30-day view", "90-day view", "Data date"],
            productRows(a, model.date).map(p => [text(p.name), text(p.status), integer(p.seats), percent(p.utilization), money(p.arr),
                `${money(p.billed, "Billed")}<span class="sub">${p.billingSource === "p06" ? "p06 collected LTM aggregate" : `${p.months}/12 months with invoice rows`}</span>`, money(p.lcm, "Billed"),
                change(monthlyChange(p.trend, 1)), `${trend(p.trend, `${p.name} closed-month billing`)}${change(monthlyChange(p.trend, 3))}`, date(p.as_of)])),
            `<p class="seller-source">Ready now: p02 contracts / p04 seats / p06 security split. Account data: ${date(a.as_of)}. `
            + "New internal feed: invoices. 30/90-day views compare closed calendar months (1/3 months), not rolling daily spend. Missing product rows do not prove whitespace. GHE Cloud usage is not inferred from account-wide GHE usage.</p>" + source(a, "invoices"));
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
        return panel("Why talk now & next-best actions", simpleTable(["Priority", "Action", "Evidence"], rows, "No evidence-backed action in the current snapshot."),
            `<p class="seller-source">Pulse rules and current internal risk/support records · ${date(model.date)}</p>`);
    }
    function consumptionDetails(a, model) {
        const products = productRows(a, model.date).filter(p => p.months > 0);
        const history = panel("13-month billed history by product", products.length ? products.map(p =>
            `<details><summary>${esc(p.name)} ${trend(p.trend, `${p.name} [Billed] USD`)}</summary>${seriesTable(p.trend)}</details>`).join("") : '<p class="empty">No data</p>',
        source(a, "invoices"));
        const units = recordPanel(a, "invoices", [["Month", "date", date], ["Product", "product", v => text(labelOf(v))],
            ["Billing source", "billing_source"], ["Amount USD", "amount", v => money(v, "Billed")],
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
        return recordPanel(a, "protection", [["Date", "date", date], ["Private repositories", "private_repos", integer], ["Code protected repos", "code_repos", integer],
            ["Secret protected repos", "secret_repos", integer], ["All commits", "commits", integer], ["Code protected commits", "code_commits", integer],
            ["Secret protected commits", "secret_commits", integer]])
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
    function activity(a, model) {
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
        const issueRows = currentRows(a, "issues");
        const issueTrend = months.map(month => [text(month), integer(issueRows.length ? issueRows.filter(r => String(r.date || "").startsWith(month)).length : null)]);
        const emailTrend = months.map(month => [text(month), integer(emails.length ? emails.filter(r => String(r.date || "").startsWith(month)).length : null)]);
        return panel("Email recency & volume", `<p>Last email: ${date(summary.lastEmail)} · Observed threads: ${integer(summary.threads)}</p>`
            + simpleTable(["Month", "Observed messages"], emailTrend), source(a, "emails"))
            + panel("Email threads & correspondents", simpleTable(["Thread", "Messages", "Latest", "Correspondents"], [...threads.values()].map(t =>
                [link(t.url, t.title), integer(t.count), date(t.dates.sort().at(-1)), text([...t.people].join(", "))])), source(a, "emails"))
            + panel("People we have spoken to (points of contact)", simpleTable(["Contact", "Role", "Email", "Evidence sources", "Last contact"],
                summary.spoken.map(p => [text(p.name), text(p.role), text(p.email), text(p.sources.join(", ")), date(p.last)])),
            source(a, "contacts") + "<p>Only exact email matches to Salesforce customer contacts count here. The contact list below alone does not prove a conversation.</p>")
            + recordPanel(a, "contacts", [["Known contact", "name"], ["Role", "role"], ["Email", "email"], ["Last CRM activity", "last_activity", date]])
            + panel("SE issues over time", simpleTable(["Created month", "Observed issues"], issueTrend)
                + "<p>Creation dates and current statuses, not reconstructed historical status transitions.</p>", source(a, "issues"))
            + recordPanel(a, "issues", recordColumns)
            + recordPanel(a, "pocs", [["Proof of concept", "title", (v, r) => link(r.url, v)], ["End date", "date", date],
                ["Status", "status"], ["Details", "description"]])
            + recordPanel(a, "meetings", [["Meeting", "title", (v, r) => link(r.url, v)], ["Date", "date", date],
                ["Attendees", "people", v => text((v || []).map(p => p.email || p.name).filter(Boolean).join(", "))]])
            + recordPanel(a, "gong", [["Call", "title", (v, r) => link(r.url, v)], ["Date", "date", date],
                ["Participants", "participants", v => text((v || []).map(p => p.name || p.email).filter(Boolean).join(", "))]])
            + recordPanel(a, "timeline", [["Activity", "title"], ["Date", "date", date], ["Type", "type"], ["Attendees (source text)", "attendees"],
                ["Products discussed (not proof of stack)", "products"], ["Notes", "notes"]])
            + recordPanel(a, "notes", recordColumns) + recordPanel(a, "partners", recordColumns)
            + panel("Gong & web signals", simpleTable(["Signal", "Source", "Date"], (model.signals || []).filter(s => s.account_id === a.id && ["gong", "web"].includes(s.source))
                .map(s => [link(s.url, s.title), text(s.source), date(s.date)])), `<p class="seller-source">Evidence-backed signals · ${date(model.date)}</p>`)
            + recordPanel(a, "tech_stack", [["Category", "category"], ["Recorded existing tool", "value"], ["Evidence field", "field"], ["PoC end date", "date", date]]);
    }
    function account360(model, ui) {
        const a = model.accounts.find(a => a.id === ui.account);
        if (!a) return header("Account 360", "Choose an account from Top 50 or an account preview.") + top50(model, ui);
        const tab = ACCOUNT_TABS.includes(ui.subview) ? ui.subview : "summary";
        const names = ["Summary", "Commercial", "Products & consumption", "Security", "Success & support", "Activity"];
        const nav = `<nav class="copilot-subnav" aria-label="Account 360 sections">${ACCOUNT_TABS.map((key, i) =>
            `<a href="#/account360/${key}/account/${encodeURIComponent(a.id)}"${tab === key ? ' aria-current="page"' : ""}>${names[i]}</a>`).join("")}</nav>`;
        const content = tab === "commercial" ? commercial(a, model) : tab === "products" ? productTable(a, model) + consumptionDetails(a, model)
            : tab === "security" ? security(a) : tab === "success" ? success(a) : tab === "activity" ? activity(a, model)
                : productTable(a, model) + whyNow(a, model) + readyPanel(a)
                    + renderRecommendations(model.recommendations, { accountId: a.id, now: ui.now });
        return header(a.name || a.id, "Account 360 · Internal sources only · Read-only seller intelligence")
            + `<p>${money(a.arr)} · Renewal: ${date(a.renewal)} · Data date: ${date(a.as_of)}</p>` + nav + content;
    }
    function top50(model, ui) {
        const basis = ["arr", "billed", "renewal"].includes(ui.sellerRank) ? ui.sellerRank : "arr";
        const ranked = rankAccounts(model, basis, ui.query || "");
        const cols = [
            { key: "name", label: "Account", value: a => a.name, cell: a => `${accountLink(a)}<span class="sub">Rank ${a.rank}</span>` },
            { key: "arr", label: "ARR", numeric: true, value: a => a.arr, cell: a => money(a.arr) },
            { key: "billed", label: "Billed LTM", numeric: true, value: a => billedLtm(a, model.date).value, cell: a => money(billedLtm(a, model.date).value, "Billed") },
            { key: "products", label: "Product ARR / billed / seats / used %", value: a => a.name, cell: a => productRows(a, model.date)
                .filter(p => p.status === "Owned" || p.status === "Metered").map(p => `<span class="sub">${esc(p.name)}: ${money(p.arr)} / ${money(p.billed, "Billed")} / ${integer(p.seats)} seats / ${percent(p.utilization)} used</span>`).join("") || noData },
            { key: "trend", label: "30 / 90-day consumption view", value: a => monthlyChange(billedSeries(a, model.date))?.delta ?? null,
                cell: a => `${trend(billedSeries(a, model.date), "Closed-month [Billed] USD")}<span class="sub">1m ${change(monthlyChange(billedSeries(a, model.date), 1))} / 3m ${change(monthlyChange(billedSeries(a, model.date), 3))}</span>` },
            { key: "renewal", label: "Renewal", numeric: true, value: a => a.renewing_in_days, cell: a => `${date(a.renewal)}<span class="sub">${integer(a.renewing_in_days)} days</span>` },
            { key: "health", label: "Copilot health", value: a => a.products?.ghcp?.health?.category, cell: a => text(a.products?.ghcp?.health?.category) },
            { key: "issues", label: "Open SE issues", numeric: true, value: a => observedCount(a, "issues", openRows(a, "issues")), cell: a => integer(observedCount(a, "issues", openRows(a, "issues"))) },
            { key: "email", label: "Last email / meeting", value: a => activitySummary(a).lastEmail, cell: a => `${date(activitySummary(a).lastEmail)}<span class="sub">${date(activitySummary(a).lastMeeting)}</span>` },
            { key: "pocs", label: "People / proofs of concept", value: a => observedCount(a, "pocs"), cell: a =>
                `${integer(activitySummary(a).spoken.length || null)} people / ${integer(observedCount(a, "pocs"))} PoCs` },
            { key: "stack", label: "Tech stack", value: a => currentRows(a, "tech_stack").map(r => r.value).join(", "), cell: a =>
                `${text(currentRows(a, "tech_stack").map(r => r.value).join(", "))}<span class="sub">PoC evidence only; ZoomInfo not connected</span>` },
            { key: "asof", label: "Source dates", value: a => a.as_of, cell: a => `Account: ${date(a.as_of)}<span class="sub">Invoices: ${date(feedOf(a, "invoices").as_of)} · ${text(states[feedOf(a, "invoices").state])}</span>` },
        ];
        return header("Top 50 accounts", "Top 50 membership uses the selected rank basis before search. Ties: account name, then ID. Missing values sort last; no missing ARR is treated as zero.")
            + `<section class="panel seller-panel" aria-labelledby="h-top50"><div class="panel-head"><h2 id="h-top50">My top 50 <span class="count-text">${ranked.length}</span></h2>`
            + `<label class="search">Search <input type="search" data-search data-focus-key="seller-search" value="${esc(ui.query || "")}" maxlength="80"></label></div>`
            + `<div class="seller-ranks" role="group" aria-label="Rank basis">${[["arr", "ARR (descending)"], ["billed", "Observed Billed LTM (descending)"], ["renewal", "Days to renewal (ascending)"]]
                .map(([key, label]) => `<button class="button tiny${key === basis ? " selected" : ""}" data-filter="seller-rank" data-value="${key}" data-focus-key="seller-rank:${key}" aria-pressed="${key === basis}">${label}</button>`).join("")}</div>`
            + '<p class="seller-source">Ready now: account, contracts, health and renewal. New internal feeds: invoices and activity. Click column headings to sort these 50; use rank basis to change membership. Billing trends use closed-month 1/3-month comparisons.</p>'
            + `<div data-region="top50">${table(ui, { id: "top50", columns: cols, rows: ranked, defaultSort: basis, defaultDir: basis === "renewal" ? 1 : -1, empty: "No accounts match this Top 50." })}</div></section>`;
    }
    function renewalTable(model, horizon = 120) {
        const accounts = model.accounts.filter(a => a.renewing_in_days != null && a.renewing_in_days >= 0 && a.renewing_in_days <= horizon)
            .sort((a, b) => a.renewing_in_days - b.renewing_in_days);
        return simpleTable(["Account", "Renewal", "Days", "Expected renewal ARR", "Copilot health", "30d seat-usage change", "Open Gainsight risks", "Support escalations"],
            accounts.map(a => [accountLink(a), date(a.renewal), integer(a.renewing_in_days), money(a.open_renewal_arr), text(a.products?.ghcp?.health?.category),
                integer(usageTrend(a)?.delta), integer(observedCount(a, "risks", openRows(a, "risks"))),
                integer(observedCount(a, "support", openRows(a, "support").filter(r => ["urgent", "high", "p1", "p2"].includes(String(r.priority).toLowerCase()))))]));
    }
    function overview(model) {
        const accounts = model.accounts.filter(a => a.in_book !== false);
        const billed = knownSum(accounts.map(a => billedLtm(a, model.date).value));
        const renewing = accounts.filter(a => a.renewing_in_days != null && a.renewing_in_days >= 0 && a.renewing_in_days <= 90);
        const forecasts = accounts.map(a => a.seller?.ready?.forecast_bill);
        const stats = [
            ["Book ARR", money(knownSum(accounts.map(a => a.arr)))],
            ["Observed Billed LTM", money(billed, "Billed")],
            ["Copilot month forecast (not billed MTD)", money(knownSum(forecasts), "Usage est.")],
            ["Open pipeline net ARR", money(knownSum(accounts.map(a => a.open_pipeline_net_arr)))],
            ["Renewals within 90 days (known dates)", integer(accounts.some(a => a.renewing_in_days != null) ? renewing.length : null)],
            ["At-risk renewals (known signals)", integer(renewing.filter(renewalRisk).length || null)],
        ];
        const mix = new Map();
        for (const a of accounts) for (const p of productRows(a, model.date)) {
            const rows = mix.get(p.key) || []; rows.push(p); mix.set(p.key, rows);
        }
        return panel("Book money summary", `<dl class="seller-metrics">${stats.map(([name, value]) => `<div><dt>${esc(name)}</dt><dd>${value}</dd></div>`).join("")}</dl>`,
            `<p class="seller-source">Ready now + new internal feeds · Snapshot ${date(model.date)}. Observed subtotals only; check coverage below. ARR, invoiced billing and usage forecasts are never added together.</p>` + readySources(model) + bookSources(model, ["invoices"]))
            + panel("All-product mix", simpleTable(["Product", "Known ARR", "Observed Billed LTM", "3-month billing change", "Explicit whitespace / unknown"],
                [...mix].map(([key, rows]) => [text(labelOf(key)), money(knownSum(rows.map(r => r.arr))), money(knownSum(rows.map(r => r.billed)), "Billed"),
                    change(bookConsumption(model).find(p => p.product === key)?.quarter),
                    `${rows.filter(r => r.status === "Whitespace").length} / ${rows.filter(r => r.status === "No data").length}`])),
            '<p class="seller-source">Contracts: p02. Billed: account consumption invoices. Three-month changes use accounts reporting both months. Product absence is unknown, not automatic whitespace.</p>' + readySources(model) + bookSources(model, ["invoices"]))
            + panel("Renewal radar · 120 days", renewalTable(model), readySources(model) + bookSources(model, ["risks", "support"]));
    }
    function consumption(model) {
        const products = bookConsumption(model);
        const movers = model.accounts.map(a => ({ a, delta: monthlyChange(billedSeries(a, model.date)) }))
            .filter(r => r.delta).sort((a, b) => b.delta.delta - a.delta.delta);
        const units = model.accounts.flatMap(a => currentRows(a, "invoices").map(r => ({ ...r, account: a })));
        return header("Consumption", "Closed-month invoiced USD only. Missing account-months stay unknown. MoM and 3-month changes use matched reporting accounts, not unmatched totals.")
            + panel("Billed by product", simpleTable(["Product", "Last complete month", "MoM (matched)", "3-month change (matched)", "13-month trend", "Reporting accounts"],
                products.map(p => [text(p.name), money(p.latest, "Billed"), change(p.mom), change(p.quarter),
                    trend(p.series, `${p.name} [Billed] USD`), `${p.reporting}/${model.accounts.length}`])),
            '<p class="seller-source">New internal feed · rev_source consumption invoices. No projected categories or synthetic zero rows.</p>' + bookSources(model, ["invoices"]))
            + panel("Top growers / decliners", simpleTable(["Account", "MoM billed change"], [
                ...movers.filter(r => r.delta.delta > 0).slice(0, 5),
                ...movers.filter(r => r.delta.delta < 0).slice(-5).reverse(),
            ].map(r => [accountLink(r.a), change(r.delta)])), bookSources(model, ["invoices"]))
            + panel("Billed quantity by unit (never combined)", simpleTable(["Account", "Month", "Product", "UOM", "Billed units", "Billed USD"], units.map(r =>
                [accountLink(r.account), date(r.date), text(labelOf(r.product)), text(r.uom), integer(r.units), money(r.amount, "Billed")])), bookSources(model, ["invoices"]))
            + panel("Actions latest usage", simpleTable(["Account", "Date", "Hosted minutes / 28d", "Self-hosted minutes / 28d", "Jobs / 28d", "Active days / 28d"],
                model.accounts.flatMap(a => latestRows(a, "actions").map(r => [accountLink(a), date(r.date), integer(r.hosted_minutes),
                    integer(r.self_hosted_minutes), integer(r.jobs), integer(r.active_days)]))), '<p class="seller-source">New internal feed · c360.fact_actions_metrics (experimental). Each account links to runner/job history.</p>');
    }
    function pipeline(model) {
        const months = new Map();
        for (const a of model.accounts) if (a.renewal) {
            const month = a.renewal.slice(0, 7);
            const values = months.get(month) || []; values.push(a); months.set(month, values);
        }
        const opportunities = model.accounts.flatMap(a => currentRows(a, "opportunities").map(r => [accountLink(a), text(r.title), date(r.date),
            text(r.status), text(r.type), money(r.expected_arr), money(r.new_arr), money(r.upsell_arr), money(r.contraction_arr), money(r.churn_arr)]));
        return header("Pipeline & renewals", "Expected license ARR is pipeline, not billed revenue. Revenue retention is distinct from Copilot MAU retention.")
            + panel("Renewals by calendar month", simpleTable(["Month", "Accounts", "Known expected renewal ARR", "Known at-risk accounts"], [...months].sort(([a], [b]) => a.localeCompare(b))
                .map(([month, accs]) => [text(month), integer(accs.length), money(knownSum(accs.map(a => a.open_renewal_arr))), integer(accs.filter(renewalRisk).length)])),
            `<p class="seller-source">Ready now · p06 account renewal dates · ${date(model.date)}</p>`)
            + panel("Renewals with health, usage and risk", renewalTable(model), readySources(model) + bookSources(model, ["risks", "support"]))
            + panel("License opportunities", simpleTable(["Account", "Opportunity", "Close", "Stage", "Type", "Expected ARR", "New ARR", "Upsell ARR", "Contraction ARR", "Churn ARR"], opportunities),
            '<p class="seller-source">New internal feed · rev_source license opportunity fact + opportunity_dim. Current stages, not historical stage snapshots.</p>' + bookSources(model, ["opportunities"]))
            + panel("Revenue retention", simpleTable(["Account", "Period end", "ARR type", "Segment", "NDRR", "GDRR", "Source date"],
                model.accounts.flatMap(a => currentRows(a, "retention").map(r => [accountLink(a), date(r.date), text(r.arr_type), text(r.arr_segment),
                    percent(r.ndrr), percent(r.gdrr), date(r.source_date)]))), '<p class="seller-source">c360.fact_arr_retention_metrics (experimental). Ratios are not summed or averaged across accounts.</p>')
            + panel("Enterprise budgets", simpleTable(["Account", "Scope", "Target", "Spend", "Currency"], model.accounts.flatMap(a => currentRows(a, "budgets")
                .map(r => [accountLink(a), text(r.scope), budgetMoney(r.target, "Budget", r.currency),
                    budgetMoney(r.spend, "Usage est.", r.currency), text(r.currency)]))),
            '<p class="seller-source">Revenue MCP get_budgets. Missing enterprise mapping or seller access remains No data. Per-user budgets are excluded; currency is not inferred.</p>' + bookSources(model, ["budgets"]));
    }
    function coverage(model) {
        return panel("Accounts x sources coverage", simpleTable(["Account", ...Object.values(FEED_LABELS)], model.accounts.map(a =>
            [accountLink(a), ...Object.keys(FEED_LABELS).map(key => {
                const f = feedOf(a, key);
                return `<span title="${esc(f.reason || f.source || FEED_LABELS[key])}">${esc(states[f.state] || "No data")}</span><span class="sub">Data: ${date(f.as_of)}<br>Collected: ${date(f.collected_at)}</span>`;
            })])), '<p>Has data is not proof of complete coverage. No rows does not mean zero. Stale/failed retained rows never contribute to current seller rollups. Account-level Gainsight gaps may reflect CO-covered parent/sibling attribution.</p>');
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
    return { account360, top50, overview, consumption, pipeline, coverage, productExtras, copilotExtras };
}
