import assert from "node:assert/strict";
import { test } from "node:test";
import { ACCOUNT_VIEWS, copilotLicenses, hashFor, parseHash, withActionStates } from "../model.mjs";
import { renderView, renderNav } from "../views.mjs";
import { activitySummary, billedLtm, billedSeries, bookConsumption, currentRows, dedupeSignals, knownSum,
    bookFeedCoverage, monthlyChange, observedCount, openRows, productRows, rankAccounts,
    allAccounts, contactDirectory, contactCount, portfolioSeries, renewalBuckets, stackGroups, openSeCount } from "../seller-data.mjs";
import { syntheticAccount, syntheticModel } from "./helpers.mjs";
import { workspaceCopilotModel, workspaceModel } from "./workspace-fixture.mjs";

const feed = records => ({ state: "ok", as_of: "2026-10-27", collected_at: "2026-10-27", records });
test("preview evidence quotes, dates and links refer to the matching synthetic source", () => {
    for (const account of workspaceModel().accounts) {
        const feeds = account.seller.feeds;
        for (const claim of feeds.tech_stack.records) {
            const sources = claim.source_type === "gong" ? feeds.gong.records : feeds.issues.records;
            const source = sources.find(r => r.id === claim.source_id);
            assert.ok(source);
            assert.equal(claim.source_url, source.url);
            assert.equal(claim.observed_at, source.date);
            assert.equal(claim.evidence_quote, source.description);
        }
    }
});
function fixture() {
    const model = syntheticModel();
    model.accounts[0].seller = { ready: { copilot_aiu_billed_lcm: 8, copilot_aiu_billed_ltm: 20, ghe_som_annual: 300 },
        feeds: {
            invoices: feed([
                { date: "2026-07-01", product: "actions", amount: 1, units: 20, uom: "minute" },
                { date: "2026-09-01", product: "actions", amount: 8, units: 20, uom: "minute" },
                { date: "2026-09-01", product: "actions", amount: 8, units: 2, uom: "job" },
            ]),
            contacts: feed([{ id: "person", name: "Synthetic person", email: "person@example.invalid" }]),
            gong: feed([{ id: "call", date: "2026-10-10", participants: [{ email: "person@example.invalid" }] }]),
            issues: feed([{ id: "issue", title: "<script>unsafe</script>", url: "javascript:alert(1)", status: "closed", date: "2026-10-01" }]),
            model_cost: feed([{ date: "2026-09-01", model: "Model A", actors: 8, amount: 20 },
                { date: "2026-09-01", model: "Model B", actors: 8, amount: 30 }]),
            tech_stack: feed([{ category: "CI", value: "Synthetic CI", field: "existing_ci", date: "2026-10-01" }]),
        } };
    return model;
}

test("Top 50 membership, tie order, search and missing-value ranking are deterministic", () => {
    const model = fixture();
    model.accounts = Array.from({ length: 55 }, (_, i) => syntheticAccount(`id-${i}`, `Synthetic ${String(i).padStart(2, "0")}`, { arr: i < 52 ? i : null }));
    model.accounts.push(syntheticAccount("outside", "Not owned", { arr: 900, in_book: false }));
    const ranked = rankAccounts(model);
    assert.equal(ranked.length, 50);
    assert.equal(ranked[0].arr, 51);
    assert.equal(ranked.at(-1).arr, 2);
    assert.equal(rankAccounts(model, "arr", "Synthetic 00").length, 0);
    model.accounts = [syntheticAccount("c", "Tie", { arr: null }), syntheticAccount("b", "Tie", { arr: 0 }), syntheticAccount("a", "Tie", { arr: 0 })];
    assert.deepEqual(rankAccounts(model).map(a => a.id), ["a", "b", "c"]);
});

