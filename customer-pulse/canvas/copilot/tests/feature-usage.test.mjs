import assert from "node:assert/strict";
import { test } from "node:test";
import { featureUsage, featureUsagePanel } from "../feature-usage.mjs";

const row = (integration, extra = {}) => ({
    integration, l7: 2, p7: 1, l28: 4, aiu28: 8, u7: 3, up7: 2, u28: 5,
    feature_metrics_known: Object.fromEntries(["l7", "p7", "l28", "aiu28", "u7", "up7", "u28"].map(k => [k, true])),
    weekly: [2, null, 1], ...extra,
});
const enterprise = (slug, integrations, surfaces = {}) => ({
    slug, integrations, settings: { surfaces },
});
const makeModel = enterprises => ({
    enterprises, sources: { "08": {}, "14": {} }, missing_sources: [], asof: { "14": "2030-01-01" },
});

test("exact integrations, policy states, and non-additive user signals remain distinct", () => {
    const model = makeModel([
        enterprise("one", [row("copilot-pr-reviews"), row("copilot-developer-cli")], { "Code review": "enabled" }),
        enterprise("two", [row("copilot-pr-reviews", { l7: 0, l28: 0, u7: 0, u28: 0, aiu28: 0 })], { "Code review": "enabled" }),
        enterprise("three", [], { "Coding agent": "disabled", "Code review": "enabled" }),
        enterprise("four", [row("copilot-developer")]),
    ]);
    const [review, agent] = featureUsage(model);
    assert.equal(review.entries.filter(e => e.gap).length, 1);
    assert.equal(review.entries[2].row, null);
    assert.equal(agent.entries.filter(e => e.row).length, 1, "CLI is not cloud agent");
    const html = featureUsagePanel(model, null, null, new Date("2030-01-02"));
    assert.match(html, /At least using \(7d \/ 28d\): 1 \/ 1/);
    assert.match(html, /Policy enabled \/ disabled \/ other or unknown: 3 \/ 0 \/ 1/);
    assert.match(html, /Enabled with an observed zero 28d UBB signal/);
    assert.doesNotMatch(html, /Users 28d/);
    assert.match(html, /Outcome metrics are not available/);
    assert.match(html, /Week 1: \$4\.00 \(2 observed\)/);
});

test("missing sources, absent rows and incomplete slices are unknown, not zero", () => {
    const e = enterprise("none", [], { "Code review": "enabled" });
    const model = makeModel([e]);
    let html = featureUsagePanel(model, e, null, new Date("2030-01-02"));
    assert.match(html, /usage is unknown/);
    assert.doesNotMatch(html, /Enabled with an observed zero/);
    e.integrations = [row("copilot-pr-reviews", { u28: null })];
    assert.equal(featureUsage(model, e)[0].entries[0].row.u28, null);
    const legacy = row("copilot-pr-reviews", { l28: 0, u28: 0, aiu28: 0 });
    delete legacy.feature_metrics_known;
    e.integrations = [legacy];
    assert.equal(featureUsage(model, e)[0].entries[0].gap, false);
    model.missing_sources = ["14"];
    html = featureUsagePanel(model, e, null, new Date("2030-01-02"));
    assert.match(html, /Integration source unavailable/);
});

test("rendering escapes untrusted labels and discloses missing and stale cutoffs", () => {
    const e = enterprise('bad"><script>alert(1)</script>', [
        row("copilot-pr-reviews", { l7: 0, l28: 0, u7: 0, u28: 0, aiu28: 0 }),
    ], { "Code review": "enabled" });
    const model = makeModel([e]);
    delete model.asof["14"];
    model.stale_sources = { "14": '2030-01-01<script>' };
    const html = featureUsagePanel(model);
    assert.doesNotMatch(html, /<script>/);
    assert.match(html, /&lt;script&gt;/);
    assert.match(html, /freshness cannot be established/);
    assert.match(html, /carried forward/);
});
