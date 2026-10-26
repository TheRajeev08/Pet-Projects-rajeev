import { randomBytes, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, rename, stat, unlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ACTION_STATES, isObject, validateModel, withActionStates } from "./model.mjs";
import { validateModel as validateCopilotModel } from "./copilot/insights.mjs";

const assets = dirname(fileURLToPath(import.meta.url));
const STATIC = {
    "/app.js": "text/javascript; charset=utf-8",
    "/model.mjs": "text/javascript; charset=utf-8",
    "/views.mjs": "text/javascript; charset=utf-8",
    "/style.css": "text/css; charset=utf-8",
    "/copilot/app.js": "text/javascript; charset=utf-8",
    "/copilot/insights.mjs": "text/javascript; charset=utf-8",
    "/copilot/impact.mjs": "text/javascript; charset=utf-8",
    "/copilot/style.css": "text/css; charset=utf-8",
};
const WORKFLOW_ID = /^[0-9a-f][0-9a-f-]{7,63}$/i;

/** Mirrors pulse/paths.py: CP_HOME, defaulting to ~/CustomerPulse. */
export function pulseHome(value = process.env.CP_HOME) {
    const home = value || join(homedir(), "CustomerPulse");
    if (home === "~") return homedir();
    if (home.startsWith("~/")) return join(homedir(), home.slice(2));
    return isAbsolute(home) ? home : resolve(home);
}

export function pulsePaths(home = pulseHome()) {
    return {
        home,
        model: join(home, "model.json"),
        status: join(home, "status.json"),
        actioned: join(home, "state", "actioned.json"),
        config: join(home, "config.json"),
    };
}

export class CanvasRequestError extends Error {
    constructor(code, status, message) {
        super(message);
        this.code = code;
        this.status = status;
    }
}

export async function revisionOf(path) {
    try {
        const { mtimeMs, size } = await stat(path);
        return `${mtimeMs}:${size}`;
    } catch (error) {
        if (error.code === "ENOENT") return null;
        throw error;
    }
}

async function readOptionalJson(path) {
    let text;
    try {
        text = await readFile(path, "utf8");
    } catch (error) {
        if (error.code === "ENOENT") return undefined;
        throw error;
    }
    return JSON.parse(text);
}

export async function readModel(path, validate = validateModel) {
    let content;
    let before;
    let after;
    try {
        before = await stat(path);
        content = await readFile(path, "utf8");
        after = await stat(path);
    } catch (error) {
        if (error.code === "ENOENT") {
            throw new CanvasRequestError("dashboard_missing", 404,
                `No Customer Pulse snapshot at ${path} yet. Run the daily refresh (customer-pulse/REFRESH.md).`);
        }
        throw new CanvasRequestError("dashboard_read_failed", 503, `Cannot read the Customer Pulse snapshot: ${error.message}`);
    }
    if (before.mtimeMs !== after.mtimeMs || before.size !== after.size) {
        throw new CanvasRequestError("dashboard_updating", 503, "The snapshot changed while being read. Try again in a moment.");
    }
    let model;
    try {
        model = validate(JSON.parse(content));
    } catch (error) {
        throw new CanvasRequestError("dashboard_invalid", 503, `The Customer Pulse snapshot cannot be used: ${error.message}`);
    }
    return { model, revision: `${after.mtimeMs}:${after.size}` };
}

export const readCopilotModel = path => readModel(path, validateCopilotModel);

export async function copilotModelPath(paths) {
    const config = await readOptionalJson(paths.config);
    if (config?.copilot_home) return join(pulseHome(config.copilot_home), "model.json");
    const pulse = await readOptionalJson(paths.model);
    return pulse?.copilot?.model_path || join(homedir(), "CopilotConsumptionDashboard", "model.json");
}