test("invoice totals preserve all charges and missing months never turn into zeros", () => {
    const a = fixture().accounts[0];
    const series = billedSeries(a, "2026-10-27");
    assert.equal(series.points.at(-1)[1], 16);
    assert.equal(series.points.at(-2)[1], null);
    assert.equal(billedLtm(a, "2026-10-27").value, 17);
    assert.equal(billedLtm(a, "2026-10-27").months, 2);
    assert.equal(monthlyChange(series), null);
    assert.equal(knownSum([null, undefined, NaN]), null);
    assert.equal(knownSum([0, null]), 0);
    a.seller.feeds.invoices.state = "stale";
    assert.equal(billedLtm(a, "2026-10-27").value, null);
});

test("book comparisons use only the matched cohort, not unmatched totals", () => {
    const model = fixture();
    model.accounts[0].seller.feeds.invoices.records.push({ date: "2026-08-01", product: "actions", amount: 4 });
    model.accounts[1].seller = { feeds: { invoices: feed([{ date: "2026-09-01", product: "actions", amount: 500 }]) } };
    const product = bookConsumption(model).find(p => p.product === "actions");
    assert.equal(product.latest, 516);
    assert.equal(product.mom.delta, 12);
    assert.equal(product.mom.accounts, 1);
});

// Live vocabulary: invoice billing_source is a channel, MRR is current-month only, and models come from contract attributes.
function liveBillingModel() {
    const row = (product, amount, billing_source, date = "2026-09-01") => ({ date, product, amount, billing_source });
    const account = (id, invoices, mrr = null, options = {}) => syntheticAccount(id, id, { ...options,
        seller: { feeds: { invoices: feed(invoices), mrr: feed(mrr ? [{ date: "2026-10-01", ...mrr }] : []) } } });
    const model = { ...fixture(), accounts: [
        account("ghe-volume-mrr", [], { ghe_license_mrr: 500 }),
        account("ghe-volume-contract", [], null, { ghe: { contracted: 50, billing: "volume", as_of: "2026-10-25" } }),
        account("ghe-mixed", [row("GitHub Enterprise Cloud", 100, "Zuora")], { ghe_license_mrr: 200 }),
        account("ghe-metered-users", [], null, { ghe: { contracted: 0, billing: "metered", metered: 12, metered_as_of: "2026-10-26" } }),
        account("ghe-volume-zero-seats", [], null, { ghe: { contracted: 0, billing: "volume", as_of: "2026-10-25" } }),
        account("metered-channels", [row("GitHub Enterprise Cloud", 40, "azure"), row("ghcs", 10, "azure"), row("GitHub Code Security", 8, "Zuora"),
            row("ghsp", 5, "Zuora"), row("Copilot", 30, "azure")]),
        account("ghas-volume", [], null, { as_of: "2026-10-25", ghas: { contracted: 20, licensing_model: "Volume", plan: "Volume - Bundled" } }),
        account("ghas-unbundled", [], { ghcs_metered_mrr: 15, ghas_license_mrr: 0 },
            { as_of: "2026-10-25", ghas: { contracted: 30, licensing_model: "Metered", plan: "Metered - Unbundled" } }),
        account("ghas-volume-no-seats", [], null, { as_of: "2026-10-25", ghas: { licensing_model: "Volume" } }),
        account("copilot-mrr-only", [], { copilot_business_mrr: 400 }),
        account("copilot-invoiced-earlier", [row("Copilot", 20, "Zuora", "2026-03-01")], { copilot_enterprise_mrr: 100 }),
        account("credit", [row("ghcs", -1, "azure"), row("code_quality", 0, "Zuora")], { ghcs_license_mrr: 0, ghe_license_mrr: -5 }),
        account("not-current", [row("ghcs", 30, "Zuora", "2026-10-01"), row("ghcs", 4, "Zuora", "2026-08-01")]),
        account("code-quality", [row("GitHub Code Quality", 7, "Zuora")]),
        account("outside", [row("ghcs", 900, "azure")], { ghe_license_mrr: 900, copilot_business_mrr: 900 }, { in_book: false }),
        account("copilot-mrr-no-rows", [], { copilot_business_mrr: 50 }),
        account("copilot-mrr-invoices-stale", [], { copilot_business_mrr: 60 }),
        account("copilot-mrr-invoices-partial", [], { copilot_enterprise_mrr: 70 }),
        account("stale", [row("ghcs", 700, "Zuora")], { ghcs_license_mrr: 500, copilot_business_mrr: 9 }),
    ] };
    const byId = id => model.accounts.find(a => a.id === id).seller.feeds;
    // Live: accounts with Copilot MRR and no invoices have a successful collection with zero invoice rows.
    Object.assign(byId("copilot-mrr-no-rows").invoices, { state: "no_rows", records: [] });
    byId("copilot-mrr-invoices-stale").invoices.state = "stale";
    byId("copilot-mrr-invoices-partial").invoices.state = "partial";
    byId("stale").invoices.state = "partial";
    byId("stale").mrr.state = "stale";
    return model;
}

