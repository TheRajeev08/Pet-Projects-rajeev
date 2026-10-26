// Customer Pulse canvas client: hash routing, rendering through views.mjs, live updates and local action state.
import { accountIndex, clipQuery, hashFor, isClosed, parseHash, SIGNAL_PRIORITIES, SIGNAL_SOURCES, validateModel } from "./model.mjs";
import { VIEW_LABELS, esc, renderDrawer, renderNav, renderNotices, renderTopbar, renderUnavailable, renderView } from "./views.mjs";

const els = {
    shell: document.getElementById("app"),
    topbar: document.getElementById("topbar"),
    nav: document.getElementById("nav"),
    notices: document.getElementById("notices"),
    main: document.getElementById("main"),
    drawer: document.getElementById("drawer-host"),
    toast: document.getElementById("toast"),
};
const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
const DISMISS_KEY = "customer-pulse:refresh-dismissed";
const UPDATED_NOTICE_MS = 15 * 60 * 1000;
const TOAST_MS = 8000;
const RETRY_MS = 1500;
const RETRIES = 5;
const OFFLINE = "Cannot reach the canvas server. Close and reopen the canvas if this continues.";
const ACTION_STATE = { done: "done", dismiss: "dismissed", reopen: null };
const ACTION_TOAST = { done: "Marked done.", dismissed: "Dismissed.", null: "Reopened." };

const state = {
    model: null,
    seen: null,
    build: null,
    refresh: { phase: "idle", requestedAt: null, completedAt: null, message: null },
    refreshSending: false,
    loading: true,
    loadError: null,
    error: null,
    liveError: null,
    statusError: null,
    view: "overview",
    account: null,
    subview: null,
    copilotAccount: null,
    query: "",
    sort: {},
    expanded: new Set(),
    kind: {},
    showClosed: false,
    signalFilters: { priority: "all", source: "all", status: "open" },
    pending: new Set(),
    dismissed: readDismissed(),
    toast: null,
};

function readDismissed() {
    try {
        return sessionStorage.getItem(DISMISS_KEY);
    } catch {
        return null;
    }
}

function writeDismissed(value) {
    try {
        sessionStorage.setItem(DISMISS_KEY, value);
    } catch {
        // Storage can be unavailable in embedded hosts; the dismissal then lasts for this page only.
    }
}

class RequestFailure extends Error {
    constructor(message, code, status = 0) {
        super(message);
        this.code = code;
        this.status = status;
    }
}

async function request(path, options = {}) {
    let response;
    try {
        response = await fetch(path, { cache: "no-store", ...options });
    } catch {
        throw new RequestFailure(OFFLINE, "offline");
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
        throw new RequestFailure(payload?.error || `The canvas server answered HTTP ${response.status}.`, payload?.code || "http_error", response.status);
    }
    if (!payload || typeof payload !== "object") throw new RequestFailure("The canvas server sent an unreadable answer.", "bad_response", response.status);
    return payload;
}

const post = (path, body) => request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Canvas-Token": token },
    body: JSON.stringify(body),
});

// Rendering ----------------------------------------------------------------------------------------

const written = new WeakMap();

/** Writes markup only when it changed, so unchanged regions keep their DOM, focus and scroll position. */
function setHTML(element, html) {
    if (written.get(element) === html) return false;
    element.innerHTML = html;
    written.set(element, html);
    return true;
}

const refreshKey = refresh => `${refresh.phase}|${refresh.completedAt || refresh.requestedAt || ""}`;

function refreshDismissed() {
    const refresh = state.refresh;
    if (!refresh || !["error", "updated"].includes(refresh.phase)) return false;
    if (state.dismissed === refreshKey(refresh)) return true;
    const completed = Date.parse(refresh.completedAt || "");
    return refresh.phase === "updated" && Number.isFinite(completed) && Date.now() - completed > UPDATED_NOTICE_MS;
}

function uiState() {
    return {
        view: state.view,
        account: state.account,
        subview: state.subview,
        copilotAccount: state.copilotAccount,
        query: state.query,
        sort: state.sort,
        expanded: state.expanded,
        kind: state.kind,
        showClosed: state.showClosed,
        signalFilters: state.signalFilters,
        refresh: state.refresh,
        refreshDismissed: refreshDismissed(),
        build: state.build,
        error: [state.error, state.liveError, state.statusError].filter(Boolean).join(" "),
        loading: state.loading,
        now: new Date(),
    };
}

