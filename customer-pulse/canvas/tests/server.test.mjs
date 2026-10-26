// HTTP-level tests for server.mjs: file helpers, routes and headers, POST guards, the refresh coordinator and /events.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import {
    CanvasRequestError, checkActionInput, createRefreshCoordinator, pulseHome, pulsePaths, readActionStates, readBuildStatus, readWorkflowId,
    revisionOf, setActionState, startCanvasServer,
} from "../server.mjs";
import { CANVAS, makeHome, openEvents, readJson, removeDir, replaceAtomically, request, syntheticHome, syntheticModel, tempDir, writeJson } from "./helpers.mjs";

const IDLE = { phase: "idle", requestedAt: null, completedAt: null, message: null };
const FORBIDDEN = "Requests must come from this canvas.";

/** Starts a canvas on an ephemeral port with the real coordinator around a counting stub launcher, and reads its CSRF token. */
async function serve(home, { launch = async () => {}, now, pollMs = 50 } = {}) {
    const paths = pulsePaths(home);
    const launches = [];
    const refresh = createRefreshCoordinator({ modelPath: paths.model, now, launch: () => { launches.push(launches.length); return launch(); } });
    const server = await startCanvasServer({ paths, refresh, port: 0, pollMs });
    try {
        const page = await request(server, "GET", "/");
        const token = page.text.match(/<meta name="csrf-token" content="([0-9a-f]+)">/)?.[1];
        return { server, paths, refresh, launches, token, origin: `http://127.0.0.1:${server.port}`, page };
    } catch (error) {
        await server.close();
        throw error;
    }
}

/** serve() with cleanup on the test: the server closes first, then the home is removed. */
async function serveHome(t, home, options) {
    let ctx;
    t.after(async () => {
        await ctx?.server.close();
        removeDir(home);
    });
    ctx = await serve(home, options);
    return ctx;
}

/** A POST that passes the guard unless a header is overridden (null removes it); string bodies are sent verbatim. */
function post(ctx, route, body = {}, headers = {}) {
    const merged = { origin: ctx.origin, "x-canvas-token": ctx.token, "content-type": "application/json", ...headers };
    for (const key of Object.keys(merged)) if (merged[key] === null) delete merged[key];
    return request(ctx.server, "POST", route, { headers: merged, body: typeof body === "string" ? body : JSON.stringify(body) });
}