test("billing channels count as metered and volume comes only from license MRR or licensing models", () => {
    const results = bookConsumption(liveBillingModel());
    const sku = key => results.find(p => p.product === key);
    const ids = key => Object.fromEntries(Object.entries(sku(key).modes).map(([k, rows]) => [k, rows && rows.map(a => a.id)]));
    assert.deepEqual(ids("ghec"), { metered: ["ghe-metered-users", "metered-channels"], volume: ["ghe-volume-mrr", "ghe-volume-contract"], both: ["ghe-mixed"] });
    assert.ok(sku("ghec").modes.volume.length > 0);
    assert.deepEqual(ids("ghcs"), { metered: ["metered-channels", "ghas-unbundled"], volume: [], both: [] });
    assert.deepEqual(ids("ghsp"), { metered: ["metered-channels"], volume: [], both: [] });
    assert.deepEqual(ids("ghas"), { metered: [], volume: ["ghas-volume"], both: [] });
    assert.deepEqual(ids("code_quality"), { metered: ["code-quality"], volume: null, both: null });
    assert.deepEqual(ids("copilot"), { metered: ["metered-channels"], volume: null, both: null });
    assert.equal(sku("copilot").volumeAvailable, false);
    assert.deepEqual(sku("copilot").mrrOnly.map(a => a.id), ["copilot-mrr-only", "copilot-mrr-no-rows"]);
    assert.equal(sku("ghec").mrrOnly, null);
    assert.deepEqual(sku("ghec").details.find(d => d.account.id === "ghe-mixed").evidence.map(e => [e.label, e.date]),
        [["Consumption invoice", "2026-09"], ["License MRR", "2026-10"]]);
    assert.deepEqual(sku("ghec").basis.map(b => [b.source, b.date, b.note]), [["invoices", "2026-09", "last closed month"],
        ["mrr", "2026-10", "current month"], ["ghe_contract", "2026-10-25", "contract snapshot"]]);
    assert.deepEqual(sku("ghas").basis.at(-1), { source: "ghas_licensing", label: "GHAS licensing model", date: "2026-10-25", note: "account attributes" });
    assert.equal(sku("ghcs").latest, 17);
    assert.equal(sku("ghcs").series.points.at(-2)[1], 4);
    assert.equal(sku("ghcs").series.points.at(-3)[1], null);
    assert.equal(results.filter(p => p.name === "Code Security").length, 1);
});

test("preview fixture uses live billing vocabulary and still reports GHE volume", () => {
    const model = workspaceModel();
    const channels = new Set(model.accounts.flatMap(a => a.seller.feeds.invoices.records.map(r => r.billing_source)));
    assert.deepEqual([...channels].sort(), ["Zuora", "azure"]);
    assert.ok(model.accounts.every(a => a.seller.feeds.mrr.records.every(r => r.date === "2026-11-01")));
    const byProduct = Object.fromEntries(bookConsumption(model).map(p => [p.product, p]));
    assert.ok(byProduct.ghec.modes.volume.length > 0);
    assert.ok(byProduct.ghec.modes.volume.some(a => !a.seller.feeds.invoices.records.some(r => r.product === "ghec")));
    assert.ok(byProduct.ghec.modes.both.some(a => a.id === "synthetic-2"));
    assert.ok(byProduct.ghas.modes.volume.length > 0);
    assert.equal(byProduct.copilot.modes.volume, null);
    assert.ok(byProduct.copilot.mrrOnly.some(a => a.id === "synthetic-58" && a.seller.feeds.invoices.state === "no_rows"));
    assert.ok(!byProduct.copilot.mrrOnly.some(a => a.id === "synthetic-49"));
    assert.deepEqual(byProduct.ghcs.modes.volume, []);
});