function render() {
    const before = describe(document.activeElement);
    const ui = uiState();
    setHTML(els.topbar, renderTopbar(state.model, ui));
    setHTML(els.nav, renderNav(state.model, ui));
    setHTML(els.notices, renderNotices(state.model, ui));
    if (state.model) setHTML(els.main, renderView(state.model, ui));
    else if (state.loadError) setHTML(els.main, renderUnavailable(state.loadError));
    els.main.setAttribute("aria-busy", String(!state.model && !state.loadError));
    renderDrawerHost(ui);
    renderToast();
    updateTitle();
    restoreFocus(before);
}

function updateTitle() {
    const account = state.model && state.account ? accountIndex(state.model).get(state.account) : null;
    document.title = ["Customer Pulse", VIEW_LABELS[state.view], account ? account.name || account.id : null].filter(Boolean).join(" · ");
}

// Focus ---------------------------------------------------------------------------------------------
// Re-rendering replaces elements, so focus is tracked by stable attributes and restored onto the new element.

const FOCUS_ATTRS = ["data-focus-key", "data-filter", "data-account", "data-view", "data-command", "data-more", "data-close",
    "data-undo", "data-toast-close", "data-search", "data-sort"];
const roots = () => [els.drawer, els.toast, els.main, els.notices, els.nav, els.topbar];
const keySelector = key => `[data-focus-key="${CSS.escape(key)}"]`;
let pendingFocus = null;

function selectorFor(element) {
    const name = FOCUS_ATTRS.find(attr => element.hasAttribute(attr));
    if (!name) return null;
    let selector = `[${name}="${CSS.escape(element.getAttribute(name))}"]`;
    if (name === "data-filter") selector += `[data-value="${CSS.escape(element.getAttribute("data-value") || "")}"]`;
    return selector;
}

function describe(element) {
    if (!(element instanceof Element) || element === document.body || !element.isConnected) return null;
    const root = roots().find(candidate => candidate.contains(element));
    if (!root) return null;
    const selector = selectorFor(element);
    if (!selector) return { root, selectors: [], index: 0, selection: null };
    let selection = null;
    try {
        if (typeof element.selectionStart === "number") selection = [element.selectionStart, element.selectionEnd];
    } catch {
        selection = null;
    }
    return { root, selectors: [selector], index: Math.max(0, [...root.querySelectorAll(selector)].indexOf(element)), selection };
}

const focusLost = () => !document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected;

function focusFrom(description) {
    if (!description?.root?.isConnected) return false;
    for (const [position, selector] of description.selectors.entries()) {
        const matches = description.root.querySelectorAll(selector);
        const target = position === 0 ? matches[description.index] || matches[matches.length - 1] : matches[0];
        if (!target || target.closest("[inert]")) continue;
        target.focus({ preventScroll: true });
        if (description.selection && typeof target.setSelectionRange === "function") {
            try {
                target.setSelectionRange(...description.selection);
            } catch {
                // Not a text field.
            }
        }
        if (document.activeElement === target) return true;
    }
    return false;
}

function focusFallback() {
    const close = drawerOpen ? els.drawer.querySelector(".drawer .close") : null;
    (close || els.main).focus({ preventScroll: true });
}

function restoreFocus(before) {
    if (pendingFocus) {
        const target = pendingFocus;
        pendingFocus = null;
        const onAction = document.activeElement?.getAttribute?.("data-id") === target.id;
        if ((focusLost() || onAction) && focusFrom(target)) return;
    }
    if (!focusLost()) return;
    if (!focusFrom(before) && before) focusFallback();
}

/** Where focus should go after an action leaves or changes its row: its counterpart, then a neighbour. */
function actionFocus(id, next, button) {
    const root = button ? roots().find(candidate => candidate.contains(button)) : drawerOpen ? els.drawer : els.main;
    if (!root) return null;
    const selectors = [keySelector(`${next ? "reopen" : "done"}:${id}`)];
    const row = button?.closest(".action-row, .signal");
    for (const sibling of [row?.nextElementSibling, row?.previousElementSibling]) {
        const key = sibling?.querySelector("[data-focus-key]")?.getAttribute("data-focus-key");
        if (key) selectors.push(keySelector(key));
    }
    const toggle = button?.closest(".panel")?.querySelector('[data-toggle="closed"]')?.getAttribute("data-focus-key");
    if (toggle) selectors.push(keySelector(toggle));
    if (row?.classList.contains("signal")) selectors.push(keySelector("signal-filter:status"));
    return { id, root, selectors, index: 0, selection: null };
}

