// Shared fixtures for the canvas suites: temp homes, the engine-built snapshot, a synthetic model and HTTP/SSE clients.
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

export const CANVAS = path.resolve(import.meta.dirname, "..");
const PULSE = path.dirname(CANVAS);
export const PYTHON = spawnSync("python3", ["--version"], { stdio: "ignore" }).status === 0;
export const NO_PYTHON = PYTHON ? false : "python3 not available";

export const tempDir = () => fs.mkdtempSync(path.join(os.tmpdir(), "cp-canvas-"));
export const removeDir = dir => dir && fs.rmSync(dir, { recursive: true, force: true });
export const readJson = file => JSON.parse(fs.readFileSync(file, "utf8"));
export const writeJson = (file, value) => fs.writeFileSync(file, JSON.stringify(value));

/** Replaces a file the way the engine does (write a sibling, then rename), so readers never see a partial file. */
export function replaceAtomically(file, text) {
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, text);
    fs.renameSync(tmp, file);
}

/** Runs the Python engine over its two-day synthetic fixtures; writes model.json and status.json into `home`. */
export function buildPulseHome(home) {
    const env = { ...process.env, CP_HOME: home, COPILOT_AGENT_SESSION_ID: "test", PYTHONDONTWRITEBYTECODE: "1" };
    const python = (...args) => execFileSync("python3", args, { cwd: PULSE, env, stdio: "pipe" });
    const fixtures = step => python("-c", `import os, sys; sys.path[:0] = ['tests', '.']; import fixtures as fx; home = os.environ['CP_HOME']; ${step}`);
    fixtures("fx.write_config(home); fx.write_day1(home)");
    python("pulse.py", "build", "--today", "2026-10-26", "--no-prune");
    fixtures("fx.write_day2(home)");
    python("pulse.py", "build", "--today", "2026-10-27", "--no-prune");
    return readJson(path.join(home, "model.json"));
}

/** A temp home holding the synthetic model and a clean build status. */
export function syntheticHome() {
    const home = tempDir();
    writeJson(path.join(home, "model.json"), syntheticModel());
    writeJson(path.join(home, "status.json"), { overall: "ok", date: "2026-10-27", generated_at: "2026-10-27T06:00:00+00:00", build_error: null });
    return home;
}

/** A snapshot home for server tests: engine-built when python3 exists, synthetic otherwise. */
export function makeHome() {
    if (!PYTHON) return syntheticHome();
    const home = tempDir();
    try {
        buildPulseHome(home);
    } catch (error) {
        removeDir(home);
        throw error;
    }
    return home;
}

export function syntheticAccount(id, name, { ghe = {}, ghas = {}, ghcp = {}, ...fields } = {}) {
    return {
        id, name, parent: null, segment: "Enterprise", industry: "Software", country: "Testland", arr: null, renewal: null,
        renewing_in_days: null, in_book: true, sfdc_url: `https://crm.example.invalid/${id}`,
        products: { ghe: { ...ghe }, ghas: { ...ghas }, ghcp: { ...ghcp }, other: { by_type: {} } }, ...fields,
    };
}

export const syntheticAction = (id, account, family, kind, priority, extra = {}) => ({
    id, rule: "test_rule", kind, priority, account_id: account.id, account_name: account.name, arr: account.arr, family,
    title: `Title ${id}`, why: `Why ${id}`, reasons: [], metrics: {}, tag: null, new: false, state: null, state_at: null, ...extra,
});

export const syntheticEvent = (id, account, family, priority, date, delta, extra = {}) => ({
    id, account_id: account.id, account_name: account.name, family, measure: "contracted", priority, date, delta,
    recent: true, reverted: false, new: false, text: `Change ${id}`, ...extra,
});