test("product pages render evidence dates, Not available volume and the Copilot MRR-only count", () => {
    const model = liveBillingModel();
    const page = (view, key) => renderView(model, { view, sellerOptions: { [`${view}Product`]: key } });
    const ghe = page("ghe", "ghec");
    assert.match(ghe, /<dt>Volume · GHE licensing \(family-level\)<\/dt><dd>2<\/dd>/);
    assert.match(ghe, /Volume rests on family-level GHE licensing \(license MRR or contract model\), which may include GHE Server/);
    assert.match(ghe, /<th scope="row">Volume · GHE licensing \(family-level\)<\/th>/);
    assert.match(ghe, /<dt>Both<\/dt><dd>1<\/dd>/);
    assert.match(ghe, /Recurring revenue <strong>2026-10<\/strong> <span class="sub">current month<\/span>/);
    assert.match(ghe, /Consumption invoices <strong>2026-09<\/strong> <span class="sub">last closed month<\/span>/);
    assert.doesNotMatch(ghe, /Recurring revenue <strong>[^<]*<\/strong> <span class="sub">last closed month/);
    const copilot = page("copilot", "copilot");
    assert.match(copilot, /<dt>Volume<\/dt><dd><span class="none"[^>]*>Not available<\/span><\/dd>/);
    assert.match(copilot, /<dt>Copilot MRR, no Copilot invoice in 13 months<\/dt><dd>2<\/dd>/);
    assert.match(copilot, /<dt>Volume<\/dt>/);
    assert.doesNotMatch(copilot, /GHE licensing \(family-level\)|Volume rests on/);
    assert.match(copilot, /<h3>Copilot metered consumption billed<\/h3>/);
    const all = renderView(model, { view: "consumption" });
    assert.match(all, /Copilot: 2 accounts have Copilot MRR but no Copilot invoice in 13 months. Not counted as volume./);
    assert.match(all, /<td data-label="Volume">2 <span class="qualifier">GHE licensing \(family-level\)<\/span><\/td>/);
    assert.doesNotMatch(all, /Unclassified/);
});

test("Consumption and each product page expose scoped history and the customer billing split", () => {
    const model = fixture();
    for (const [view, key, title] of [["consumption", "ghsp", "Secret Protection"], ["ghas", "code_quality", "Code Quality"],
        ["ghe", "ghec", "GHE Cloud"], ["copilot", "copilot aiu", "Copilot AI credits"], ["consumption", "actions", "Actions"]]) {
        const html = renderView(model, { view, sellerOptions: { [`${view}Product`]: key } });
        assert.ok(html.includes(`<h3>${title} metered consumption billed</h3>`));
        assert.ok(html.includes("Volume license billing is not in this feed"));
        assert.ok(html.includes("Metered vs. volume customers"));
        assert.ok(html.includes(`data-focus-key="billing:${key}" aria-pressed="true"`));
        if (["ghas", "consumption"].includes(view)) {
            for (const name of ["Secret Protection", "Code Security", "Code Quality", "GHAS (bundled)"])
                assert.ok(html.includes(`>${name}</button>`));
        }
        if (key === "ghsp") {
            assert.match(html, /No reported Billed USD in this window/);
            assert.match(html, /No positive billing-model evidence for Secret Protection\./);
            assert.doesNotMatch(html, /Actions latest usage/);
        }
    }
});

test("successful known statuses distinguish zero open issues from missing evidence", () => {
    const model = fixture(), a = model.accounts[0];
    assert.equal(observedCount(a, "issues", openRows(a, "issues")), 0);
    assert.equal(observedCount(model.accounts[1], "issues"), null);
    assert.equal(currentRows(a, "model_cost").length, 2);
    assert.equal(activitySummary(a).spoken.length, 1);
    assert.equal(activitySummary(a).lastMeeting, "2026-10-10");
    assert.equal(productRows(model.accounts[1], model.date).find(p => p.key === "ghcs").status, "No data");
});