/** The last build outcome from status.json, or null when it is missing or unreadable. */
export async function readBuildStatus(path) {
    try {
        const doc = await readOptionalJson(path);
        if (!isObject(doc)) return null;
        const text = key => typeof doc[key] === "string" ? doc[key] : null;
        return { overall: text("overall"), date: text("date"), generated_at: text("generated_at"), build_error: text("build_error") };
    } catch {
        return null;
    }
}

/** Done/dismissed decisions keyed by action or signal id. An unreadable file is treated as empty for display. */
export async function readActionStates(path) {
    try {
        const doc = await readOptionalJson(path);
        return isObject(doc?.items) ? doc.items : {};
    } catch {
        return {};
    }
}

/** The refresh automation id from config.json, falling back to the copy recorded in the model. */
export async function readWorkflowId(paths, model = null) {
    let configured = null;
    try {
        configured = (await readOptionalJson(paths.config))?.workflow_id ?? null;
    } catch {
        configured = null;
    }
    const id = configured || model?.copilot?.workflow_id || null;
    return typeof id === "string" && WORKFLOW_ID.test(id) ? id : null;
}

export function checkActionInput(input) {
    if (!isObject(input)) throw new CanvasRequestError("action_input_invalid", 400, "Send a JSON object with id and state.");
    const keys = Object.keys(input).sort();
    if (keys.length !== 2 || keys[0] !== "id" || keys[1] !== "state") {
        throw new CanvasRequestError("action_input_invalid", 400, "Send exactly id and state.");
    }
    if (typeof input.id !== "string" || !input.id || input.id.length > 200) {
        throw new CanvasRequestError("action_input_invalid", 400, "The action id is invalid.");
    }
    if (input.state !== null && !ACTION_STATES.includes(input.state)) {
        throw new CanvasRequestError("action_input_invalid", 400, `State must be ${ACTION_STATES.join(", ")} or null.`);
    }
    return { id: input.id, state: input.state };
}

async function writeJsonAtomic(path, payload) {
    await mkdir(dirname(path), { recursive: true });
    const tmp = `${path}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
    try {
        await writeFile(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
        await rename(tmp, path);
    } catch (error) {
        await unlink(tmp).catch(() => {});
        throw error;
    }
}

async function applyActionState(paths, id, state, now) {
    const { model } = await readModel(paths.model);
    if (![...model.actions, ...(model.signals || [])].some(action => action?.id === id)) {
        throw new CanvasRequestError("action_unknown", 404, "That action or signal is not in the current snapshot. Reload and try again.");
    }
    let doc;
    try {
        doc = await readOptionalJson(paths.actioned);
    } catch (error) {
        throw new CanvasRequestError("action_state_unreadable", 503, `Cannot read ${paths.actioned} (${error.message}). Fix or remove it, then retry.`);
    }
    if (doc !== undefined && !isObject(doc)) {
        throw new CanvasRequestError("action_state_unreadable", 503, `${paths.actioned} is not a JSON object. Fix or remove it, then retry.`);
    }
    const items = isObject(doc?.items) ? { ...doc.items } : {};
    const at = state ? now().toISOString() : null;
    if (state) items[id] = { state, at };
    else delete items[id];
    await writeJsonAtomic(paths.actioned, { ...(doc || {}), items });
    return { id, state, state_at: at };
}

let actionWrites = Promise.resolve();

/** Records or clears a done/dismissed decision. Writes are serialized across canvas instances in this process. */
export function setActionState({ paths, id, state, now = () => new Date() }) {
    const run = actionWrites.then(() => applyActionState(paths, id, state, now));
    actionWrites = run.catch(() => {});
    return run;
}

export function createRefreshCoordinator({ modelPath, launch, now = Date.now, timeoutMs = 45 * 60 * 1000 }) {
    let state = { phase: "idle", requestedAt: null, completedAt: null, message: null };
    let baseline = null;
    const pending = () => ["launching", "requested"].includes(state.phase);
    const iso = ms => new Date(ms).toISOString();

    function status() {
        if (pending() && now() - state.requestedAt > timeoutMs) {
            state = { ...state, phase: "error", completedAt: iso(now()),
                message: `No rebuilt snapshot appeared within ${Math.round(timeoutMs / 60000)} minutes. Check the automation run in Copilot, then retry.` };
        }
        return { ...state, requestedAt: state.requestedAt === null ? null : iso(state.requestedAt) };
    }

    /** A snapshot that differs from the one present at request time means the rebuild landed, even after a timeout. */
    function observe(revision) {
        if (["requested", "error"].includes(state.phase) && state.requestedAt !== null && revision !== null && revision !== baseline) {
            state = { ...state, phase: "updated", completedAt: iso(now()), message: "Refreshed. Showing the new snapshot." };
        }
        return status();
    }

    /** Ends a pending request when the engine recorded a build failure at or after the request time. */
    function fail(message, at) {
        if (state.phase === "requested" && Number.isFinite(at) && at >= state.requestedAt) {
            state = { ...state, phase: "error", completedAt: iso(now()), message };
        }
        return status();
    }

    async function request() {
        if (["launching", "requested"].includes(status().phase)) {
            throw new CanvasRequestError("refresh_in_progress", 409, "A refresh is already running. Check the automation run in Copilot.");
        }
        // Claim the refresh before any await so an overlapping request sees it and gets a 409.
        state = { phase: "launching", requestedAt: now(), completedAt: null, message: "Asking Copilot to run the refresh automation…" };
        try {
            baseline = await revisionOf(modelPath);
            await launch();
            state = { ...state, phase: "requested",
                message: "Refresh requested. The automation collects every source and rebuilds the snapshot; this usually takes 10–30 minutes." };
            return status();
        } catch (error) {
            state = { ...state, phase: "error", completedAt: iso(now()), message: `Could not request the refresh: ${error.message}` };
            throw new CanvasRequestError("refresh_launch_failed", 502, state.message);
        }
    }
    return { request, observe, fail, status };
}

function headers(res, type, classic = false) {
    res.setHeader("Content-Type", type);
    res.setHeader("Cache-Control", "no-store, max-age=0");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
    if (type.startsWith("text/html")) {
        res.setHeader("Content-Security-Policy", `default-src 'self'; script-src 'self'${classic ? " 'unsafe-inline'" : ""}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'`);
    }
}

