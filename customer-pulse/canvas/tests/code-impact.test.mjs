import assert from "node:assert/strict";
import { test } from "node:test";
import { codeImpactAccountHtml, codeImpactEnterpriseHtml, codeImpactPortfolioHtml, codeImpactSummaryHtml,
    impactApplies, impactPayload, impactSourceState } from "../copilot/code-impact.mjs";
import { featureUsagePanel } from "../copilot/feature-usage.mjs";
import { copilotLicenses } from "../model.mjs";
import { renderDrawer, renderView } from "../views.mjs";
import { workspaceCopilotModel, workspaceModel } from "./workspace-fixture.mjs";

// Rendered output must never leak excluded narrative fields or JavaScript artefacts.
const LEAKS = /sales_pitch|csm_field_notes|expansion_pitch|NaN|undefined|\[object Object\]|>null</;
const setup = () => {
    const model = workspaceModel();
    const copilot = workspaceCopilotModel(model);
    return { model, copilot, impact: impactPayload(copilot) };
};
const kpiValue = (html, label) => html.match(new RegExp(`${label}</span><strong class="ci-kpi-value">(.*?)</strong>`))?.[1];
const enterprise = (copilot, slug) => copilot.enterprises.find(e => e.slug === slug);

test("source states keep absent, missing and carried-forward distinct from collected", () => {
    const { copilot } = setup();
    assert.deepEqual(impactSourceState(copilot, "19"), { state: "collected", rows: 6 });
    assert.deepEqual(impactSourceState(copilot, "21"), { state: "stale", rows: 1, from: "2026-11-20" });
    assert.equal(impactSourceState({ ...copilot, missing_sources: ["20"] }, "20").state, "missing");
    const { "22": _, ...rest } = copilot.sources;
    assert.equal(impactSourceState({ ...copilot, sources: rest }, "22").state, "absent");
    assert.match(codeImpactPortfolioHtml(impactPayload(copilot)), /Carried from Nov 20, 2026; this source was not collected/);
});

