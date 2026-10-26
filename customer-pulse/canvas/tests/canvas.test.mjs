// Pure-module tests: model.mjs helpers, views.mjs renderers, and the Python engine → canvas contract.
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import {
    QUERY_MAX, SOURCE_STATES, VIEWS, actionsFor, addDays, attentionIndex, clipQuery, dayAge, eventsFor, hasProduct, hashFor, isDay,
    parseHash, productSeats, resolveAccount, seriesChange, seriesPoints, sortActions, summarize, validateModel, whitespace,
    withActionStates,
} from "../model.mjs";
import {
    ROW_LIMIT, esc, fmtDay, fmtInt, fmtMoney, fmtPct, fmtSigned, renderDrawer, renderNav, renderNotices, renderTopbar,
    renderUnavailable, renderView, sparkline,
} from "../views.mjs";
import { NO_PYTHON, buildPulseHome, removeDir, syntheticAccount, syntheticAction, syntheticEvent, syntheticModel, tempDir } from "./helpers.mjs";

const NOW = new Date(2026, 9, 27, 12);
const DASH = "\u2013";
const ids = list => list.map(item => item.id);
const captures = (html, pattern) => [...html.matchAll(pattern)].map(match => match[1]);
const rowIds = html => captures(html, /<th scope="row"><button type="button" class="link account-link" data-account="([^"]+)"/g);
const actionIds = html => captures(html, /data-focus-key="(?:done|reopen):([^"]+)"/g);
const occurrences = (html, text) => html.split(text).length - 1;

function section(html, id) {
    const start = html.indexOf(`aria-labelledby="${id}"`);
    assert.ok(start >= 0, `${id} is rendered`);
    return html.slice(start, html.indexOf("</section>", start));
}

function funnelStep(html, label) {
    const match = html.match(new RegExp(`<span class="funnel-label">${label}</span><span class="funnel-bar"><span style="width:[\\d.]+%"></span></span>`
        + '<span class="funnel-value">(.*?)(?:<span class="sub">(.*?)</span>)?</span>'));
    assert.ok(match, `the ${label} funnel step is rendered`);
    return [match[1], match[2] ?? ""];
}

function moreButton(html, key) {
    const match = html.match(new RegExp(`data-more="${key}" data-focus-key="more:${key}" aria-expanded="(true|false)">([^<]*)</button>`));
    return match ? `${match[2]} (${match[1] === "true" ? "expanded" : "collapsed"})` : null;
}

function assertClean(html, where) {
    for (const bad of ["undefined", "NaN", "[object Object]", "<Orbit>"]) assert.ok(!html.includes(bad), `${where} renders ${bad}`);
}

const PANEL_KEYS = ["overview", "ghe", "ghas", "copilot"];
const TABLE_IDS = ["accounts", "ghe", "ghas", "ghcp", "ws-ghe", "ws-ghas", "ws-ghcp"];
const everyPanel = value => Object.fromEntries(PANEL_KEYS.map(key => [key, value]));
const UI_VARIANTS = {
    default: {},
    query: { query: "orbit" },
    "hostile query": { query: `<Orbit> & "x" 'y'` },
    risks: { kind: everyPanel("risk") },
    opportunities: { kind: everyPanel("opportunity") },
    "show done": { showClosed: true },
    expanded: { showClosed: true, expanded: new Set([...PANEL_KEYS.flatMap(key => [`actions:${key}`, `changes:${key}`]), "renewals", ...TABLE_IDS.map(id => `table:${id}`)]) },
    sorted: { sort: Object.fromEntries(TABLE_IDS.map(id => [id, { key: "arr", dir: -1 }])) },
    refreshing: { refresh: { phase: "requested", message: "Working <now>" }, build: { build_error: "boom <x>", generated_at: "2026-10-27T23:00:00Z" } },
};

/** Renders every view, notice bar, nav, topbar and drawer for every UI variant and checks nothing leaks. */
function sweep(model, where) {
    for (const [name, variant] of Object.entries(UI_VARIANTS)) {
        for (const view of VIEWS) {
            const ui = { ...variant, view, now: NOW };
            assertClean(renderView(model, ui), `${where}: ${view} (${name})`);
            assertClean(renderNotices(model, ui), `${where}: notices on ${view} (${name})`);
            assertClean(renderNav(model, ui), `${where}: nav on ${view} (${name})`);
        }
        assertClean(renderTopbar(model, variant), `${where}: topbar (${name})`);
    }
    for (const account of model.accounts) assertClean(renderDrawer(model, { account: account.id }), `${where}: drawer for ${account.id}`);
}

/** The synthetic model with a hostile account name, parent and signals, for escaping sweeps. */
function hostileModel() {
    const model = syntheticModel();
    const name = "Gamma & <Orbit> Tools";
    model.accounts[2].name = name;
    model.accounts[1].parent = "Parent <Orbit> Group";
    for (const item of [...model.actions, ...model.events]) if (item.account_id === "t-3") item.account_name = name;
    model.signals = [
        { date: "2026-10-26", priority: "High", new: true, source: "News <wire>", tags: ["AI", 7], account_id: "t-3", account_name: name,
            title: "Launch <Orbit>", url: "https://news.example.invalid/a?b=1&c=2", summary: "Why <Orbit> matters" },
        { date: "2026-10-25", priority: "Bogus", account_name: "Unlisted <Orbit> Co", title: "No <Orbit> link", url: "javascript:alert(1)" },
    ];
    return model;
}

/** 60 accounts, 12 actions, 9 changes and 7 renewals, to exercise every "Show all" limit. */
function bigModel() {
    const model = syntheticModel();
    model.accounts = Array.from({ length: 60 }, (_, index) => {
        const days = index < 7 ? 10 + index : 200 + index;
        const label = String(index).padStart(2, "0");
        return syntheticAccount(`r-${label}`, `Row ${label}`, { arr: 1000 + index, renewing_in_days: days, renewal: addDays("2026-10-27", days), ghe: { contracted: 10 + index } });
    });
    model.actions = Array.from({ length: 12 }, (_, index) => syntheticAction(`b-${index}`, model.accounts[index], "ghe", "risk", "Medium"));
    model.events = Array.from({ length: 9 }, (_, index) => syntheticEvent(`f-${index}`, model.accounts[index], "ghe", "Notify", "2026-10-26", 5));
    return model;
}