describe("server.mjs helpers", () => {
    test("pulseHome mirrors the engine's CP_HOME rules and pulsePaths lays out the home", () => {
        assert.equal(pulseHome("~"), os.homedir());
        assert.equal(pulseHome("~/pulse-x"), path.join(os.homedir(), "pulse-x"));
        assert.equal(pulseHome(""), path.join(os.homedir(), "CustomerPulse"));
        assert.equal(pulseHome("/srv/pulse"), "/srv/pulse");
        assert.equal(pulseHome("rel/pulse"), path.resolve("rel/pulse"));
        const home = path.join(os.tmpdir(), "pulse-home");
        assert.deepEqual(pulsePaths(home), {
            home, model: path.join(home, "model.json"), status: path.join(home, "status.json"),
            actioned: path.join(home, "state", "actioned.json"), config: path.join(home, "config.json"),
        });
    });

    test("revision, build status, decisions and workflow id readers tolerate missing and malformed files", async t => {
        const home = tempDir();
        t.after(() => removeDir(home));
        const paths = pulsePaths(home);
        assert.equal(await revisionOf(paths.model), null);
        writeJson(paths.model, { a: 1 });
        const { mtimeMs, size } = fs.statSync(paths.model);
        assert.equal(await revisionOf(paths.model), `${mtimeMs}:${size}`);
        assert.equal(await readBuildStatus(paths.status), null);
        for (const text of ["{oops", "[1]", "null"]) {
            fs.writeFileSync(paths.status, text);
            assert.equal(await readBuildStatus(paths.status), null, text);
        }
        writeJson(paths.status, { overall: "failed", date: "2026-10-27", generated_at: 5, build_error: "boom", extra: true });
        assert.deepEqual(await readBuildStatus(paths.status), { overall: "failed", date: "2026-10-27", generated_at: null, build_error: "boom" });
        assert.deepEqual(await readActionStates(paths.actioned), {});
        fs.mkdirSync(path.dirname(paths.actioned));
        for (const text of ["{oops", '{"items":[1]}', '{"items":"x"}']) {
            fs.writeFileSync(paths.actioned, text);
            assert.deepEqual(await readActionStates(paths.actioned), {}, text);
        }
        writeJson(paths.actioned, { items: { "a-1": { state: "done", at: null } } });
        assert.deepEqual(await readActionStates(paths.actioned), { "a-1": { state: "done", at: null } });
        const model = { copilot: { workflow_id: "0a1b2c3d-4e5f" } };
        assert.equal(await readWorkflowId(paths), null);
        assert.equal(await readWorkflowId(paths, model), "0a1b2c3d-4e5f");
        assert.equal(await readWorkflowId(paths, { copilot: { workflow_id: "x; rm -rf ~" } }), null);
        writeJson(paths.config, { workflow_id: "ABCDEF12" });
        assert.equal(await readWorkflowId(paths, model), "ABCDEF12");
        writeJson(paths.config, { workflow_id: "not-a-workflow!" });
        assert.equal(await readWorkflowId(paths, model), null);
        fs.writeFileSync(paths.config, "{oops");
        assert.equal(await readWorkflowId(paths, model), "0a1b2c3d-4e5f");
    });

    test("checkActionInput accepts exactly an id and a known state", () => {
        assert.deepEqual(checkActionInput({ id: "a-1", state: "done" }), { id: "a-1", state: "done" });
        assert.deepEqual(checkActionInput({ state: null, id: "a-1" }), { id: "a-1", state: null });
        assert.equal(checkActionInput({ id: "x".repeat(200), state: "dismissed" }).state, "dismissed");
        const bad = [null, [], "a-1", {}, { id: "a-1" }, { id: "a-1", state: "done", extra: 1 }, { id: "", state: "done" }, { id: 5, state: "done" },
            { id: "x".repeat(201), state: "done" }, { id: "a-1", state: "open" }, { id: "a-1", state: "Done" }, { id: "a-1", state: undefined }];
        for (const input of bad) {
            assert.throws(() => checkActionInput(input), error => error instanceof CanvasRequestError && error.code === "action_input_invalid" && error.status === 400,
                JSON.stringify(input));
        }
    });

    test("setActionState records and clears decisions, keeping other content", async t => {
        const home = syntheticHome();
        t.after(() => removeDir(home));
        const paths = pulsePaths(home);
        const now = () => new Date("2026-10-27T09:30:00.000Z");
        const at = "2026-10-27T09:30:00.000Z";
        assert.deepEqual(await setActionState({ paths, id: "a-1", state: "done", now }), { id: "a-1", state: "done", state_at: at });
        assert.deepEqual(readJson(paths.actioned), { items: { "a-1": { state: "done", at } } });
        writeJson(paths.actioned, { version: 3, items: { "a-1": { state: "done", at: "x" }, gone: { state: "dismissed", at: null } } });
        assert.deepEqual(await setActionState({ paths, id: "a-2", state: "dismissed", now }), { id: "a-2", state: "dismissed", state_at: at });
        assert.deepEqual(await setActionState({ paths, id: "a-1", state: null, now }), { id: "a-1", state: null, state_at: null });
        assert.deepEqual(readJson(paths.actioned), { version: 3, items: { gone: { state: "dismissed", at: null }, "a-2": { state: "dismissed", at } } });
        await assert.rejects(setActionState({ paths, id: "zz", state: "done" }), { code: "action_unknown", status: 404 });
        for (const text of ["{oops", "[1,2]"]) {
            fs.writeFileSync(paths.actioned, text);
            await assert.rejects(setActionState({ paths, id: "a-1", state: "done" }), { code: "action_state_unreadable", status: 503 }, text);
            assert.equal(fs.readFileSync(paths.actioned, "utf8"), text);
        }
        assert.deepEqual(fs.readdirSync(path.dirname(paths.actioned)), ["actioned.json"]);
    });

    test("concurrent setActionState calls are serialized so no decision is lost", async t => {
        const home = syntheticHome();
        t.after(() => removeDir(home));
        const paths = pulsePaths(home);
        const ids = ["a-1", "a-2", "a-3", "a-4", "a-5"];
        await Promise.all(ids.map(id => setActionState({ paths, id, state: "done" })));
        assert.deepEqual(Object.keys(readJson(paths.actioned).items).sort(), ids);
        assert.deepEqual(fs.readdirSync(path.dirname(paths.actioned)), ["actioned.json"]);
    });

    test("setActionState needs a snapshot", async t => {
        const home = tempDir();
        t.after(() => removeDir(home));
        const paths = pulsePaths(home);
        await assert.rejects(setActionState({ paths, id: "a-1", state: "done" }), { code: "dashboard_missing", status: 404 });
        assert.equal(fs.existsSync(paths.actioned), false);
    });
});

