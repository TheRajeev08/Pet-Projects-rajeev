import { strict as assert } from "node:assert";
import { test } from "node:test";
import { renderRecommendations } from "../recommendations.mjs";

const feed = { status: "available", source_date: "2029-12-30", carried: true, collected_from: "2030-01-01T00:00:00Z", possibly_truncated: true, items: [
    { account_id: "a", account_name: "<SCRIPT>", in_book: true, status: "New", action_by: "2029-01-01",
        url: "javascript:alert(1)", signals: [{ recommended_product: "Copilot Business", ml_driven_insights: "<img src=x>" }] },
    { account_id: "b", in_book: false, status: "Stale", signals: [{ recommended_product: "GHAS" }] },
] };
test("unknown, empty, filters, escaping, and safe links", () => {
    assert.match(renderRecommendations(null), /count is unknown/);
    assert.match(renderRecommendations({ status: "available", items: [] }), /No recommendations match/);
    assert.match(renderRecommendations({ status: "available", items: [{ signals: [null] }] }), /count is unknown/);
    const open = renderRecommendations(feed, { now: new Date("2030-01-01T12:00:00Z") });
    assert.match(open, /Possible truncation/);
    assert.match(open, /Carried forward/);
    assert.match(open, /Source age: 2 day/);
    assert.match(open, /overdue/);
    assert.match(open, /&lt;SCRIPT&gt;/);
    assert.match(open, /&lt;img src=x&gt;/);
    assert.doesNotMatch(open, /javascript:|<script>|<img/i);
    assert.doesNotMatch(open, /GHAS/);
    assert.match(renderRecommendations(feed, { filter: "stale" }), /outside owned book/);
    assert.doesNotMatch(renderRecommendations(feed, { filter: "stale" }), /Copilot Business/);
});
test("Impact requires an account and shows only its Copilot recommendations", () => {
    assert.match(renderRecommendations(feed, { impact: true }), /No confirmed/);
    const html = renderRecommendations(feed, { impact: true, accountId: "a" });
    assert.match(html, /Copilot Business/);
    assert.doesNotMatch(html, /GHAS/);
});