describe("model.mjs", () => {
    test("validateModel accepts a well-formed model and returns it untouched", () => {
        const model = syntheticModel();
        const copy = structuredClone(model);
        assert.equal(validateModel(model), model);
        assert.deepEqual(model, copy);
        const { signals, ...withoutSignals } = model;
        assert.equal(validateModel(withoutSignals), withoutSignals);
    });

    test("validateModel names the first structural problem", () => {
        const broken = change => {
            const model = syntheticModel();
            change(model);
            return model;
        };
        const cases = [
            [null, "the model is not a JSON object"],
            [[], "the model is not a JSON object"],
            [broken(m => { m.schema = 2; }), "unsupported model schema 2 (expected 1)"],
            [broken(m => { m.schema = "1"; }), 'unsupported model schema "1" (expected 1)'],
            [broken(m => { delete m.date; }), "the model date is missing or invalid"],
            [broken(m => { m.date = "2026-02-30"; }), "the model date is missing or invalid"],
            ...["accounts", "events", "actions", "sources"].map(key => [broken(m => { m[key] = {}; }), `"${key}" must be a list`]),
            [broken(m => { m.signals = null; }), '"signals" must be a list'],
            [broken(m => { m.kpis = []; }), '"kpis" must be an object'],
            [broken(m => { m.accounts[1].id = ""; }), "account 1 has no id"],
            [broken(m => { m.accounts[1] = "t-2"; }), "account 1 has no id"],
            [broken(m => { m.accounts[1].id = "t-1"; }), "account t-1 appears twice"],
            [broken(m => { delete m.accounts[2].products.ghcp; }), "account t-3 has no product summary"],
            [broken(m => { delete m.actions[0].id; }), "action 0 has no id"],
            [broken(m => { m.actions[1].id = "a-1"; }), "action a-1 appears twice"],
            [broken(m => { m.actions[0].priority = "Urgent"; }), 'action a-1 has unknown priority "Urgent"'],
            [broken(m => { m.events[0].id = 5; }), "event 0 has no id"],
            [broken(m => { m.events[0].priority = "high"; }), 'event e-1 has unknown priority "high"'],
            [broken(m => { m.events[0].date = "2026-10-32"; }), "event e-1 has an invalid date"],
        ];
        for (const [model, message] of cases) assert.throws(() => validateModel(model), { message }, message);
    });

    // Inherited Object.prototype keys must not pass as priorities: priorityRank("constructor") would make every sort compare NaN.
    test("validateModel rejects priorities that are Object.prototype keys", () => {
        const model = syntheticModel();
        model.actions[0].priority = "constructor";
        assert.throws(() => validateModel(model), { message: 'action a-1 has unknown priority "constructor"' });
        const other = syntheticModel();
        other.events[0].priority = "toString";
        assert.throws(() => validateModel(other), { message: 'event e-1 has unknown priority "toString"' });
    });

    test("isDay, addDays and dayAge handle calendar edges", () => {
        assert.equal(isDay("2024-02-29"), true);
        for (const value of ["2026-02-29", "2026-13-01", "2026-10-27T00:00:00Z", "20261027", "", null, 20261027]) assert.equal(isDay(value), false, String(value));
        assert.equal(addDays("2026-03-01", -1), "2026-02-28");
        assert.equal(addDays("2024-12-31", 1), "2025-01-01");
        assert.equal(dayAge("2026-10-27", NOW), 0);
        assert.equal(dayAge("2026-10-24", NOW), 3);
        assert.equal(dayAge("2026-10-28", NOW), -1);
        assert.equal(dayAge("2026-10-27", new Date(2026, 9, 28, 0, 1)), 1);
        assert.equal(dayAge("not a day", NOW), null);
    });

    test("clipQuery keeps at most QUERY_MAX whole characters", () => {
        assert.equal(QUERY_MAX, 80);
        assert.equal(clipQuery("x".repeat(100)), "x".repeat(80));
        assert.equal(clipQuery(`${"x".repeat(79)}😀z`), `${"x".repeat(79)}😀`);
        assert.equal(clipQuery("  padded  "), "  padded  ");
        assert.equal(clipQuery("a\uD800b"), "a\uFFFDb");
        assert.equal(clipQuery(null), "");
        assert.equal(clipQuery(42), "42");
    });

    test("hashFor and parseHash round-trip every view, account and query", () => {
        assert.equal(hashFor(), "#/overview");
        assert.equal(hashFor({ view: "bogus" }), "#/overview");
        assert.equal(hashFor({ view: "ghas", account: "t 1/x", query: "a&b" }), "#/ghas/account/t%201%2Fx?q=a%26b");
        assert.equal(hashFor({ view: "data", query: "x".repeat(90) }), `#/data?q=${"x".repeat(80)}`);
        for (const view of VIEWS) {
            for (const account of [null, "t-1", "a/b ?#%"]) {
                for (const query of ["", "q & <x> ?#/"]) assert.deepEqual(parseHash(hashFor({ view, account, query })), { view, account, query });
            }
        }
        const overview = { view: "overview", account: null, query: "" };
        assert.deepEqual(parseHash(""), overview);
        assert.deepEqual(parseHash(null), overview);
        assert.deepEqual(parseHash("#ghe"), { ...overview, view: "ghe" });
        assert.deepEqual(parseHash("#/bogus/account/t-2?q=x"), { view: "overview", account: "t-2", query: "x" });
        assert.deepEqual(parseHash("#/ghe/account/a/b"), { view: "ghe", account: "a/b", query: "" });
        assert.deepEqual(parseHash("#/ghe/account/"), { view: "ghe", account: null, query: "" });
        assert.deepEqual(parseHash("#/ghe/account/%E0%A4%A"), { view: "ghe", account: null, query: "" });
        assert.equal(parseHash(`#/overview?q=${"y".repeat(90)}`).query, "y".repeat(80));
    });

    test("withActionStates overlays recorded decisions without mutating the model", () => {
        const model = syntheticModel();
        const snapshot = structuredClone(model);
        assert.equal(withActionStates(model, null), model);
        assert.equal(withActionStates(model, []), model);
        const a3 = { state: "done", at: "2026-10-26T10:00:00.000Z" };
        const applied = withActionStates(model, { "a-1": { state: "dismissed", at: "2026-10-27T08:00:00.000Z" }, "a-3": a3, "a-4": { state: "bogus" }, zz: { state: "done" } });
        assert.deepEqual(model, snapshot);
        const byId = Object.fromEntries(applied.actions.map(action => [action.id, action]));
        assert.deepEqual([byId["a-1"].state, byId["a-1"].state_at, byId["a-1"].new], ["dismissed", "2026-10-27T08:00:00.000Z", false]);
        assert.equal(byId["a-3"], model.actions[2]);
        assert.equal(byId["a-4"], model.actions[3]);
        const reopened = withActionStates(model, {}).actions.find(action => action.id === "a-3");
        assert.deepEqual([reopened.state, reopened.state_at], [null, null]);
        assert.deepEqual(ids(actionsFor(withActionStates(model, { "a-2": { state: "dismissed", at: null }, "a-3": a3 }))), ["a-5", "a-1", "a-4"]);
    });

    test("sortActions orders by priority, newness, risk, ARR, account name and id without mutating", () => {
        const item = (id, extra = {}) => ({ id, priority: "Medium", kind: "opportunity", new: false, arr: 0, account_name: "M", ...extra });
        const input = [
            item("u", { priority: "Urgent" }), item("z", { priority: "Info" }), item("m1", { arr: 10 }), item("m2", { arr: 20 }),
            item("m3", { kind: "risk" }), item("m5b", { new: true, account_name: "B" }), item("m5a", { new: true, account_name: "B" }),
            item("m4", { new: true, account_name: "A" }), item("n", { priority: "Notify", new: true }), item("m6", { arr: 20, account_name: "A" }),
            item("h", { priority: "High" }),
        ];
        const before = ids(input);
        assert.deepEqual(ids(sortActions(input)), ["h", "m4", "m5a", "m5b", "m3", "m6", "m2", "m1", "n", "z", "u"]);
        assert.deepEqual(ids(input), before);
    });

    test("actionsFor filters by family, account, kind and closed state", () => {
        const model = syntheticModel();
        const list = filter => ids(actionsFor(model, filter));
        assert.deepEqual(list(), ["a-2", "a-5", "a-1", "a-4"]);
        assert.deepEqual(list({ includeClosed: true }), ["a-2", "a-5", "a-1", "a-3", "a-4"]);
        assert.deepEqual(list({ family: "ghe" }), ["a-2", "a-5", "a-1"]);
        assert.deepEqual(list({ family: "ghcp" }), ["a-5", "a-4"]);
        assert.deepEqual(list({ family: "ghas" }), ["a-5"]);
        assert.deepEqual(list({ family: "ghas", includeClosed: true }), ["a-5", "a-3"]);
        assert.deepEqual(list({ accountId: "t-1" }), ["a-5", "a-1"]);
        assert.deepEqual(list({ kind: "risk" }), ["a-2", "a-5"]);
        assert.deepEqual(list({ kind: "opportunity" }), ["a-1", "a-4"]);
        assert.deepEqual(list({ accountId: "t-1", kind: "risk", includeClosed: true }), ["a-5", "a-3"]);
    });

    test("eventsFor filters and orders seat changes", () => {
        const model = syntheticModel();
        const list = filter => ids(eventsFor(model, filter));
        assert.deepEqual(list(), ["e-1", "e-5", "e-3", "e-2", "e-4"]);
        assert.deepEqual(list({ recentOnly: true, includeReverted: false, minPriority: "Notify" }), ["e-1", "e-5"]);
        assert.deepEqual(list({ family: "ghe" }), ["e-1", "e-3", "e-4"]);
        assert.deepEqual(list({ accountId: "t-1" }), ["e-5", "e-3", "e-2"]);
        assert.deepEqual(list({ minPriority: "Medium" }), ["e-1", "e-4"]);
        assert.deepEqual(list({ recentOnly: true }), ["e-1", "e-5", "e-3", "e-2"]);
    });

    test("productSeats and hasProduct follow the engine's product definitions", () => {
        const seats = (family, product) => productSeats({ products: { [family]: product } }, family);
        assert.deepEqual(seats("ghe", { contracted: 100, metered: 5 }), { value: 100, basis: "contracted" });
        assert.deepEqual(seats("ghe", { contracted: 0, metered: 0 }), { value: 0, basis: "metered" });
        assert.deepEqual(seats("ghe", { ubu: 7 }), { value: 7, basis: "billable" });
        assert.deepEqual(seats("ghas", { contracted: 0, committers: 12 }), { value: 12, basis: "committers" });
        assert.deepEqual(seats("ghas", { committers: 0 }), { value: null, basis: null });
        assert.deepEqual(seats("ghcp", { contracted: 30, assigned: 10 }), { value: 30, basis: "contracted" });
        assert.deepEqual(seats("ghcp", { assigned: 10 }), { value: 10, basis: "assigned" });
        assert.deepEqual(seats("other", { contracted: 5 }), { value: null, basis: null });
        assert.deepEqual(productSeats(null, "ghe"), { value: null, basis: null });
        const has = (family, product) => hasProduct({ products: { [family]: product } }, family);
        assert.equal(has("ghe", { contracted: 0, metered: 3 }), true);
        assert.equal(has("ghe", { ubu: 2 }), true);
        assert.equal(has("ghe", { contracted: 0, metered: 0 }), false);
        assert.equal(has("ghas", { committers: 4 }), true);
        assert.equal(has("ghas", {}), false);
        assert.equal(has("ghcp", { assigned: 1 }), true);
        assert.equal(has("ghcp", { contracted: 0, assigned: 0 }), false);
        assert.equal(has("other", { contracted: 5 }), false);
        assert.equal(hasProduct(null, "ghe"), false);
    });

    test("whitespace lists in-book accounts that could adopt a product, largest first", () => {
        const model = syntheticModel();
        assert.deepEqual(ids(whitespace(model, "ghe")), ["t-3"]);
        assert.deepEqual(ids(whitespace(model, "ghas")), ["t-2"]);
        assert.deepEqual(ids(whitespace(model, "ghcp")), ["t-2"]);
        model.accounts.push(syntheticAccount("t-4", "Delta Co", { arr: 900, in_book: false }), syntheticAccount("t-5", "Epsilon Co", { arr: 150 }),
            syntheticAccount("t-6", "Aardvark Co", { arr: 100 }));
        assert.deepEqual(ids(whitespace(model, "ghe")), ["t-5", "t-6", "t-3"]);
    });

    test("attentionIndex ranks accounts by their most urgent open item", () => {
        const model = syntheticModel();
        assert.deepEqual(Object.fromEntries(attentionIndex(model)), {
            "t-1": { rank: 1, actions: 2, top: "Medium" },
            "t-2": { rank: 0, actions: 1, top: "High" },
            "t-3": { rank: 0, actions: 1, top: "High" },
        });
        const closed = withActionStates(model, { "a-2": { state: "done", at: null }, "a-3": { state: "done", at: "2026-10-26T10:00:00.000Z" } });
        assert.equal(attentionIndex(closed).has("t-2"), false);
    });

    test("resolveAccount finds an id, an exact name or a unique partial name", () => {
        const model = syntheticModel();
        assert.equal(resolveAccount(model, "T-2"), "t-2");
        assert.equal(resolveAccount(model, "  alpha widgets "), "t-1");
        assert.equal(resolveAccount(model, "gadg"), "t-2");
        for (const text of ["a", "", null, "zeta"]) assert.equal(resolveAccount(model, text), null, String(text));
        model.accounts.push(syntheticAccount("t-4", "Alpha Widgets Europe"));
        assert.equal(resolveAccount(model, "alpha widgets"), "t-1");
        assert.equal(resolveAccount(model, "alpha"), null);
    });

    test("seriesPoints and seriesChange read both series shapes", () => {
        assert.deepEqual(seriesPoints({ start: "2026-10-30", values: [1, null, "3"] }), [["2026-10-30", 1], ["2026-10-31", null], ["2026-11-01", null]]);
        assert.deepEqual(seriesPoints({ points: [["2026-10-01", 5], ["bad", 1], "x", ["2026-10-03", NaN]] }), [["2026-10-01", 5], ["2026-10-03", null]]);
        for (const series of [null, {}, { start: "x", values: [1] }]) assert.deepEqual(seriesPoints(series), []);
        const daily = { start: "2026-10-01", values: [10, 12, 15, 20] };
        assert.deepEqual(seriesChange(daily, 2), { delta: 8, since: "2026-10-02", value: 20, as_of: "2026-10-04" });
        assert.equal(seriesChange(daily, 10), null);
        assert.equal(seriesChange({ start: "2026-10-01", values: [10] }, 1), null);
        assert.deepEqual(seriesChange({ points: [["2026-09-01", 4], ["2026-10-01", null], ["2026-10-20", 9]] }, 7), { delta: 5, since: "2026-09-01", value: 9, as_of: "2026-10-20" });
    });

    test("summarize returns a compact JSON-safe digest", () => {
        const model = syntheticModel();
        model.kpis.ghe.contracted = { value: 100, as_of: "2026-10-27", d7: -5, d7_since: "2026-10-20" };
        const summary = summarize(model, { limit: 2 });
        assert.deepEqual(Object.keys(summary), ["date", "generated_at", "owner", "data_status", "accounts", "arr", "renewing_within_120_days",
            "products", "open_actions", "top_actions", "recent_changes", "sources_needing_attention", "signals",
            "open_signals", "new_verified_high_signals", "top_signals"]);
        assert.deepEqual([summary.date, summary.owner, summary.data_status, summary.accounts, summary.arr, summary.renewing_within_120_days],
            ["2026-10-27", "Test Owner", "failed", 3, 600, 1]);
        assert.deepEqual(summary.open_actions, { High: 1, Medium: 2, Notify: 1 });
        assert.deepEqual(summary.top_actions, [
            { id: "a-2", priority: "High", kind: "risk", account: "Beta Gadgets", product: "All products", title: "Title a-2", why: "Why a-2", new: false },
            { id: "a-5", priority: "Medium", kind: "risk", account: "Alpha Widgets", product: "All products", title: "Title a-5", why: "Why a-5", new: true },
        ]);
        assert.deepEqual(summary.recent_changes.map(change => [change.account, change.product]), [["Gamma Tools", "GHE"], ["Alpha Widgets", "Copilot"]]);
        assert.deepEqual(summary.sources_needing_attention, [
            { source: "Metered feed", state: "Stale", as_of: "2026-10-20", notes: ["late <load>"] },
            { source: "Volume feed", state: "Not refreshed today", as_of: "2026-10-24", notes: [] },
            { source: "Health feed", state: "Not refreshed today", as_of: null, notes: [] },
            { source: "Committer feed", state: "No data", as_of: null, notes: [] },
        ]);
        assert.deepEqual(summary.products.ghe.contracted_seats, { value: 100, as_of: "2026-10-27", change: -5, change_since: "2026-10-20" });
        assert.equal(summary.products.ghe.metered_users, null);
        assert.deepEqual(summary.products.copilot.health_categories, { Red: 2, Green: 1 });
        assert.equal(summary.signals, 0);
        assert.equal(summarize(model).top_actions.length, 4);
        assert.deepEqual(JSON.parse(JSON.stringify(summary)), summary);
    });
});

