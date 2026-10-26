import assert from "node:assert/strict";
import { test } from "node:test";
import { formatSeatEvent, summarize } from "../model.mjs";
import { esc, renderDrawer, renderView } from "../views.mjs";
import { syntheticEvent, syntheticModel } from "./helpers.mjs";

const event = extra => ({
    family: "ghe", measure: "contracted", kind: "added", from: 10, to: 12, delta: 2,
    date: "2026-10-27", since: "2026-10-26", gap_days: 1,
    text: "+2 GHE contracted seats (10 → 12)", ...extra,
});

const cases = [
    ["increase", {}, "GHE seats under contract increased by 2 seats (from 10 to 12)"],
    ["decrease", { kind: "dropped", from: 12, to: 10, delta: -2 },
        "GHE seats under contract decreased by 2 seats (from 12 to 10)"],
    ["singular increase", { to: 11, delta: 1 }, "GHE seats under contract increased by 1 seat (from 10 to 11)"],
    ["singular decrease", { kind: "dropped", to: 9, delta: -1 }, "GHE seats under contract decreased by 1 seat (from 10 to 9)"],
    ["new product", { family: "ghcp", kind: "new_product", from: 0, to: 12, delta: 12 },
        "Copilot seats under contract increased by 12 seats (from 0 to 12)"],
    ["to zero", { kind: "product_to_zero", to: 0, delta: -10 },
        "GHE seats under contract decreased by 10 seats (from 10 to 0)"],
    ["no change", { to: 10, delta: 0 }, "GHE seats under contract remained unchanged (from 10 to 10)"],
    ["zero to zero", { from: 0, to: 0, delta: 0 }, "GHE seats under contract remained unchanged (from 0 to 0)"],
    ["assigned", { family: "ghcp", measure: "assigned" }, "Copilot assigned seats increased by 2 seats (from 10 to 12)"],
    ["metered", { measure: "metered", to: 11, delta: 1 }, "GHE metered users increased by 1 user (from 10 to 11)"],
    ["billable", { measure: "ubu" }, "GHE billable users increased by 2 users (from 10 to 12)"],
    ["committer", { family: "ghas", measure: "committers", to: 9, delta: -1, kind: "dropped" },
        "GHAS active committers decreased by 1 committer (from 10 to 9)"],
    ["product type", { family: "ghas", product_type: "GHSP" },
        "Secret Protection seats under contract increased by 2 seats (from 10 to 12)"],
    ["large numbers", { from: 1000, to: 2000, delta: 1000 },
        "GHE seats under contract increased by 1,000 seats (from 1,000 to 2,000)"],
    ["trend up", { kind: "trend_up", gap_days: 8, since: "2026-10-19" },
        "GHE seats under contract increased by 2 seats over 7+ days (from 10 to 12)"],
    ["trend down", { kind: "trend_down", to: 8, delta: -2, gap_days: 8, since: "2026-10-19" },
        "GHE seats under contract decreased by 2 seats over 7+ days (from 10 to 8)"],
    ["stopped reporting", { measure: "metered", kind: "stopped", to: null, delta: null },
        "GHE metered users stopped reporting (last 10)"],
    ["started reporting", { measure: "assigned", kind: "started", from: null, delta: null, to: 0 },
        "GHE assigned seats started reporting at 0"],
    ["resumed reporting", { measure: "ubu", kind: "resumed", from: 0, to: 1, delta: 1 },
        "GHE billable users reporting again (from 0 to 1)"],
    ["reporting gap", { measure: "assigned", kind: "resumed", gap_days: 3, since: "2026-10-24" },
        "GHE assigned seats reporting again (from 10 to 12) since 2026-10-24"],
    ["invalid gap date", { gap_days: 3, since: "2026-99-99" },
        "GHE seats under contract increased by 2 seats (from 10 to 12)"],
    ["real change after gap", { after_gap: true, gap_days: 3, since: "2026-10-24" },
        "GHE seats under contract increased by 2 seats (from 10 to 12) since 2026-10-24"],
    ["saved gap suffix", { text: "old wording since 2026-10-24", since: "2026-10-24", gap_days: undefined },
        "GHE seats under contract increased by 2 seats (from 10 to 12) since 2026-10-24"],
];

for (const [name, extra, expected] of cases) {
    test(`seat wording: ${name}`, () => {
        const input = event(extra);
        const original = structuredClone(input);
        assert.equal(formatSeatEvent(input), expected);
        assert.deepEqual(input, original, "formatting must not mutate the saved event");
    });
}

