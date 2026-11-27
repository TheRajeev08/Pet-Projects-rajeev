// Explicitly synthetic, reproducible review data. Both product roots stay beneath the supplied home.
import fs from "node:fs";
import path from "node:path";
import { syntheticAccount, syntheticAction, syntheticModel } from "./helpers.mjs";

export function workspaceModel() {
    const model = syntheticModel();
    model.date = "2026-11-27";
    model.generated_at = "2026-11-27T06:00:00Z";
    model.status = "ok";
    model.owner = "Synthetic preview - not customer data";
    model.sources = [{ feed: "book", label: "Synthetic book", state: "ok", as_of: model.date, messages: [] }];
    const names = ["Alder Labs", "Beacon Systems", "Cedar Works", "Drift Engineering", "Elm Software",
        "Fieldstone Research", "Grove Robotics", "Harbor Platforms", "Iris Manufacturing", "Juniper Digital"];
    const feed = (records, state = "ok") => ({ state, records, as_of: model.date, collected_at: model.date });
    model.accounts = Array.from({ length: 64 }, (_, i) => {
        const months = Array.from({ length: 13 }, (_, j) => {
            const d = new Date("2025-10-01T00:00:00Z"); d.setUTCMonth(d.getUTCMonth() + j);
            return d.toISOString().slice(0, 10);
        });
        // Live vocabulary: billing_source is the channel (Zuora/azure), never the billing model. Volume-only GHE,
        // volume GHAS and Copilot MRR-only accounts have no matching consumption invoices at all.
        const volumeGhe = i % 5 === 1, mixedGhe = i === 2, ghasVolume = i % 4 === 2, copilotMrrOnly = i % 9 === 4;
        const invoices = months.flatMap((date, j) => (i % 7 === 0 && j === 8) ? [] :
            ["ghec", "copilot", "actions", "ghcs", "ghsp", "code_quality", "ghas", "copilot aiu", "copilot_enterprise"].flatMap((product, p) => {
                if ((product === "code_quality" && j < 4) || (product === "ghas" && i % 4 !== 0)
                    || (product === "ghec" && volumeGhe) || (["ghcs", "ghsp"].includes(product) && ghasVolume)
                    || (["copilot", "copilot_enterprise"].includes(product) && copilotMrrOnly)) return [];
                const amount = Math.round((6000 - i * 40) * (1 + .04 * j + .16 * Math.sin(j / 2 + p)) / (p + 1));
                const channels = (i + p) % 11 === 0 ? ["Zuora", "azure"] : [(i + p) % 3 === 0 ? "azure" : "Zuora"];
                return channels.map(billing_source => ({ date, product, amount: amount / channels.length, billing_source,
                    units: (100 + j) / channels.length, uom: product === "actions" ? "minute" : product === "copilot aiu" ? "AI unit" : "seat" }));
            }));
        // MRR is the current month only (2026-11), as in the live mrr feed. Component license MRR is reported as zero.
        const mrr = i % 8 === 7 ? [] : [{ date: "2026-11-01", source_date: "2026-11-26",
            ghe_license_mrr: volumeGhe && i % 10 === 6 || mixedGhe ? 9000 - i * 50 : 0, ghe_metered_mrr: volumeGhe ? 0 : 4200 - i * 30,
            ghas_license_mrr: ghasVolume ? 3600 : 0, ghas_metered_mrr: 0, ghcs_license_mrr: 0, ghcs_metered_mrr: ghasVolume ? 0 : 1100,
            ghsp_license_mrr: 0, ghsp_metered_mrr: ghasVolume ? 0 : 700, copilot_business_mrr: 3800 - i * 20,
            copilot_enterprise_mrr: i % 6 === 0 ? 1500 : 0, copilot_standalone_mrr: 0 }];
        const gheModel = mixedGhe ? "mixed" : volumeGhe ? (i % 10 === 1 ? "volume" : undefined) : "metered";
        const people = [
            { id: `person-${i}-1`, name: "Synthetic Alex", role: "Engineering director", email: `alex${i}@example.invalid`, last_activity: "2026-11-20" },
            { id: `person-${i}-2`, name: "Synthetic Morgan", role: "Platform lead", email: null },
            { id: `person-${i}-3`, name: "Synthetic Taylor", role: "Procurement", email: `taylor${i}@example.invalid` },
            { id: `person-${i}-4`, name: "Synthetic Casey", role: "Shared mailbox contact", email: `alex${i}@example.invalid` },
        ];
        const series = { points: months.map((d, j) => [d, 180 + i + j * 2]) };
        const issue = { id: `issue-${i}`, title: "Synthetic platform discovery", date: "2026-11-20", status: i % 2 ? "closed" : "open", se_issue: true,
            url: `https://example.invalid/synthetic/issues/${i}`, description: "We use Jenkins." };
        const call = { id: `call-${i}`, date: "2026-11-23", title: "Synthetic technical discovery",
            url: `https://example.invalid/synthetic/calls/${i}`, description: "We are evaluating CircleCI.", participants: [{ email: people[2].email }] };
        const claim = { schema: 1, technology: "Jenkins", category: "CI/CD", usage_status: "reported_in_use", source_type: "se_issue",
            source_id: issue.id, source_url: issue.url, observed_at: issue.date, collected_at: model.date,
            evidence_quote: "We use Jenkins.", content_hash: "synthetic", verification_state: "quote_verified", source_current: true, source_state: "fresh" };
        const a = syntheticAccount(`synthetic-${i}`, `Synthetic ${names[i % names.length]}${i >= 10 ? ` ${i + 1}` : ""}`,
            { arr: i === 63 ? null : 420000 - i * 5000, renewing_in_days: i === 62 ? null : i * 5 - 5,
                renewal: i === 62 ? null : new Date(Date.parse(model.date) + (i * 5 - 5) * 86400000).toISOString().slice(0, 10),
                as_of: "2026-11-25", open_pipeline_net_arr: i % 3 ? 35000 : null, open_renewal_arr: 80000,
                ghe: { contracted: gheModel === "metered" ? 0 : 240, metered: volumeGhe ? 0 : 220, billing: gheModel, as_of: "2026-11-25",
                    metered_as_of: "2026-11-26", series: { metered: series }, by_type: { "GitHub Enterprise Cloud": { seats: 240, arr: 110000 } } },
                ghcp: { contracted: 200, assigned: 182, health: { category: ["Green", "Yellow", "Red"][i % 3] }, series: { assigned: series }, funnel: { assigned: 182, active_1d28: 160, engaged_1d28: 132 } },
                ghas: { contracted: 130, committers: 98, series: { committers: series }, by_type: { GHCS: { seats: 130, arr: 46000 } },
                    ...(ghasVolume ? { licensing_model: "Volume", plan: "Volume - Bundled" }
                        : i % 4 === 0 ? { licensing_model: "Metered", plan: "Metered - Unbundled" } : {}) },
                seller: { ready: { copilot_arr: 84000 }, feeds: {
                    contacts: feed(people, i === 7 ? "stale" : "ok"),
                    emails: feed([{ id: `email-${i}`, date: "2026-11-24", title: "Synthetic enablement follow-up",
                        people: [{ email: people[0].email }], thread: `thread-${i}` }]),
                    gong: feed([call]),
                    // Live: an MRR-only account's invoice collection succeeds with zero rows (no_rows); a stale feed never counts.
                    issues: feed([issue]), invoices: i === 58 ? feed([], "no_rows") : feed(invoices, i === 49 ? "stale" : "ok"), mrr: feed(mrr, mrr.length ? "ok" : "no_rows"), tech_stack: feed([claim, { ...claim, technology: "CircleCI",
                        usage_status: "evaluating", source_type: "gong", source_id: call.id, source_url: call.url,
                        observed_at: call.date, evidence_quote: call.description }], "partial"),
                    support: feed([], "no_rows"), risks: feed([], "no_rows"),
                } } });
        return a;
    });
    model.accounts[12].name = "Synthetic long account name for International Platform Engineering and Infrastructure Services";
    model.actions = model.accounts.slice(0, 7).map((a, i) => syntheticAction(`synthetic-action-${i}`, a, ["ghe", "ghas", "ghcp"][i % 3],
        i % 2 ? "opportunity" : "risk", i === 0 ? "High" : "Medium", {
            title: ["Renewal with a known adoption risk", "Seats approaching contracted capacity", "Review Copilot adoption"][i % 3],
            why: "Synthetic evidence for layout review. No real account recommendation.", new: i === 0 }));
    model.events = [];
    model.kpis = { overall: { accounts: 64, arr: model.accounts.reduce((n, a) => n + (a.arr || 0), 0) },
        ghe: { accounts: 64, contracted: { value: 15360 }, metered: { value: 14080 } },
        ghas: { accounts: 64, contracted: { value: 8320 }, committers: { value: 6272 } },
        ghcp: { accounts: 64, contracted: { value: 12800 }, assigned: { value: 11648 }, categories: { Green: 22, Yellow: 21, Red: 21 } } };
    return model;
}