test("SE counts do not classify every account-linked record as engineering evidence", () => {
    const a = fixture().accounts[0];
    assert.equal(openSeCount(a), null);
    a.seller.feeds.issues.records = [{ id: "se", se_issue: true, status: "closed" },
        { id: "account-plan", se_issue: false, status: "open" }];
    assert.equal(openSeCount(a), 0);
    a.seller.feeds.issues.records[0].status = "open";
    assert.equal(openSeCount(a), 1);
    a.seller.feeds.issues.state = "partial";
    assert.equal(openSeCount(a), null);
});

test("Account 360 Summary and Copilot show dated readings and outcomes for the selected ID only", () => {
    const model = workspaceModel(), dashboard = workspaceCopilotModel(model);
    const [a, b] = model.accounts;
    b.name = a.name;
    const e = dashboard.enterprises[0];
    dashboard.enterprises.push({ ...e, enterprise_id: "unconfirmed-enterprise", slug: "name-match-only",
        match: { confidence: "single", source: "name", candidates: [{ id: a.id }] } });
    dashboard.stale_sources["08"] = "2026-11-21";
    dashboard.accounts[0].integrations.push({ surface: "IDE chat", aiu28: 0, l28: 2 }); // legacy normalized zero, unknown
    const ui = { view: "account360", account: a.id, copilotLicenses: copilotLicenses(model, dashboard) };
    const summary = renderView(model, ui);
    assert.match(summary, /Copilot adoption[\s\S]*Assigned seats<\/dt><dd>182[\s\S]*Active users · 28 days<\/dt><dd>160[\s\S]*Engaged users · 28 days<\/dt><dd>132/);
    assert.match(summary, /120<\/dd>.*\+11 users WoW · q03 · Through Nov 26, 2026/);
    assert.match(summary, /href="#\/account360\/copilot\/account\/synthetic-0"/);
    assert.match(summary, /href="#\/copilot\/desk\/account\/synthetic-0"/);
    const detail = renderView(model, { ...ui, subview: "copilot" });
    assert.match(detail, /Synthetic model A<\/th><td[^>]*>351<\/td><td[^>]*>75\.0%/);
    assert.match(detail, /IDE chat<\/th><td[^>]*><span class="none">Not available<\/span><\/td><td[^>]*>288/);
    assert.match(detail, /Carried from Nov 21, 2026/);
    assert.match(detail, /PRs merged: 274 · 28-day cohort/);
    assert.match(detail, /Coding agent: Disabled by policy/);
    assert.match(detail, /Coding agent opportunities/);
    assert.doesNotMatch(detail, /name-match-only/);
    const switched = renderView(model, { ...ui, account: b.id, subview: "copilot" });
    assert.match(switched, /Active users · 7 days<\/dt><dd>0<\/dd>.*WoW not available/);
    assert.match(switched, /Not available: no reported model rows/);
    assert.doesNotMatch(switched, /Synthetic model A|PRs merged: 274|name-match-only/);
});