describe("canvas server", () => {
    let home;
    let ctx;
    let actionId;
    before(async () => {
        home = makeHome();
        ctx = await serve(home);
        actionId = readJson(ctx.paths.model).actions[0].id;
    });
    after(async () => {
        await ctx?.server.close();
        removeDir(home);
    });

    test("GET / serves the page with a per-server CSRF token and strict headers", async t => {
        const { page, token } = ctx;
        assert.equal(page.status, 200);
        assert.equal(page.headers["content-type"], "text/html; charset=utf-8");
        const csp = page.headers["content-security-policy"];
        for (const directive of ["default-src 'self'", "script-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'none'"]) {
            assert.ok(csp.split(";").map(part => part.trim()).includes(directive), directive);
        }
        assert.doesNotMatch(csp, /script-src[^;]*unsafe/);
        assert.equal(page.headers["x-content-type-options"], "nosniff");
        assert.equal(page.headers["cache-control"], "no-store, max-age=0");
        assert.equal(page.headers["referrer-policy"], "no-referrer");
        assert.equal(page.headers["cross-origin-resource-policy"], "same-origin");
        assert.match(token, /^[0-9a-f]{48}$/);
        assert.equal(page.text, fs.readFileSync(path.join(CANVAS, "index.html"), "utf8").replace("__CSRF_TOKEN__", token));
        assert.equal((await request(ctx.server, "GET", "/?view=ghe")).text, page.text);
        const other = await serveHome(t, tempDir());
        assert.match(other.token, /^[0-9a-f]{48}$/);
        assert.notEqual(other.token, token);
    });

    test("serves the four client assets byte for byte with script and style types", async () => {
        const assets = { "/app.js": "text/javascript", "/model.mjs": "text/javascript", "/views.mjs": "text/javascript", "/style.css": "text/css" };
        for (const [route, type] of Object.entries(assets)) {
            const res = await request(ctx.server, "GET", route);
            assert.equal(res.status, 200, route);
            assert.equal(res.headers["content-type"], `${type}; charset=utf-8`, route);
            assert.equal(res.headers["x-content-type-options"], "nosniff", route);
            assert.equal(res.headers["cache-control"], "no-store, max-age=0", route);
            assert.ok(res.raw.equals(fs.readFileSync(path.join(CANVAS, route.slice(1)))), route);
        }
        assert.equal((await request(ctx.server, "GET", "/style.css?v=2")).status, 200);
    });

    test("never serves other files, traversal attempts or other methods", async () => {
        const blocked = [
            ["GET", "/index.html"], ["GET", "/server.mjs"], ["GET", "/extension.mjs"], ["GET", "/README.md"], ["GET", "/tests/canvas.test.mjs"],
            ["GET", "/tests/helpers.mjs"], ["GET", "/../package.json"], ["GET", "/../pulse.py"], ["GET", "/%2e%2e/server.mjs"], ["GET", "/..%2fserver.mjs"],
            ["GET", "/%2e%2e%2fpulse.py"], ["GET", "/static/../../pulse.py"], ["GET", "/app.js/"], ["GET", "/APP.JS"], ["GET", "/app.js%00.css"],
            ["GET", "//etc/passwd"], ["POST", "/app.js"], ["PUT", "/api/model"], ["DELETE", "/"], ["GET", "/api/refresh"], ["GET", "/api/action-state"],
        ];
        for (const [method, route] of blocked) {
            const res = await request(ctx.server, method, route);
            assert.equal(res.status, 404, `${method} ${route}`);
            assert.deepEqual(res.json, { error: "This canvas route does not exist.", code: "not_found" }, `${method} ${route}`);
            assert.equal(res.headers["x-content-type-options"], "nosniff");
        }
    });

    test("GET /api/model returns the validated snapshot with its revision, build and refresh state", async () => {
        const res = await request(ctx.server, "GET", "/api/model");
        assert.equal(res.status, 200);
        assert.equal(res.headers["content-type"], "application/json; charset=utf-8");
        assert.equal(res.headers["cache-control"], "no-store, max-age=0");
        assert.deepEqual(Object.keys(res.json).sort(), ["build", "model", "refresh", "revision"]);
        const file = readJson(ctx.paths.model);
        assert.equal(res.json.model.date, file.date);
        assert.deepEqual(res.json.model.accounts, file.accounts);
        assert.deepEqual(res.json.model.actions.map(action => action.id), file.actions.map(action => action.id));
        assert.equal(res.json.revision, `${await revisionOf(ctx.paths.model)}|null|${await revisionOf(ctx.paths.status)}`);
        const status = readJson(ctx.paths.status);
        const text = key => typeof status[key] === "string" ? status[key] : null;
        assert.deepEqual(res.json.build, { overall: text("overall"), date: text("date"), generated_at: text("generated_at"), build_error: text("build_error") });
        assert.deepEqual(res.json.refresh, IDLE);
        assert.ok(!res.text.includes(ctx.token));
        const live = await request(ctx.server, "GET", "/api/status");
        assert.equal(live.status, 200);
        assert.deepEqual(live.json, { refresh: IDLE, revision: res.json.revision, build: res.json.build });
        assert.ok(!live.text.includes(ctx.token));
    });

    test("POST endpoints require this canvas's exact origin, its token and a JSON content type", async () => {
        const { origin, token } = ctx;
        const port = ctx.server.port;
        const cases = [
            { origin: null }, { origin: `http://localhost:${port}` }, { origin: "http://127.0.0.1:1" }, { origin: `${origin}/` }, { origin: "null" },
            { origin: `https://127.0.0.1:${port}` }, { origin: `http://127.0.0.1:${port}.evil.example` },
            { "x-canvas-token": null }, { "x-canvas-token": "" }, { "x-canvas-token": "0".repeat(48) }, { "x-canvas-token": token.slice(1) },
            { "x-canvas-token": `${token}0` }, { "x-canvas-token": token.toUpperCase() }, { "x-canvas-token": [token, token] },
            { "content-type": null }, { "content-type": "text/plain" }, { "content-type": "application/x-www-form-urlencoded" },
            { "content-type": "multipart/form-data; boundary=x" }, { "content-type": "text/plain; application/json" },
        ];
        for (const headers of cases) {
            const label = JSON.stringify(headers);
            const refresh = await post(ctx, "/api/refresh", {}, headers);
            assert.equal(refresh.status, 403, label);
            assert.deepEqual(refresh.json, { error: FORBIDDEN, code: "refresh_forbidden" }, label);
            const action = await post(ctx, "/api/action-state", { id: actionId, state: "done" }, headers);
            assert.equal(action.status, 403, label);
            assert.deepEqual(action.json, { error: FORBIDDEN, code: "action_forbidden" }, label);
        }
        const unchecked = await post(ctx, "/api/refresh", "x".repeat(400), { "x-canvas-token": null });
        assert.equal(unchecked.status, 403, "the guard runs before the body is read");
        assert.equal(ctx.launches.length, 0);
        assert.equal(fs.existsSync(ctx.paths.actioned), false);
        assert.deepEqual(ctx.refresh.status(), IDLE);
    });

    test("POST bodies must be small JSON of the expected shape", async () => {
        const cases = [
            ["/api/refresh", JSON.stringify({ pad: "x".repeat(300) }), 413, "refresh_input_too_large"],
            ["/api/refresh", "{not json", 400, "refresh_input_invalid"],
            ["/api/refresh", "", 400, "refresh_input_invalid"],
            ["/api/refresh", "null", 400, "refresh_input_invalid"],
            ["/api/refresh", "[]", 400, "refresh_input_invalid"],
            ["/api/refresh", '{"force":true}', 400, "refresh_input_invalid"],
            ["/api/action-state", JSON.stringify({ id: actionId, state: "done", pad: "x".repeat(600) }), 413, "action_input_too_large"],
            ["/api/action-state", "{not json", 400, "action_input_invalid"],
            ["/api/action-state", JSON.stringify([actionId, "done"]), 400, "action_input_invalid"],
            ["/api/action-state", JSON.stringify({ id: actionId }), 400, "action_input_invalid"],
            ["/api/action-state", JSON.stringify({ id: actionId, state: "open" }), 400, "action_input_invalid"],
            ["/api/action-state", JSON.stringify({ id: actionId, state: "done", extra: 1 }), 400, "action_input_invalid"],
            ["/api/action-state", JSON.stringify({ id: "", state: "done" }), 400, "action_input_invalid"],
        ];
        for (const [route, body, status, code] of cases) {
            const res = await post(ctx, route, body);
            assert.equal(res.status, status, `${route} ${body.slice(0, 30)}`);
            assert.equal(res.json.code, code, `${route} ${body.slice(0, 30)}`);
        }
        assert.equal(ctx.launches.length, 0);
        assert.equal(fs.existsSync(ctx.paths.actioned), false);
    });

    test("POST /api/action-state records, overlays and reopens a decision", async () => {
        const before = await request(ctx.server, "GET", "/api/model");
        const done = await post(ctx, "/api/action-state", { id: actionId, state: "done" });
        assert.equal(done.status, 200);
        assert.deepEqual([done.json.id, done.json.state], [actionId, "done"]);
        assert.ok(Number.isFinite(Date.parse(done.json.state_at)));
        assert.deepEqual(readJson(ctx.paths.actioned), { items: { [actionId]: { state: "done", at: done.json.state_at } } });
        const after = await request(ctx.server, "GET", "/api/model");
        const action = after.json.model.actions.find(item => item.id === actionId);
        assert.deepEqual([action.state, action.state_at, action.new], ["done", done.json.state_at, false]);
        assert.notEqual(after.json.revision, before.json.revision);
        assert.equal(after.json.revision.split("|")[1], await revisionOf(ctx.paths.actioned));
        const reopened = await post(ctx, "/api/action-state", { id: actionId, state: null });
        assert.deepEqual([reopened.status, reopened.json], [200, { id: actionId, state: null, state_at: null }]);
        assert.deepEqual(readJson(ctx.paths.actioned), { items: {} });
        assert.equal((await request(ctx.server, "GET", "/api/model")).json.model.actions.find(item => item.id === actionId).state, null);
        const dismissed = await post(ctx, "/api/action-state", { id: actionId, state: "dismissed" }, { "content-type": "application/json; charset=utf-8" });
        assert.equal(dismissed.status, 200);
        assert.equal(readJson(ctx.paths.actioned).items[actionId].state, "dismissed");
        fs.rmSync(ctx.paths.actioned);
    });

    test("POST /api/action-state refuses unknown actions and an unreadable decisions file", async () => {
        const unknown = await post(ctx, "/api/action-state", { id: "t-unknown", state: "done" });
        assert.deepEqual([unknown.status, unknown.json.code], [404, "action_unknown"]);
        fs.mkdirSync(path.dirname(ctx.paths.actioned), { recursive: true });
        fs.writeFileSync(ctx.paths.actioned, "{oops");
        const unreadable = await post(ctx, "/api/action-state", { id: actionId, state: "done" });
        assert.deepEqual([unreadable.status, unreadable.json.code], [503, "action_state_unreadable"]);
        assert.equal(fs.readFileSync(ctx.paths.actioned, "utf8"), "{oops");
        const model = await request(ctx.server, "GET", "/api/model");
        assert.equal(model.status, 200);
        assert.ok(model.json.model.actions.every(action => action.state === null));
        fs.rmSync(ctx.paths.actioned);
    });
});