export function createWorkspaceHome(home) {
    if (!path.isAbsolute(home)) throw new Error("Provide an absolute synthetic preview directory.");
    if (fs.existsSync(home)) {
        const stat = fs.lstatSync(home);
        if (!stat.isDirectory() || stat.isSymbolicLink() || fs.readdirSync(home).length) {
            throw new Error("Refusing preview destination: use a new or empty directory, never an existing dashboard.");
        }
    }
    fs.mkdirSync(home, { recursive: true });
    const copilot = path.join(home, "synthetic-copilot");
    fs.mkdirSync(copilot, { recursive: true });
    const writeNewJson = (file, data) => fs.writeFileSync(file, JSON.stringify(data), { flag: "wx" });
    const model = workspaceModel();
    model.copilot = { model_path: path.join(copilot, "model.json") };
    writeNewJson(path.join(home, "config.json"), { copilot_home: copilot, owner_name: model.owner });
    writeNewJson(path.join(home, "model.json"), model);
    writeNewJson(path.join(home, "status.json"), { overall: "ok", date: model.date, generated_at: model.generated_at });
    writeNewJson(path.join(copilot, "model.json"), {
        data_date: model.date, generated_at: model.generated_at, missing_sources: [], sources: {}, definitions: {},
        asof: { "07": model.date }, changes: [], accounts: model.accounts.map((a, i) => ({ id: a.id, name: a.name, consuming: true,
            aiu: [868, 820, 850, 760, 732, 780, 695, 642, 665, 590, 562, 585, 510].map((units, week) =>
                (i + week) % 11 === 0 ? null : units + i * 8),
            assigned: 182, active_28d: 160, engaged_1d28: 132, arr: a.arr, integrations: [], factors: [], health: "Green" })),
        portfolio: { owned: 64, consuming: 64, a7: [110, 120], gross: [200, 210], at_risk: [2, 1] }, enterprises: [],
    });
    fs.writeFileSync(path.join(copilot, "dashboard.html"), "<!doctype html><title>Synthetic classic preview</title><h1>Synthetic classic preview</h1><p>No customer data.</p>", { flag: "wx" });
    return model;
}