test("missing dashboard, unmatched IDs, and unavailable sources never turn into usage or a broken drill-down", () => {
    const model = syntheticModel(), a = model.accounts[0];
    const ui = { view: "account360", subview: "copilot", account: a.id };
    const offline = renderView(model, { ...ui, copilotLicenses: copilotLicenses(model) });
    assert.match(offline, /Showing Pulse figures only/);
    assert.match(offline, /Active users · 28 days<\/dt><dd>4/);
    assert.doesNotMatch(offline, /Open in Adoption desk/);
    const dashboard = { sources: { "03": {}, "07": {}, "11": {}, "02": {} }, asof: { "02": "2026-10-20" },
        missing_sources: ["03", "11"], accounts: [{ id: a.id, a7: [2, 1], gross: [52], models: [{ model: "unavailable-model", l28: 50 }],
            c360_assigned: 0, c360_active_1d28: 38, engaged_1d28: 27 }] };
    a.products.ghcp = {};
    const snap = { ...ui, copilotLicenses: copilotLicenses(model, dashboard) };
    const detail = renderView(model, snap);
    assert.match(detail, /Assigned seats<\/dt><dd>0<\/dd>.*q02 · Through Oct 20, 2026/);
    assert.match(detail, /Active users · 28 days<\/dt><dd>38/);
    assert.match(detail, /Gross usage value: not available for a dated window/);
    assert.doesNotMatch(detail, /unavailable-model|\+1 users WoW/);
    const unmatched = renderView(model, { ...snap, account: model.accounts[1].id });
    assert.match(unmatched, /No matching Salesforce ID/);
    assert.doesNotMatch(unmatched, /Open in Adoption desk|Active users · 28 days<\/dt><dd>38/);
});

