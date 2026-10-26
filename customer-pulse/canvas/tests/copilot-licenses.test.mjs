import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { copilotFilterCounts, copilotLicenses, copilotRowsFor, copilotTotals, summarize } from "../model.mjs";
import { renderDrawer, renderView } from "../views.mjs";
import { syntheticAccount, syntheticAction, syntheticEvent, syntheticModel } from "./helpers.mjs";

function fixture() {
    const model = syntheticModel();
    model.accounts = [
        syntheticAccount("a", "Example <A>", { ghcp: { contracted: 100, business: 80, enterprise: 20, assigned: 70,
            contracted_as_of: "2030-01-01", as_of: "2030-01-03", health: { category: "Red" },
            funnel: { active_1d28: 60, engaged_1d28: 40 } }, renewing_in_days: 30 }),
        syntheticAccount("b", "Example B", { ghcp: { contracted: 0, business: 0, enterprise: 0, assigned: 3,
            funnel: { active_1d28: 2, engaged_1d28: 1 } }, renewing_in_days: -1 }),
        syntheticAccount("c", "Example C", { ghcp: { contracted: 10, assigned: null, funnel: { active_1d28: null, engaged_1d28: null } },
            renewing_in_days: 91 }),
        syntheticAccount("d", "Example D"),
    ];
    model.actions = [];
    model.events = [];
    model.sources = [{ feed: "attributes", as_of: "2030-01-02", state: "ok" }];
    return model;
}

const dashboard = {
    data_date: "2030-01-04", asof: { "09": "2030-01-01" },
    accounts: [{ id: "a", contracted_seats: 100 }, { id: "b", contracted_seats: 5 }, { id: "c", contracted_seats: null }],
    enterprises: [
        { slug: "example-a", match: { confidence: "confirmed", candidates: [{ id: "a" }] } },
        { slug: "example-b", match: { confidence: "single", candidates: [{ id: "b" }] } },
        { slug: "not-safe", match: { confidence: "ambiguous", candidates: [{ id: "c" }] } },
        { slug: "not-unique", match: { confidence: "single", candidates: [{ id: "a" }, { id: "c" }] } },
    ],
};

test("license totals are Pulse-only, null-preserving and label partial metric coverage", () => {
    const model = fixture();
    const original = JSON.stringify(model);
    const totals = copilotTotals(model);
    assert.deepEqual([totals.licenses, totals.business, totals.enterprise, totals.assigned, totals.active28, totals.engaged28],
        [110, 80, 20, 73, 62, 41]);
    assert.deepEqual(totals.reporting.metrics.licenses, { n: 3, of: 3 });
    assert.deepEqual(totals.reporting.metrics.engaged28, { n: 2, of: 3 });
    assert.equal(totals.reporting.n, 2);
    assert.equal(copilotTotals({ ...model, accounts: [] }).licenses, null);
    assert.equal(JSON.stringify(model), original);
});

test("reconciliation joins only unique confirmed/single slugs and never replaces Pulse zero", () => {
    const result = copilotLicenses(fixture(), dashboard);
    assert.deepEqual(result.rows.map(row => row.slug), ["example-a", "example-b", null]);
    assert.equal(result.rows[1].licenses, 0);
    assert.equal(result.rows[1].dashboardLicenses, 5);
    assert.equal(result.rows[1].flags.source_mismatch, true);
    assert.equal(result.rows[0].flags.unassigned, true);
    assert.equal(result.rows[0].flags.engaged_lt_50, true);
    assert.equal(result.rows[0].flags.renewing_90, true);
    assert.equal(result.rows[1].flags.over_assigned, true);
    assert.equal(result.rows[1].flags.engaged_lt_50, false);
    assert.equal(result.rows[2].flags.source_mismatch, false);
    assert.deepEqual(result.flags, { unassigned: 1, over_assigned: 1, engaged_lt_50: 1, renewing_90: 1, source_mismatch: 1 });
    assert.equal(copilotLicenses(fixture()).reconciliation, "unavailable");
});