test("an older model reports every outcome as not reported, never as zero", () => {
    const { copilot } = setup();
    const old = { ...copilot, sources: { "07": { rows: 1 } }, asof: { "07": copilot.asof["07"] }, stale_sources: {} };
    for (const a of old.accounts) { delete a.ccr; delete a.cca; delete a.aiu_by_feature; }
    const impact = impactPayload(old);
    const html = codeImpactPortfolioHtml(impact) + codeImpactAccountHtml(impact, "synthetic-0") + codeImpactSummaryHtml(impact, "synthetic-0");
    assert.match(html, /Code review \(CCR\) not reported\.<\/strong> Not in this snapshot/);
    assert.match(html, /Coding agent \(CCA\) not reported\.<\/strong> Not in this snapshot/);
    assert.match(html, /AI units by feature not reported\.<\/strong> Not in this snapshot/);
    assert.doesNotMatch(html, /ci-kpi-value">0(\.0)?%?</);
    assert.equal(kpiValue(html, "Review coverage"), '<span class="ci-na">Not reported</span>');
    assert.equal(impactApplies(impact, "synthetic-0"), false);
    const missing = impactPayload({ ...copilot, missing_sources: ["19", "20", "22"] });
    assert.match(codeImpactAccountHtml(missing, "synthetic-0"), /Not collected in the latest refresh\./);
});

test("portfolio uses the latest complete month, marks month to date and labels the benchmark comparison", () => {
    const { impact } = setup();
    const html = codeImpactPortfolioHtml(impact);
    assert.equal(kpiValue(html, "Review coverage"), "55.4%");
    assert.match(html, /CCR-reviewed ÷ reviewed PRs · Oct 2026/);
    assert.match(html, /<span class="ci-dot ci-s\d ci-mtd"[^>]*title="[^"]*\(month to date\)/);
    assert.match(html, /Nov 2026 <span class="ci-tag">MTD<\/span>/);
    assert.match(html, /Labels compare the adoption score with the industry benchmark/);
    assert.match(html, /AI units are Copilot's metered usage unit, not usage value and not a billable amount/);
    assert.doesNotMatch(html, LEAKS);
});

test("opportunity bands keep unrecognised source bands as text and show stale provenance", () => {
    const { impact } = setup();
    const html = codeImpactPortfolioHtml(impact, { accountLink: (id, name) => `<a data-account="${id}">${name}</a>` });
    for (const band of ["S", "A", "B"]) assert.match(html, new RegExp(`ci-band ci-band-${band}" title=`));
    assert.match(html, /<td data-label="Band">unrecognised<\/td>/);
    assert.match(html, /<strong>C<\/strong> Early stage <span>0<\/span>/);
    assert.match(html, /data-account="synthetic-8"/);
    assert.match(html, /Readiness is the product of the PR volume, lead time and repository components/);
    assert.match(html, /not commitments or forecasts/);
});

test("enterprise detail handles observed, benchmark gaps, withheld and missing rows", () => {
    const { copilot, impact } = setup();
    const beacon = codeImpactEnterpriseHtml(impact, enterprise(copilot, "synthetic-beacon"));
    assert.match(beacon, /id="enterprise-code-review"/);
    assert.match(beacon, /The benchmark is compared with the adoption score \(mean of coverage and penetration\), not with coverage alone/);
    assert.match(beacon, /PR lead time · Oct 2026/);
    assert.match(beacon, /Observational, not causal/);
    assert.match(beacon, /Weekly AI units by Copilot feature/);
    const cedar = codeImpactEnterpriseHtml(impact, enterprise(copilot, "synthetic-cedar"));
    assert.match(cedar, /Withheld\.<\/strong> The CCR source links this enterprise to a different Salesforce account/);
    assert.doesNotMatch(cedar, /ci-kpi-value">\d/);
    const drift = codeImpactEnterpriseHtml(impact, enterprise(copilot, "synthetic-drift"));
    assert.match(drift, /No code review row for this enterprise in the latest collection/);
    assert.match(drift, /Organization-level rows appear on the linked Salesforce account/);
    assert.doesNotMatch(beacon + cedar + drift, LEAKS);
});

test("account detail attributes only through confirmed links and owners", () => {
    const { impact } = setup();
    const two = codeImpactAccountHtml(impact, "synthetic-0");
    assert.match(two, /synthetic-alder-eu/);
    assert.match(two, /<th scope="col">Enterprise<\/th>/);
    assert.match(two, /Owners<\/span><strong class="ci-kpi-value">2</);
    assert.match(codeImpactAccountHtml(impact, "synthetic-1"), /PR lead time · Oct 2026/);
    assert.match(codeImpactAccountHtml(impact, "synthetic-2"), /withheld for every linked enterprise/);
    const drift = codeImpactAccountHtml(impact, "synthetic-3");
    assert.match(drift, /The linked enterprise returned no code review rows/);
    assert.doesNotMatch(drift, /<td data-label="Feature">Coding agent</, "a never-used surface is absent, not zero");
    assert.match(codeImpactAccountHtml(impact, "synthetic-6"), /No confirmed GitHub enterprise link/);
    assert.match(codeImpactAccountHtml(impact, "synthetic-30"), /No feature usage reported in the 13-week window/);
    assert.equal(impactApplies(impact, "synthetic-8"), true);
    assert.equal(impactApplies(impact, "synthetic-12"), true, "reported AI units alone apply");
    assert.equal(impactApplies(impact, "synthetic-30"), false, "no enterprise link, no owner rows and no AI units");
    const onlyOpportunity = (missing = []) => {
        const copilot = setup().copilot;
        const a = copilot.accounts.find(x => x.id === "synthetic-9");
        a.aiu_by_feature = {};
        return impactApplies(impactPayload({ ...copilot, missing_sources: missing }), "synthetic-9");
    };
    assert.equal(onlyOpportunity(), true, "carried-forward new logo readiness alone applies");
    assert.equal(onlyOpportunity(["21"]), false, "opportunity rows from an uncollected source do not apply");
});

test("hostile source text is escaped on every surface", () => {
    const { model, copilot } = setup();
    const evil = '<img src=x onerror="alert(1)">';
    const beacon = enterprise(copilot, "synthetic-beacon");
    beacon.slug = evil;
    beacon.ccr.industry = evil;
    beacon.ccr.series.forEach(p => { p.vs_industry = evil; });
    copilot.accounts[1].ccr.enterprises[0].slug = evil;
    copilot.accounts[1].name = evil;
    const owner = copilot.accounts[1].cca.owners[0];
    owner.series.forEach(p => { p.stage = evil; });
    owner.interpretation = evil;
    copilot.cca_opportunities.expansion.forEach(e => { e.account = evil; e.stage = evil; e.band = evil; e.guidance = { action: evil, lift_summary: evil }; });
    copilot.portfolio.surface_weekly_aiu = { [evil]: [1, 2, 3] };
    const impact = impactPayload(copilot);
    const html = codeImpactPortfolioHtml(impact) + codeImpactEnterpriseHtml(impact, beacon)
        + codeImpactAccountHtml(impact, "synthetic-1") + codeImpactSummaryHtml(impact, "synthetic-1")
        + renderView(model, { view: "copilot", copilotLicenses: copilotLicenses(model, copilot) });
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});

test("Pulse Copilot view, drawer and Account 360 carry the impact sections for applicable accounts", () => {
    const { model, copilot } = setup();
    const data = copilotLicenses(model, copilot);
    const view = renderView(model, { view: "copilot", copilotLicenses: data });
    assert.equal(view.match(/Code review &amp; coding agent impact<\/h2>/g)?.length, 1);
    assert.match(view, /id="copilot-code-impact"/);
    assert.doesNotMatch(view, LEAKS);
    assert.match(renderView(model, { view: "copilot", copilotLicensesLoading: true }), /Loading Copilot outcome metrics/);
    const drawer = renderDrawer(model, { account: "synthetic-0", copilotLicenses: data });
    assert.match(drawer, /href="#\/account360\/products\/account\/synthetic-0"/);
    assert.equal(kpiValue(drawer, "Coding agent PR share") != null, true);
    const products = renderView(model, { view: "account360", account: "synthetic-1", subview: "products", copilotLicenses: data });
    assert.match(products, /Copilot code review, coding agent &amp; AI units/);
    assert.match(products, /PR lead time · Oct 2026/);
    const plain = workspaceModel();
    const target = plain.accounts.find(a => a.id === "synthetic-30");
    delete target.products.ghcp;
    target.products.ghcp = { contracted: 0, assigned: 0 };
    const quiet = copilotLicenses(plain, copilot);
    assert.equal(impactApplies(quiet.impact, "synthetic-30"), false);
    assert.doesNotMatch(renderView(plain, { view: "account360", account: "synthetic-30", subview: "products", copilotLicenses: quiet }),
        /Copilot code review, coding agent/);
    assert.doesNotMatch(renderDrawer(plain, { account: "synthetic-30", copilotLicenses: quiet }), /Code review &amp; coding agent<\/h3>/);
});

test("the feature-usage panel points to separately collected outcome sources", () => {
    const { copilot } = setup();
    const html = featureUsagePanel(copilot, null, null, new Date("2026-11-27"));
    assert.match(html, /PR outcomes come from separate sources .*code review, monthly, coding agent adoption and EPI, monthly/);
    assert.doesNotMatch(html, /Outcome metrics are not available/);
});
