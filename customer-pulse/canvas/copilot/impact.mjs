/**
 * Copilot Impact, mirroring the GitHub Copilot Impact dashboard.
 *
 * The payloads come from `get_copilot_impact` per GitHub enterprise slug, collected by the daily
 * refresh. Every field is optional: enterprises legitimately return `feature_disabled` or
 * `low_or_no_usage`, phases can be present with no measurements, and multipliers are omitted when
 * there is no baseline to compare against. Nothing here is rendered as zero when it is absent.
 */

export const PHASE_ORDER = ["No Cohort", "Phase 1", "Phase 2", "Phase 3"];

export const PHASE_NOTES = Object.freeze({
    "No Cohort": "Assigned but not yet engaged with Copilot in the window.",
    "Phase 1": "Code-first use: completions and chat in the editor.",
    "Phase 2": "Expanding into reviews and wider Copilot surfaces.",
    "Phase 3": "Agent-first use, the most advanced adoption cohort.",
});

export const NO_DATA_REASONS = Object.freeze({
    feature_disabled: "Copilot Impact is switched off for this enterprise.",
    low_or_no_usage: "Too little Copilot usage for the dashboard to report on.",
});

/** Surface order for display; matches the builder's integration taxonomy. */
export const SURFACE_ORDER = Object.freeze([
    "IDE chat", "Copilot CLI", "Code review", "Coding agent", "Copilot app",
    "GitHub.com chat", "Mobile", "Security agents", "Third-party agents", "SDK / integrations", "Other",
]);

export const ENABLEMENT_LABELS = Object.freeze({
    enabled: "Enabled",
    disabled: "Disabled by policy",
    unconfigured: "Not configured",
    no_policy: "No policy available",
    unknown: "Enablement unknown",
});

/**
 * How a surface stands for one enterprise: what it is costing, who is using it, and whether the
 * policy behind it is even switched on. "Enabled but unused" is only claimed when a policy
 * confirms the surface is on — absence of UBB alone never proves a feature is unused.
 */
export function surfaceRows(enterprise) {
    const used = new Map();
    for (const row of enterprise?.integrations || []) {
        const name = row.surface || "Other";
        const entry = used.get(name) || {
            surface: name, l7: 0, p7: 0, l28: 0, aiu28: 0, u7: 0, u28: 0,
            integrations: [], weekly: [],
        };
        entry.l7 += row.l7 || 0;
        entry.p7 += row.p7 || 0;
        entry.l28 += row.l28 || 0;
        entry.aiu28 += row.aiu28 || 0;
        // Per-slice distinct counts are not summed across integrations; take the largest slice.
        entry.u7 = Math.max(entry.u7, row.u7 || 0);
        entry.u28 = Math.max(entry.u28, row.u28 || 0);
        entry.integrations.push(row);
        (row.weekly || []).forEach((value, index) => {
            if (typeof value === "number" && Number.isFinite(value)) {
                entry.weekly[index] = (entry.weekly[index] || 0) + value;
            }
        });
        used.set(name, entry);
    }
    const policies = enterprise?.settings?.surfaces || null;
    const names = new Set([...used.keys(), ...Object.keys(policies || {})]);
    return [...names]
        .sort((a, b) => {
            const ai = SURFACE_ORDER.indexOf(a), bi = SURFACE_ORDER.indexOf(b);
            return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
        })
        .map(name => {
            const entry = used.get(name) || {
                surface: name, l7: 0, p7: 0, l28: 0, aiu28: 0, u7: 0, u28: 0, integrations: [], weekly: [],
            };
            const enablement = policies ? (policies[name] || "unknown") : "unknown";
            const active = entry.l28 > 0 || entry.u28 > 0;
            return {
                ...entry, enablement,
                active,
                // The actionable gap: switched on, nobody using it.
                gap: !active && enablement === "enabled",
                status: active ? "in use"
                    : enablement === "enabled" ? "enabled, not used"
                        : enablement === "disabled" ? "disabled by policy"
                            : enablement === "unconfigured" ? "not configured"
                                : "no usage recorded",
            };
        });
}