describe("canvas server without a usable snapshot", () => {
    test("a missing snapshot is a 404 the page can explain, and nothing can be actioned", async t => {
        const ctx = await serveHome(t, tempDir());
        assert.equal(ctx.page.status, 200);
        const res = await request(ctx.server, "GET", "/api/model");
        assert.deepEqual([res.status, res.json.code], [404, "dashboard_missing"]);
        assert.ok(res.json.error.includes(ctx.paths.model));
        const status = await request(ctx.server, "GET", "/api/status");
        assert.deepEqual(status.json, { refresh: IDLE, revision: "null|null|null", build: null });
        const action = await post(ctx, "/api/action-state", { id: "a-1", state: "done" });
        assert.deepEqual([action.status, action.json.code], [404, "dashboard_missing"]);
        assert.equal(fs.existsSync(ctx.paths.actioned), false);
    });

    test("an unreadable or invalid snapshot is a 503 with the reason", async t => {
        const ctx = await serveHome(t, tempDir());
        const cases = [
            ["{not json", null],
            [JSON.stringify({ ...syntheticModel(), schema: 2 }), "unsupported model schema 2 (expected 1)"],
            [JSON.stringify({ ...syntheticModel(), actions: {} }), '"actions" must be a list'],
        ];
        for (const [text, reason] of cases) {
            replaceAtomically(ctx.paths.model, text);
            const res = await request(ctx.server, "GET", "/api/model");
            assert.deepEqual([res.status, res.json.code], [503, "dashboard_invalid"], text.slice(0, 20));
            assert.ok(res.json.error.startsWith("The Customer Pulse snapshot cannot be used: "));
            if (reason) assert.equal(res.json.error, `The Customer Pulse snapshot cannot be used: ${reason}`);
        }
        fs.rmSync(ctx.paths.model);
        fs.mkdirSync(ctx.paths.model);
        const unreadable = await request(ctx.server, "GET", "/api/model");
        assert.deepEqual([unreadable.status, unreadable.json.code], [503, "dashboard_read_failed"]);
        assert.equal((await request(ctx.server, "GET", "/api/status")).status, 200);
    });
});