test("filters count searched customers and totals follow the active filter", () => {
    const model = fixture();
    const data = copilotLicenses(model, dashboard);
    assert.deepEqual(copilotFilterCounts(data.rows), { all: 3, attention: 2, unassigned: 1, engaged_lt_50: 1, renewing_90: 1 });
    assert.equal(copilotRowsFor(data.rows, "all", "example-b")[0].account_id, "b");
    assert.equal(copilotRowsFor(data.rows, "renewing_90").length, 1);
    const html = renderView(model, { view: "copilot", copilotLicenses: data, copilotFilter: "unassigned" });
    assert.match(html, /Filtered total<span class="sub">1 customers/);
    assert.match(html, /100<span class="sub">1 account reporting/);
    assert.doesNotMatch(html, /data-account="b"/);
});

test("Portfolio and drawer escape data, preserve nulls, show source disagreement and every customer", () => {
    const model = fixture();
    const data = copilotLicenses(model, dashboard);
    const html = renderView(model, { view: "copilot", copilotLicenses: data });
    assert.match(html, /Example &lt;A&gt;/);
    assert.doesNotMatch(html, /Example <A>/);
    assert.match(html, /Pulse 0 \/ dashboard 5/);
    assert.match(html, /2 of 3 reporting/);
    assert.match(html, /<td class="num col-active28"[^>]*><span title="[^"]*">–/);
    assert.match(html, /aria-sort="ascending"/);
    assert.equal((html.match(/<tr tabindex="0"/g) || []).length, 3);
    assert.ok(html.indexOf("Customers") < html.indexOf('id="h-actions-copilot"'));
    assert.match(renderDrawer(model, { account: "b", copilotLicenses: data }), /Pulse 0 .*Copilot dashboard 5/);
    assert.match(renderDrawer(model, { account: "a", copilotLicenses: data }), /example-a/);
    assert.match(renderDrawer(model, { account: "a", copilotLicenses: data }), /Open in Adoption desk/);
    model.accounts = Array.from({ length: 65 }, (_, index) => syntheticAccount(`row-${index}`, `Synthetic ${index}`,
        { ghcp: { contracted: 1, assigned: 1 } }));
    assert.equal((renderView({ ...model }, { view: "copilot" }).match(/<tr tabindex="0"/g) || []).length, 65);
});

test("zero-seat reconciliation-only customers remain visible instead of hiding mismatches", () => {
    const model = fixture();
    model.accounts.push(syntheticAccount("extra", "Synthetic Former Customer", { ghcp: { contracted: 0, assigned: null } }));
    const cp = { ...dashboard, accounts: [...dashboard.accounts, { id: "extra", contracted_seats: 7 }] };
    const result = copilotLicenses(model, cp);
    const extra = result.rows.find(row => row.account_id === "extra");
    assert.equal(extra.reconciliationOnly, true);
    assert.equal(extra.flags.source_mismatch, true);
    assert.equal(result.totals.licenses, 110);
    assert.equal(copilotTotals(model).reporting.of, 3);
    assert.match(renderView(model, { view: "copilot", copilotLicenses: result }), /Reconciliation only/);
    assert.match(renderDrawer(model, { account: "extra", copilotLicenses: result }), /Copilot dashboard 7/);
});

test("get_summary adds adoption totals without replacing existing fields or numeric signals", () => {
    const model = fixture();
    model.kpis.ghcp.assigned = { value: 73, as_of: "2030-01-03" };
    model.signals = [{ id: "synthetic", verified: false }];
    const cp = summarize(model).products.copilot;
    assert.equal(cp.licenses_total, 110);
    assert.equal(cp.active_devs_28d, 62);
    assert.equal(cp.engaged_devs_28d, 41);
    assert.equal(cp.assigned_seats.value, 73);
    assert.equal(summarize(model).signals, 1);
});