export function enablementKnown(enterprise) {
    return Boolean(enterprise?.settings?.surfaces);
}

/** Portfolio surface adoption: who uses each surface, and where it is on but unused. */
export function surfaceAdoption(model) {
    const enterprises = model.enterprises || [];
    const withEnablement = enterprises.filter(enablementKnown).length;
    const totals = new Map();
    for (const enterprise of enterprises) {
        for (const row of surfaceRows(enterprise)) {
            const entry = totals.get(row.surface) || {
                surface: row.surface, usingEnterprises: 0, gapEnterprises: 0,
                disabledEnterprises: 0, l28: 0, l7: 0, users: 0, gaps: [],
            };
            if (row.active) {
                entry.usingEnterprises += 1;
                entry.l28 += row.l28;
                entry.l7 += row.l7;
                entry.users += row.u28;
            } else if (row.gap) {
                entry.gapEnterprises += 1;
                entry.gaps.push(enterprise.slug);
            } else if (row.enablement === "disabled") {
                entry.disabledEnterprises += 1;
            }
            totals.set(row.surface, entry);
        }
    }
    const rows = [...totals.values()]
        .filter(entry => entry.usingEnterprises || entry.gapEnterprises || entry.disabledEnterprises)
        .sort((a, b) => b.l28 - a.l28 || b.usingEnterprises - a.usingEnterprises);
    // Enablement counts only cover enterprises whose policies have been collected; say so.
    rows.enablementCoverage = { known: withEnablement, total: enterprises.length };
    return rows;
}

export function adoptionFacts(enterprise) {
    const a = enterprise?.adoption;
    if (!a) return null;
    const value = key => {
        const v = a[key];
        return typeof v === "number" && Number.isFinite(v) ? v : null;
    };
    const text = key => (typeof a[key] === "string" && a[key].trim()) ? a[key].trim() : null;
    return {
        licensed: value("licensed"), ubbUsers: value("ubb_users"),
        adoptionRate: value("adoption_rate"), wowUbbUsers: value("wow_ubb_users"),
        promoUtil: value("promo_util"), standardUtil: value("standard_util"),
        overageRisk: a.overage_risk === true,
        band: text("adoption_band"), utilizationBand: text("utilization_band"),
        entitlement: text("projected_entitlement_status"), insight: text("insight_category"),
        categories: text("feature_categories"), integrationCount: value("integration_count"),
        modelCount: value("model_count"), maturity: text("adoption_maturity"),
        spendIntensity: text("spend_intensity"), breadth: text("product_breadth"),
        theme: text("conversation_theme"),
        weekAsOf: text("week_asof")?.slice(0, 10) || null,
        monthAsOf: text("month_asof")?.slice(0, 10) || null,
    };
}

const isNumber = value => typeof value === "number" && Number.isFinite(value);
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);

