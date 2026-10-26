import assert from "node:assert/strict";
import vm from "node:vm";
import { mkdtemp, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
    accountPlays, accountStatus, adoptionTotals, contractMovement, dataCaveats, getQueue, hasSurfaceSignal,
    integrityIssues, isNewMetric, queueByLane, riskProfile, summarize, usageMovement, validateModel,
} from "../insights.mjs";
import {
    adoptionFacts, enablementKnown, enterpriseRows, impactState, multipliers, phases,
    portfolioImpact, roi, surfaceAdoption, surfaceRows, totalEngagedUsers,
} from "../impact.mjs";
import { CanvasRequestError, createRefreshCoordinator, readCopilotModel as readModel, startCanvasServer, pulsePaths } from "../../server.mjs";

async function testPaths(dir) {
    const paths = pulsePaths(join(dir, "pulse"));
    const { mkdir } = await import("node:fs/promises");
    await mkdir(paths.home);
    await writeFile(paths.config, JSON.stringify({ copilot_home: dir }));
    await writeFile(paths.model, JSON.stringify({
        schema: 1, date: "2030-01-07", accounts: [], actions: [], events: [], sources: [], kpis: {},
    }));
    return paths;
}

function account(name, extra = {}) {
    return {
        id: name.replace(/\W/g, ""), name, consuming: true, active_this_week: true,
        active_last_week: true, inactive_this_week: false, active_7d: 12, active_28d: 20,
        assigned: 30, utilization: 2 / 3, low_utilization: false, health_cat: "Green",
        at_risk: 0, wow_at_risk: 0, a7: [12, 12, 12, 12, 12], a28: [20, 20, 20, 20, 20],
        gross: [20, 20, 20, 20, 20], ubb_users: [8, 8, 8, 8, 8], accepted: [40, 40, 40, 40, 40],
        shown: [100, 100, 100, 100, 100], aiu: [100, 100, 100, 100, 100],
        gross_7d: 20, gross_28d: 80, integrations: [], models: [], linked_usage: [],
        usage_on_sibling: [], usage_unlinked: false, declining: false, renewing_in_days: 200,
        ghe_seats: 100, total_arr: 80000, arr_series: [{ date: "current", seats: 30, total_arr: 80000 }],
        ...extra,
    };
}

function fixture(accounts = []) {
    return {
        data_date: "2030-01-07", generated_at: "2030-01-07T08:00",
        accounts, portfolio: {
            owned: accounts.length, consuming: accounts.filter(a => a.consuming).length,
            a7: [12, 13], gross: [20, 21], at_risk: [2, 1],
        },
        definitions: { consuming: "Shared dashboard definition" }, sources: { "01": { rows: accounts.length } },
        missing_sources: [], asof: { "02": "2030-01-06", "03": "2030-01-06", "06": "2030-01-04" }, changes: [],
    };
}