function json(res, status, payload) {
    res.statusCode = status;
    headers(res, "application/json; charset=utf-8");
    res.end(JSON.stringify(payload));
}

/** Page navigations (a frame or a new tab) get a readable page, marked so the embedding canvas can offer recovery. */
function notFoundPage(res) {
    res.statusCode = 404;
    headers(res, "text/html; charset=utf-8");
    res.end('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="canvas-error" content="this canvas has no page at that address.">'
        + "<title>Page not found · Customer Pulse</title></head><body><h1>This Customer Pulse page does not exist</h1>"
        + "<p>If Customer Pulse was updated while this canvas was open, close it and open Customer Pulse again to load the update.</p></body></html>");
}

function equalToken(actual, expected) {
    if (typeof actual !== "string" || actual.length !== expected.length) return false;
    return timingSafeEqual(Buffer.from(actual), Buffer.from(expected));
}

async function readJsonBody(req, limit, code) {
    const chunks = [];
    let size = 0;
    for await (const chunk of req) {
        size += chunk.length;
        if (size > limit) throw new CanvasRequestError(`${code}_too_large`, 413, "The request is too large.");
        chunks.push(chunk);
    }
    try {
        return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
        throw new CanvasRequestError(`${code}_invalid`, 400, "The request body must be JSON.");
    }
}