// Drawer --------------------------------------------------------------------------------------------

let drawerOpen = false;
let drawerAccount = null;
let opener = null;
let openingTimer = null;

function renderDrawerHost(ui) {
    if (!state.model || !state.account) {
        if (!drawerOpen) return;
        drawerOpen = false;
        drawerAccount = null;
        clearTimeout(openingTimer);
        els.drawer.classList.remove("opening");
        setHTML(els.drawer, "");
        els.shell.inert = false;
        document.body.classList.remove("drawer-open");
        const back = opener;
        opener = null;
        if (!focusFrom(back)) els.main.focus({ preventScroll: true });
        return;
    }
    const body = els.drawer.querySelector(".drawer-body");
    const scroll = body && drawerAccount === state.account ? body.scrollTop : 0;
    if (setHTML(els.drawer, renderDrawer(state.model, ui))) {
        const fresh = els.drawer.querySelector(".drawer-body");
        if (fresh) fresh.scrollTop = scroll;
    }
    els.shell.inert = true;
    document.body.classList.add("drawer-open");
    if (drawerOpen && drawerAccount === state.account) return;
    if (!drawerOpen) {
        // The class drives the slide-in once; removing it stops later re-renders from animating again.
        els.drawer.classList.add("opening");
        clearTimeout(openingTimer);
        openingTimer = setTimeout(() => els.drawer.classList.remove("opening"), 400);
    }
    drawerOpen = true;
    drawerAccount = state.account;
    els.drawer.querySelector(".drawer .close")?.focus({ preventScroll: true });
}

// Toast ---------------------------------------------------------------------------------------------

let toastTimer = null;

function showToast(message, { tone = "info", undo = null } = {}) {
    state.toast = { message, tone, undo };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(expireToast, TOAST_MS);
    render();
}

function expireToast() {
    // Keep the message while the pointer or keyboard is on it, so Undo stays reachable.
    if (els.toast.matches(":hover") || els.toast.contains(document.activeElement)) {
        toastTimer = setTimeout(expireToast, 2000);
        return;
    }
    hideToast();
}

function hideToast() {
    clearTimeout(toastTimer);
    if (!state.toast) return;
    state.toast = null;
    render();
}

function runUndo() {
    const undo = state.toast?.undo;
    if (!undo) return;
    clearTimeout(toastTimer);
    // The toast is removed by the undo's own render, so focus moves to the restored action rather than the page.
    state.toast = null;
    undo();
    render();
}

function renderToast() {
    const toast = state.toast;
    setHTML(els.toast, toast
        ? `<div class="toast-card${toast.tone === "error" ? " error" : ""}"><p>${esc(toast.message)}</p>`
            + `${toast.undo ? '<button type="button" class="button tiny" data-undo>Undo</button>' : ""}`
            + '<button type="button" class="button tiny subtle icon" data-toast-close aria-label="Dismiss message">×</button></div>'
        : "");
}

// Routing -------------------------------------------------------------------------------------------
// The hash "#/<view>[/account/<id>][?q=<search>]" is the source of truth, so Back closes the drawer.

let closingTimer = null;

function setHistory(hash, mode, data = null) {
    try {
        if (mode === "push") history.pushState(data, "", hash);
        else history.replaceState(data, "", hash);
        return true;
    } catch {
        return false;
    }
}

function apply(next) {
    const viewChanged = next.view !== state.view;
    state.view = next.view;
    state.subview = next.subview || null;
    state.account = next.account;
    state.query = next.query;
    if (viewChanged) window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    render();
}

function route() {
    clearTimeout(closingTimer);
    closingTimer = null;
    const next = parseHash(location.hash);
    const canonical = hashFor(next);
    if (location.hash !== canonical) setHistory(canonical, "replace", next.account ? history.state : null);
    apply(next);
}

function openAccount(id, trigger) {
    if (!id) return;
    if (!drawerOpen) opener = describe(trigger);
    const next = { view: state.view, subview: state.subview, account: id, query: state.query };
    setHistory(hashFor(next), state.account ? "replace" : "push", { drawer: true });
    apply(next);
}