/** Three accounts, five actions (one done) and five events with every filter dimension represented. */
export function syntheticModel() {
    const accounts = [
        syntheticAccount("t-1", "Alpha Widgets", { arr: 300, renewal: "2026-12-01", renewing_in_days: 35,
            ghe: { contracted: 100 }, ghas: { contracted: 20, committers: 12 }, ghcp: { assigned: 10, funnel: { assigned: 10, installed_1d28: null, active_1d28: 4, engaged_1d28: 3 } } }),
        syntheticAccount("t-2", "Beta Gadgets", { arr: 200, ghe: { contracted: 0, metered: 40 } }),
        syntheticAccount("t-3", "Gamma Tools", { arr: 100 }),
    ];
    const byId = Object.fromEntries(accounts.map(account => [account.id, account]));
    const action = (id, accountId, ...rest) => syntheticAction(id, byId[accountId], ...rest);
    const event = (id, accountId, ...rest) => syntheticEvent(id, byId[accountId], ...rest);
    return {
        schema: 1, date: "2026-10-27", generated_at: "2026-10-27T06:00:00+00:00", owner: "Test Owner", status: "failed", baseline: false,
        changes_window_days: 7, thresholds: { renewal_days: 120 },
        kpis: { overall: { accounts: 3, arr: 600, renewing_120: 1 }, ghe: { accounts: 2 }, ghas: { accounts: 1 }, ghcp: { accounts: 1, categories: { Red: 2, Green: 1 } } },
        accounts,
        actions: [
            action("a-1", "t-1", "ghe", "opportunity", "Medium", { new: true }),
            action("a-2", "t-2", "all", "risk", "High"),
            action("a-3", "t-1", "ghas", "risk", "Medium", { state: "done", state_at: "2026-10-26T10:00:00.000Z" }),
            action("a-4", "t-3", "ghcp", "opportunity", "Notify"),
            action("a-5", "t-1", "all", "risk", "Medium", { new: true }),
        ],
        events: [
            event("e-1", "t-3", "ghe", "High", "2026-10-26", -50, { new: true }),
            event("e-2", "t-1", "ghas", "Info", "2026-10-25", 0),
            event("e-3", "t-1", "ghe", "Notify", "2026-10-26", 5, { reverted: true }),
            event("e-4", "t-2", "ghe", "Medium", "2026-10-10", -20, { recent: false }),
            event("e-5", "t-1", "ghcp", "Notify", "2026-10-26", 12),
        ],
        sources: [
            { feed: "book", label: "Book feed", state: "ok", as_of: "2026-10-27", messages: [] },
            { feed: "metered", label: "Metered feed", state: "stale", as_of: "2026-10-20", messages: ["late <load>"] },
            { feed: "volume", label: "Volume feed", state: "failed", as_of: "2026-10-24", messages: [] },
            { feed: "copilot", label: "Health feed", state: "failed", as_of: null },
            { feed: "ghas", label: "Committer feed", state: "missing" },
        ],
        signals: [],
    };
}

/** One HTTP request on a fresh connection; the path is sent verbatim so traversal attempts reach the server. */
export function request(server, method, route, { headers = {}, body } = {}) {
    return new Promise((resolve, reject) => {
        const req = http.request({ host: "127.0.0.1", port: server.port, method, path: route, headers, agent: false }, res => {
            const chunks = [];
            res.on("data", chunk => chunks.push(chunk));
            res.on("error", reject);
            res.on("end", () => {
                const raw = Buffer.concat(chunks);
                let json;
                try {
                    json = JSON.parse(raw.toString("utf8"));
                } catch {
                    json = undefined;
                }
                resolve({ status: res.statusCode, headers: res.headers, raw, text: raw.toString("utf8"), json });
            });
        });
        req.on("error", reject);
        req.end(body);
    });
}

/** Opens /events and parses the stream; `next(match)` resolves with the next event (in order) that matches. */
export function openEvents(server, { timeoutMs = 3000 } = {}) {
    return new Promise((resolve, reject) => {
        const events = [];
        const waiters = new Set();
        let buffer = "";
        let cursor = 0;
        let comments = 0;
        const req = http.get({ host: "127.0.0.1", port: server.port, path: "/events", agent: false }, res => {
            res.setEncoding("utf8");
            res.on("error", () => {});
            res.on("data", chunk => {
                buffer += chunk;
                let end;
                while ((end = buffer.indexOf("\n\n")) >= 0) {
                    const block = buffer.slice(0, end);
                    buffer = buffer.slice(end + 2);
                    if (block.startsWith(":")) comments += 1;
                    const name = block.match(/^event: (.+)$/m)?.[1];
                    if (name) events.push({ event: name, data: JSON.parse(block.match(/^data: (.+)$/m)?.[1] ?? "null") });
                }
                for (const waiter of waiters) waiter();
            });
            resolve({
                status: res.statusCode,
                headers: res.headers,
                events,
                get comments() {
                    return comments;
                },
                next(match = () => true) {
                    return new Promise((done, fail) => {
                        const check = () => {
                            const index = events.findIndex((item, position) => position >= cursor && match(item));
                            if (index < 0) return;
                            cursor = index + 1;
                            clearTimeout(timer);
                            waiters.delete(check);
                            done(events[index]);
                        };
                        const timer = setTimeout(() => {
                            waiters.delete(check);
                            fail(new Error(`No matching event within ${timeoutMs} ms; received: ${events.map(item => item.event).join(", ")}`));
                        }, timeoutMs);
                        waiters.add(check);
                        check();
                    });
                },
                close() {
                    req.destroy();
                },
            });
        });
        req.on("error", error => (req.destroyed ? null : reject(error)));
    });
}