/** Several numeric fields arrive as strings in the payload. */
export function toNumber(value) {
    if (isNumber(value)) return value;
    if (typeof value !== "string") return null;
    const parsed = Number(value.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : null;
}

export function impactOf(enterprise) {
    const payload = enterprise?.impact;
    if (!object(payload)) return null;
    return object(payload.impact) ? payload.impact : null;
}

export function impactState(enterprise) {
    if (!enterprise?.collected_from) return { state: "uncollected", label: "Not collected yet" };
    const payload = enterprise.impact;
    if (object(payload) && payload.noDataReason) {
        return { state: payload.noDataReason, label: NO_DATA_REASONS[payload.noDataReason] || payload.noDataReason };
    }
    return impactOf(enterprise)
        ? { state: "available", label: "Reporting" }
        : { state: "unavailable", label: "No Impact data returned" };
}

export function reportingEnterprises(model) {
    return (model.enterprises || []).filter(enterprise => impactOf(enterprise));
}

/** Phase rows in dashboard order, with absent measurements preserved as null. */
export function phases(enterprise) {
    const impact = impactOf(enterprise);
    if (!impact || !Array.isArray(impact.adoptionPhases)) return [];
    const byName = new Map(impact.adoptionPhases.map(row => [row.phase, row]));
    return PHASE_ORDER.map(name => {
        const row = byName.get(name) || {};
        return {
            phase: name,
            note: PHASE_NOTES[name],
            shareOfUsers: isNumber(row.shareOfUsers) ? row.shareOfUsers : null,
            engagedUsers: toNumber(row.totalEngagedUsers),
            locPerDay: isNumber(row.avgLocAddedPerDay) ? row.avgLocAddedPerDay : null,
            prsMerged: isNumber(row.avgPullRequestsMerged) ? row.avgPullRequestsMerged : null,
            mergeMinutes: isNumber(row.avgPullRequestsMedianMinutesToMerge) ? row.avgPullRequestsMedianMinutesToMerge : null,
            mergeTime: row.prMergeTime ?? null,
            reported: isNumber(row.shareOfUsers),
        };
    });
}

/**
 * Adoption multipliers. `direction` is authoritative: a large multiplier with direction "negative"
 * means engaged users are worse on that measure, so the sign must never be inferred from size.
 */
export function multipliers(enterprise) {
    const impact = impactOf(enterprise);
    const source = impact?.adoptionMultiplier;
    if (!object(source)) return [];
    return [
        { key: "codeShipped", label: "Code shipped", hint: "Lines of code shipped per engaged developer versus the non-engaged baseline." },
        { key: "timeToMerge", label: "Time to merge", hint: "Median pull-request merge time for engaged developers versus the baseline." },
    ].flatMap(entry => {
        const value = source[entry.key];
        if (!object(value)) return [];
        return [{
            ...entry,
            baseline: value.baselineValue ?? null,
            engaged: value.engagedValue ?? null,
            multiplier: isNumber(value.multiplier) ? value.multiplier : null,
            direction: value.direction || "neutral",
        }];
    });
}

export function engagementTrend(enterprise) {
    const impact = impactOf(enterprise);
    if (!impact || !Array.isArray(impact.engagementTrends)) return [];
    return impact.engagementTrends.map(point => ({
        label: point.label ?? null,
        endDate: point.endDate ?? null,
        prsMerged: toNumber(point.prsMerged),
        cohortMix: object(point.cohortMix) ? point.cohortMix : null,
    }));
}

export function roi(enterprise) {
    const impact = impactOf(enterprise);
    const section = impact?.roiSection;
    if (!object(section) || !Array.isArray(section.phases)) return null;
    return {
        salary: toNumber(section.defaultSalary),
        groups: section.phases.map(group => ({
            label: group.groupLabel ?? null,
            credits: toNumber(group.aiCreditsPerMonth),
            costPerDevMonth: isNumber(group.costPerDevMonth) ? group.costPerDevMonth : null,
            payrollPercent: isNumber(group.payrollPercent) ? group.payrollPercent : null,
            prsPerMonth: isNumber(group.prsPerMonth) ? group.prsPerMonth : null,
            fasterMergesPercent: isNumber(group.fasterMergesPercent) ? group.fasterMergesPercent : null,
        })),
    };
}

export function totalEngagedUsers(enterprise) {
    const counted = phases(enterprise)
        .map(row => row.engagedUsers)
        .filter(isNumber);
    return counted.length ? counted.reduce((sum, value) => sum + value, 0) : null;
}

export function productivity(enterprise) {
    const impact = impactOf(enterprise);
    return impact ? toNumber(impact.productivityGainsTotalMerged) : null;
}

/**
 * Portfolio view across every reporting enterprise.
 *
 * Phase mix is weighted by engaged users, because a ten-user enterprise and a two-hundred-user one
 * are not equal evidence. Enterprises that report no engaged-user counts are excluded from the mix
 * rather than counted as zero, and the excluded count is returned so the view can say so.
 */
export function portfolioImpact(model) {
    const all = model.enterprises || [];
    const reporting = reportingEnterprises(model);
    const collected = all.filter(enterprise => enterprise.collected_from);
    const states = {};
    for (const enterprise of all) {
        const { state } = impactState(enterprise);
        states[state] = (states[state] || 0) + 1;
    }

    const weighted = new Map(PHASE_ORDER.map(name => [name, 0]));
    let weightedTotal = 0;
    let withoutCounts = 0;
    for (const enterprise of reporting) {
        const rows = phases(enterprise);
        const total = rows.map(row => row.engagedUsers).filter(isNumber).reduce((sum, v) => sum + v, 0);
        if (!total) { withoutCounts += 1; continue; }
        for (const row of rows) {
            if (isNumber(row.engagedUsers)) weighted.set(row.phase, weighted.get(row.phase) + row.engagedUsers);
        }
        weightedTotal += total;
    }

    const prsByMonth = new Map();
    for (const enterprise of reporting) {
        for (const point of engagementTrend(enterprise)) {
            if (!point.label || !isNumber(point.prsMerged)) continue;
            const key = point.endDate || point.label;
            const entry = prsByMonth.get(key) || { label: point.label, endDate: point.endDate, prsMerged: 0, enterprises: 0 };
            entry.prsMerged += point.prsMerged;
            entry.enterprises += 1;
            prsByMonth.set(key, entry);
        }
    }

    const creditValues = reporting.flatMap(enterprise => (roi(enterprise)?.groups || [])
        .map(group => group.costPerDevMonth).filter(isNumber));
    const merged = reporting.map(productivity).filter(isNumber);
    const engaged = reporting.map(totalEngagedUsers).filter(isNumber);

    return {
        total: all.length,
        collected: collected.length,
        reporting: reporting.length,
        states,
        phaseMix: weightedTotal
            ? PHASE_ORDER.map(name => ({
                phase: name, note: PHASE_NOTES[name],
                users: weighted.get(name),
                share: weighted.get(name) / weightedTotal * 100,
            }))
            : [],
        phaseMixEnterprises: reporting.length - withoutCounts,
        phaseMixUsers: weightedTotal,
        engagedUsers: engaged.length ? engaged.reduce((sum, v) => sum + v, 0) : null,
        prsTrend: [...prsByMonth.values()].sort((a, b) =>
            String(a.endDate || a.label).localeCompare(String(b.endDate || b.label))),
        prsMergedTotal: merged.length ? merged.reduce((sum, v) => sum + v, 0) : null,
        prsMergedEnterprises: merged.length,
        avgCostPerDevMonth: creditValues.length
            ? creditValues.reduce((sum, v) => sum + v, 0) / creditValues.length : null,
        salary: roi(reporting.find(enterprise => roi(enterprise)))?.salary ?? null,
        reportDateRange: reporting.map(enterprise => enterprise.impact?.reportDateRange).find(Boolean) || null,
        cohortWindowDays: reporting.map(enterprise => enterprise.impact?.cohortWindowDays).find(isNumber) || null,
    };
}

/** Enterprises ranked for the portfolio table, reporting ones first. */
export function enterpriseRows(model) {
    return (model.enterprises || []).map(enterprise => {
        const state = impactState(enterprise);
        const mult = multipliers(enterprise);
        const phaseRows = phases(enterprise);
        const surfaces = surfaceRows(enterprise);
        const advanced = phaseRows
            .filter(row => ["Phase 2", "Phase 3"].includes(row.phase) && isNumber(row.shareOfUsers))
            .reduce((sum, row) => sum + row.shareOfUsers, 0);
        return {
            enterprise, state, surfaces,
            surfacesUsed: surfaces.filter(row => row.active).length,
            surfaceGaps: surfaces.filter(row => row.gap).map(row => row.surface),
            engagedUsers: totalEngagedUsers(enterprise),
            advancedShare: phaseRows.some(row => isNumber(row.shareOfUsers)) ? advanced : null,
            prsMerged: productivity(enterprise),
            codeShipped: mult.find(entry => entry.key === "codeShipped") || null,
            timeToMerge: mult.find(entry => entry.key === "timeToMerge") || null,
        };
    }).sort((a, b) =>
        (b.state.state === "available") - (a.state.state === "available") ||
        (b.engagedUsers ?? -1) - (a.engagedUsers ?? -1) ||
        a.enterprise.slug.localeCompare(b.enterprise.slug));
}
