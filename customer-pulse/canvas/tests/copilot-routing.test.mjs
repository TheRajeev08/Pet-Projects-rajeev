import assert from "node:assert/strict";
import { test } from "node:test";
import { hashFor, parseHash } from "../model.mjs";
import { embedProblem, renderNav, renderView } from "../views.mjs";

test("Copilot sub-view round trips with the existing drawer and search grammar", () => {
    for (const input of [
        { view: "copilot", subview: "seats", account: null, query: "" },
        { view: "copilot", subview: "seats", account: "synthetic/a", query: "test & more" },
    ]) {
        assert.deepEqual(parseHash(hashFor(input)), input);
    }
    assert.equal(hashFor({ view: "ghe", subview: "seats" }), "#/ghe");
    assert.deepEqual(parseHash("#/copilot"), { view: "copilot", account: null, query: "" });
});

test("Copilot defaults to a same-origin iframe and retains a separate-tab fallback", () => {
    const html = renderView({}, { view: "copilot", copilotAccount: "synthetic/a" });
    assert.match(html, /src="copilot\/\?embed=1#\/account\/synthetic%2Fa"/);
    assert.match(html, /href="#\/copilot\/seats"/);
    assert.match(html, /Open Copilot dashboard in its own tab/);
});

test("Impact is a top-level tab that opens the embedded Impact view directly", () => {
    assert.deepEqual(parseHash("#/impact"), { view: "impact", account: null, query: "" });
    assert.equal(hashFor({ view: "impact", subview: "seats", account: "synthetic/a" }), "#/impact/account/synthetic%2Fa");
    assert.deepEqual(parseHash("#/impact/account/synthetic%2Fa"), { view: "impact", account: "synthetic/a", query: "" });
    assert.ok(renderNav(null, { view: "impact" }).includes('<a href="#/impact" data-view="impact" aria-current="page">Impact</a>'));
    // Independent of the Copilot tab's selected account or sub-view.
    const html = renderView({}, { view: "impact", copilotAccount: "synthetic/a", subview: "seats" });
    assert.match(html, /<iframe class="copilot-frame" name="copilot-impact" title="Copilot Impact dashboard" data-embed src="copilot\/\?embed=1#\/impact">/);
    assert.doesNotMatch(html, /synthetic|copilot-dashboard|Seats &amp; actions/);
    assert.match(html, /href="copilot\/#\/impact" target="_blank"/);
});

test("a failed embed renders a recovery panel for that page only, never the raw error", () => {
    const failure = { src: "copilot/?embed=1", detail: "the canvas server answered “<b>nope</b>”" };
    const copilot = renderView({}, { view: "copilot", embedFailure: failure });
    assert.doesNotMatch(copilot, /<iframe/);
    assert.match(copilot, /role="alert"><h2>Copilot adoption dashboard could not load<\/h2>/);
    assert.match(copilot, /&lt;b&gt;nope&lt;\/b&gt;/);
    assert.match(copilot, /data-command="retry-embed"/);
    assert.match(copilot, /href="#\/copilot\/seats"/);
    assert.match(renderView({}, { view: "impact", embedFailure: failure }), /<iframe[^>]+src="copilot\/\?embed=1#\/impact"/);
});

test("embedProblem accepts the dashboard and explains JSON errors, error pages and unreachable frames", () => {
    const frame = (doc, throws = false) => ({ get contentDocument() { if (throws) throw new Error("blocked"); return doc; } });
    const doc = (contentType, text = "", marker = null) => ({
        contentType, body: { textContent: text },
        querySelector: selector => (selector === 'meta[name="canvas-error"]' && marker !== null ? { getAttribute: () => marker } : null),
    });
    assert.equal(embedProblem(frame(doc("text/html"))), null);
    assert.equal(embedProblem(frame(doc("application/json", '{"error":"This canvas route does not exist.","code":"not_found"}'))),
        "the canvas server answered “This canvas route does not exist.”");
    assert.equal(embedProblem(frame(doc("application/json", "not json"))), "the canvas server sent something other than the dashboard page.");
    assert.equal(embedProblem(frame(doc("text/html", "", "gone."))), "gone.");
    assert.equal(embedProblem(frame(null)), "the page could not be reached.");
    assert.equal(embedProblem(frame(null, true)), "the page could not be reached.");
});
