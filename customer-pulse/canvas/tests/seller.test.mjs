import assert from "node:assert/strict";
import { test } from "node:test";
import { ACCOUNT_VIEWS, hashFor, parseHash, withActionStates } from "../model.mjs";
import { renderView } from "../views.mjs";
import { activitySummary, billedLtm, billedSeries, bookConsumption, currentRows, dedupeSignals, knownSum,
    bookFeedCoverage, monthlyChange, observedCount, openRows, productRows, rankAccounts } from "../seller-data.mjs";
import { syntheticAccount, syntheticModel } from "./helpers.mjs";

const feed = records => ({ state: "ok", as_of: "2026-10-27", collected_at: "2026-10-27", records });
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
    const product = bookConsumption(model)[0];
    assert.equal(product.latest, 516);
    assert.equal(product.mom.delta, 12);
    assert.equal(product.mom.accounts, 1);
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

test("all six Account 360 routes round-trip and render without changing the model", () => {
    const model = fixture(), before = structuredClone(model);
    for (const subview of ACCOUNT_VIEWS) {
        const route = { view: "account360", subview, account: model.accounts[0].id, query: "Synthetic" };
        assert.deepEqual(parseHash(hashFor(route)), route);
        const html = renderView(model, route);
        assert.match(html, /Account 360/);
        assert.match(html, /aria-label="Account 360 sections"/);
        assert.match(html, /No data/);
        assert.doesNotMatch(html, /undefined|NaN|<script>unsafe|href="javascript:/);
    }
    assert.deepEqual(model, before);
    const activity = renderView(model, { view: "account360", subview: "activity", account: model.accounts[0].id });
    assert.match(activity, /&lt;script&gt;unsafe/);
    assert.match(activity, /Synthetic CI/);
});

test("new screens label each monetary type and expose source coverage", () => {
    const model = fixture();
    for (const view of ["overview", "top50", "consumption", "pipeline", "ghe", "ghas", "copilot", "data"]) {
        const html = renderView(model, { view });
        assert.doesNotMatch(html, /\bundefined\b|\bNaN\b/);
        if (["overview", "top50", "consumption", "copilot"].includes(view)) assert.match(html, /\[Billed\]/);
    }
    assert.match(renderView(model, { view: "top50" }), /Synthetic CI/);
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

test("internal signals ignore local mutations; exact recommendation duplicates alone collapse", () => {
    const model = fixture();
    model.signals = [{ id: "internal", account_id: "a", evidence: "Risk identified", read_only: true, status: "open" },
        { id: "distinct", account_id: "b", evidence: "Risk identified", status: "open" }];
    assert.equal(withActionStates(model, { internal: { state: "done" } }).signals[0].status, "open");
    const unique = dedupeSignals(model.signals, { items: [{ account_id: "a", signals: [{ ml_driven_insights: " RISK identified " }] }] });
    assert.deepEqual(unique.map(s => s.id), ["distinct"]);
    assert.match(renderView(model, { view: "signals", signalFilters: { status: "all" } }), /Read-only source record/);
});
