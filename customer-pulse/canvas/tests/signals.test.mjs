// Synthetic-only coverage of signal rendering, local decisions and the existing browser action flow.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { test } from "node:test";
import * as modelHelpers from "../model.mjs";
import { newHighCount, renderDrawer, renderNav, renderTopbar, renderView } from "../views.mjs";
import { createRefreshCoordinator, pulsePaths, readActionStates, setActionState, startCanvasServer } from "../server.mjs";
import { CANVAS, readJson, removeDir, request, syntheticModel, tempDir, writeJson } from "./helpers.mjs";

const { signalsFor, summarize, withActionStates } = modelHelpers;
const signal = (id, fields = {}) => ({
    id: `sig:${id}`, account_id: "t-1", account_name: "Alpha Widgets", source: "gong", date: "2026-10-27",
    title: `Signal ${id}`, evidence: "Synthetic engineering priority quote.", url: "https://example.invalid/evidence",
    source_id: `source-${id}`, competitors: ["Example Rival"], themes: ["developer_productivity"],
    play: "Explore a synthetic pilot", priority: "High", reason: "Synthetic team is evaluating platforms.",
    confidence: .9, verified: true, verification: "verified", status: "open", new: true,
    first_seen: "2026-10-27", ...fields,
});
function fixture() {
    return { ...syntheticModel(), signals: [
        signal("high"),
        signal("notify", { account_id: "t-2", account_name: "Beta Gadgets", source: "web", priority: "Notify", date: "2026-10-26" }),
        signal("info", { account_id: "t-3", account_name: "Gamma Tools", source: "zoominfo", priority: "Info" }),
        signal("unverified", { verified: false, verification: "needs verification" }),
        signal("done", { status: "done" }),
        signal("dismissed", { status: "dismissed" }),
        signal("old", { new: false, date: "2026-10-25" }),
    ] };
}
const ids = rows => rows.map(row => row.id);
const feed = (model, ui = {}) => renderView(model, { view: "signals", ...ui });
const rows = html => [...html.matchAll(/data-action="(?:done|reopen)" data-id="([^"]+)"/g)].map(match => match[1]);
const section = (html, heading) => html.slice(html.indexOf(heading), html.indexOf("</section>", html.indexOf(heading)));

test("signals are optional on older snapshots and never mutate while sorting", () => {
    const model = fixture();
    const before = JSON.stringify(model);
    assert.equal(signalsFor(model).length, 5);
    assert.equal(JSON.stringify(model), before);
    assert.ok(signalsFor(model).findIndex(item => item.id === "sig:notify") < signalsFor(model).findIndex(item => item.id === "sig:old"));
    delete model.signals;
    assert.deepEqual(signalsFor(model), []);
    assert.match(feed(model), /No signals yet/);
});

test("signal feed combines priority, source, account search and explicit status filters", () => {
    const model = fixture();
    assert.deepEqual(ids(signalsFor(model, { priority: "Notify", source: "web", query: "BETA" })), ["sig:notify"]);
    assert.deepEqual(ids(signalsFor(model, { priority: "Info", source: "zoominfo" })), ["sig:info"]);
    assert.deepEqual(ids(signalsFor(model, { status: "done" })), ["sig:done"]);
    assert.deepEqual(ids(signalsFor(model, { status: "dismissed" })), ["sig:dismissed"]);
    assert.equal(signalsFor(model, { status: "all" }).length, 7);
    assert.equal(signalsFor(model, { source: "web", priority: "High" }).length, 0);
    assert.deepEqual(rows(feed(model, { signalFilters: { source: "web", priority: "Notify", status: "open" }, query: "Beta" })), ["sig:notify"]);
});

test("account search resolves canonical names, fallback names and ids, not evidence", () => {
    const model = fixture();
    model.signals = [signal("one", { account_name: undefined, name: "Alternate Synthetic Name" })];
    for (const query of ["Alpha", "t-1", "alternate", "  ALPHA  "]) assert.equal(signalsFor(model, { query }).length, 1);
    assert.deepEqual(signalsFor(model, { query: "engineering priority quote" }), []);
    assert.deepEqual(signalsFor(model, { accountId: "t-2" }), []);
});