function closeDrawer() {
    if (!state.account || closingTimer) return;
    const next = { view: state.view, subview: state.subview, account: null, query: state.query };
    if (history.state?.drawer) {
        // Return to the entry the drawer was opened from; fall back if the host swallows the navigation.
        closingTimer = setTimeout(() => {
            closingTimer = null;
            if (!state.account) return;
            setHistory(hashFor(next), "replace");
            apply(next);
        }, 400);
        history.back();
        return;
    }
    setHistory(hashFor(next), "replace");
    apply(next);
}

/** Updates only the searched table and its count, so the search box keeps focus, caret and IME state. */
function swapSearchRegion(input) {
    const panel = input.closest("section.panel");
    const labelled = panel?.getAttribute("aria-labelledby");
    if (!labelled) return false;
    const html = renderView(state.model, uiState());
    const template = document.createElement("template");
    template.innerHTML = html;
    const fresh = template.content.querySelector(`section.panel[aria-labelledby="${CSS.escape(labelled)}"]`);
    const parts = [`#${CSS.escape(labelled)}`, "[data-region]"].map(selector => [panel.querySelector(selector), fresh?.querySelector(selector)]);
    if (parts.some(([current, replacement]) => !current || !replacement)) return false;
    for (const [current, replacement] of parts) current.replaceWith(replacement);
    written.set(els.main, html);
    return true;
}

function onSearch(input) {
    const query = clipQuery(input.value);
    if (query === state.query) return;
    state.query = query;
    setHistory(hashFor({ view: state.view, subview: state.subview, account: state.account, query }), "replace", history.state);
    if (!state.model || !swapSearchRegion(input)) render();
}

// Data ----------------------------------------------------------------------------------------------

let loadingRun = null;
let queuedLoad = null;
let events = null;

/** Single-flight load; a request that arrives mid-load runs once more afterwards. */
function loadModel(options = {}) {
    if (loadingRun) {
        queuedLoad = { toast: options.toast || queuedLoad?.toast || null };
        return loadingRun;
    }
    loadingRun = fetchModel(options).finally(() => {
        loadingRun = null;
        if (queuedLoad) {
            const next = queuedLoad;
            queuedLoad = null;
            loadModel(next);
        }
    });
    return loadingRun;
}

async function fetchModel({ toast = null, attempt = 0 } = {}) {
    state.loading = true;
    try {
        const payload = await request("/api/model", { headers: { Accept: "application/json" } });
        let model;
        try {
            model = validateModel(payload.model);
        } catch (error) {
            throw new RequestFailure(`The snapshot is not valid: ${error.message}.`, "dashboard_invalid");
        }
        state.model = model;
        state.seen = payload.revision ?? null;
        state.build = payload.build ?? null;
        if (payload.refresh && !state.refreshSending) state.refresh = payload.refresh;
        state.loadError = null;
        state.error = null;
        state.loading = false;
        render();
        if (toast) showToast(toast);
    } catch (error) {
        if (error.code === "dashboard_updating" && attempt < RETRIES) {
            await new Promise(resolve => setTimeout(resolve, RETRY_MS));
            return fetchModel({ toast, attempt: attempt + 1 });
        }
        if (error.code === "offline") state.seen = null;
        if (state.model) state.error = `Could not load the latest snapshot. ${error.message}`;
        else state.loadError = { code: error.code || "unknown", message: error.message };
        state.loading = false;
        render();
    }
}

/** Revisions cover the snapshot, action states and build status; each new one is loaded once. */
function noteRevision(revision) {
    if (typeof revision !== "string" || revision === state.seen) return;
    state.seen = revision;
    loadModel();
}

function handleStatus(payload) {
    if (!payload || typeof payload !== "object") return;
    state.statusError = null;
    state.liveError = null;
    if (payload.refresh && !state.refreshSending) state.refresh = payload.refresh;
    if ("build" in payload) state.build = payload.build ?? null;
    render();
    noteRevision(payload.revision);
}

async function updateStatus() {
    try {
        handleStatus(await request("/api/status", { headers: { Accept: "application/json" } }));
    } catch (error) {
        state.statusError = error.code === "offline" ? OFFLINE : `Cannot check for updates. ${error.message}`;
        render();
    }
}

const parse = text => {
    try {
        return JSON.parse(text);
    } catch {
        return null;
    }
};