describe("views.mjs", () => {
    test("esc escapes every HTML-significant character", () => {
        assert.equal(esc(`<a href="x">Tom & Jerry's</a>`), "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;");
        assert.equal(esc(null), "");
        assert.equal(esc(undefined), "");
        assert.equal(esc(0), "0");
    });

    test("formatters render missing values as a dash", () => {
        for (const value of [null, undefined, NaN, Infinity, "12"]) assert.equal(fmtInt(value), DASH);
        assert.equal(fmtInt(1234.6), "1,235");
        assert.deepEqual([0, 999, 1500, 2000, 222500, 1.25e6, -2500, 2e9].map(fmtMoney), ["$0", "$999", "$1.5k", "$2k", "$223k", "$1.3M", "\u2212$2.5k", "$2B"]);
        assert.equal(fmtMoney(null), DASH);
        assert.equal(fmtPct(0.456), "46%");
        assert.equal(fmtPct(0.456, 1), "45.6%");
        assert.equal(fmtPct(null), DASH);
        assert.equal(fmtDay("2026-10-26"), "Oct 26");
        assert.equal(fmtDay("2026-10-26", { year: true }), "Oct 26, 2026");
        assert.equal(fmtDay("2026-10-26T23:00:00Z"), "Oct 26");
        for (const value of ["bogus", 20261026, null]) assert.equal(fmtDay(value), DASH);
        assert.deepEqual([5, -1234, 0.4, -0.4].map(fmtSigned), ["+5", "\u22121,234", "0", "0"]);
        assert.equal(fmtSigned(null), "");
    });

    test("sparkline draws known points, breaks at gaps and needs two points", () => {
        for (const series of [null, { start: "2026-10-01", values: [] }, { start: "2026-10-01", values: [5] }, { start: "2026-10-01", values: [5, null] }]) {
            assert.equal(sparkline(series), "");
        }
        const svg = sparkline({ start: "2026-10-01", values: [1, 3, 2] }, { label: "Seats <GHE>" });
        assert.match(svg, /^<svg class="spark" viewBox="0 0 120 32"/);
        assert.ok(svg.includes('<path d="M0.0 29.0L60.0 3.0L120.0 16.0"/>'));
        assert.ok(svg.includes('aria-label="Seats &lt;GHE&gt;: 1 to 2 between Oct 1 and Oct 3"'));
        assert.ok(svg.includes("<title>Seats &lt;GHE&gt;: 1 to 2 between Oct 1 and Oct 3</title>"));
        assert.ok(sparkline({ start: "2026-10-01", values: [1, null, 3, 2] }).includes('d="M0.0 29.0M80.0 3.0L120.0 16.0"'));
        assert.ok(sparkline({ start: "2026-10-01", values: [4, 4] }).includes('d="M0.0 16.0L120.0 16.0"'));
    });

    test("topbar, nav and notices render without a model", () => {
        assert.match(renderTopbar(null), /No snapshot loaded/);
        assert.match(renderTopbar(null, { loading: true }), /Loading the snapshot…/);
        assert.match(renderTopbar(null, { refresh: { phase: "requested" } }), /data-command="refresh" aria-disabled="true">Refreshing…<\/button>/);
        assert.match(renderTopbar(null, { refresh: { phase: "error" } }), /data-command="refresh">Refresh<\/button>/);
        assert.ok(!renderTopbar(null).includes("pill"));
        const nav = renderNav(null, { view: "ghas" });
        assert.equal(occurrences(nav, "<a "), VIEWS.length);
        assert.ok(nav.includes('<a href="#/ghas" data-view="ghas" aria-current="page">GHAS</a>'));
        assert.ok(!nav.includes('class="count'));
        assert.equal(renderNotices(null), "");
        assert.equal(renderNotices(null, { error: "Lost <link>" }), '<div class="notice error" role="alert"><p>Lost &lt;link&gt;</p></div>');
    });

    test("refresh notices show progress, success and errors until dismissed", () => {
        const dismiss = '<button type="button" class="button tiny subtle" data-command="dismiss-refresh">Dismiss</button>';
        assert.equal(renderNotices(null, { refresh: { phase: "idle", message: "x" } }), "");
        assert.equal(renderNotices(null, { refresh: { phase: "requested", message: "Asking <now>" } }), '<div class="notice info" role="status"><p>Asking &lt;now&gt;</p></div>');
        assert.equal(renderNotices(null, { refresh: { phase: "error", message: "Failed & <x>" } }), `<div class="notice error" role="alert"><p>Failed &amp; &lt;x&gt;</p>${dismiss}</div>`);
        assert.equal(renderNotices(null, { refresh: { phase: "updated", message: "Done." } }), `<div class="notice success" role="status"><p>Done.</p>${dismiss}</div>`);
        assert.equal(renderNotices(null, { refresh: { phase: "error", message: "x" }, refreshDismissed: true }), "");
    });

    test("topbar and nav summarise sources, new High items and open actions", () => {
        const model = syntheticModel();
        const top = renderTopbar(model);
        assert.ok(top.includes('<a class="pill warn" href="#/data" data-view="data">4 sources need attention</a>'));
        assert.ok(top.includes(">1 new High</span>"));
        assert.ok(top.includes("Snapshot Oct 27, 2026 · built "));
        assert.ok(renderTopbar({ ...model, sources: model.sources.slice(0, 2) }).includes(">1 source needs attention</a>"));
        assert.ok(renderTopbar({ ...model, sources: model.sources.slice(0, 1) }).includes('<a class="pill ok" href="#/data" data-view="data">All sources current</a>'));
        const nav = renderNav(model, { view: "overview" });
        assert.ok(nav.includes('<a href="#/overview" data-view="overview" aria-current="page">Overview</a>'));
        assert.ok(nav.includes('data-view="ghe">GHE<span class="count high" title="3 open actions, 1 High">3</span></a>'));
        assert.ok(nav.includes('data-view="copilot">Copilot<span class="count" title="2 open actions">2</span></a>'));
        assert.ok(nav.includes('data-view="ghas">GHAS<span class="count" title="1 open action">1</span></a>'));
        assert.ok(nav.includes('data-view="signals">Signals</a>'));
        const withSignals = renderNav({ ...model, signals: [{ id: "s-1" }, null, "x", { id: "s-2" }] }, { view: "overview" });
        assert.ok(withSignals.includes('data-view="signals">Signals</a>'), "unverified signals do not count toward the badge");
        assert.ok(renderNav({ ...model, signals: [null, 7] }, { view: "overview" }).includes('data-view="signals">Signals</a>'));
    });

    test("source notices group unhealthy feeds by state and follow the product tab", () => {
        const model = syntheticModel();
        const notice = view => renderNotices(model, { view, now: NOW });
        const wrap = list => `<div class="notice warning" role="status"><p>Some data is not current. ${list} <a href="#/data" data-view="data">See data sources</a></p></div>`;
        assert.equal(notice("overview"), wrap("Stale: Metered feed. Not refreshed today: Volume feed, Health feed. No data: Committer feed."));
        assert.equal(notice("ghe"), wrap("Stale: Metered feed. Not refreshed today: Volume feed."));
        assert.equal(notice("copilot"), wrap("Not refreshed today: Health feed."));
        assert.equal(notice("ghas"), wrap("No data: Committer feed."));
        assert.equal(notice("signals"), "");
        assert.equal(notice("data"), "");
        assert.equal(renderNotices(model, { now: NOW }), "");
    });

    test("build, stale and baseline notices", () => {
        const model = syntheticModel();
        const build = { build_error: "boom <x>", generated_at: "2026-10-27T07:00:00+00:00" };
        assert.equal(renderNotices(model, { view: "data", now: NOW, build }),
            '<div class="notice error" role="alert"><p>The latest refresh could not rebuild the snapshot: boom &lt;x&gt; Showing the snapshot from Oct 27, 2026.</p></div>');
        assert.equal(renderNotices(model, { view: "data", now: NOW, build: { ...build, generated_at: "2026-10-27T05:00:00+00:00" } }), "");
        assert.ok(renderNotices(model, { view: "data", now: NOW, build: { ...build, build_error: "y".repeat(400) } }).includes(`${"y".repeat(300)} Showing`));
        assert.equal(renderNotices(model, { view: "data", now: new Date(2026, 9, 30, 9) }),
            '<div class="notice warning" role="status"><p>This snapshot is from Oct 27, 2026, 3 days ago. Use Refresh to rebuild it.</p></div>');
        assert.equal(renderNotices(model, { view: "data", now: new Date(2026, 9, 28, 9) }), "");
        const baseline = { ...model, baseline: true, sources: [] };
        for (const view of VIEWS) assert.equal(renderNotices(baseline, { view, now: NOW }).includes("First snapshot:"), PANEL_KEYS.includes(view), view);
    });

    test("action panels filter by kind, show closed actions on request and offer reopen", () => {
        const model = syntheticModel();
        const panel = ui => section(renderView(model, { view: "overview", now: NOW, ...ui }), "h-actions-overview");
        assert.deepEqual(actionIds(panel({})), ["a-2", "a-5", "a-1", "a-4"]);
        const risks = panel({ kind: { overview: "risk" } });
        assert.deepEqual(actionIds(risks), ["a-2", "a-5"]);
        assert.ok(risks.includes('data-filter="kind:overview" data-value="risk" aria-pressed="true"'));
        assert.deepEqual(actionIds(panel({ kind: { overview: "opportunity" } })), ["a-1", "a-4"]);
        const closed = panel({ showClosed: true });
        assert.deepEqual(actionIds(closed), ["a-2", "a-5", "a-1", "a-3", "a-4"]);
        assert.ok(closed.includes('<li class="action-row priority-medium closed">'));
        assert.ok(closed.includes('<span class="state">Done Oct 26</span><button type="button" class="button tiny subtle" data-action="reopen" data-id="a-3" data-focus-key="reopen:a-3">Reopen</button>'));
        assert.ok(closed.includes('data-toggle="closed" data-focus-key="closed:overview" checked>'));
        assert.deepEqual(actionIds(section(renderView(model, { view: "ghe", now: NOW }), "h-actions-ghe")), ["a-2", "a-5", "a-1"]);
        assert.ok(section(renderView(model, { view: "ghas", now: NOW, kind: { ghas: "opportunity" } }), "h-actions-ghas").includes('<p class="empty">No open opportunities for GHAS.</p>'));
    });

    test("long lists stop at their limits and expand with Show all", () => {
        const model = bigModel();
        const overview = renderView(model, { view: "overview", now: NOW });
        assert.equal(rowIds(section(overview, "h-accounts")).length, ROW_LIMIT);
        assert.equal(moreButton(overview, "table:accounts"), "Show all 60 accounts (collapsed)");
        assert.equal(actionIds(section(overview, "h-actions-overview")).length, 8);
        assert.equal(moreButton(overview, "actions:overview"), "Show all 12 (collapsed)");
        assert.equal(occurrences(section(overview, "h-changes-overview"), '<li class="change-row'), 8);
        assert.equal(moreButton(overview, "changes:overview"), "Show all 9 (collapsed)");
        assert.equal(occurrences(section(overview, "h-renewals"), "<li><div>"), 6);
        assert.equal(moreButton(overview, "renewals"), "Show all 7 (collapsed)");
        const expanded = renderView(model, { view: "overview", now: NOW, expanded: new Set(["table:accounts", "actions:overview", "changes:overview", "renewals"]) });
        assert.equal(rowIds(section(expanded, "h-accounts")).length, 60);
        assert.equal(actionIds(section(expanded, "h-actions-overview")).length, 12);
        assert.equal(occurrences(section(expanded, "h-changes-overview"), '<li class="change-row'), 9);
        assert.equal(occurrences(section(expanded, "h-renewals"), "<li><div>"), 7);
        for (const key of ["table:accounts", "actions:overview", "changes:overview", "renewals"]) assert.equal(moreButton(expanded, key), "Show fewer (expanded)");
        const ghe = renderView(model, { view: "ghe", now: NOW });
        assert.equal(rowIds(section(ghe, "h-table-ghe")).length, ROW_LIMIT);
        assert.equal(moreButton(ghe, "table:ghe"), "Show all 60 accounts (collapsed)");
        assert.equal(moreButton(ghe, "actions:ghe"), "Show all 12 (collapsed)");
        assert.equal(moreButton(ghe, "changes:ghe"), null);
        assert.ok(section(ghe, "h-ws-ghe").includes("Every account already has it."));
    });

    test("account tables sort by any column and keep missing values last", () => {
        const model = syntheticModel();
        const order = sort => rowIds(section(renderView(model, { view: "overview", now: NOW, sort: sort ? { accounts: sort } : undefined }), "h-accounts"));
        assert.deepEqual(order(), ["t-2", "t-3", "t-1"]);
        assert.deepEqual(order({ key: "arr", dir: -1 }), ["t-1", "t-2", "t-3"]);
        assert.deepEqual(order({ key: "arr", dir: 1 }), ["t-3", "t-2", "t-1"]);
        assert.deepEqual(order({ key: "name", dir: -1 }), ["t-3", "t-2", "t-1"]);
        assert.deepEqual(order({ key: "bogus", dir: 1 }), ["t-1", "t-2", "t-3"]);
        assert.deepEqual(order({ key: "renewal", dir: -1 }), ["t-1", "t-2", "t-3"]);
        assert.ok(renderView(model, { view: "overview", now: NOW, sort: { accounts: { key: "arr", dir: -1 } } }).includes('aria-sort="descending"'));
    });

    test("search filters account tables and escapes the query", () => {
        const model = syntheticModel();
        const overview = renderView(model, { view: "overview", now: NOW, query: "  BETA " });
        assert.deepEqual(rowIds(section(overview, "h-accounts")), ["t-2"]);
        assert.ok(overview.includes('Accounts <span class="count-text">1</span>'));
        assert.deepEqual(rowIds(section(renderView(model, { view: "overview", now: NOW, query: "testland" }), "h-accounts")).length, 3);
        const none = renderView(model, { view: "overview", now: NOW, query: '<x> & "y"' });
        assert.ok(none.includes("No accounts match “&lt;x&gt; &amp; &quot;y&quot;”."));
        assert.ok(none.includes('value="&lt;x&gt; &amp; &quot;y&quot;"'));
        assert.ok(renderView(model, { view: "copilot", subview: "seats", now: NOW, query: "zzz" }).includes("No Copilot accounts match “zzz”."));
        model.accounts[0].products.ghcp = {};
        assert.ok(renderView(model, { view: "copilot", subview: "seats", now: NOW }).includes("No accounts with Copilot yet."));
    });

    test("the Copilot view shows health chips and never reads an unreported funnel step as zero", () => {
        const html = renderView(syntheticModel(), { view: "copilot", subview: "seats", now: NOW });
        assert.ok(html.includes('<span class="health Red" title="2 accounts with Red Copilot health">2 Red</span>'
            + '<span class="health Green" title="1 account with Green Copilot health">1 Green</span>'));
        const funnel = section(html, "h-funnel");
        assert.deepEqual(funnelStep(funnel, "Assigned"), ["10", ""]);
        assert.ok(!funnel.includes("Installed"));
        assert.deepEqual(funnelStep(funnel, "Active 28d"), ["4", ""]);
        assert.deepEqual(funnelStep(funnel, "Engaged 28d"), ["3", ""]);
        assert.ok(funnel.includes("A dash means not reported."));
        assert.ok(section(html, "h-mix").includes('<span class="health Red">Red 2</span>'));
    });

    test("the drawer shows the account, every action and a compact funnel", () => {
        const model = syntheticModel();
        const html = renderDrawer(model, { account: "t-1" });
        assert.ok(html.includes('<h2 id="drawer-title">Alpha Widgets</h2>'));
        assert.ok(html.includes('<a class="button subtle tiny" href="https://crm.example.invalid/t-1" target="_blank" rel="noopener noreferrer">Open in Salesforce</a>'));
        assert.ok(html.includes('Actions <span class="count-text">2</span>'));
        assert.deepEqual(actionIds(html), ["a-5", "a-1", "a-3"]);
        assert.ok(html.includes("Installed IDE users 28d (partial)"));
        assert.deepEqual(funnelStep(html, "Active 28d"), ["4", ""]);
        assert.ok(html.includes('<span style="width:40.00%"></span></span><span class="funnel-value">4</span>'));
        assert.ok(renderDrawer(model, { account: "nope" }).includes('<h2 id="drawer-title">Account not found</h2>'));
        model.accounts[1].sfdc_url = "javascript:alert(1)";
        assert.ok(!renderDrawer(model, { account: "t-2" }).includes("Open in Salesforce"));
    });

    test("signals render safely, newest first, with an empty state", () => {
        assert.ok(renderView(syntheticModel(), { view: "signals" }).includes("No signals yet"));
        const model = hostileModel();
        model.signals.push("noise");
        const html = renderView(model, { view: "signals" });
        assert.equal(occurrences(html, '<li class="signal">'), 2);
        assert.ok(html.indexOf("Launch &lt;Orbit&gt;") < html.indexOf("No &lt;Orbit&gt; link"));
        assert.ok(html.includes('<a href="https://news.example.invalid/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">Launch &lt;Orbit&gt;</a>'));
        assert.ok(html.includes('<p class="title">No &lt;Orbit&gt; link</p>'));
        assert.ok(!html.includes("javascript:"));
        assert.ok(html.includes('<span class="family">News &lt;wire&gt;</span>'));
        assert.ok(html.includes('<span class="chip">AI</span>') && !html.includes('<span class="chip">7</span>'));
        assert.ok(html.includes('data-account="t-3"'));
        assert.ok(html.includes('<p class="who">Unlisted &lt;Orbit&gt; Co</p>'));
        assert.equal(occurrences(html, '<span class="tag tag-high">High</span>'), 1);
        assert.ok(!html.includes("tag-bogus"));
    });

    test("the data view lists every source with its state and escaped notes", () => {
        const html = renderView(syntheticModel(), { view: "data" });
        for (const label of ["Book feed", "Metered feed", "Volume feed", "Health feed", "Committer feed"]) assert.ok(html.includes(`<th scope="row">${label}</th>`), label);
        assert.ok(html.includes('<span class="state-chip stale">Stale</span>'));
        assert.ok(html.includes("late &lt;load&gt;"));
    });

    test("renderUnavailable explains a missing or unusable snapshot", () => {
        assert.equal(renderUnavailable({ code: "dashboard_missing", message: "No <file>" }),
            '<section class="panel empty-state"><h1>No snapshot yet</h1><p>No &lt;file&gt;</p><p>Use Refresh to run the daily automation, or run it from the Automations view.</p></section>');
        assert.equal(renderUnavailable({ code: "dashboard_invalid", message: "Bad & <json>" }),
            '<section class="panel empty-state"><h1>The snapshot is unavailable</h1><p>Bad &amp; &lt;json&gt;</p></section>');
        assert.equal(renderUnavailable(null), '<section class="panel empty-state"><h1>The snapshot is unavailable</h1><p>Unknown error.</p></section>');
    });

    test("every view, notice bar and drawer escapes hostile text in every UI state", () => {
        const model = hostileModel();
        sweep(model, "synthetic model");
        assert.ok(renderView(model, { view: "overview", now: NOW }).includes("Gamma &amp; &lt;Orbit&gt; Tools"));
        assert.ok(renderDrawer(model, { account: "t-2" }).includes("Part of Parent &lt;Orbit&gt; Group"));
        sweep(bigModel(), "large model");
    });
});