test("new High classification requires explicit verification, newness and open status", () => {
    const model = fixture();
    model.signals.push(signal("contradictory", { verification: "needs verification" }),
        signal("missing-verification", { verified: undefined }), signal("string-verification", { verified: "true" }));
    assert.deepEqual(ids(signalsFor(model, { newHigh: true })), ["sig:high"]);
});

test("feed shows evidence quote, competitor/theme chips, play, reason and verification", () => {
    const html = feed(fixture());
    for (const text of ["<blockquote", "Synthetic engineering priority quote.", 'title="Competitor">Example Rival',
        'title="Theme">developer_productivity', "<strong>Play:</strong>", "<strong>Reason:</strong>", "Needs verification", ">Verified</span>"]) {
        assert.ok(html.includes(text), text);
    }
    assert.match(html, /data-action="done" data-id="sig:high"/);
    assert.match(html, /data-action="dismiss" data-id="sig:high"/);
    assert.ok(!html.includes('data-id="sig:done"'));
});

test("all signal text and URLs are escaped in the feed, overview and drawer", () => {
    const model = fixture();
    model.signals = [signal('hostile"&', { evidence: '<script>alert("quote")</script>', title: "<img onerror=bad>",
        play: "<play>", reason: "<reason>", competitors: ["<rival>", null], themes: ['<theme "id">'],
        account_name: "<account>", url: "javascript:alert(1)" })];
    for (const html of [feed(model), renderView(model, { view: "overview" }), renderDrawer(model, { account: "t-1" })]) {
        assert.ok(!html.includes("<script>") && !html.includes("<img ") && !html.includes("javascript:"));
        assert.ok(html.includes("&lt;script&gt;") && html.includes("&lt;rival&gt;") && html.includes("&lt;play&gt;"));
        assert.ok(html.includes('data-id="sig:hostile&quot;&amp;"'));
    }
});

test("Done and Dismissed filters expose Reopen through the shared controls", () => {
    for (const [status, id, text] of [["done", "sig:done", "Done"], ["dismissed", "sig:dismissed", "Dismissed"]]) {
        const html = feed(fixture(), { signalFilters: { status } });
        assert.deepEqual(rows(html), [id]);
        assert.ok(html.includes(`<span class="state">${text}</span>`));
        assert.ok(html.includes(`data-action="reopen" data-id="${id}"`));
        assert.match(html, new RegExp(`<option value="${status}" selected>`));
    }
});

test("signal filter controls are labelled and empty filtered results keep controls", () => {
    const html = feed(fixture(), { signalFilters: { priority: "Info", source: "gong" }, query: 'x"<tag>' });
    for (const text of ["Priority<select", "Source<select", "Status<select", "Account search<input", "No signals match these filters.",
        'value="x&quot;&lt;tag&gt;"', "data-region", 'data-focus-key="search:signals"']) assert.ok(html.includes(text), text);
});

test("feed and overview long lists use the existing show-all mechanism", () => {
    const model = fixture();
    model.signals = Array.from({ length: 55 }, (_, index) => signal(`many-${index}`));
    assert.equal(rows(feed(model)).length, 50);
    assert.equal(rows(feed(model, { expanded: new Set(["signals:feed"]) })).length, 55);
    assert.equal(rows(section(renderView(model, { view: "overview" }), 'id="h-new-signals"')).length, 8);
    assert.equal(rows(section(renderView(model, { view: "overview", expanded: new Set(["signals:overview"]) }), 'id="h-new-signals"')).length, 55);
});

test("Overview highlights only new verified High open signals and badge counts agree", () => {
    const model = fixture();
    const overview = renderView(model, { view: "overview" });
    assert.deepEqual(rows(section(overview, 'id="h-new-signals"')), ["sig:high"]);
    assert.match(renderNav(model), /Signals<span class="count high" title="1 new verified High open signals">1/);
    const baseline = newHighCount(syntheticModel());
    assert.equal(newHighCount(model), baseline + 1);
    assert.ok(renderTopbar(model).includes(`>${baseline + 1} new High</span>`));
    model.signals[0].status = "done";
    assert.equal(newHighCount(model), baseline);
    assert.match(renderNav(model), /data-view="signals">Signals<\/a>/);
});