function connectEvents() {
    if (typeof EventSource !== "function") return;
    events = new EventSource("/events");
    events.addEventListener("status", event => handleStatus(parse(event.data)));
    events.addEventListener("model", event => noteRevision(parse(event.data)?.revision));
    events.addEventListener("open", () => {
        if (!state.liveError && !state.statusError) return;
        state.liveError = null;
        state.statusError = null;
        render();
    });
    // The server's own "error" events carry data; data-less ones are connection drops, which EventSource retries.
    events.addEventListener("error", event => {
        if (!(event instanceof MessageEvent) || !event.data) return;
        state.liveError = parse(event.data)?.error || "Live updates failed.";
        render();
    });
}

async function requestRefresh() {
    if (state.refreshSending || ["launching", "requested"].includes(state.refresh?.phase)) return;
    state.refreshSending = true;
    state.refresh = { phase: "launching", requestedAt: new Date().toISOString(), completedAt: null, message: "Asking Copilot to run the refresh automation…" };
    render();
    try {
        const payload = await post("/api/refresh", {});
        state.refreshSending = false;
        if (payload.refresh) state.refresh = payload.refresh;
    } catch (error) {
        state.refreshSending = false;
        if (error.code === "refresh_in_progress") {
            state.refresh = { ...state.refresh, phase: "requested", message: error.message };
            await updateStatus();
        } else {
            state.refresh = { phase: "error", requestedAt: null, completedAt: new Date().toISOString(), message: error.message };
        }
    }
    render();
}

function patchAction(id, replace) {
    const key = state.model.actions.some(action => action.id === id) ? "actions" : "signals";
    state.model = { ...state.model, [key]: (state.model[key] || []).map(action => action?.id === id ? replace(action) : action) };
}

/** Records done, dismissed or reopened (null) locally, optimistically, with Undo. Nothing goes to Salesforce. */
async function changeAction(id, next, { button = null, undoable = true } = {}) {
    const original = [...(state.model?.actions || []), ...(state.model?.signals || [])].find(action => action?.id === id);
    if (!original || state.pending.has(id) || !(next === null || next === "done" || next === "dismissed")) return;
    const signal = state.model.signals?.includes(original);
    const previous = isClosed(original) ? original.status ?? original.state : null;
    if (previous === next) return;
    state.pending.add(id);
    pendingFocus = actionFocus(id, next, button);
    const decision = value => signal ? { status: value || "open" } : { state: value };
    patchAction(id, action => ({ ...action, ...decision(next), state_at: next ? new Date().toISOString() : null, new: next ? false : action.new }));
    render();
    try {
        const result = await post("/api/action-state", { id, state: next });
        patchAction(id, action => ({ ...action, ...decision(result.state ?? null), state_at: result.state_at ?? null }));
        if (undoable) showToast(ACTION_TOAST[String(next)], { undo: () => changeAction(id, previous, { undoable: false }) });
        else showToast("Restored.");
    } catch (error) {
        patchAction(id, () => original);
        showToast(`Could not update the ${signal ? "signal" : "action"}. ${error.message}`, { tone: "error" });
    } finally {
        state.pending.delete(id);
        render();
        loadModel();
    }
}

// Events --------------------------------------------------------------------------------------------

const CLICKABLE = "[data-copilot-account],[data-account],[data-close],[data-action],[data-command],[data-filter],[data-more],[data-sort],[data-undo],[data-toast-close]";
const KINDS = ["all", "risk", "opportunity"];
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

function runCommand(command) {
    if (command === "reload") {
        loadModel({ toast: "Reloaded the snapshot." });
    } else if (command === "refresh") {
        requestRefresh();
    } else if (command === "dismiss-refresh") {
        state.dismissed = refreshKey(state.refresh);
        writeDismissed(state.dismissed);
        render();
    }
}

/** An active column flips direction; a new numeric column starts high-to-low, except renewal (soonest first). */
function sortBy(button) {
    const key = button.getAttribute("data-sort");
    const header = button.closest("th");
    const current = header?.getAttribute("aria-sort");
    const dir = current ? (current === "ascending" ? -1 : 1) : header?.classList.contains("num") && key !== "renewal" ? -1 : 1;
    state.sort[button.getAttribute("data-table")] = { key, dir };
    render();
}

function toggleMore(button) {
    const key = button.getAttribute("data-more");
    if (state.expanded.has(key)) state.expanded.delete(key);
    else state.expanded.add(key);
    render();
    // Collapsing a long list moves the toggle up; keep it on screen.
    if (document.activeElement?.getAttribute?.("data-more") === key) document.activeElement.scrollIntoView({ block: "nearest" });
}

