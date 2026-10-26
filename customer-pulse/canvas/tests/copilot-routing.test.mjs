import assert from "node:assert/strict";
import { test } from "node:test";
import { hashFor, parseHash } from "../model.mjs";
import { renderView } from "../views.mjs";

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