describe("POST /api/refresh", () => {
    test("launches the automation once and refuses a second request while it runs", async t => {
        const ctx = await serveHome(t, syntheticHome());
        const first = await post(ctx, "/api/refresh");
        assert.equal(first.status, 202);
        assert.equal(first.json.refresh.phase, "requested");
        assert.match(first.json.refresh.message, /^Refresh requested\./);
        assert.ok(Number.isFinite(Date.parse(first.json.refresh.requestedAt)));
        const second = await post(ctx, "/api/refresh");
        assert.deepEqual([second.status, second.json.code], [409, "refresh_in_progress"]);
        assert.equal(ctx.launches.length, 1);
        assert.equal((await request(ctx.server, "GET", "/api/status")).json.refresh.phase, "requested");
    });

    test("a launch failure is a 502 that can be retried", async t => {
        let failing = true;
        const ctx = await serveHome(t, syntheticHome(), { launch: async () => { if (failing) throw new Error("automation <missing>"); } });
        const failed = await post(ctx, "/api/refresh");
        assert.equal(failed.status, 502);
        assert.deepEqual(failed.json, { error: "Could not request the refresh: automation <missing>", code: "refresh_launch_failed" });
        assert.equal((await request(ctx.server, "GET", "/api/status")).json.refresh.phase, "error");
        failing = false;
        assert.equal((await post(ctx, "/api/refresh")).status, 202);
        assert.equal(ctx.launches.length, 2);
    });

    test("a launcher that throws synchronously is also a 502", async t => {
        const ctx = await serveHome(t, syntheticHome(), { launch: () => { throw new Error("no host"); } });
        const res = await post(ctx, "/api/refresh");
        assert.deepEqual([res.status, res.json], [502, { error: "Could not request the refresh: no host", code: "refresh_launch_failed" }]);
    });

    test("only a build failure recorded after the request ends it", async t => {
        const home = syntheticHome();
        const status = path.join(home, "status.json");
        writeJson(status, { overall: "failed", date: "2020-01-01", generated_at: "2020-01-01T00:00:00+00:00", build_error: "old failure" });
        const ctx = await serveHome(t, home);
        assert.equal((await post(ctx, "/api/refresh")).status, 202);
        assert.equal((await request(ctx.server, "GET", "/api/status")).json.refresh.phase, "requested");
        const error = `boom <${"x".repeat(400)}>`;
        replaceAtomically(status, JSON.stringify({ overall: "failed", date: "2026-10-27", generated_at: new Date(Date.now() + 1000).toISOString(), build_error: error }));
        const res = await request(ctx.server, "GET", "/api/status");
        assert.equal(res.json.refresh.phase, "error");
        assert.equal(res.json.refresh.message, `The refresh ran but could not rebuild the snapshot: ${error.slice(0, 300)}`);
        assert.equal(res.json.build.build_error, error);
    });

    test("a request that never produces a snapshot times out and can be retried", async t => {
        let clock = Date.parse("2026-10-27T08:00:00.000Z");
        const ctx = await serveHome(t, syntheticHome(), { now: () => clock });
        assert.equal((await post(ctx, "/api/refresh")).status, 202);
        clock += 45 * 60 * 1000 + 1;
        const res = await request(ctx.server, "GET", "/api/status");
        assert.deepEqual(res.json.refresh, {
            phase: "error", requestedAt: "2026-10-27T08:00:00.000Z", completedAt: "2026-10-27T08:45:00.001Z",
            message: "No rebuilt snapshot appeared within 45 minutes. Check the automation run in Copilot, then retry.",
        });
        assert.equal((await post(ctx, "/api/refresh")).status, 202);
        assert.equal(ctx.launches.length, 2);
    });
});