test("three-cell strips share full-width responsive layout without orphan cells or touching strips", async () => {
    const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
    const css = await readFile(new URL("../style.css", import.meta.url), "utf8");
    const context = vm.createContext({
        state: { model: { portfolio: {} } }, hasSources: () => true,
        adoptionTotals: () => Object.fromEntries(["assigned", "active_28d", "engaged_1d28"]
            .map(key => [key, { value: 1234, n: 1234, of: 12345 }])),
        metricName: (_, text) => text, num: value => String(value), money: value => String(value),
        H: value => String(value), sourceNote: () => "", detailTable: () => "",
    });
    vm.runInContext(source.slice(source.indexOf("function adoptionFunnel("), source.indexOf("function forecastWhitespace(")), context);
    vm.runInContext(source.slice(source.indexOf("function portfolioForecast("), source.indexOf("function empty(")), context);
    for (const fn of ["adoptionFunnel", "portfolioForecast"]) {
        const html = vm.runInContext(`${fn}()`, context);
        assert.match(html, /class="fact-strip fact-strip-three/);
        assert.equal((html.match(/class="fact"/g) || []).length, 3);
    }
    assert.match(css, /\.fact-strip\.fact-strip-three \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}/);
    assert.match(css, /\.fact-strip \+ \.fact-strip \{ margin-top: 14px; \}/);
    assert.match(css, /\.fact:last-child:nth-child\(odd\) \{ grid-column: 1 \/ -1; \}/);
    assert.match(css, /\.fact-strip-three > \.fact \{ border-bottom: 0; border-right: 1px solid var\(--line\); grid-column: auto; \}/);
    assert.match(css, /\.fact-strip-three > \.fact:last-child \{ border-right: 0; grid-column: auto; \}/);
    assert.match(css, /@media \(max-width: 540px\) \{\s*\.fact-strip\.fact-strip-three \{ grid-template-columns: minmax\(0, 1fr\); \}/);
    assert.match(css, /\.fact-strip-three > \.fact:last-child \{ border-bottom: 0; \}/);
});

test("adoption funnel sums account values instead of reading weekly portfolio arrays", () => {
    const model = fixture([
        account("Synthetic One", { assigned: 10, active_28d: 6, engaged_1d28: 4 }),
        account("Synthetic Two", { assigned: 5, active_28d: 2, engaged_1d28: null }),
    ]);
    model.portfolio.assigned = [900, 800];
    model.portfolio.a28 = [700, 600];
    assert.deepEqual(adoptionTotals(model), {
        assigned: { value: 15, n: 2, of: 2 }, active_28d: { value: 8, n: 2, of: 2 },
        engaged_1d28: { value: 4, n: 1, of: 2 },
    });
    model.missing_sources = ["06", "03", "02"];
    for (const metric of Object.values(adoptionTotals(model))) assert.equal(metric.value, null);
});

const accounts = [
    account("Alphaworks", { declining: true, trend_4w: -.24, renewing_in_days: 30, renewal: "2030-02-06" }),
    account("Betamax", { active_this_week: false, active_last_week: true,
        inactive_this_week: true, active_7d: 0, a7: [0, 8, 8, 8, 8], gross: [0, 10, 10, 10, 10] }),
    account("Gammaray", { active_this_week: false, active_last_week: false,
        inactive_this_week: false, usage_unlinked: true, gross: [0, 0, 0, 0, 0],
        a7: [null, null, null, null, null], linked_gross_28d: 240,
        linked_usage: [{ name: "Gammaray Sync", gross_28d: 240 }] }),
    account("Delta Pilot", { consuming: false, active_this_week: false,
        active_last_week: false, ghe_seats: 200, gross_7d: 0 }),
];

test("forecast pool and modeled whitespace add Grow plays without changing bare whitespace", () => {
    const a = account("Forecast Example", {
        entitlement_status: "Projected to Exceed Included Pool", forecast_above_pool: 20,
        ws_play: "Sell seats", ws_whitespace_seats: 40, ws_estimate_source: "headcount",
    });
    const model = fixture([a]);
    for (const type of ["pool", "seat-whitespace"]) {
        assert.equal(accountPlays(model, a).find(play => play.type === type).lane, "grow");
    }
    assert.equal(accountPlays({ ...model, missing_sources: ["16", "17"] }, a).length, 0);
    assert.equal(accountPlays(model, { ...a, ws_estimate_source: "rejected_as_implausible" })
        .some(play => play.type === "seat-whitespace"), false);
    assert.equal(accountPlays(fixture([accounts[3]]), accounts[3]).some(play => play.type === "whitespace"), true);
});

test("New metric badges expire after thirty days and ignore invalid dates", () => {
    const model = { new_metrics: [{ key: "forecast_bill", since: "2030-01-01" }] };
    assert.equal(isNewMetric(model, "forecast_bill", new Date("2030-01-30T00:00:00Z")), true);
    assert.equal(isNewMetric(model, "forecast_bill", new Date("2030-01-31T00:00:00Z")), false);
    assert.equal(isNewMetric(model, "forecast_bill", new Date("2029-12-31T00:00:00Z")), false);
    assert.equal(isNewMetric(model, "missing"), false);
});

test("model validation keeps the Salesforce-owned account grain", () => {
    const model = fixture(accounts);
    assert.equal(validateModel(model).accounts.length, 4);
    assert.throws(() => validateModel({ ...model, portfolio: { ...model.portfolio, owned: 3 } }), /do not match/);
    assert.throws(() => validateModel(fixture([accounts[0], accounts[0]])), /duplicate account identity/);
    assert.throws(() => validateModel({ ...model, data_date: "2030-02-30" }), /invalid data date/);
});

test("plays land in lanes that describe the next step, not a numeric score", () => {
    const model = fixture(accounts);
    const lanes = Object.fromEntries(queueByLane(model).map(lane =>
        [lane.id, lane.items.map(item => item.account.name)]));
    assert.deepEqual(lanes.verify, ["Gammaray"]);
    // Within a lane the order is affected users, then percent change: Alphaworks has 12 declining
    // users against Betamax's 8 stopped ones, and that ordering is visible rather than scored.
    assert.deepEqual(lanes.now, ["Alphaworks", "Betamax"]);
    assert.deepEqual(lanes.grow, ["Delta Pilot"]);
    assert.equal(getQueue(model).every(item => typeof item.play.priority === "undefined"), true);
});

test("a licence reduction explains a matching usage drop instead of being called a data fault", () => {
    const cut = account("Licence Cut", {
        a7: [10, 50, 50, 50, 50], a28: [60, 60, 60, 60, 60], ubb_users: [40, 40, 40, 40, 40],
        accepted: [400, 400, 400, 400, 400], shown: [1000, 1000, 1000, 1000, 1000],
        gross: [700, 700, 700, 700, 700], assigned: 60, contracted_seats: 10,
        arr_series: [{ date: "2029-12-31", seats: 100, total_arr: 30000 },
            { date: "2030-01-31", seats: 60, total_arr: 30000 },
            { date: "current", seats: 10, total_arr: 30000 }],
    });
    const model = fixture([cut]);
    const movement = usageMovement(model, cut);
    assert.equal(movement.licenceDriven, true);
    assert.equal(movement.matchesSeats, true, "weekly actives landing on the seat count is the signature");
    assert.equal(movement.corroborated, false, "no telemetry signal corroborates it, yet it is still real");

    const play = accountPlays(model, cut)[0];
    assert.equal(play.type, "licence");
    assert.equal(play.lane, "now");
    assert.equal(play.title, "Licence reduction with matching usage drop");
    const text = play.evidence.join(" ");
    assert.match(text, /60 \u2192 10/);
    assert.match(text, /after 100/, "the earlier peak gives the full contraction");
    assert.match(text, /now match the contracted seat count/);
    assert.match(text, /still include pre-reduction days/);
    assert.doesNotMatch(text, /telemetry or attribution problem/,
        "a licence cut must never be reported as a telemetry fault");
    assert.match(play.nextStep, /contraction, not a telemetry problem/);
});

test("a licence cut with no usage drop is still raised as a contraction", () => {
    const quiet = account("Quiet Cut", {
        a7: [2, 2, 3, 2, 2], active_7d: 2, assigned: 5, contracted_seats: 5,
        arr_series: [{ date: "2030-01-31", seats: 20, total_arr: 9000 },
            { date: "current", seats: 5, total_arr: 9000 }],
    });
    const play = accountPlays(fixture([quiet]), quiet)[0];
    assert.equal(play.type, "licence");
    assert.equal(play.lane, "now");
    assert.equal(play.title, "Contracted Copilot licences reduced");
    assert.match(play.evidence.join(" "), /20 \u2192 5/);
});

test("a conflicting drop with no licence change is still reported as a data question", () => {
    const conflicting = account("Still Conflicting", {
        a7: [10, 50, 50, 50, 50], a28: [60, 60, 60, 60, 60], ubb_users: [40, 40, 40, 40, 40],
        accepted: [400, 400, 400, 400, 400], gross: [700, 700, 700, 700, 700],
        contracted_seats: 60,
        arr_series: [{ date: "2030-01-31", seats: 60, total_arr: 9000 },
            { date: "current", seats: 60, total_arr: 9000 }],
    });
    const play = accountPlays(fixture([conflicting]), conflicting)[0];
    assert.equal(play.type, "movement");
    assert.match(play.title, /signals conflict/);
    assert.match(play.evidence.join(" "), /Contracted Copilot seats are unchanged at 60/);
});

test("a sharp drop that other signals contradict is framed as a data question, not churn", () => {
    const conflicting = account("Conflicted", {
        a7: [10, 50, 50, 50, 50], a28: [60, 60, 60, 60, 60], ubb_users: [40, 40, 40, 40, 40],
        accepted: [400, 400, 400, 400, 400], shown: [1000, 1000, 1000, 1000, 1000],
        gross: [700, 700, 700, 700, 700],
    });
    const model = fixture([conflicting]);
    const movement = usageMovement(model, conflicting);
    assert.equal(movement.corroborated, false);
    const play = accountPlays(model, conflicting)[0];
    assert.equal(play.lane, "now", "the user asked for these to be surfaced as Act now");
    assert.match(play.title, /signals conflict/);
    assert.match(play.evidence.join(" "), /Other signals do not agree/);
    assert.match(play.evidence.join(" "), /telemetry or attribution problem/);
    assert.match(play.nextStep, /before raising this with the customer/);
    assert.doesNotMatch(play.evidence.join(" "), /collapse|churn/i);
});

test("a sharp drop that independent signals confirm is framed as a real usage drop", () => {
    const corroborated = account("Corroborated", {
        a7: [10, 30, 30, 30, 30], a28: [40, 40, 40, 40, 40], ubb_users: [5, 20, 20, 20, 20],
        accepted: [50, 250, 250, 250, 250], shown: [100, 500, 500, 500, 500],
        gross: [50, 200, 200, 200, 200],
    });
    const model = fixture([corroborated]);
    const movement = usageMovement(model, corroborated);
    assert.equal(movement.corroborated, true);
    const play = accountPlays(model, corroborated)[0];
    assert.equal(play.lane, "now");
    assert.equal(play.title, "Sharp usage drop");
    assert.match(play.evidence.join(" "), /Corroborated by/);
});

test("the movement detector ignores small accounts and ordinary variation", () => {
    const tiny = account("Tiny", { a7: [1, 6, 6, 5, 6] });
    const wobble = account("Wobble", { a7: [40, 50, 48, 52, 49] });
    const model = fixture([tiny, wobble]);
    assert.equal(usageMovement(model, tiny), null, "a baseline under 10 users is too small to call");
    assert.equal(usageMovement(model, wobble), null, "a 20% dip is not a collapse");
});

test("health evidence names the account's own dominant risk factor and never implies inactivity", () => {
    const happy = account("Flagged Example", {
        health_cat: "Red", health: 0.3, health_prev: "Red", health_moved: false,
        product_health: 0, commercial_health: 0.9, assigned: 80, at_risk: 80, active_28d: 70,
        a7: [45, 40, 45, 60, 60, 60, 60, 60], trend_4w: -0.2, declining: true, wow_a7: 5,
        gross: [400, 400, 400, 800, 1200], utilization: 0.875,
        risk: { risk_mau: 5, risk_intracohort_90d: 80, risk_no_activity_180d: 0, risk_pru_mau_retention: 5 },
    });
    const model = fixture([happy]);
    const profile = riskProfile(happy);
    assert.equal(profile.dominant.key, "risk_intracohort_90d");
    assert.equal(profile.overlapping, true);
    const play = accountPlays(model, happy).find(item => item.type === "health");
    const text = play.evidence.join(" ");
    assert.match(text, /declining against their own 90-day baseline \(80\)/);
    assert.match(text, /not a count of inactive users/);
    assert.match(text, /Factors overlap/);
    assert.match(text, /70 users were active in the last 28 days/);
    assert.match(text, /product 0\.00, commercial 0\.90/);
    assert.match(text, /as of 2030-01-04/, "health facts carry their own source date");
    assert.match(text, /as of 2030-01-06/, "activity facts carry a different source date");
    assert.match(text, /most recent week moved up 5/, "a recent improvement is not hidden");
    assert.equal(play.lane, "watch", "a persistently red account is not new news");
});

test("a different account reports a different dominant factor", () => {
    const dormantCohort = account("Dormant Cohort", {
        health_cat: "Red", at_risk: 80, assigned: 90, active_28d: 10,
        risk: { risk_mau: 3, risk_intracohort_90d: 12, risk_no_activity_180d: 68, risk_pru_mau_retention: 0 },
    });
    const play = accountPlays(fixture([dormantCohort]), dormantCohort).find(item => item.type === "health");
    assert.match(play.evidence.join(" "), /no recorded activity in 180 days \(68\)/);
});

test("health moving into red is urgent, unlike a persistently red account", () => {
    const moved = account("Newly Red", {
        health_cat: "Red", health_prev: "Yellow", health_moved: true, at_risk: 9, assigned: 20,
        risk: { risk_intracohort_90d: 9 },
    });
    const play = accountPlays(fixture([moved]), moved).find(item => item.type === "health");
    assert.equal(play.lane, "now");
    assert.match(play.title, /Yellow \u2192 Red/);
});

test("stoppage leads its row instead of hiding behind renewal wording", () => {
    const both = account("Stopped And Renewing", {
        active_this_week: false, active_last_week: true, inactive_this_week: true,
        declining: true, trend_4w: -0.5, renewing_in_days: 20, renewal: "2030-01-27",
        a7: [0, 14, 14, 14, 14], gross: [0, 30, 30, 30, 30],
    });
    const plays = accountPlays(fixture([both]), both);
    assert.equal(plays[0].type, "stopped");
    assert.match(plays[0].title, /renewal in 20 days/);
    assert.equal(plays.some(play => play.type === "renewal"), false, "renewal is folded in, not duplicated");
});

test("one deterioration is reported once, not as several independent findings", () => {
    const declining = account("Declining Red", {
        health_cat: "Red", at_risk: 20, assigned: 40, active_28d: 25, declining: true,
        trend_4w: -0.27, a7: [42, 35, 42, 60, 64, 55, 58, 67],
        risk: { risk_intracohort_90d: 20 },
    });
    const plays = accountPlays(fixture([declining]), declining);
    const types = plays.map(play => play.type);
    assert.equal(types.includes("health"), true);
    assert.equal(types.includes("decline"), false, "the health card already states the same trend");
    assert.match(plays.find(play => play.type === "health").shortLabel, /27% down/,
        "the decline is still visible as a signal on the row");
});

test("every matched signal stays visible on the row", () => {
    const multi = account("Multi Signal", {
        health_cat: "Red", at_risk: 19, wow_at_risk: 8, assigned: 20, active_28d: 7,
        utilization: 0.35, low_utilization: true, declining: true, trend_4w: -0.79,
        active_this_week: false, inactive_this_week: true, a7: [0, 2, 2, 3, 2],
        gross: [0, 1, 1, 1, 1], risk: { risk_pru_mau_retention: 11, risk_intracohort_90d: 8 },
    });
    const item = getQueue(fixture([multi]))[0];
    assert.ok(item.scenario.length >= 3, `expected several signals, saw ${item.scenario.join(", ")}`);
    assert.ok(item.plays.length >= 3);
});

test("unlinked usage suppresses adoption conclusions drawn from untrusted telemetry", () => {
    const unlinked = account("Unlinked Example", {
        usage_unlinked: true, health_cat: "Red", at_risk: 5, declining: true, trend_4w: -0.4,
        risk: { risk_intracohort_90d: 5 },
    });
    const plays = accountPlays(fixture([unlinked]), unlinked);
    assert.equal(plays[0].lane, "verify");
    assert.equal(plays[0].type, "attribution");
    assert.equal(plays.some(play => ["health", "decline", "movement", "utilization"].includes(play.type)), false,
        "adoption plays must not outrank or accompany an unresolved attribution problem");
});

test("unlinked and sibling usage is never classified as inactive", () => {
    const sibling = account("Epsilon Sibling", {
        active_this_week: false, inactive_this_week: false,
        usage_on_sibling: [{ id: "S1", name: "Sibling", gross_28d: 100 }],
    });
    const model = fixture([...accounts, sibling]);
    assert.equal(accountStatus(model, accounts[2]), "unlinked");
    assert.equal(accountStatus(model, sibling), "sibling");
    assert.equal(accountPlays(model, sibling)[0].type, "attribution");
    assert.equal(accountPlays(model, sibling).some(play => play.type === "inactive"), false);
});

test("source gaps withhold inactivity and whitespace instead of inventing zeros", () => {
    const model = { ...fixture(accounts), missing_sources: ["03", "07"] };
    assert.equal(accountStatus(model, accounts[1]), "unknown");
    assert.equal(accountPlays(model, accounts[1]).some(play => ["stopped", "inactive", "decline", "movement"].includes(play.type)), false);
    assert.equal(accountPlays(model, accounts[3]).some(play => play.type === "whitespace"), false);
    assert.equal(summarize(model).active, null);
    assert.equal(summarize(model).whitespace, null);
    assert.equal(summarize(model).owned, 4);
});

test("stopped evidence uses whichever prior signal exists, never an invented user count", () => {
    const spendOnly = account("Spend Only", {
        active_this_week: false, inactive_this_week: true, active_last_week: true,
        a7: [null, null, null, null], gross: [0, 25, 25, 25],
    });
    const play = accountPlays(fixture([spendOnly]), spendOnly).find(item => item.type === "stopped");
    assert.match(play.evidence.join(" "), /\$25 of UBB gross was recorded in the prior seven days/);
    assert.doesNotMatch(play.evidence.join(" "), /unknown/);

    const dormant = account("Dormant Example", {
        active_this_week: false, active_last_week: false, inactive_this_week: true,
        assigned: null, a7: [0, 0, 0, 0], gross: [0, 0, 0, 0],
    });
    const idle = accountPlays(fixture([dormant]), dormant).find(item => item.type === "inactive");
    assert.doesNotMatch(idle.evidence.join(" "), /seats are assigned/);
});

test("workflow and surface signals describe missing UBB, not proven non-use", () => {
    const a = account("Single Surface", { integrations: [{ surface: "IDE chat", l28: 30, u28: 5 }], active_28d: 35 });
    const play = accountPlays(fixture([a]), a).find(item => item.type === "workflow");
    assert.match(play.evidence.join(" "), /does not prove those features are unused/);
    assert.equal(play.lane, "grow");
    assert.equal(accountPlays({ ...fixture([a]), missing_sources: ["08"] }, a).some(item => item.type === "workflow"), false);
    const historical = account("Historical", { integrations: [{ surface: "Copilot CLI", l7: 0, u7: 0, l28: 50, u28: 3 }] });
    assert.equal(hasSurfaceSignal(historical, "Copilot CLI", "7d"), false);
    assert.equal(hasSurfaceSignal(historical, "Copilot CLI", "28d"), true);
});

test("usage-based consumption with no seats stays in the queue instead of falling through every rule", () => {
    const payg = account("Metered Example", {
        cb_seats: 0, ce_seats: 0, contracted_seats: 0,
        active_7d: null, active_28d: null, assigned: null, health_cat: null, utilization: null,
        gross_28d: 1190, ghe_seats: 200, total_arr: 44352,
    });
    const model = fixture([payg]);
    const play = accountPlays(model, payg).find(item => item.type === "payg");
    assert.equal(play.lane, "grow");
    assert.match(play.evidence.join(" "), /no contracted, Business or Enterprise seats recorded/);
    assert.match(play.evidence.join(" "), /not a data fault/,
        "absence from the seat model is expected for metered usage and must not read as a defect");
    assert.match(play.evidence.join(" "), /200 GitHub Enterprise seats and \$44,352/);
    assert.equal(getQueue(model).some(item => item.account.id === payg.id), true,
        "an account with real Copilot spend must never be dropped from the desk");

    const licensed = account("Licensed Example", { contracted_seats: 14, cb_seats: 0, ce_seats: 14 });
    assert.equal(accountPlays(fixture([licensed]), licensed).some(item => item.type === "payg"), false);

    const prospect = account("Prospect Example", { consuming: false, cb_seats: 0, ce_seats: 0, contracted_seats: 0, gross_28d: 0 });
    const prospectPlays = accountPlays(fixture([prospect]), prospect);
    assert.equal(prospectPlays.some(item => item.type === "payg"), false);
    assert.equal(prospectPlays.some(item => item.type === "whitespace"), true);

    assert.equal(accountPlays({ ...model, missing_sources: ["09"] }, payg).some(item => item.type === "payg"), false,
        "the seat position must be known before claiming an account has no licences");
});

test("integrity flags a specific contradiction but systemic patterns become one portfolio caveat", () => {
    const mismatch = account("Seat Mismatch", { contracted_seats: 10, assigned: 73 });
    const generic = integrityIssues(fixture([mismatch]), mismatch)[0];
    assert.match(generic, /73 assigned seats are recorded against 10 contracted/);
    assert.doesNotMatch(generic, /may be wrong/, "the contract figure is not assumed to be the wrong one");
    assert.equal(integrityIssues(fixture([accounts[0]]), accounts[0]).length, 0);

    // After a licence cut the gap is expected, and the stale side is named explicitly.
    const afterCut = account("After Cut", {
        contracted_seats: 10, assigned: 73,
        arr_series: [{ date: "2030-01-31", seats: 73, total_arr: 5000 }, { date: "current", seats: 10, total_arr: 5000 }],
    });
    const explained = integrityIssues(fixture([afterCut]), afterCut)[0];
    assert.match(explained, /predates the licence reduction/);
    assert.match(explained, /Expect it to catch up/);

    // An incomplete current ARR period affects much of the book, so it is reported once, not per account.
    const blanked = ["A", "B", "C"].map(letter => account(`Blank ${letter}`, {
        arr_series: [{ date: "2029-12-31", seats: 10, total_arr: 5000 }, { date: "current", seats: 10, total_arr: 0 }],
    }));
    const model = fixture(blanked);
    assert.equal(blanked.every(item => integrityIssues(model, item).length === 0), true);
    assert.match(dataCaveats(model).join(" "), /report \$0 in the latest contract snapshot/);
    assert.match(dataCaveats(model).join(" "), /different cutoffs/);
});

function enterprise(slug, extra = {}, match = { confidence: "none", candidates: [] }) {
    return {
        slug, enterprise_id: slug, plan: "Enterprise Cloud", segment: "Corporate",
        region: "India", industry: null, deployment: "GHEC", match,
        collected_from: "2030-01-07T08:00:00Z", has_impact: Boolean(extra.impact?.impact),
        integrations: [], settings: null, adoption: null,
        ...extra,
    };
}

function impactPayload(overrides = {}) {
    return {
        cohortWindowDays: 28, dataAvailable: true, reportDateRange: "Sep 5 - Oct 2",
        impact: {
            adoptionMultiplier: {
                codeShipped: { baselineValue: "4.0", engagedValue: "22.1", multiplier: 5.5, direction: "positive" },
                timeToMerge: { baselineValue: "54.7m", engagedValue: "5.7d", multiplier: 150.3, direction: "negative" },
            },
            adoptionPhases: [
                { phase: "No Cohort", shareOfUsers: 10, totalEngagedUsers: "5", avgLocAddedPerDay: 1 },
                { phase: "Phase 1", phaseNumber: 1, shareOfUsers: 30, totalEngagedUsers: "15" },
                { phase: "Phase 2", phaseNumber: 2 },
                { phase: "Phase 3", phaseNumber: 3, shareOfUsers: 60, totalEngagedUsers: "30", avgPullRequestsMerged: 12 },
            ],
            engagementTrends: [
                { label: "Aug", endDate: "2026-08-31", prsMerged: "100", cohortMix: { "Phase 1": 40, "Phase 3": 60 } },
                { label: "Sep", endDate: "2026-09-30", prsMerged: "150" },
            ],
            productivityGainsTotalMerged: "140",
            roiSection: {
                defaultSalary: "130000",
                phases: [{ groupLabel: "Phase 0-1", aiCreditsPerMonth: "5000", costPerDevMonth: 50, payrollPercent: 0.46, prsPerMonth: 1 },
                    { groupLabel: "Phase 2-3", aiCreditsPerMonth: "6000", costPerDevMonth: 60, payrollPercent: 0.55, prsPerMonth: 12, fasterMergesPercent: -396.1 }],
            },
            ...overrides,
        },
    };
}

test("a large multiplier in the wrong direction is never read as an improvement", () => {
    const item = enterprise("acme", { impact: impactPayload() });
    const [code, merge] = multipliers(item);
    assert.equal(code.direction, "positive");
    assert.equal(merge.multiplier, 150.3);
    assert.equal(merge.direction, "negative", "engaged users merge slower here despite a huge multiplier");
    assert.equal(merge.baseline, "54.7m");
    assert.equal(merge.engaged, "5.7d");
});

test("phases keep dashboard order and mark unreported cohorts rather than zeroing them", () => {
    const rows = phases(enterprise("acme", { impact: impactPayload() }));
    assert.deepEqual(rows.map(row => row.phase), ["No Cohort", "Phase 1", "Phase 2", "Phase 3"]);
    const phase2 = rows.find(row => row.phase === "Phase 2");
    assert.equal(phase2.reported, false);
    assert.equal(phase2.shareOfUsers, null);
    assert.equal(phase2.engagedUsers, null, "an empty phase is not a zero");
    assert.equal(rows.find(row => row.phase === "Phase 1").engagedUsers, 15, "string counts are parsed");
    assert.equal(totalEngagedUsers(enterprise("acme", { impact: impactPayload() })), 50);
});

test("enterprises that cannot report are distinguished from ones not yet collected", () => {
    const disabled = enterprise("off", { impact: { cohortWindowDays: 28, noDataReason: "feature_disabled" } });
    const quiet = enterprise("quiet", { impact: { cohortWindowDays: 28, noDataReason: "low_or_no_usage" } });
    const pending = enterprise("pending", { impact: null, collected_from: null });
    assert.equal(impactState(disabled).state, "feature_disabled");
    assert.match(impactState(disabled).label, /switched off/);
    assert.equal(impactState(quiet).state, "low_or_no_usage");
    assert.equal(impactState(pending).state, "uncollected");
    assert.equal(phases(disabled).length, 0);
    assert.equal(multipliers(pending).length, 0);
});

test("portfolio impact weights the phase mix by engaged developers and states its coverage", () => {
    const big = enterprise("big", { impact: impactPayload() });
    const small = enterprise("small", {
        impact: impactPayload({
            adoptionPhases: [{ phase: "Phase 1", phaseNumber: 1, shareOfUsers: 100, totalEngagedUsers: "2" }],
            productivityGainsTotalMerged: "10",
        }),
    });
    const disabled = enterprise("off", { impact: { noDataReason: "feature_disabled" } });
    const pending = enterprise("pending", { impact: null, collected_from: null });
    const model = { enterprises: [big, small, disabled, pending] };
    const p = portfolioImpact(model);
    assert.equal(p.total, 4);
    assert.equal(p.reporting, 2);
    assert.equal(p.collected, 3, "an uncollected enterprise is not counted as collected");
    assert.equal(p.states.feature_disabled, 1);
    assert.equal(p.states.uncollected, 1);
    assert.equal(p.phaseMixUsers, 52);
    const phase1 = p.phaseMix.find(row => row.phase === "Phase 1");
    // 15 of big's 50 plus small's 2, over 52 engaged developers in total.
    assert.equal(phase1.users, 17);
    assert.ok(Math.abs(phase1.share - 17 / 52 * 100) < 0.001);
    assert.equal(p.prsMergedTotal, 150);
    assert.equal(p.prsMergedEnterprises, 2);
    assert.equal(p.salary, 130000);
});

test("monthly PR totals are summed across reporting enterprises in date order", () => {
    const model = { enterprises: [enterprise("a", { impact: impactPayload() }), enterprise("b", { impact: impactPayload() })] };
    const trend = portfolioImpact(model).prsTrend;
    assert.deepEqual(trend.map(point => point.label), ["Aug", "Sep"]);
    assert.equal(trend[0].prsMerged, 200);
    assert.equal(trend[0].enterprises, 2);
});

test("enterprise rows surface the suggested match without asserting it", () => {
    const single = enterprise("acme", { impact: impactPayload() },
        { confidence: "single", candidates: [{ id: "A1", name: "Acme Ltd", consuming: true }] });
    const ambiguous = enterprise("ambi", { impact: impactPayload() },
        { confidence: "ambiguous", candidates: [{ id: "B1", name: "One" }, { id: "B2", name: "Two" }] });
    const none = enterprise("solo", { impact: null, collected_from: null });
    const rows = enterpriseRows({ enterprises: [none, ambiguous, single] });
    assert.equal(rows[0].state.state, "available", "reporting enterprises sort ahead of uncollected ones");
    assert.equal(rows.at(-1).enterprise.slug, "solo");
    assert.equal(rows.find(r => r.enterprise.slug === "acme").enterprise.match.confidence, "single");
    assert.equal(rows.find(r => r.enterprise.slug === "ambi").enterprise.match.candidates.length, 2);
    assert.equal(rows.find(r => r.enterprise.slug === "acme").advancedShare, 60);
});

test("a surface is only called unused when a policy confirms it is switched on", () => {
    const item = enterprise("acme", {
        impact: impactPayload(),
        integrations: [
            { integration: "vscode-chat", surface: "IDE chat", l7: 10, l28: 40, aiu28: 400, u7: 3, u28: 5, weekly: [40, 30, 20] },
            { integration: "jetbrains-chat", surface: "IDE chat", l7: 2, l28: 8, aiu28: 80, u7: 1, u28: 2, weekly: [8, 4, 2] },
        ],
        settings: {
            surfaces: {
                "IDE chat": "enabled", "Copilot CLI": "enabled", "Code review": "disabled",
                "Coding agent": "unconfigured", "Mobile": "no_policy",
            },
            policies: [], count: 40,
        },
    });
    const rows = surfaceRows(item);
    const find = name => rows.find(row => row.surface === name);

    const ide = find("IDE chat");
    assert.equal(ide.active, true);
    assert.equal(ide.l28, 48, "spend is summed across a surface's integrations");
    assert.equal(ide.u28, 5, "per-slice user counts are maxed, never summed");
    assert.deepEqual(ide.weekly.slice(0, 3), [48, 34, 22], "weekly series are summed across integrations");
    assert.equal(ide.gap, false);

    assert.equal(find("Copilot CLI").gap, true, "enabled with no usage is the actionable gap");
    assert.equal(find("Copilot CLI").status, "enabled, not used");
    assert.equal(find("Code review").gap, false);
    assert.equal(find("Code review").status, "disabled by policy");
    assert.equal(find("Coding agent").status, "not configured");
    assert.equal(find("Mobile").gap, false, "an absent policy is never treated as enabled");
});

test("without feature policies, no surface is claimed to be unused", () => {
    const item = enterprise("quiet", {
        impact: impactPayload(),
        integrations: [{ integration: "vscode-chat", surface: "IDE chat", l28: 10, u28: 2, weekly: [10] }],
        settings: null,
    });
    assert.equal(enablementKnown(item), false);
    const rows = surfaceRows(item);
    assert.equal(rows.every(row => row.gap === false), true);
    assert.equal(rows.find(row => row.surface === "IDE chat").enablement, "unknown");
    assert.equal(rows.length, 1, "only surfaces with recorded usage appear when policies are unknown");
});

test("portfolio surface adoption separates use, idle enablement and disablement", () => {
    const using = enterprise("using", {
        integrations: [{ integration: "copilot-developer-cli", surface: "Copilot CLI", l28: 100, l7: 20, u28: 6, weekly: [100] }],
        settings: { surfaces: { "Copilot CLI": "enabled", "Code review": "enabled" }, policies: [], count: 2 },
    });
    const idle = enterprise("idle", {
        integrations: [],
        settings: { surfaces: { "Copilot CLI": "enabled", "Code review": "disabled" }, policies: [], count: 2 },
    });
    const unknown = enterprise("unknown", { integrations: [], settings: null });
    const rows = surfaceAdoption({ enterprises: [using, idle, unknown] });
    const cli = rows.find(row => row.surface === "Copilot CLI");
    assert.equal(cli.usingEnterprises, 1);
    assert.equal(cli.gapEnterprises, 1);
    assert.deepEqual(cli.gaps, ["idle"]);
    assert.equal(cli.l28, 100);
    assert.equal(cli.users, 6);
    const review = rows.find(row => row.surface === "Code review");
    assert.equal(review.gapEnterprises, 1, "enabled on the using enterprise but never used");
    assert.equal(review.disabledEnterprises, 1);
    assert.deepEqual(rows.enablementCoverage, { known: 2, total: 3 },
        "enablement counts state how much of the book they cover");
});

test("the warehouse mapping is marked confirmed and name matching stays labelled", () => {
    const confirmed = enterprise("acme", { impact: impactPayload() },
        { confidence: "confirmed", source: "warehouse",
            candidates: [{ id: "A1", name: "Acme Ltd", consuming: true }] });
    const guessed = enterprise("guess", { impact: impactPayload() },
        { confidence: "single", source: "name", candidates: [{ id: "B1", name: "Guess Ltd" }] });
    const rows = enterpriseRows({ enterprises: [confirmed, guessed] });
    assert.equal(rows.find(r => r.enterprise.slug === "acme").enterprise.match.source, "warehouse");
    assert.equal(rows.find(r => r.enterprise.slug === "guess").enterprise.match.confidence, "single");
});

test("adoption facts pass through licensed-versus-active users and keep classifications as labels", () => {
    const item = enterprise("acme", {
        impact: impactPayload(),
        adoption: {
            licensed: 33, ubb_users: 26, adoption_rate: 0.7878, wow_ubb_users: 3,
            promo_util: null, standard_util: 2.88, overage_risk: true,
            feature_categories: "Chat", integration_count: 4, model_count: 19,
            adoption_maturity: "Growing adoption", product_breadth: "Advanced multi-surface usage",
            conversation_theme: "Connect high usage to business outcomes",
            week_asof: "2026-09-26T00:00:00Z", month_asof: "2026-09-01",
        },
    });
    const f = adoptionFacts(item);
    assert.equal(f.licensed, 33);
    assert.equal(f.ubbUsers, 26);
    assert.equal(f.overageRisk, true);
    assert.equal(f.promoUtil, null, "an absent pool figure stays null rather than becoming zero");
    assert.equal(f.weekAsOf, "2026-09-26");
    assert.equal(f.theme, "Connect high usage to business outcomes");
    assert.equal(adoptionFacts(enterprise("bare", { impact: impactPayload() })), null);
});

test("roi is reported as a modelled estimate with its salary assumption intact", () => {
    const section = roi(enterprise("acme", { impact: impactPayload() }));
    assert.equal(section.salary, 130000);
    assert.equal(section.groups.length, 2);
    assert.equal(section.groups[1].fasterMergesPercent, -396.1,
        "a negative faster-merges figure is preserved, not clamped");
    assert.equal(section.groups[0].credits, 5000);
    assert.equal(roi(enterprise("bare", { impact: { noDataReason: "feature_disabled" } })), null);
});

test("model read errors and refresh coordinator distinguish request from completion", async () => {
    const dir = await mkdtemp(join(tmpdir(), "copilot-adoption-test-"));
    const file = join(dir, "model.json");
    try {
        await assert.rejects(readModel(file), error => error instanceof CanvasRequestError && error.code === "dashboard_missing");
        await writeFile(file, JSON.stringify(fixture(accounts)));
        const first = await readModel(file);
        let launched = 0;
        let clock = Date.now();
        const refresh = createRefreshCoordinator({
            modelPath: file, launch: async () => { launched++; }, now: () => clock, timeoutMs: 1000,
        });
        assert.equal((await refresh.request()).phase, "requested");
        assert.equal(launched, 1);
        await assert.rejects(refresh.request(), error => error.code === "refresh_in_progress");
        assert.equal(refresh.observe(first.revision).phase, "requested");
        await writeFile(file, JSON.stringify({ ...fixture(accounts), generated_at: "2030-01-07T09:00" }));
        await utimes(file, new Date(clock + 3000), new Date(clock + 3000));
        assert.equal(refresh.observe((await readModel(file)).revision).phase, "updated");
        assert.equal((await refresh.request()).phase, "requested");
        clock += 1001;
        assert.match(refresh.status().message, /No rebuilt snapshot/);
        assert.equal(refresh.status().phase, "error");
        await writeFile(file, "{ invalid json");
        await assert.rejects(readModel(file), error => error.code === "dashboard_invalid");
    } finally {
        await rm(dir, { recursive: true, force: true });
    }
});

test("refresh launch failure is surfaced rather than reported as requested", async () => {
    const dir = await mkdtemp(join(tmpdir(), "copilot-adoption-launch-"));
    const file = join(dir, "model.json");
    try {
        await writeFile(file, JSON.stringify(fixture(accounts)));
        const refresh = createRefreshCoordinator({
            modelPath: file, launch: async () => { throw new Error("Automation unavailable"); },
        });
        await assert.rejects(refresh.request(), error =>
            error.code === "refresh_launch_failed" && /Automation unavailable/.test(error.message));
        assert.equal(refresh.status().phase, "error");
    } finally {
        await rm(dir, { recursive: true, force: true });
    }
});

test("loopback server serves the model and guards live refresh requests", async () => {
    const dir = await mkdtemp(join(tmpdir(), "copilot-adoption-server-"));
    const file = join(dir, "model.json");
    let entry;
    try {
        await writeFile(file, JSON.stringify(fixture(accounts)));
        await writeFile(join(dir, "dashboard.html"), "<!doctype html><body>Offline Ask</body>");
        let launches = 0;
        const paths = await testPaths(dir);
        const refresh = createRefreshCoordinator({ modelPath: paths.model, launch: async () => { launches++; } });
        entry = await startCanvasServer({ paths, refresh });
        const page = await (await fetch(new URL("/copilot/", entry.url))).text();
        const token = page.match(/meta name="csrf-token" content="([a-f0-9]+)"/)?.[1];
        assert.ok(token);
        const modelResponse = await fetch(new URL("/copilot/api/model", entry.url));
        assert.equal(modelResponse.status, 200);
        assert.equal((await modelResponse.json()).model.accounts.length, 4);
        assert.equal((await fetch(new URL("/copilot/impact.mjs", entry.url))).status, 200);
        const classic = await fetch(new URL("/copilot/classic", entry.url));
        assert.match(classic.headers.get("content-security-policy"), /script-src 'self' 'unsafe-inline'/);
        assert.match(await classic.text(), /href="\.\/".*Back to adoption canvas/);
        assert.doesNotMatch((await fetch(new URL("/copilot/", entry.url))).headers.get("content-security-policy"), /script-src 'self' 'unsafe-inline'/);
        assert.equal((await fetch(new URL("/copilot/%2e%2e%2fserver.mjs", entry.url))).status, 404);
        const send = (origin, key) => fetch(new URL("/copilot/api/refresh", entry.url), {
            method: "POST", headers: {
                "Origin": origin, "Content-Type": "application/json", "X-Canvas-Token": key,
            }, body: "{}",
        });
        assert.equal((await send("https://untrusted.example", token)).status, 403);
        assert.equal((await send(entry.url.slice(0, -1), "bad-token")).status, 403);
        assert.equal((await fetch(new URL("/copilot/api/refresh", entry.url), {
            method: "POST", headers: { Origin: entry.url.slice(0, -1), "Content-Type": "text/plain", "X-Canvas-Token": token }, body: "{}",
        })).status, 403);
        assert.equal((await fetch(new URL("/api/refresh", entry.url), {
            method: "POST", headers: { Origin: entry.url.slice(0, -1), "Content-Type": "application/json", "X-Canvas-Token": token },
            body: '{"unexpected":true}',
        })).status, 400);
        assert.equal((await send(entry.url.slice(0, -1), token)).status, 202);
        assert.equal(launches, 1);
        assert.equal((await send(entry.url.slice(0, -1), token)).status, 409);
        assert.equal((await (await fetch(new URL("/copilot/api/status", entry.url))).json()).refresh.phase, "requested");
        assert.equal((await readFile(file, "utf8")).includes("Alphaworks"), true);
    } finally {
        if (entry) await entry.close();
        await rm(dir, { recursive: true, force: true });
    }
});

test("a rebuilt model is announced and completes only after a valid read", async () => {
    const dir = await mkdtemp(join(tmpdir(), "copilot-adoption-events-"));
    const file = join(dir, "model.json");
    let entry;
    let reader;
    try {
        await writeFile(file, JSON.stringify(fixture(accounts)));
        const paths = await testPaths(dir);
        const refresh = createRefreshCoordinator({ modelPath: paths.model, launch: async () => {} });
        entry = await startCanvasServer({ paths, refresh, pollMs: 40 });
        await refresh.request();
        const response = await fetch(new URL("/copilot/events", entry.url));
        reader = response.body.getReader();
        await reader.read();
        await writeFile(file, JSON.stringify({ ...fixture(accounts), generated_at: "2030-01-07T10:00" }));
        const changed = new Date(Date.now() + 3000);
        await utimes(file, changed, changed);
        let timer;
        try {
            await Promise.race([
                (async () => {
                    let text = "";
                    while (!text.includes("event: model")) {
                        const chunk = await reader.read();
                        if (chunk.done) throw new Error("The event stream closed before the model changed.");
                        text += new TextDecoder().decode(chunk.value);
                    }
                })(),
                new Promise((_, reject) => {
                    timer = setTimeout(() => reject(new Error("No model-change event arrived.")), 6500);
                }),
            ]);
        } finally {
            clearTimeout(timer);
        }
        assert.equal(refresh.status().phase, "requested");
        const updated = await fetch(new URL("/copilot/api/model", entry.url));
        assert.equal(updated.status, 200);
        assert.equal((await (await fetch(new URL("/copilot/api/status", entry.url))).json()).refresh.phase, "requested");
        const changedPulse = new Date(Date.now() + 6000);
        await utimes(paths.model, changedPulse, changedPulse);
        assert.equal((await (await fetch(new URL("/copilot/api/status", entry.url))).json()).refresh.phase, "updated");
    } finally {
        if (reader) await reader.cancel();
        if (entry) await entry.close();
        await rm(dir, { recursive: true, force: true });
    }
});