describe("Python engine → canvas contract", { skip: NO_PYTHON }, () => {
    let home;
    let model;
    before(() => {
        home = tempDir();
        model = buildPulseHome(home);
    });
    after(() => removeDir(home));

    test("validateModel accepts the engine's snapshot and summarize digests it", () => {
        assert.equal(validateModel(model), model);
        assert.equal(model.date, "2026-10-27");
        assert.ok(model.accounts.some(account => account.name === "Nimbus & <Orbit> Ltd"));
        const summary = summarize(model);
        assert.equal(summary.date, model.date);
        const open = model.actions.filter(action => !action.state);
        assert.deepEqual(summary.open_actions, Object.fromEntries(["High", "Medium", "Notify"].map(priority => [priority, open.filter(action => action.priority === priority).length])));
        assert.equal(summary.top_actions.length, Math.min(8, open.length));
        assert.equal(summary.sources_needing_attention.length, model.sources.filter(source => source.state !== "ok").length);
        assert.deepEqual(JSON.parse(JSON.stringify(summary)), summary);
    });

    test("the engine leaves unreported Copilot funnel steps null, never zero", () => {
        const funnels = model.accounts.map(account => account.products.ghcp.funnel);
        const reported = funnels.filter(funnel => typeof funnel?.assigned === "number");
        assert.ok(reported.length > 0);
        for (const funnel of funnels) assert.deepEqual([funnel.installed_1d28, funnel.active_1d28], [null, null]);
        for (const funnel of reported) assert.equal(typeof funnel.engaged_1d28, "number");
    });

    test("every view, notice bar and drawer renders cleanly from the snapshot", () => {
        sweep(model, "engine model");
        const items = Object.fromEntries(model.actions.slice(0, 3).map((action, index) => [action.id, { state: index ? "dismissed" : "done", at: "2026-10-27T09:00:00.000Z" }]));
        sweep(withActionStates(model, items), "engine model with closed actions");
    });

    test("the hostile account name is escaped and searchable", () => {
        const nimbus = model.accounts.find(account => account.name === "Nimbus & <Orbit> Ltd");
        assert.ok(renderView(model, { view: "overview", now: NOW }).includes("Nimbus &amp; &lt;Orbit&gt; Ltd"));
        assert.ok(renderDrawer(model, { account: nimbus.id }).includes('<h2 id="drawer-title">Nimbus &amp; &lt;Orbit&gt; Ltd</h2>'));
        assert.deepEqual(rowIds(section(renderView(model, { view: "overview", now: NOW, query: "orbit" }), "h-accounts")), [nimbus.id]);
        assert.equal(resolveAccount(model, "nimbus"), nimbus.id);
    });

    test("action panels list every engine action by kind", () => {
        const expanded = new Set(["actions:overview"]);
        assert.deepEqual(actionIds(section(renderView(model, { view: "overview", now: NOW, expanded }), "h-actions-overview")), ids(actionsFor(model)));
        for (const kind of ["risk", "opportunity"]) {
            assert.ok(actionsFor(model, { kind }).length > 0, kind);
            const panel = section(renderView(model, { view: "overview", now: NOW, expanded, kind: { overview: kind } }), "h-actions-overview");
            assert.deepEqual(actionIds(panel), ids(actionsFor(model, { kind })), kind);
        }
    });

    test("the overview shows one data warning that names each state once", () => {
        const html = renderNotices(model, { view: "overview", now: NOW });
        assert.equal(occurrences(html, "Some data is not current."), 1);
        const unhealthy = model.sources.filter(source => source.state !== "ok");
        assert.ok(unhealthy.some(source => source.state === "failed"));
        for (const label of new Set(unhealthy.map(source => SOURCE_STATES[source.state]))) assert.equal(occurrences(html, `${label}:`), 1, label);
        for (const source of unhealthy) assert.equal(occurrences(html, esc(source.label)), 1, source.label);
    });

    test("product tabs warn only about the feeds behind them", () => {
        const notice = view => renderNotices(model, { view, now: NOW });
        assert.ok(notice("copilot").includes("Some data is not current. Not refreshed today: Copilot health, Copilot seats. <a"));
        assert.ok(notice("ghe").includes("Some data is not current. Not refreshed today: GHE metered users, GHE billable users. <a"));
        assert.ok(notice("ghas").includes("Some data is not current. Not refreshed today: GHAS committers. <a"));
        assert.ok(notice("overview").includes("Account attributes"));
        for (const view of ["copilot", "ghe", "ghas", "signals", "data"]) assert.ok(!notice(view).includes("Account attributes"), view);
        assert.ok(!notice("signals").includes("Some data is not current."));
    });

    test("the Copilot view and drawer show a dash for unreported funnel steps", () => {
        const account = model.accounts.find(item => typeof item.products.ghcp.funnel?.assigned === "number");
        const { funnel } = account.products.ghcp;
        const view = renderView(model, { view: "copilot", subview: "seats", now: NOW });
        const steps = section(view, "h-funnel");
        assert.ok(!steps.includes("Installed"));
        assert.deepEqual(funnelStep(steps, "Active 28d"), [DASH, ""]);
        const licenseTotal = model.accounts.filter(account => hasProduct(account, "ghcp")).reduce((sum, account) => sum + (account.products.ghcp.contracted || 0), 0);
        assert.deepEqual(funnelStep(steps, "Engaged 28d"), [fmtInt(funnel.engaged_1d28), `${((funnel.engaged_1d28 / licenseTotal) * 100).toFixed(0)}%`]);
        const drawer = renderDrawer(model, { account: account.id });
        assert.deepEqual(funnelStep(drawer, "Assigned"), [fmtInt(funnel.assigned), ""]);
        assert.ok(drawer.includes("Installed IDE users 28d (partial)"));
        assert.deepEqual(funnelStep(drawer, "Active 28d"), [DASH, ""]);
        const red = model.kpis.ghcp.categories.Red;
        assert.ok(view.includes(`<span class="health Red" title="${red} ${red === 1 ? "account" : "accounts"} with Red Copilot health">${red} Red</span>`));
    });
});
