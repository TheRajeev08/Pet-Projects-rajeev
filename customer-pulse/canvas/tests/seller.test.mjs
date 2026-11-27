import assert from "node:assert/strict";
import { test } from "node:test";
import { ACCOUNT_VIEWS, hashFor, parseHash, withActionStates } from "../model.mjs";
import { renderView, renderNav } from "../views.mjs";
import { activitySummary, billedLtm, billedSeries, bookConsumption, currentRows, dedupeSignals, knownSum,
    bookFeedCoverage, monthlyChange, observedCount, openRows, productRows, rankAccounts,
    allAccounts, contactDirectory, contactCount, portfolioSeries, renewalBuckets, stackGroups, openSeCount } from "../seller-data.mjs";
import { syntheticAccount, syntheticModel } from "./helpers.mjs";
import { workspaceModel } from "./workspace-fixture.mjs";

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

test("GHAS component trends and customer modes use exact products and one closed month", () => {
    const row = (product, amount, billing_source, date = "2026-09-01") => ({ date, product, amount, billing_source });
    const account = (id, invoices, mrr = {}, options = {}) => syntheticAccount(id, id, { ...options,
        seller: { feeds: { invoices: feed(invoices), mrr: feed([{ date: "2026-09-01", ...mrr }]) } } });
    const model = { ...fixture(), accounts: [
        account("mixed", [row("GHCS", 10, "metered"), row("GitHub Code Security", 8, "metered"), row("ghsp", 5, "volume")], { ghcs_license_mrr: 99 }),
        account("metered", [], { ghcs_metered_mrr: 15, ghas_license_mrr: 999 }),
        account("unclassified", [row("Code Security", 3, "reseller"), row("GitHub Code Quality", 7, "metered")]),
        account("volume", [row("ghcs", 2, "volume"), row("Advanced Security", 4, "volume")]),
        account("not-current", [row("ghcs", 30, "metered", "2026-10-01"), row("ghcs", 4, "volume", "2026-08-01"), row("ghsp", 0, "metered")]),
        account("credit", [row("ghcs", -1, "volume"), row("code_quality", 0, "volume")]),
        account("outside", [row("ghcs", 900, "volume")], {}, { in_book: false }),
        account("stale", [row("ghcs", 700, "metered")], { ghcs_license_mrr: 500 }),
    ] };
    model.accounts.at(-1).seller.feeds.invoices.state = "partial";
    model.accounts.at(-1).seller.feeds.mrr.state = "stale";
    model.accounts[4].seller.feeds.mrr.records.push({ date: "2026-08-01", ghcs_license_mrr: 3 });
    const results = bookConsumption(model);
    const sku = key => results.find(p => p.product === key);
    assert.deepEqual(["ghcs", "ghsp", "code_quality", "ghas"].map(k => sku(k).latest), [22, 5, 7, 4]);
    assert.deepEqual(Object.fromEntries(Object.entries(sku("ghcs").modes).map(([k, rows]) => [k, rows.map(a => a.id)])),
        { metered: ["metered"], volume: ["volume"], both: ["mixed"], unknown: ["unclassified"] });
    assert.deepEqual(sku("code_quality").modes.metered.map(a => a.id), ["unclassified"]);
    assert.equal(sku("ghsp").series.coverage.at(-1), 2);
    assert.equal(sku("ghcs").series.total, 7);
    assert.equal(sku("ghcs").series.points.at(-2)[1], 4);
    assert.equal(sku("ghcs").series.points.at(-3)[1], null);
    assert.equal(results.filter(p => p.name === "Code Security").length, 1);
});

test("Consumption and each product page expose scoped history and the customer billing split", () => {
    const model = fixture();
    for (const [view, key, title] of [["consumption", "ghsp", "Secret Protection"], ["ghas", "code_quality", "Code Quality"],
        ["ghe", "ghec", "GHE Cloud"], ["copilot", "copilot aiu", "Copilot AI credits"], ["consumption", "actions", "Actions"]]) {
        const html = renderView(model, { view, sellerOptions: { [`${view}Product`]: key } });
        assert.ok(html.includes(`<h3>${title} billed trend</h3>`));
        assert.ok(html.includes("Metered vs. volume customers"));
        assert.ok(html.includes(`data-focus-key="billing:${key}" aria-pressed="true"`));
        if (["ghas", "consumption"].includes(view)) {
            for (const name of ["Secret Protection", "Code Security", "Code Quality", "GHAS (bundled)"])
                assert.ok(html.includes(`>${name}</button>`));
        }
        if (key === "ghsp") {
            assert.match(html, /No reported Billed USD in this window/);
            assert.match(html, /No positive product billing evidence for 2026-09/);
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

test("all six Account 360 routes round-trip and render without changing the model", () => {
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