describe("createRefreshCoordinator", () => {
    let home;
    before(() => {
        home = syntheticHome();
    });
    after(() => removeDir(home));

    const START = "2026-10-27T08:00:00.000Z";
    const REQUESTED = "Refresh requested. The automation collects every source and rebuilds the snapshot; this usually takes 10–30 minutes.";
    function coordinator(launch = async () => {}) {
        const clock = { now: Date.parse(START) };
        const launches = [];
        const refresh = createRefreshCoordinator({
            modelPath: path.join(home, "model.json"), timeoutMs: 120000, now: () => clock.now,
            launch: () => { launches.push(clock.now); return launch(); },
        });
        return { refresh, clock, launches };
    }

    test("starts idle, ignores observations, and moves to requested after one launch", async () => {
        const { refresh, clock, launches } = coordinator();
        assert.deepEqual(refresh.status(), IDLE);
        assert.deepEqual(refresh.observe("x:1"), IDLE);
        assert.deepEqual(refresh.fail("boom", clock.now), IDLE);
        assert.deepEqual(await refresh.request(), { phase: "requested", requestedAt: START, completedAt: null, message: REQUESTED });
        await assert.rejects(refresh.request(), { code: "refresh_in_progress", status: 409 });
        assert.equal(launches.length, 1);
    });

    test("reports launching while the automation is being asked, and refuses a second request then", async () => {
        let release;
        let started;
        const launched = new Promise(resolve => { started = resolve; });
        const { refresh, launches } = coordinator(() => {
            started();
            return new Promise(resolve => { release = resolve; });
        });
        const pending = refresh.request();
        await launched;
        assert.deepEqual(refresh.status(), { phase: "launching", requestedAt: START, completedAt: null, message: "Asking Copilot to run the refresh automation…" });
        await assert.rejects(refresh.request(), { code: "refresh_in_progress" });
        release();
        assert.equal((await pending).phase, "requested");
        assert.equal(launches.length, 1);
    });

    test("a changed snapshot revision completes the request; the request-time one does not", async () => {
        const { refresh, clock } = coordinator();
        const baseline = await revisionOf(path.join(home, "model.json"));
        await refresh.request();
        assert.equal(refresh.observe(baseline).phase, "requested");
        assert.equal(refresh.observe(null).phase, "requested");
        clock.now += 5000;
        assert.deepEqual(refresh.observe("changed:1"),
            { phase: "updated", requestedAt: START, completedAt: "2026-10-27T08:00:05.000Z", message: "Refreshed. Showing the new snapshot." });
        assert.equal(refresh.fail("late", clock.now).phase, "updated");
        assert.equal((await refresh.request()).phase, "requested");
    });

    test("times out after timeoutMs, and a snapshot that lands later still completes it", async () => {
        const { refresh, clock } = coordinator();
        await refresh.request();
        clock.now += 120000;
        assert.equal(refresh.status().phase, "requested");
        clock.now += 1;
        const timedOut = refresh.status();
        assert.deepEqual(timedOut, {
            phase: "error", requestedAt: START, completedAt: "2026-10-27T08:02:00.001Z",
            message: "No rebuilt snapshot appeared within 2 minutes. Check the automation run in Copilot, then retry.",
        });
        assert.deepEqual(refresh.fail("later", clock.now), timedOut);
        assert.equal(refresh.observe("late:1").phase, "updated");
    });

    test("fail ends a requested refresh only for builds at or after the request", async () => {
        const { refresh, clock } = coordinator();
        await refresh.request();
        const requestedAt = clock.now;
        for (const at of [requestedAt - 1, NaN, undefined, Infinity]) assert.equal(refresh.fail("ignored", at).phase, "requested", String(at));
        clock.now += 1000;
        assert.deepEqual(refresh.fail("Build <failed>", requestedAt), { phase: "error", requestedAt: START, completedAt: "2026-10-27T08:00:01.000Z", message: "Build <failed>" });
        assert.equal(refresh.observe("changed:1").phase, "updated");
    });

    test("a failed launch, sync or async, is an error that can be retried", async () => {
        let attempt = 0;
        const { refresh, launches } = coordinator(async () => {
            attempt += 1;
            if (attempt === 1) throw new Error("rejected <x>");
        });
        await assert.rejects(refresh.request(), error => error instanceof CanvasRequestError && error.code === "refresh_launch_failed"
            && error.status === 502 && error.message === "Could not request the refresh: rejected <x>");
        assert.deepEqual(refresh.status(), { phase: "error", requestedAt: START, completedAt: START, message: "Could not request the refresh: rejected <x>" });
        assert.equal((await refresh.request()).phase, "requested");
        assert.equal(launches.length, 2);
        const sync = coordinator(() => { throw new Error("sync"); });
        await assert.rejects(sync.refresh.request(), { code: "refresh_launch_failed", message: "Could not request the refresh: sync" });
        assert.equal(sync.refresh.status().phase, "error");
    });

    // Two overlapping requests must launch the automation once; the second gets refresh_in_progress.
    test("overlapping requests launch the automation once", async () => {
        const { refresh, launches } = coordinator();
        const results = await Promise.allSettled([refresh.request(), refresh.request()]);
        assert.equal(launches.length, 1);
        assert.deepEqual(results.map(result => result.status).sort(), ["fulfilled", "rejected"]);
        assert.equal(results.find(result => result.status === "rejected").reason.code, "refresh_in_progress");
    });
});

