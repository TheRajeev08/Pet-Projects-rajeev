// Run: node tests/test_ask_engine.js
// Uses a synthetic model (no customer data). If ~/CopilotConsumptionDashboard/model.json
// exists, also checks the engine agrees with ask.py's shared flags on the real data.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const AskEngine = require(path.join(__dirname, "..", "ask_engine.js"));

function acct(o) {
  const base = {
    id: o.name.replace(/\W/g, ""), name: o.name, consuming: true, active_7d: 0, active_28d: 0, assigned: 0, utilization: null,
    gross_7d: 0, gross_28d: 0, wow_a7: 0, wow_gross: 0, at_risk: 0, health: null, health_cat: null, health_prev: null, health_moved: false,
    active_this_week: false, active_last_week: false, inactive_this_week: false, declining: false, growing: false, low_utilization: false,
    renewing_in_days: null, renewal: null, region: "Northland", segment: "Corporate", integrations: [], models: [], top_surface: null, top_model: null,
    a7: [], gross: [],
  };
  return Object.assign(base, o);
}
const D = {
  data_date: "2030-01-07", asof: "2030-01-07",
  portfolio: { models: { "claude-sonnet-9": {}, "gpt-9": {} } },
  accounts: [
    acct({ name: "Alphaworks Corp", active_7d: 60, active_28d: 70, assigned: 100, utilization: 0.7, gross_7d: 900, wow_a7: 5, wow_gross: 100,
      health: 0.8, health_cat: "Green", health_prev: "Green", active_this_week: true, active_last_week: true, renewing_in_days: 30, renewal: "2030-02-06",
      integrations: [{ surface: "Copilot CLI", l28: 50 }], models: [{ model: "claude-sonnet-9", l28: 40 }], a7: [60, 55], gross: [900, 800] }),
    acct({ name: "Betamax Ltd", active_7d: 0, active_28d: 4, assigned: 20, utilization: 0.2, gross_7d: 0, health: 0.3, health_cat: "Red", health_prev: "Yellow",
      health_moved: true, inactive_this_week: true, declining: true, low_utilization: true, renewing_in_days: 200,
      models: [{ model: "gpt-9", l28: 3 }], a7: [0, 0], gross: [0, 0] }),
    acct({ name: "Gammaray Systems", active_7d: 0, active_28d: 12, assigned: 15, utilization: 0.8, gross_7d: 0, wow_a7: -9, wow_gross: -50,
      health: 0.5, health_cat: "Yellow", health_prev: "Yellow", active_last_week: true, inactive_this_week: true, a7: [0, 9], gross: [0, 50] }),
    acct({ name: "Deltoid Nonuser", consuming: false }),
    acct({ name: "Epsilon Private Limited", usage_unlinked: true, signals: ["Copilot billed"], copilot_billed_lcm: 1200, copilot_billed_ltm: 9000 }),
  ],
  changes: [
    { kind: "active_users", severity: "bad", id: "GammaraySystems", account: "Gammaray Systems", text: "Weekly active users 9 → 0" },
    { kind: "spend", severity: "good", id: "AlphaworksCorp", account: "Alphaworks Corp", text: "UBB spend up" },
  ],
};
const e = AskEngine(D);
const names = r => (r.rows || []).map(a => a.name).sort();
let n = 0;
function t(q, check) { const r = e.ask(q); try { check(r); n++; } catch (err) { console.error("FAIL:", q, "\n ", r.type, r.interpreted, r.answer); throw err; } }

t("which accounts are not active this week?", r => assert.deepStrictEqual(names(r), ["Betamax Ltd", "Gammaray Systems"]));
t("accounts using copilot cli with usage not linked", r => assert.deepStrictEqual(names(r), []));
t("accounts that stopped this week", r => assert.deepStrictEqual(names(r), ["Gammaray Systems"]));
t("which accounts have red health", r => assert.deepStrictEqual(names(r), ["Betamax Ltd"]));
t("health worsened", r => assert.deepStrictEqual(names(r), ["Betamax Ltd"]));
t("which accounts use copilot cli", r => assert.deepStrictEqual(names(r), ["Alphaworks Corp"]));
t("accounts not using copilot cli", r => assert.deepStrictEqual(names(r), ["Betamax Ltd", "Epsilon Private Limited", "Gammaray Systems"]));
t("accounts using claude models", r => assert.deepStrictEqual(names(r), ["Alphaworks Corp"]));
t("accounts not using claude", r => assert.deepStrictEqual(names(r), ["Betamax Ltd", "Epsilon Private Limited", "Gammaray Systems"]));
t("accounts with more than 50 active users", r => assert.deepStrictEqual(names(r), ["Alphaworks Corp"]));
t("accounts with fewer than 5 users and red health", r => assert.deepStrictEqual(names(r), ["Betamax Ltd"]));
t("accounts with between 10 and 50 assigned seats", r => assert.deepStrictEqual(names(r), ["Betamax Ltd", "Gammaray Systems"]));
t("accounts with utilization under 50%", r => assert.deepStrictEqual(names(r), ["Betamax Ltd"]));
t("low utilization accounts", r => assert.deepStrictEqual(names(r), ["Betamax Ltd"]));
t("who is renewing in the next 90 days", r => assert.deepStrictEqual(names(r), ["Alphaworks Corp"]));
t("how many accounts are declining", r => assert.deepStrictEqual(names(r), ["Betamax Ltd"]));
t("biggest drop in weekly active users", r => assert.deepStrictEqual(names(r), ["Gammaray Systems"]));
t("top 1 accounts by spend", r => assert.deepStrictEqual(names(r), ["Alphaworks Corp"]));
t("total ubb spend this week", r => assert.match(r.answer, /\$900/));
t("accounts not using copilot yet", r => assert.deepStrictEqual(names(r), ["Deltoid Nonuser"]));
t("is alphaworks active this week?", r => assert.match(r.answer, /^Yes/));
t("is betamax active this week?", r => assert.match(r.answer, /^No/));
t("what changed this week", r => { assert.strictEqual(r.type, "changes"); assert.strictEqual(r.rows.length, 2); });
t("negative changes", r => assert.strictEqual(r.rows.length, 1));
t("accounts in northland with more than 50 users", r => assert.deepStrictEqual(names(r), ["Alphaworks Corp"]));
t("accounts with usage not linked", r => assert.deepStrictEqual(names(r), ["Epsilon Private Limited"]));
t("epsilon limited", r => assert.match(r.answer, /billed \$1,200/));
t("help", r => assert.strictEqual(r.type, "help"));
t("gibberish blah", r => { assert.strictEqual(r.ok, false); assert.ok(r.suggestions.length > 0); });

// Optional parity check on the real (local, never committed) model.
const real = path.join(process.env.CCD_HOME || path.join(require("os").homedir(), "CopilotConsumptionDashboard"), "model.json");
if (fs.existsSync(real)) {
  const RD = JSON.parse(fs.readFileSync(real, "utf8"));
  const re = AskEngine(RD);
  const got = re.ask("which accounts are not active this week?").rows.map(a => a.id).sort();
  const want = RD.accounts.filter(a => a.inactive_this_week).map(a => a.id).sort();
  assert.deepStrictEqual(got, want); n++;
  for (const q of ["top 5 by spend", "what changed this week", "which models are used most", "surface breakdown", "how is the portfolio doing"]) { re.ask(q); n++; }
}
console.log(`ok ${n} checks`);