test("embedded chrome is hidden and messages validate both origin and window source", () => {
    const css = readFileSync(new URL("../copilot/style.css", import.meta.url), "utf8");
    assert.match(css, /\.embedded \.topbar, \.embedded \.navigation, \.embedded \.footer,[\s\S]*?display: none/);
    assert.match(css, /\.embedded \.snapshot-ribbon, \.embedded \.desk-caveat/);
    assert.match(css, /\.embedded \.table-wrap \{ max-height: none; overflow: visible;/);
    const child = readFileSync(new URL("../copilot/app.js", import.meta.url), "utf8");
    const parent = readFileSync(new URL("../app.js", import.meta.url), "utf8");
    assert.match(child, /new ResizeObserver/);
    assert.match(child, /event\.origin !== location\.origin \|\| event\.source !== parent/);
    assert.match(parent, /event\.origin !== location\.origin \|\| !frame \|\| event\.source !== frame\.contentWindow/);
    assert.match(parent, /event\.target\.matches\("tr\[data-account\]"\)/);
});

test("child navigation preserves the connected frame while replacing the parent controls", () => {
    const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
    let removed = 0;
    let inserted = 0;
    const frame = { name: "copilot-dashboard", getAttribute: () => "copilot/?embed=1#/desk" };
    const fresh = { ...frame, remove: () => {} };
    const content = { querySelector: () => fresh };
    const main = {
        children: [{ remove: () => { removed++; } }, frame],
        querySelector: () => frame,
        insertBefore: (node, before) => { assert.equal(node, content); assert.equal(before, frame); inserted++; },
    };
    const context = vm.createContext({ els: { main }, state: { embedSrc: "copilot/?embed=1#/desk" },
        document: { createElement: () => ({ content }) } });
    vm.runInContext(source.slice(source.indexOf("const written ="), source.indexOf("const refreshKey ="))
        + '\nsetMainHTML("changed controls"); setMainHTML("changed controls");', context);
    assert.equal(removed, 1);
    assert.equal(inserted, 1);
});

test("embedded route notification runs even when no search field has focus", () => {
    const source = readFileSync(new URL("../copilot/app.js", import.meta.url), "utf8");
    let notified = 0;
    const app = {};
    vm.runInNewContext(source.slice(source.indexOf("function render()"), source.indexOf("async function loadModel()")) + "\nrender();",
        { state: { model: {}, view: "desk" }, document: { activeElement: null }, app,
            renderHeader() {}, renderNotices() {}, renderDesk: () => "Synthetic desk",
            notifyParentRoute: () => { notified++; }, chartId: 0 });
    assert.equal(app.innerHTML, "Synthetic desk");
    assert.equal(notified, 1);
});

test("narrow customer cells retain priority, action count and dated renewal without duplicate visible copies", () => {
    const model = fixture();
    model.accounts[0].renewal = "2030-02-01";
    model.actions = [syntheticAction("synthetic-risk", model.accounts[0], "ghcp", "risk", "High")];
    const html = renderView(model, { view: "copilot" });
    assert.match(html, /<span class="compact-attention"><span class="tag tag-high">High<\/span><span>1 action<\/span><\/span>/);
    assert.match(html, /<span class="compact-renewal renewing-soon"><span class="signal-unit">Renews Feb 1, 2030<\/span> <span class="signal-unit">\(30 days\)<\/span><\/span>/);
    assert.match(html, /class="col-renewal"/);
    assert.match(html, /class="col-attention"/);
    const css = readFileSync(new URL("../style.css", import.meta.url), "utf8");
    assert.match(css, /\.copilot-customers \.compact-signals \{ display: none; \}/);
    assert.match(css, /@container \(max-width: 950px\) \{\s*\.copilot-customers \.col-renewal, \.copilot-customers \.col-attention \{ display: none; \}\s*\.copilot-customers \.compact-signals \{ display: flex;/);
    assert.doesNotMatch(css.slice(css.indexOf("@container (max-width: 560px)")), /compact-signals.*display:\s*none/);
});

test("Portfolio wrapping units cover long names, reconciliation, action-only, notify-only and renewal-only rows", () => {
    const model = fixture();
    model.accounts[0].name = "Synthetic International Industrial Research and Development Company";
    model.accounts[0].products.ghcp.business = 123;
    model.accounts[0].products.ghcp.enterprise = 456;
    model.accounts[1].products.ghcp.assigned = 0;
    model.actions = [1, 2, 3].map(n => syntheticAction(`risk-${n}`, model.accounts[0], "ghcp", "risk", "High"));
    model.events = [syntheticEvent("notify-only", model.accounts[1], "ghcp", "Notify", "2030-01-01", 1)];
    model.accounts[2].renewal = "2030-02-01";
    model.accounts[2].renewing_in_days = 5;
    const html = renderView(model, { view: "copilot", copilotLicenses: copilotLicenses(model, dashboard) });
    assert.match(html, /Synthetic International Industrial Research and Development Company/);
    assert.match(html, /<span class="license-plan">Business 123<\/span><span class="plan-separator" aria-hidden="true"> · <\/span><span class="license-plan">Enterprise 456<\/span>/);
    assert.match(html, /<span class="compact-signals"><span class="compact-attention"><span class="tag tag-high">High<\/span><span>3 actions<\/span><\/span><\/span>/);
    assert.match(html, /Reconciliation only/);
    assert.match(html, /<span class="compact-signals"><span class="compact-attention"><span class="tag tag-notify">Notify<\/span><\/span><\/span>/);
    assert.match(html, /<span class="compact-signals"><span class="compact-renewal renewing-soon">/);
    assert.match(html, /<span class="signal-unit">\(5 days\)<\/span>/);
    const css = readFileSync(new URL("../style.css", import.meta.url), "utf8");
    assert.match(css, /\.license-plan, \.copilot-customers \.signal-unit \{[^}]*white-space: nowrap; overflow-wrap: normal;/);
    assert.match(css, /\.compact-attention \{[^}]*white-space: nowrap;/);
    assert.match(css, /\.compact-renewal \{[^}]*flex-basis: 100%;/);
    assert.doesNotMatch(css, /compact-signals > :last-child/);
    const narrow = css.slice(css.indexOf("@container (max-width: 950px)"));
    assert.match(narrow, /\.license-plan \{ display: block; \}/);
    assert.match(narrow, /\.plan-separator \{ display: none; \}/);
});

test("all plot consumers receive container-independent line, grid and label styling", () => {
    const source = readFileSync(new URL("../copilot/app.js", import.meta.url), "utf8");
    const context = vm.createContext({
        chartId: 0, isNumber: Number.isFinite, H: value => String(value), formatAxis: value => String(value),
    });
    const html = vm.runInContext(source.slice(source.indexOf("function plot("), source.indexOf("function chartPanel("))
        + '\nplot([{ name: "Synthetic trend", values: [9, 3, 7] }]);', context);
    assert.match(html, /^<svg class="plot"/);
    assert.match(html, /<polyline class="line"/);
    const css = readFileSync(new URL("../copilot/style.css", import.meta.url), "utf8");
    assert.match(css, /(?:^|\n)svg\.plot \.line \{[^}]*fill: none;/);
    assert.match(css, /(?:^|\n)svg\.plot text \{ fill: var\(--muted\);/);
    assert.match(css, /(?:^|\n)svg\.plot \.grid \{ stroke: var\(--line\);/);
});

test("desk adoption strip clarifies dashboard readings while retaining cutoff guidance", () => {
    const source = readFileSync(new URL("../copilot/app.js", import.meta.url), "utf8");
    const context = vm.createContext({
        state: { model: { accounts: [] } }, adoptionTotals: () => Object.fromEntries(["assigned", "active_28d", "engaged_1d28"]
            .map(key => [key, { value: null, n: 0, of: 0 }])),
        hasSources: () => true, metricName: (_, label) => label, num: value => value ?? "—",
        dataValue: () => null,
    });
    vm.runInContext(source.slice(source.indexOf("function adoptionFunnel("), source.indexOf("function forecastWhitespace(")), context);
    const html = vm.runInContext("adoptionFunnel()", context);
    assert.match(html, /class="fact-strip adoption-funnel"/);
    const css = readFileSync(new URL("../copilot/style.css", import.meta.url), "utf8");
    assert.match(css, /\.adoption-funnel \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}/);
    assert.match(css, /@media \(max-width: 740px\)[\s\S]*\.fact-strip \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/);
    assert.match(html, /Dashboard source\. Portfolio uses Pulse's dated license figures, so Licenses and Assigned can differ for a few accounts\./);
    assert.match(html, /Source cutoffs differ; a dash means not reported\./);
    assert.doesNotMatch(vm.runInContext("adoptionFunnel({ assigned: 4 })", context), /Dashboard source/);
});