describe("GET /events", () => {
    test("streams status, then model and status events when the snapshot is replaced", async t => {
        const ctx = await serveHome(t, syntheticHome());
        const events = await openEvents(ctx.server);
        try {
            assert.equal(events.status, 200);
            assert.equal(events.headers["content-type"], "text/event-stream; charset=utf-8");
            assert.equal(events.headers["cache-control"], "no-store, max-age=0");
            const initial = await events.next(item => item.event === "status");
            assert.deepEqual(initial.data.refresh, IDLE);
            assert.equal(initial.data.revision, (await request(ctx.server, "GET", "/api/status")).json.revision);
            assert.equal((await post(ctx, "/api/refresh")).status, 202);
            await events.next(item => item.event === "status" && item.data.refresh.phase === "requested");
            const model = syntheticModel();
            model.owner = "Changed Owner";
            replaceAtomically(ctx.paths.model, JSON.stringify(model));
            const changed = await events.next(item => item.event === "model");
            assert.notEqual(changed.data.revision, initial.data.revision);
            const updated = await events.next(item => item.event === "status" && item.data.refresh.phase === "updated");
            assert.equal(updated.data.refresh.message, "Refreshed. Showing the new snapshot.");
            assert.ok(events.comments > 0, "keepalive comments are sent");
            const res = await request(ctx.server, "GET", "/api/model");
            assert.equal(res.json.model.owner, "Changed Owner");
            assert.equal(res.json.revision, changed.data.revision);
        } finally {
            events.close();
        }
    });

    test("recording an action decision is pushed as a model event", async t => {
        const ctx = await serveHome(t, syntheticHome());
        const events = await openEvents(ctx.server);
        try {
            const initial = await events.next(item => item.event === "status");
            assert.equal((await post(ctx, "/api/action-state", { id: "a-1", state: "done" })).status, 200);
            const changed = await events.next(item => item.event === "model");
            assert.notEqual(changed.data.revision, initial.data.revision);
            assert.equal(changed.data.revision.split("|")[1], await revisionOf(ctx.paths.actioned));
        } finally {
            events.close();
        }
    });
});