test("all Account 360 routes round-trip and render without changing the model", () => {
    const model = fixture(), before = structuredClone(model);
    for (const subview of ACCOUNT_VIEWS) {
        const route = { view: "account360", subview, account: model.accounts[0].id, query: "Synthetic" };
        assert.deepEqual(parseHash(hashFor(route)), route);
        const html = renderView(model, route);
        assert.match(html, /Account 360/);
        assert.match(renderNav(model, route), /aria-label="Account 360 sections"/);
        assert.match(html, /No data/);
        assert.doesNotMatch(html, /undefined|NaN|<script>unsafe|href="javascript:/);
    }
    assert.deepEqual(model, before);
    const activity = renderView(model, { view: "account360", subview: "activity", account: model.accounts[0].id });
    assert.match(activity, /&lt;script&gt;unsafe/);
    assert.doesNotMatch(activity, /Synthetic CI|Proof of concept|PoC end date/);
});

test("new screens label each monetary type and expose source coverage", () => {
    const model = fixture();
    for (const view of ["overview", "top50", "consumption", "pipeline", "ghe", "ghas", "copilot", "data"]) {
        const html = renderView(model, { view });
        assert.doesNotMatch(html, /\bundefined\b|\bNaN\b/);
        if (["overview", "top50", "consumption", "copilot"].includes(view)) assert.match(html, /[Bb]illed/);
    }
    assert.doesNotMatch(renderView(model, { view: "top50" }), /Synthetic CI|proofs of concept|PoC evidence/);
    assert.match(renderView(model, { view: "top50", sellerRank: "billed" }), /data-value="billed"[^>]*aria-pressed="true"/);
    assert.match(renderView(model, { view: "data" }), /No rows does not mean zero/);
    assert.match(renderView(model, { view: "ghe" }), /\[Model est\.\]/);
    model.accounts[0].seller.feeds.budgets = feed([{ scope: "Synthetic", target: 100.25, spend: 25.5 }]);
    const budgets = renderView(model, { view: "pipeline" });
    assert.match(budgets, /100\.25.*\[Budget\].*currency not supplied/);
    assert.doesNotMatch(budgets, /\$100\.25/);
    assert.deepEqual(bookFeedCoverage(model, "invoices"), { state: "partial", first: "2026-10-27", as_of: "2026-10-27",
        collected_at: "2026-10-27", reporting: 1, total: 3 });
});

test("contacts are stable-ID records, not email-deduplicated interaction counts", () => {
    const model = fixture(), a = model.accounts[0];
    a.seller.feeds.contacts.records.push({ id: "no-email", name: "No email", role: "Buyer" },
        { id: "shared-email", name: "Second person", email: "person@example.invalid" },
        { id: "unseen", name: "No interaction", email: "unseen@example.invalid" });
    assert.equal(contactCount(a), 4);
    assert.equal(contactDirectory(a).filter(p => p.interaction).length, 2);
    assert.equal(contactDirectory(a).find(p => p.id === "no-email").interaction, null);
    a.seller.feeds.contacts.state = "stale";
    assert.equal(contactCount(a), 4);
    assert.equal(contactDirectory(a).filter(p => p.interaction).length, 0);
    const html = renderView(model, { view: "account360", account: a.id, subview: "contacts" });
    for (const label of ["No email", "Second person", "No interaction", "Stale"]) assert.ok(html.includes(label));
    a.seller.feeds.contacts = { state: "no_rows", records: [] };
    assert.equal(contactCount(a), 0);
    assert.match(renderView(model, { view: "account360", account: a.id, subview: "contacts" }), /No contacts returned/);
    a.seller.feeds.contacts.state = "failed";
    assert.equal(contactCount(a), null);
});

test("directory includes the full book and renewal buckets do not overlap", () => {
    const model = fixture();
    model.accounts = Array.from({ length: 75 }, (_, i) => syntheticAccount(`id-${i}`, `Synthetic ${i}`, { arr: i }));
    assert.equal(allAccounts(model).length, 75);
    assert.equal(rankAccounts(model).length, 50);
    assert.match(renderView(model, { view: "account360", query: "Synthetic 0" }), /Synthetic 0/);
    const days = [-1, 0, 30, 31, 60, 61, 90, 91, 120, 121, null];
    model.accounts = days.map((renewing_in_days, i) => syntheticAccount(`a${i}`, `A${i}`, { renewing_in_days }));
    const buckets = renewalBuckets(model);
    assert.deepEqual(buckets.map(b => b.accounts.length), [2, 2, 2, 2, 1, 1, 1]);
    assert.equal(new Set(buckets.flatMap(b => b.accounts.map(a => a.id))).size, days.length);
});

test("chart data uses typed amounts and shows coverage, gaps and single observations", () => {
    const model = fixture();
    assert.deepEqual(portfolioSeries(model).points, billedSeries(model.accounts[0], model.date).points);
    assert.equal(portfolioSeries(model).coverage.at(-1), 1);
    const html = renderView(model, { view: "overview" });
    assert.equal((html.match(/<h1>/g) || []).length, 1);
    assert.doesNotMatch(html, /Your accounts today/);
    assert.match(html, /Exact values/);
    model.accounts[0].seller.feeds.invoices.records = [{ date: "2026-09-01", product: "actions", amount: -3.25 }];
    const one = renderView(model, { view: "account360", account: model.accounts[0].id, subview: "products" });
    assert.match(one, /<circle/);
    assert.match(one, /-3\.25/);
});

test("conflicting, ambiguous and superseded stack evidence never becomes current adoption", () => {
    const a = fixture().accounts[0];
    const row = { schema: 1, technology: "Jenkins", category: "CI/CD", usage_status: "reported_in_use",
        verification_state: "quote_verified", source_current: true, source_url: "https://example.invalid/source",
        observed_at: "2026-10-01", evidence_quote: "We use Jenkins." };
    a.seller.feeds.tech_stack = feed([row, { ...row, usage_status: "historical", evidence_quote: "We no longer use Jenkins." }]);
    assert.equal(stackGroups(a)[0].status, "conflicting");
    a.seller.feeds.tech_stack.records[0].source_current = false;
    assert.equal(stackGroups(a)[0].status, "historical");
    a.seller.feeds.tech_stack.records[1].verification_state = "ambiguous";
    assert.equal(stackGroups(a)[0].status, "mention_only");
});

test("internal signals ignore local mutations; exact recommendation duplicates alone collapse", () => {
    const model = fixture();
    model.signals = [{ id: "internal", account_id: "a", evidence: "Risk identified", read_only: true, status: "open" },
        { id: "distinct", account_id: "b", evidence: "Risk identified", status: "open" }];
    assert.equal(withActionStates(model, { internal: { state: "done" } }).signals[0].status, "open");
    const unique = dedupeSignals(model.signals, { items: [{ account_id: "a", signals: [{ ml_driven_insights: " RISK identified " }] }] });
    assert.deepEqual(unique.map(s => s.id), ["distinct"]);
    assert.match(renderView(model, { view: "signals", signalFilters: { status: "all" } }), /Read-only source record/);
});