test("incomplete and unknown events preserve their saved text, never inventing zero or direction", () => {
    for (const extra of [
        { from: null }, { to: null }, { from: undefined }, { to: NaN }, { delta: null },
        { delta: Infinity }, { from: "10" }, { kind: "future_kind" }, { measure: "future_measure" },
        { measure: "toString" }, { kind: undefined }, { family: undefined },
        { kind: "migration", detail: [{ type: "GHSP", from: 10, to: 0 }, { type: "GHCS", from: 0, to: 10 }] },
        { family: "book", measure: "owner", kind: "entered_book" },
        { family: "book", measure: "owner", kind: "left_book" },
    ]) {
        const input = event({ ...extra, text: "Saved <event> & details since 2026-10-24" });
        assert.equal(formatSeatEvent(input), input.text);
    }
    assert.equal(formatSeatEvent({}), "");
    for (const kind of ["stopped", "started", "resumed"]) {
        const text = formatSeatEvent(event({ kind, measure: "assigned",
            from: kind === "started" ? null : 10, to: kind === "stopped" ? null : 12, delta: null }));
        assert.doesNotMatch(text, /increased|decreased|contract/);
    }
});

test("all native surfaces and summary replace stale snapshot wording without changing model or summary shape", () => {
    for (const [family, view] of [["ghe", "ghe"], ["ghas", "ghas"], ["ghcp", "copilot"]]) {
        for (const extra of [{}, { kind: "dropped", from: 12, to: 10, delta: -2 }]) {
            const model = syntheticModel();
            model.signals = [{ id: "sig:synthetic", priority: "Info", status: "open" }];
            const change = syntheticEvent("wording", model.accounts[0], family, "Notify", model.date, extra.delta ?? 2,
                event({ family, ...extra }));
            model.events = [change];
            const original = structuredClone(model);
            const expected = formatSeatEvent(change);
            for (const html of [renderView(model, { view: "overview" }), renderView(model, { view }),
                renderDrawer(model, { account: model.accounts[0].id })]) {
                assert.ok(html.includes(`<p class="change-text">${esc(expected)}</p>`));
                assert.ok(!html.includes("+2 GHE contracted seats"));
            }
            const summary = summarize(model);
            assert.deepEqual(summary.recent_changes, [{
                date: change.date, priority: change.priority, account: change.account_name,
                product: { ghe: "GHE", ghas: "GHAS", ghcp: "Copilot" }[family], change: expected, new: false,
            }]);
            const legacy = summarize({ ...model, events: [{ ...change, kind: "unknown" }] });
            assert.deepEqual({ ...summary, recent_changes: [] }, { ...legacy, recent_changes: [] });
            assert.equal(summary.signals, 1);
            assert.deepEqual(model, original);
        }
    }
});

test("gap dates, reverted labels, migrations and unknown text survive shared rendering safely", () => {
    const model = syntheticModel();
    const base = syntheticEvent("gap", model.accounts[0], "ghe", "Notify", model.date, 2,
        event({ since: "2026-10-24", gap_days: 3 }));
    const migration = { ...base, id: "migration", priority: "Info", kind: "migration",
        text: "GHE seats moved between products (Example <plan> 10 → 0, Other 0 → 10) since 2026-10-24" };
    const unknown = { ...base, id: "unknown", kind: "future", text: "<script>unknown & unchanged</script>" };
    model.events = [base, { ...base, id: "reverted", reverted: true }, migration, unknown];
    const html = renderView(model, { view: "ghe" });
    assert.match(html, /Oct 24 – Oct 27/);
    assert.match(html, /title="Previous value reported Oct 24, 2026"/);
    assert.match(html, /<li class="change-row reverted">/);
    assert.match(html, /<span class="chip">Reverted<\/span>/);
    assert.ok(html.includes(esc(migration.text.replace(" since 2026-10-24", ""))));
    assert.ok(html.includes(esc(unknown.text)));
    assert.ok(!html.includes("<script>"));
    const summary = summarize(model);
    assert.equal(summary.recent_changes.length, 2, "reverted and Info filtering is unchanged");
    assert.ok(summary.recent_changes.some(change => change.change.endsWith(" since 2026-10-24")));
    for (const surface of [renderView(model, { view: "overview" }), renderDrawer(model, { account: model.accounts[0].id })]) {
        assert.ok(surface.includes("Oct 24 – Oct 27"));
    }
    const hostile = event({ product_type: '<Plan & "test">' });
    model.events = [{ ...base, ...hostile }];
    const escaped = renderView(model, { view: "ghe" });
    assert.ok(escaped.includes(esc(formatSeatEvent(hostile))));
    assert.ok(!escaped.includes(hostile.product_type));
    const trend = event({ kind: "trend_up", since: "2026-10-19", gap_days: 8 });
    model.events = [{ ...base, ...trend }];
    const trendHtml = renderView(model, { view: "ghe" });
    assert.ok(trendHtml.includes("over 7+ days"));
    assert.ok(!trendHtml.includes("Previous value reported"));
});