test("account drawers scope signals to that account and allow closed items to reopen", () => {
    const html = renderDrawer(fixture(), { account: "t-1" });
    const signals = section(html, "<h3>Signals ");
    assert.deepEqual(rows(signals), ["sig:dismissed", "sig:done", "sig:high", "sig:unverified", "sig:old"]);
    assert.ok(!signals.includes("Signal notify") && !signals.includes("Signal info"));
    assert.match(html, /data-copilot-account="t-1"/);
    assert.match(renderDrawer(fixture(), { account: "t-2" }), /Signal notify/);
});

test("signal source statuses show Not connected, freshness and escaped notes", () => {
    const model = fixture();
    model.sources.push({ feed: "signals_gong", label: "Gong", state: "not_connected", as_of: null, messages: ["Connect <source>"] },
        { feed: "signals_web", label: "Web", state: "ok", as_of: model.date, messages: [] },
        { feed: "signals_zoominfo", label: "ZoomInfo", state: "failed", messages: [] });
    const html = feed(model);
    for (const text of ["Gong", "Web", "ZoomInfo", "Not connected", "Current", "Not refreshed today", "As of Oct 27", "Connect &lt;source&gt;"]) {
        assert.ok(html.includes(text), text);
    }
    assert.match(renderView(model, { view: "data" }), /Not connected/);
    assert.equal(summarize(model).sources_needing_attention.find(source => source.source === "Gong").state, "Not connected");
    assert.equal((feed(syntheticModel()).match(/Not connected/g) || []).length, 3);
});

test("engine-shaped disconnected feeds with optional and null qid render open signals", () => {
    const model = syntheticModel();
    const sources = ["gong", "web", "zoominfo"];
    model.signals = sources.map(source => signal(`engine-${source}`, {
        source, state: null, status: "open", new: true,
    }));
    model.sources.push(...sources.map(source => ({
        feed: `signals_${source}`, label: `${source[0].toUpperCase()}${source.slice(1)} signals`,
        state: "not_connected", as_of: null, messages: ["Not connected"], optional: true,
        qid: null, raw_date: null, via: "session-log", held: [], rows: 1,
    })));
    modelHelpers.validateModel(model);
    const html = feed(model);
    assert.deepEqual(rows(html).sort(), model.signals.map(item => item.id).sort());
    assert.equal((html.match(/class="state-chip not_connected">Not connected/g) || []).length, 3);
    assert.equal((html.match(/data-action="done"/g) || []).length, 3);
    assert.equal((html.match(/data-action="dismiss"/g) || []).length, 3);
    assert.ok(!html.includes('data-action="reopen"'));
    const data = renderView(model, { view: "data" });
    for (const source of sources) {
        assert.ok(data.includes(`${source[0].toUpperCase()}${source.slice(1)} signals`));
    }
    assert.equal((data.match(/class="state-chip not_connected">Not connected/g) || []).length, 3);
    const summary = summarize(model);
    assert.equal(summary.new_verified_high_signals, 3);
    assert.equal(summary.top_signals.length, 3);
    assert.ok(summary.top_signals.every(item => item.new && item.status === "open"));
    assert.ok(model.signals.every(item => item.state === null && item.status === "open" && item.new === true));
});

test("get_summary carries actionable signal ids, evidence, verification and counts", () => {
    const summary = summarize(fixture(), { limit: 2 });
    assert.equal(summary.signals, 7);
    assert.deepEqual(summary.open_signals, { High: 3, Notify: 1, Info: 1 });
    assert.equal(summary.new_verified_high_signals, 1);
    assert.equal(summary.top_signals.length, 2);
    assert.ok(summary.top_signals.every(item => item.status === "open" && item.id.startsWith("sig:") && item.evidence && item.play && item.reason));
    const unverified = summarize(fixture()).top_signals.find(item => item.id === "sig:unverified");
    assert.equal(unverified.verification, "needs verification");
    assert.equal(unverified.verified, false);
    assert.deepEqual(JSON.parse(JSON.stringify(summary)), summary);
});