document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target.closest(CLICKABLE) : null;
    if (!target || target.closest("[inert]")) return;
    if (target.hasAttribute("data-copilot-account")) {
        event.preventDefault();
        state.copilotAccount = target.getAttribute("data-copilot-account");
        const next = { view: "copilot", account: null, query: "" };
        setHistory(hashFor(next), "push");
        apply(next);
    } else if (target.hasAttribute("data-account")) {
        event.preventDefault();
        openAccount(target.getAttribute("data-account"), target);
    } else if (target.hasAttribute("data-close")) {
        closeDrawer();
    } else if (target.hasAttribute("data-action")) {
        const name = target.getAttribute("data-action");
        if (name in ACTION_STATE) changeAction(target.getAttribute("data-id"), ACTION_STATE[name], { button: target });
    } else if (target.hasAttribute("data-command")) {
        runCommand(target.getAttribute("data-command"));
    } else if (target.hasAttribute("data-filter")) {
        const filter = target.getAttribute("data-filter");
        const value = target.getAttribute("data-value");
        if (filter.startsWith("kind:") && KINDS.includes(value)) {
            state.kind[filter.slice(5)] = value;
            render();
        }
    } else if (target.hasAttribute("data-more")) {
        toggleMore(target);
    } else if (target.hasAttribute("data-sort")) {
        sortBy(target);
    } else if (target.hasAttribute("data-undo")) {
        runUndo();
    } else if (target.hasAttribute("data-toast-close")) {
        hideToast();
    }
});

document.addEventListener("change", event => {
    const input = event.target;
    if (input instanceof HTMLSelectElement && input.matches("[data-signal-filter]")) {
        const key = input.getAttribute("data-signal-filter");
        const options = { priority: ["all", ...SIGNAL_PRIORITIES], source: ["all", ...Object.keys(SIGNAL_SOURCES)], status: ["open", "done", "dismissed", "all"] };
        if (options[key]?.includes(input.value)) {
            state.signalFilters[key] = input.value;
            render();
        }
    }
    if (input instanceof HTMLInputElement && input.matches('[data-toggle="closed"]')) {
        state.showClosed = input.checked;
        render();
    }
});

document.addEventListener("input", event => {
    if (event.target instanceof HTMLInputElement && event.target.matches("[data-search]")) onSearch(event.target);
});

const isTyping = target => target instanceof HTMLElement
    && (target.isContentEditable || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
        || (target instanceof HTMLInputElement && !["checkbox", "radio", "button", "submit", "reset"].includes(target.type)));

/** Keeps Tab inside the open drawer (and the toast, so Undo stays reachable). */
function trapTab(event) {
    const focusable = [...els.drawer.querySelectorAll(FOCUSABLE), ...els.toast.querySelectorAll(FOCUSABLE)];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const inside = focusable.includes(document.activeElement);
    if (event.shiftKey && (!inside || document.activeElement === first)) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
        event.preventDefault();
        first.focus();
    }
}

document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.isComposing) return;
    const plain = !event.metaKey && !event.ctrlKey && !event.altKey;
    if (event.key === "Escape") {
        if (state.account) {
            event.preventDefault();
            closeDrawer();
        } else if (state.toast && els.toast.contains(event.target)) {
            hideToast();
        }
    } else if (event.key === "Tab" && drawerOpen) {
        trapTab(event);
    } else if (event.key === "/" && plain && !isTyping(event.target) && !state.account) {
        const search = els.main.querySelector("input[data-search]");
        if (search) {
            event.preventDefault();
            search.focus();
            search.select();
        }
    } else if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "z"
        && !isTyping(event.target) && state.toast?.undo) {
        event.preventDefault();
        runUndo();
    }
});

// Boot ----------------------------------------------------------------------------------------------

const liveOpen = () => typeof EventSource === "function" && events?.readyState === EventSource.OPEN;

window.addEventListener("hashchange", route);
window.addEventListener("popstate", route);
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") updateStatus();
});
route();
loadModel();
connectEvents();
// Poll only while the live stream is down; re-render each minute so the stale-snapshot notice stays truthful.
setInterval(() => {
    if (document.visibilityState === "visible" && !liveOpen()) updateStatus();
}, 15000);
setInterval(() => {
    if (document.visibilityState === "visible") render();
}, 60000);