export async function startCanvasServer({ paths = pulsePaths(), refresh, port = 0, pollMs = 4000 }) {
    const token = randomBytes(24).toString("hex");
    const clients = new Set();
    const copilotClients = new Set();
    let address;

    async function revisions() {
        const [model, actioned, status] = await Promise.all([paths.model, paths.actioned, paths.status].map(revisionOf));
        return { model, combined: `${model}|${actioned}|${status}` };
    }

    async function currentBuild() {
        const build = await readBuildStatus(paths.status);
        if (build?.build_error && build.generated_at) {
            refresh.fail(`The refresh ran but could not rebuild the snapshot: ${build.build_error.slice(0, 300)}`, Date.parse(build.generated_at));
        }
        return build;
    }

    async function statusPayload() {
        const [rev, build] = await Promise.all([revisions(), currentBuild()]);
        refresh.observe(rev.model);
        return { refresh: refresh.status(), revision: rev.combined, build };
    }

    function guard(req, code) {
        if (req.headers.origin !== `http://127.0.0.1:${address.port}` || !equalToken(req.headers["x-canvas-token"], token)
            || !req.headers["content-type"]?.startsWith("application/json")) {
            throw new CanvasRequestError(code, 403, "Requests must come from this canvas.");
        }
    }

    const broadcast = (event, data) => {
        for (const client of clients) client.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    };
    const copilotStatus = async () => {
        await statusPayload();
        return { refresh: refresh.status(), revision: await revisionOf(await copilotModelPath(paths)) };
    };

    const server = createServer(async (req, res) => {
        try {
            const route = new URL(req.url, "http://127.0.0.1").pathname;
            if (req.method === "GET" && route === "/copilot") {
                res.writeHead(302, { Location: "/copilot/" });
                res.end();
            } else if (req.method === "GET" && route === "/copilot/") {
                const html = (await readFile(join(assets, "copilot", "index.html"), "utf8")).replace("__CSRF_TOKEN__", token);
                headers(res, "text/html; charset=utf-8");
                res.end(html);
            } else if (req.method === "GET" && route === "/copilot/api/model") {
                json(res, 200, await readCopilotModel(await copilotModelPath(paths)));
            } else if (req.method === "GET" && route === "/copilot/api/status") {
                json(res, 200, await copilotStatus());
            } else if (req.method === "GET" && route === "/copilot/classic") {
                const path = await copilotModelPath(paths);
                let generated;
                try {
                    generated = await readFile(join(dirname(path), "dashboard.html"), "utf8");
                } catch (error) {
                    if (error.code !== "ENOENT") throw error;
                    throw new CanvasRequestError("dashboard_missing", 404, "The classic Copilot dashboard is missing. Run the dashboard refresh.");
                }
                headers(res, "text/html; charset=utf-8", true);
                res.end(generated.replace("<body>", '<body><div style="padding:10px 28px"><a href="./">Back to adoption canvas &larr;</a></div>'));
            } else if (req.method === "GET" && route === "/copilot/events") {
                headers(res, "text/event-stream; charset=utf-8");
                res.setHeader("Connection", "keep-alive");
                res.write(`event: status\ndata: ${JSON.stringify(await copilotStatus())}\n\n`);
                copilotClients.add(res);
                req.on("close", () => copilotClients.delete(res));
            } else if (req.method === "GET" && route === "/") {
                const html = (await readFile(join(assets, "index.html"), "utf8")).replace("__CSRF_TOKEN__", token);
                headers(res, "text/html; charset=utf-8");
                res.end(html);
            } else if (req.method === "GET" && STATIC[route]) {
                const content = await readFile(join(assets, route.slice(1)));
                headers(res, STATIC[route]);
                res.end(content);
            } else if (req.method === "GET" && route === "/api/model") {
                // Stat the overlay files before reading them so a concurrent change always produces a newer revision.
                const [actionedRev, statusRev] = await Promise.all([revisionOf(paths.actioned), revisionOf(paths.status)]);
                const [result, items, build] = await Promise.all([readModel(paths.model), readActionStates(paths.actioned), currentBuild()]);
                refresh.observe(result.revision);
                json(res, 200, {
                    model: withActionStates(result.model, items),
                    revision: `${result.revision}|${actionedRev}|${statusRev}`,
                    build,
                    refresh: refresh.status(),
                });
            } else if (req.method === "GET" && route === "/api/status") {
                json(res, 200, await statusPayload());
            } else if (req.method === "GET" && route === "/events") {
                headers(res, "text/event-stream; charset=utf-8");
                res.setHeader("Connection", "keep-alive");
                res.write(`event: status\ndata: ${JSON.stringify(await statusPayload())}\n\n`);
                clients.add(res);
                req.on("close", () => clients.delete(res));
            } else if (req.method === "POST" && ["/api/refresh", "/copilot/api/refresh"].includes(route)) {
                guard(req, "refresh_forbidden");
                const input = await readJsonBody(req, 256, "refresh_input");
                if (!isObject(input) || Object.keys(input).length) {
                    throw new CanvasRequestError("refresh_input_invalid", 400, "The refresh request does not accept arguments.");
                }
                json(res, 202, { refresh: await refresh.request() });
            } else if (req.method === "POST" && route === "/api/action-state") {
                guard(req, "action_forbidden");
                const { id, state } = checkActionInput(await readJsonBody(req, 512, "action_input"));
                json(res, 200, await setActionState({ paths, id, state }));
            } else if (req.method === "GET" && /\btext\/html\b/.test(req.headers.accept || "")) {
                notFoundPage(res);
            } else {
                json(res, 404, { error: "This canvas route does not exist.", code: "not_found" });
            }
        } catch (error) {
            if (res.headersSent) {
                res.destroy(error);
                return;
            }
            if (error instanceof CanvasRequestError) json(res, error.status, { error: error.message, code: error.code });
            else json(res, 500, { error: `Canvas request failed: ${error.message}`, code: "internal" });
        }
    });

    await new Promise((resolvePromise, reject) => {
        server.once("error", reject);
        server.listen(port, "127.0.0.1", resolvePromise);
    });
    address = server.address();

    let lastRevision = (await revisions()).combined;
    let lastStatus = null;
    let lastCopilotRevision = await revisionOf(await copilotModelPath(paths));
    let lastCopilotStatus = null;
    let polling = false;
    let failing = false;
    const poll = setInterval(async () => {
        if (polling) return;
        polling = true;
        try {
            const payload = await statusPayload();
            const copilot = await copilotStatus();
            for (const client of copilotClients) {
                if (copilot.revision !== lastCopilotRevision) client.write(`event: model\ndata: ${JSON.stringify({ revision: copilot.revision })}\n\n`);
                if (JSON.stringify(copilot) !== lastCopilotStatus) client.write(`event: status\ndata: ${JSON.stringify(copilot)}\n\n`);
                client.write(": keepalive\n\n");
            }
            lastCopilotRevision = copilot.revision;
            lastCopilotStatus = JSON.stringify(copilot);
            if (failing) {
                // Re-send status after a failed poll so clients can clear the error they were shown.
                failing = false;
                lastStatus = null;
            }
            if (payload.revision !== lastRevision) {
                lastRevision = payload.revision;
                broadcast("model", { revision: payload.revision });
            }
            const signature = JSON.stringify({ refresh: payload.refresh, build: payload.build });
            if (signature !== lastStatus) {
                lastStatus = signature;
                broadcast("status", payload);
            }
            for (const client of clients) client.write(": keepalive\n\n");
        } catch (error) {
            failing = true;
            broadcast("error", { error: `Cannot check the local snapshot: ${error.message}` });
            for (const client of copilotClients) client.write(`event: error\ndata: ${JSON.stringify({ error: `Cannot check Copilot: ${error.message}` })}\n\n`);
        } finally {
            polling = false;
        }
    }, pollMs);

    return {
        url: `http://127.0.0.1:${address.port}/`,
        port: address.port,
        async close() {
            clearInterval(poll);
            for (const client of clients) client.end();
            clients.clear();
            for (const client of copilotClients) client.end();
            copilotClients.clear();
            const closed = new Promise((resolvePromise, reject) => server.close(error => error ? reject(error) : resolvePromise()));
            server.closeAllConnections?.();
            await closed;
        },
    };
}