test("extension get_summary applies saved signal decisions and describes signal actions", async () => {
    let canvas;
    const source = fs.readFileSync(path.join(CANVAS, "extension.mjs"), "utf8").replace(/^import[\s\S]*?;\n/gm, "");
    const context = vm.createContext({
        ...modelHelpers, CanvasError: class extends Error {},
        createCanvas: definition => { canvas = definition; return definition; },
        joinSession: async () => ({}), pulsePaths: () => ({}),
        createRefreshCoordinator: () => ({ observe: () => {}, status: () => ({ phase: "idle" }) }),
        readModel: async () => ({ model: fixture(), revision: "synthetic" }),
        readBuildStatus: async () => null,
        readActionStates: async () => ({ "sig:high": { state: "done" }, "sig:done": { state: "done" },
            "sig:dismissed": { state: "dismissed" } }),
    });
    await vm.runInContext(`(async () => { ${source} })()`, context);
    const summary = await canvas.actions.find(action => action.name === "get_summary").handler();
    assert.equal(summary.new_verified_high_signals, 0);
    assert.ok(!summary.top_signals.some(item => item.id === "sig:high"));
    assert.match(canvas.actions.find(action => action.name === "get_summary").description, /verification/);
    assert.match(canvas.actions.find(action => action.name === "set_action_state").description, /signal/);
});

test("local state overlays both actions and signals without mutating the model", () => {
    const model = fixture();
    const before = JSON.stringify(model);
    const at = "2026-10-27T07:00:00Z";
    const result = withActionStates(model, { "sig:high": { state: "done", at }, "sig:notify": { state: "dismissed", at },
        "sig:done": { state: "done", at }, "sig:dismissed": { state: "dismissed", at }, "a-2": { state: "done", at } });
    assert.equal(result.signals[0].status, "done");
    assert.equal(result.signals[0].state_at, at);
    assert.equal(result.signals[0].new, false);
    assert.equal(result.signals[1].status, "dismissed");
    assert.equal(result.actions.find(item => item.id === "a-2").state, "done");
    assert.equal(summarize(result).new_verified_high_signals, 0);
    assert.equal(JSON.stringify(model), before);
    const reopened = withActionStates(model, {});
    assert.equal(reopened.signals[0].status, "open");
    assert.equal(withActionStates(model, { "sig:high": { state: "invalid" } }).signals[0].status, "open");
});

test("signal decisions persist through guarded HTTP API, reload and reopen", async t => {
    const home = tempDir();
    const paths = pulsePaths(home);
    writeJson(paths.model, fixture());
    const refresh = createRefreshCoordinator({ modelPath: paths.model, launch: async () => {} });
    const server = await startCanvasServer({ paths, refresh, port: 0 });
    t.after(async () => { await server.close(); removeDir(home); });
    const page = await request(server, "GET", "/");
    const token = page.text.match(/name="csrf-token" content="([^"]+)"/)[1];
    const headers = { "content-type": "application/json", origin: `http://127.0.0.1:${server.port}`, "x-canvas-token": token };
    const post = (id, state, hdr = headers) => request(server, "POST", "/api/action-state", { headers: hdr, body: JSON.stringify({ id, state }) });
    assert.equal((await post("sig:high", "done", {})).status, 403);
    for (const state of ["done", "dismissed", null]) {
        const response = await post("sig:high", state);
        assert.equal(response.status, 200);
        const loaded = (await request(server, "GET", "/api/model")).json.model;
        assert.equal(loaded.signals[0].status, state || "open");
        assert.equal(loaded.signals[0].new, state ? false : true);
        assert.equal(readJson(paths.actioned).items["sig:high"]?.state ?? null, state);
    }
    assert.equal((await post("sig:unknown", "done")).status, 404);
    assert.equal((await post("sig:high", "open")).status, 400);
    const embedded = await request(server, "GET", "/copilot/?embed=1");
    assert.equal(embedded.status, 200);
    assert.match(embedded.text, /app\.js/);
    const missingPage = await request(server, "GET", "/copilot/nowhere", { headers: { Accept: "text/html,*/*" } });
    assert.equal(missingPage.status, 404);
    assert.match(missingPage.headers["content-type"], /^text\/html/);
    assert.match(missingPage.text, /<meta name="canvas-error" content="[^"]+">/);
    assert.doesNotMatch(missingPage.text, /not_found/);
    assert.equal((await request(server, "GET", "/copilot/nowhere")).json.code, "not_found");
});

test("concurrent action and signal writes preserve each other and unreadable state is held back", async t => {
    const home = tempDir();
    t.after(() => removeDir(home));
    const paths = pulsePaths(home);
    writeJson(paths.model, fixture());
    await Promise.all([setActionState({ paths, id: "sig:high", state: "done" }),
        setActionState({ paths, id: "a-2", state: "dismissed" })]);
    assert.equal((await readActionStates(paths.actioned))["sig:high"].state, "done");
    assert.equal((await readActionStates(paths.actioned))["a-2"].state, "dismissed");
    fs.writeFileSync(paths.actioned, "{broken");
    await assert.rejects(setActionState({ paths, id: "sig:high", state: null }), { code: "action_state_unreadable" });
    assert.equal(fs.readFileSync(paths.actioned, "utf8"), "{broken");
});

// Run the actual browser mutation/listener functions without booting network polling or needing a DOM package.
function browserHarness(fetch) {
    const listeners = {};
    class Select {
        constructor(key, value) { this.key = key; this.value = value; }
        matches() { return true; }
        getAttribute() { return this.key; }
    }
    const source = fs.readFileSync(path.join(CANVAS, "app.js"), "utf8").replace(/^import .*;\n/gm, "").split("// Boot ")[0];
    const context = vm.createContext({
        ...modelHelpers, fetch, HTMLSelectElement: Select, HTMLInputElement: class {}, CSS: { escape: value => value },
        setTimeout: () => 0, clearTimeout: () => {},
        MutationObserver: class { observe() {} }, matchMedia: () => ({ addEventListener() {} }),
        window: { addEventListener() {} },
        document: { getElementById: () => ({}), querySelector: () => null,
            addEventListener: (name, listener) => { listeners[name] = listener; } },
    });
    vm.runInContext(`${source}\nrender = () => {}; loadModel = () => Promise.resolve(); actionFocus = () => null;
        globalThis.client = { state, changeAction, uiState };`, context);
    context.client.state.model = fixture();
    return { ...context.client, listeners, Select };
}

test("browser signal Done, Undo, Dismiss and Reopen use the existing state endpoint", async () => {
    const calls = [];
    const client = browserHarness(async (url, options) => {
        calls.push([url, JSON.parse(options.body)]);
        return { ok: true, json: async () => ({ ...JSON.parse(options.body), state_at: null }) };
    });
    const current = () => client.state.model.signals[0];
    await client.changeAction("sig:high", "done");
    assert.equal(current().status, "done");
    assert.equal(current().new, false);
    await client.state.toast.undo();
    assert.equal(current().status, "open");
    await client.changeAction("sig:high", "dismissed");
    assert.equal(current().status, "dismissed");
    await client.changeAction("sig:high", null);
    assert.equal(current().status, "open");
    assert.deepEqual(calls.map(call => call[1].state), ["done", null, "dismissed", null]);
    assert.ok(calls.every(call => call[0] === "/api/action-state"));
    await client.changeAction("a-2", "done");
    assert.equal(client.state.model.actions.find(item => item.id === "a-2").state, "done");
});

test("browser optimistically closes signals, ignores duplicate pending writes and rolls failures back", async () => {
    let finish;
    let calls = 0;
    const client = browserHarness(() => { calls += 1; return new Promise(resolve => { finish = resolve; }); });
    const original = client.state.model.signals[0];
    const pending = client.changeAction("sig:high", "done");
    assert.equal(client.state.model.signals[0].status, "done");
    await client.changeAction("sig:high", "dismissed");
    assert.equal(calls, 1);
    finish({ ok: false, status: 503, json: async () => ({ error: "Synthetic write failed" }) });
    await pending;
    assert.equal(client.state.model.signals[0], original);
    assert.equal(client.state.pending.size, 0);
    assert.match(client.state.toast.message, /Could not update the signal/);
});

test("browser signal filter listeners update only supported options and expose render state", () => {
    const client = browserHarness();
    for (const [key, value] of [["priority", "Notify"], ["source", "zoominfo"], ["status", "dismissed"]]) {
        client.listeners.change({ target: new client.Select(key, value) });
        assert.equal(client.uiState().signalFilters[key], value);
    }
    client.listeners.change({ target: new client.Select("priority", "Bogus") });
    assert.equal(client.state.signalFilters.priority, "Notify");
});
