export const SOURCE_NAMES = Object.freeze({
    "01": "Salesforce ownership",
    "02": "Account and billing",
    "03": "Active users",
    "04": "IDE acceptance",
    "05": "Engagement and retention",
    "06": "Copilot health and seats",
    "07": "UBB usage and pools",
    "08": "Integrations",
    "09": "Contracted seats",
    "10": "Surface history",
    "11": "Models",
    "12": "Unowned account matches",
    "13": "Owned GitHub enterprises",
    "14": "Enterprise surfaces",
    "15": "Enterprise adoption",
    "16": "UBB forecast and pool status",
    "17": "Copilot whitespace model",
});

/** Queue lanes. Rank orders the lanes; within a lane, sorting is explicit and never score-based. */
export const LANES = Object.freeze([
    { id: "verify", label: "Verify first", rank: 0,
        blurb: "Adoption cannot be judged until the underlying data is trustworthy." },
    { id: "now", label: "Act now", rank: 1,
        blurb: "Urgent this week, whether the next step is a customer conversation or a data check." },
    { id: "watch", label: "Watch", rank: 2,
        blurb: "Deterioration worth tracking, without an emergency." },
    { id: "grow", label: "Grow", rank: 3,
        blurb: "Expansion and whitespace opportunities." },
]);

const LANE_RANK = Object.fromEntries(LANES.map(lane => [lane.id, lane.rank]));

/** Within one account, the play that leads the row. Stoppage must never hide behind renewal wording. */
const PLAY_ORDER = ["attribution", "licence", "movement", "stopped", "renewal", "health",
    "risk", "decline", "inactive", "utilization", "rollout", "workflow", "payg", "pool", "seat-whitespace", "whitespace"];

const RISK_FACTORS = Object.freeze({
    risk_intracohort_90d: "declining against their own 90-day baseline",
    risk_no_activity_180d: "no recorded activity in 180 days",
    risk_pru_mau_retention: "poor month-over-month retention",
    risk_mau: "low monthly active use",
});

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const isNumber = value => typeof value === "number" && Number.isFinite(value);
const number = value => isNumber(value) ? fmt.format(value) : "unknown";
const money = value => isNumber(value) ? usd.format(value) : "unknown";
const percent = value => isNumber(value) ? `${Math.round(Math.abs(value) * 100)}%` : "unknown";
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const users = count => `${number(count)} ${count === 1 ? "user" : "users"}`;

/** Contract snapshots label the open period "current"; render it as prose, not a date. */
const periodLabel = date => date === "current" ? "the current period" : `the ${date} snapshot`;

function median(values) {
    const sorted = values.filter(isNumber).slice().sort((a, b) => a - b);
    if (!sorted.length) return null;
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function average(values) {
    const numbers = (values || []).filter(isNumber);
    return numbers.length ? numbers.reduce((sum, value) => sum + value, 0) / numbers.length : null;
}

/** Latest value against the median of the preceding four weeks. Used for every movement comparison. */
function againstBaseline(series) {
    const values = series || [];
    const latest = isNumber(values[0]) ? values[0] : null;
    const baseline = median(values.slice(1, 5));
    if (latest === null || baseline === null || baseline <= 0) return { available: false };
    return { available: true, latest, baseline, change: (latest - baseline) / baseline };
}

export function asOf(model, id) {
    return model.asof?.[id] || null;
}

/** Appends the source cutoff so facts from differently-dated sources are never implied to be synchronized. */
function dated(model, text, id) {
    const date = asOf(model, id);
    return date ? `${text} (as of ${date})` : text;
}

export function validateModel(model) {
    if (!object(model) || !Array.isArray(model.accounts) || !object(model.portfolio)) {
        throw new TypeError("The dashboard model is missing its accounts or portfolio. Re-run the daily refresh.");
    }
    const date = model.data_date;
    if (typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
        Number.isNaN(Date.parse(`${date}T00:00:00Z`)) ||
        new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) {
        throw new TypeError("The dashboard model has an invalid data date. Re-run the daily refresh.");
    }
    if (!Array.isArray(model.missing_sources) || !object(model.sources) || !object(model.definitions)) {
        throw new TypeError("The dashboard model is missing source/definition metadata. Re-run the daily refresh.");
    }
    if (model.portfolio.owned !== model.accounts.length ||
        model.portfolio.consuming !== model.accounts.filter(account => account.consuming === true).length) {
        throw new TypeError("Dashboard account totals do not match the owned accounts. Re-run the daily refresh.");
    }
    const ids = new Set();
    for (const account of model.accounts) {
        if (!object(account) || typeof account.id !== "string" || !account.id ||
            typeof account.name !== "string" || !account.name || ids.has(account.id) ||
            typeof account.consuming !== "boolean") {
            throw new TypeError("The dashboard has a missing or duplicate account identity. Re-run the daily refresh.");
        }
        ids.add(account.id);
    }
    return model;
}

export function hasSources(model, ...ids) {
    return ids.every(id => !model.missing_sources.includes(id));
}

export function isNewMetric(model, key, now = new Date()) {
    const entry = (model.new_metrics || []).find(metric => metric.key === key);
    const age = entry ? (now.getTime() - Date.parse(`${entry.since}T00:00:00Z`)) / 86400000 : NaN;
    return Number.isFinite(age) && age >= 0 && age < 30;
}

export function accountStatus(model, account) {
    if (account.usage_unlinked) return "unlinked";
    if ((account.usage_on_sibling || []).length) return "sibling";
    if (account.active_this_week) return "active";
    if (!hasSources(model, "02", "03", "06", "07", "12")) return "unknown";
    if (!account.consuming) return "whitespace";
    if (account.inactive_this_week) return "inactive";
    return "unknown";
}

export function hasSurfaceSignal(account, surface, window = "28d") {
    return (account.integrations || []).some(row => row.surface === surface &&
        (window === "7d" ? (row.l7 || 0) > 0 || (row.u7 || 0) > 0 :
            (row.l28 || 0) > 0 || (row.u28 || 0) > 0));
}

/**
 * The upstream health model flags users for several different reasons and the factor counts overlap,
 * so the dominant factor is reported per account rather than with one portfolio-wide definition.
 */
export function riskProfile(account) {
    if (!object(account.risk)) return null;
    const entries = Object.entries(account.risk)
        .filter(([, value]) => isNumber(value) && value > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([key, value]) => ({ key, value, label: RISK_FACTORS[key] || key }));
    if (!entries.length) return null;
    const total = isNumber(account.at_risk) ? account.at_risk : null;
    const sum = entries.reduce((carry, entry) => carry + entry.value, 0);
    return { entries, dominant: entries[0], total, overlapping: total !== null && sum > total };
}

/**
 * Movement in contracted Copilot seats between the two most recent contract snapshots, plus the
 * highest level seen in the series. A reduction here explains a usage fall that no telemetry
 * signal would corroborate on its own, so it is checked before any drop is classified.
 */
export function contractMovement(model, account) {
    if (!hasSources(model, "09")) return null;
    const points = (account.arr_series || []).filter(point => isNumber(point.seats));
    if (points.length < 2) return null;
    const latest = points[points.length - 1];
    const previous = points[points.length - 2];
    if (previous.seats <= 0) return null;
    const peak = points.reduce((best, point) => point.seats > best.seats ? point : best, points[0]);
    return {
        latest: latest.seats, previous: previous.seats, date: latest.date, previousDate: previous.date,
        peak: peak.seats, peakDate: peak.date,
        change: (latest.seats - previous.seats) / previous.seats,
        reduced: latest.seats < previous.seats,
    };
}

/**
 * Detects a sharp fall in weekly active users and explains it.
 *
 * A contracted-seat reduction is checked first: when licences are cut, usage follows, and the
 * trailing-window measures (28-day actives, UBB users) still include pre-reduction days, so they
 * cannot corroborate yet. Treating that as a telemetry fault would send a seller into the wrong
 * conversation. Only when nothing at all explains the drop is it reported as a data question.
 */
export function usageMovement(model, account) {
    if (!account.consuming || account.usage_unlinked || !hasSources(model, "03")) return null;
    const actives = againstBaseline(account.a7);
    if (!actives.available || actives.baseline < 10) return null;
    const lost = actives.baseline - actives.latest;
    if (actives.latest > actives.baseline * 0.5 || lost < 10) return null;

    const candidates = [
        { name: "28-day active users", series: account.a28, source: "03", trailing: true },
        { name: "UBB users", series: account.ubb_users, source: "07", trailing: true },
        { name: "accepted suggestions", series: account.accepted, source: "04" },
        { name: "UBB gross", series: account.gross, source: "07", money: true },
    ];
    const checks = candidates
        .filter(candidate => hasSources(model, candidate.source))
        .map(candidate => ({ ...candidate, ...againstBaseline(candidate.series) }))
        .filter(candidate => candidate.available);
    const fell = checks.filter(check => check.change <= -0.2);
    const held = checks.filter(check => check.change > -0.2);

    const contract = contractMovement(model, account);
    // A licence cut of comparable scale is a complete explanation on its own.
    const licenceDriven = Boolean(contract && contract.reduced && contract.change <= -0.2 &&
        contract.latest < actives.baseline);
    return {
        latest: actives.latest, baseline: actives.baseline, lost,
        change: (actives.latest - actives.baseline) / actives.baseline,
        checks, fell, held, contract, licenceDriven,
        // Weekly actives landing at the new seat count is the signature of a licence-driven fall.
        matchesSeats: Boolean(contract && isNumber(contract.latest) && contract.latest > 0 &&
            Math.abs(actives.latest - contract.latest) <= Math.max(2, contract.latest * 0.2)),
        corroborated: fell.length >= 2,
        comparable: checks.length >= 2,
    };
}

/**
 * Account-specific cross-source contradictions. Deliberately narrow: patterns that affect a large
 * share of the book are pipeline characteristics, not account findings, and belong in dataCaveats.
 */
export function integrityIssues(model, account) {
    const issues = [];
    if (hasSources(model, "06", "09") && isNumber(account.contracted_seats) &&
        isNumber(account.assigned) && account.contracted_seats > 0 && account.assigned > 0 &&
        account.assigned >= account.contracted_seats * 2 && account.assigned - account.contracted_seats >= 10) {
        const contract = contractMovement(model, account);
        // A recent licence cut makes this gap expected: the health source is dated earlier and
        // still reports the old seat count. The contract figure is the current one, not the suspect one.
        if (contract && contract.reduced && contract.change <= -0.2) {
            issues.push(dated(model, `Assigned seats still read ${number(account.assigned)} against ${number(account.contracted_seats)} contracted, because the seat source predates the licence reduction. Expect it to catch up.`, "06"));
        } else {
            issues.push(`${number(account.assigned)} assigned seats are recorded against ${number(account.contracted_seats)} contracted, from differently dated snapshots. Confirm the current position before quoting seats.`);
        }
    }
    return issues;
}

/** Portfolio-wide data characteristics, reported once instead of flagging every affected account. */
export function dataCaveats(model) {
    const caveats = [];
    if (hasSources(model, "09")) {
        const withPrior = model.accounts.filter(account => {
            const series = account.arr_series || [];
            return series.slice(0, -1).some(point => isNumber(point.total_arr) && point.total_arr > 0);
        });
        const blank = withPrior.filter(account => {
            const series = account.arr_series || [];
            return series[series.length - 1]?.total_arr === 0;
        });
        if (withPrior.length && blank.length / withPrior.length >= 0.1) {
            caveats.push(`${number(blank.length)} of ${number(withPrior.length)} accounts with prior ARR report $0 in the latest contract snapshot. Monthly ARR facts land per product, so treat the current period as incomplete rather than as lost ARR.`);
        }
    }
    const dates = Object.entries(model.asof || {}).filter(([, value]) => value);
    if (dates.length > 1) {
        const sorted = dates.map(([, value]) => value).sort();
        if (sorted[0] !== sorted[sorted.length - 1]) {
            caveats.push(`Sources carry different cutoffs, from ${sorted[0]} to ${sorted[sorted.length - 1]}. Figures from different sources are not synchronized.`);
        }
    }
    return caveats;
}

export function accountPlays(model, account) {
    const plays = [];
    const add = play => plays.push(play);
    const linked = account.linked_usage || [];
    const siblings = account.usage_on_sibling || [];
    const days = account.renewing_in_days;
    const renewing = isNumber(days) && days >= 0 && days <= 90;
    const activityComplete = hasSources(model, "03", "07");
    // Usage that is not attributed to this account makes every downstream adoption read unreliable.
    const trustworthy = !account.usage_unlinked;

    const integrity = integrityIssues(model, account);
    if (hasSources(model, "16") && account.entitlement_status === "Projected to Exceed Included Pool") {
        add({
            type: "pool", lane: "grow", shortLabel: "above included pool",
            title: "Projected to exceed included pool",
            evidence: [dated(model, `${money(account.forecast_above_pool)} projected above included pool; this is not invoiced overage.`, "16")],
            nextStep: "Confirm the forecast and included pool with the admin, then discuss an appropriate consumption budget.",
            sources: ["16"], affected: null, change: null,
        });
    }
    if (hasSources(model, "17") && account.ws_estimate_source !== "rejected_as_implausible"
        && account.ws_play === "Sell seats" && account.ws_whitespace_seats > 0) {
        add({
            type: "seat-whitespace", lane: "grow", shortLabel: "modeled seat whitespace",
            title: "Validate Copilot seat whitespace",
            evidence: [dated(model, `${number(account.ws_whitespace_seats)} whitespace seats. Model estimate · parent-level.`, "17"),
                ...[account.ws_play_reason, account.ws_play_context].filter(Boolean)],
            nextStep: "Validate the modeled developer population and current assigned seats before proposing expansion.",
            sources: ["17"], affected: account.ws_whitespace_seats, change: null,
        });
    }

    if (account.usage_unlinked) {
        add({
            type: "attribution", lane: "verify", shortLabel: "usage not linked",
            title: "Resolve usage attribution",
            evidence: linked.length
                ? [`${money(account.linked_gross_28d)} of 28-day UBB sits on ${linked.length} unowned look-alike account${linked.length === 1 ? "" : "s"} and is excluded from this account's totals.`,
                    "No usage telemetry is attributed to this Salesforce account, so adoption cannot be assessed yet."]
                : [`Copilot was billed ${money(account.copilot_billed_lcm)} last month, but no usage telemetry is attributed to this Salesforce account.`,
                    "Adoption, health and renewal conclusions are unreliable until the mapping is fixed."],
            nextStep: "Ask Sales Ops to correct the enterprise-to-account mapping before assessing adoption.",
            sources: linked.length ? ["02", "12"] : ["02", "03", "07"], affected: null, change: null,
        });
    } else if (siblings.length) {
        add({
            type: "attribution", lane: "watch", shortLabel: "usage on sibling",
            title: "Review sibling attribution",
            evidence: [`Usage is recorded on ${siblings.length} other owned look-alike account${siblings.length === 1 ? "" : "s"}, so this account is not treated as inactive.`],
            nextStep: "Confirm the account relationship with Sales Ops and avoid double-counting usage.",
            sources: ["02", "03", "07"], affected: null, change: null,
        });
    }

    const movement = trustworthy ? usageMovement(model, account) : null;
    if (movement && !account.inactive_this_week) {
        const drop = `Weekly active users fell to ${number(movement.latest)} from a ${number(movement.baseline)} median over the previous four weeks, a ${percent(movement.change)} fall of ${users(Math.round(movement.lost))}.`;
        const describe = check => `${check.name} ${check.change >= 0 ? "rose" : "fell"} ${percent(check.change)} to ${check.money ? money(check.latest) : number(check.latest)}`;
        const contract = movement.contract;
        if (movement.licenceDriven) {
            const peak = contract.peak > contract.previous
                ? `, after ${number(contract.peak)} at ${periodLabel(contract.peakDate)}` : "";
            const trailing = movement.held.filter(check => check.trailing);
            add({
                type: "licence", lane: "now", shortLabel: "licences cut",
                title: "Licence reduction with matching usage drop",
                evidence: [
                    dated(model, `Contracted Copilot seats fell ${number(contract.previous)} \u2192 ${number(contract.latest)} in ${periodLabel(contract.date)}${peak}.`, "09"),
                    dated(model, drop, "03"),
                    ...(movement.matchesSeats
                        ? ["Weekly active users now match the contracted seat count, so usage has followed the licence position."]
                        : []),
                    ...(trailing.length
                        ? [`${trailing.map(check => check.name).join(" and ")} still include pre-reduction days and will catch up. UBB gross need not fall when fewer, heavier users remain.`]
                        : []),
                ],
                nextStep: "Confirm the downgrade and its reason with the customer. This is a contraction, not a telemetry problem.",
                sources: ["09", "03", ...new Set(movement.checks.map(check => check.source))],
                affected: Math.round(movement.lost), change: movement.change,
            });
        } else if (movement.corroborated) {
            add({
                type: "movement", lane: "now", shortLabel: "sharp usage drop",
                title: "Sharp usage drop",
                evidence: [dated(model, drop, "03"),
                    `Corroborated by ${movement.fell.map(describe).join(", and ")}.`],
                nextStep: "Contact the admin about the drop and confirm what changed for the affected team.",
                sources: ["03", ...new Set(movement.fell.map(check => check.source))],
                affected: Math.round(movement.lost), change: movement.change,
            });
        } else {
            add({
                type: "movement", lane: "now", shortLabel: "signals conflict",
                title: "Verify sudden usage drop \u2014 signals conflict",
                evidence: [dated(model, drop, "03"),
                    movement.held.length
                        ? `Other signals do not agree: ${movement.held.map(describe).join(", and ")}.`
                        : "No independent signal is available to corroborate the drop.",
                    ...(contract && !contract.reduced
                        ? [dated(model, `Contracted Copilot seats are unchanged at ${number(contract.latest)}, so a licence reduction does not explain it.`, "09")]
                        : []),
                    "A fall in one metric while the others hold usually indicates a telemetry or attribution problem rather than lost adoption."],
                nextStep: "Check the telemetry and account mapping before raising this with the customer.",
                sources: ["03", ...new Set(movement.checks.map(check => check.source))],
                affected: Math.round(movement.lost), change: movement.change,
            });
        }
    }

    // A licence cut without a matching usage fall is still a contraction the seller must know about.
    const contraction = trustworthy && account.consuming && !movement
        ? contractMovement(model, account) : null;
    if (contraction && contraction.reduced && contraction.change <= -0.2 &&
        contraction.previous - contraction.latest >= 3) {
        add({
            type: "licence", lane: "now", shortLabel: "licences cut",
            title: "Contracted Copilot licences reduced",
            evidence: [
                dated(model, `Contracted Copilot seats fell ${number(contraction.previous)} \u2192 ${number(contraction.latest)} in ${periodLabel(contraction.date)}${contraction.peak > contraction.previous ? `, after ${number(contraction.peak)} at ${periodLabel(contraction.peakDate)}` : ""}.`, "09"),
                isNumber(account.active_7d)
                    ? dated(model, `Weekly active users are ${number(account.active_7d)}.`, "03")
                    : "Weekly active users are not reported for this account.",
            ],
            nextStep: "Confirm the reduction and its reason with the customer before planning renewal or expansion.",
            sources: ["09", "03"],
            affected: contraction.previous - contraction.latest, change: contraction.change,
        });
    }

    if (trustworthy && account.consuming && activityComplete && account.inactive_this_week && account.active_last_week) {
        const priorUsers = account.a7?.[1];
        const priorSpend = account.gross?.[1];
        const prior = isNumber(priorUsers) && priorUsers > 0
            ? `${users(priorUsers)} were active in the prior seven days`
            : isNumber(priorSpend) && priorSpend > 0
                ? `${money(priorSpend)} of UBB gross was recorded in the prior seven days`
                : "activity was recorded in the prior seven days, though the user count is unavailable";
        add({
            type: "stopped", lane: "now", shortLabel: "activity stopped",
            title: renewing ? `Activity stopped, renewal in ${number(days)} days` : "Activity stopped this week",
            evidence: [dated(model, `No active users and no UBB gross spend in the latest seven days; ${prior}.`, "03"),
                ...(renewing ? [`The renewal is ${number(days)} days away.`] : [])],
            nextStep: "Check access and telemetry with the admin, then re-engage the affected cohort.",
            sources: ["03", "07", ...(renewing ? ["02"] : [])],
            affected: isNumber(priorUsers) ? priorUsers : null, change: -1,
        });
    } else if (trustworthy && account.consuming && activityComplete && account.inactive_this_week) {
        add({
            type: "inactive", lane: "watch", shortLabel: "dormant",
            title: "Review dormant Copilot adoption",
            evidence: [dated(model, `${isNumber(account.assigned) && account.assigned > 0 ? `${users(account.assigned)} are assigned seats, but no` : "No"} active users or UBB gross spend were recorded this week, despite a Copilot signal in the last 90 days.`, "03")],
            nextStep: "Confirm seat allocation and telemetry with the admin, then plan targeted reactivation.",
            sources: ["03", "06", "07"],
            affected: isNumber(account.assigned) ? account.assigned : null, change: null,
        });
    }

    if (trustworthy && account.consuming && activityComplete && renewing && account.declining &&
        !plays.some(play => play.type === "stopped")) {
        const recent = average((account.a7 || []).slice(0, 4));
        const prior = average((account.a7 || []).slice(4, 8));
        add({
            type: "renewal", lane: "now", shortLabel: `renews in ${number(days)}d`,
            title: "Renewal with declining adoption",
            evidence: [dated(model, `Weekly active users average ${number(recent)} over the last four weeks, down ${percent(account.trend_4w)} from ${number(prior)} in the four weeks before.`, "03"),
                `The renewal is ${number(days)} days away${account.renewal ? ` (${account.renewal})` : ""}.`],
            nextStep: "Review the decline with the customer sponsor and agree a recovery plan before renewal.",
            sources: ["02", "03"],
            affected: isNumber(recent) ? Math.round(recent) : null, change: account.trend_4w,
        });
    }

    if (trustworthy && account.consuming && hasSources(model, "06") && account.health_cat === "Red") {
        const profile = riskProfile(account);
        // A permanently-Red account is not news; only a change or a nearby renewal is urgent.
        const newlyRed = Boolean(account.health_moved);
        const evidence = [];
        if (hasSources(model, "03")) {
            const recent = average((account.a7 || []).slice(0, 4));
            const prior = average((account.a7 || []).slice(4, 8));
            if (isNumber(recent) && isNumber(prior) && prior > 0) {
                evidence.push(dated(model, `Weekly active users average ${number(recent)} over the last four weeks versus ${number(prior)} in the four weeks before, ${account.trend_4w < 0 ? "down" : "up"} ${percent(account.trend_4w)}.`, "03"));
            }
            const latestWeek = againstBaseline(account.a7);
            if (isNumber(account.wow_a7) && account.wow_a7 !== 0 && latestWeek.available) {
                evidence.push(`The most recent week moved ${account.wow_a7 > 0 ? "up" : "down"} ${number(Math.abs(account.wow_a7))} to ${number(account.a7[0])}.`);
            }
        }
        const spend = againstBaseline(account.gross);
        if (hasSources(model, "07") && spend.available && Math.abs(spend.change) >= 0.2) {
            evidence.push(dated(model, `UBB gross is ${money(spend.latest)} against a ${money(spend.baseline)} median over the previous four weeks.`, "07"));
        }
        evidence.push(dated(model, `The upstream health model is Red at ${isNumber(account.health) ? account.health.toFixed(2) : "unknown"}: product ${isNumber(account.product_health) ? account.product_health.toFixed(2) : "unknown"}, commercial ${isNumber(account.commercial_health) ? account.commercial_health.toFixed(2) : "unknown"}.`, "06"));
        if (profile) {
            evidence.push(`${isNumber(account.assigned) ? `${number(profile.total)} of ${number(account.assigned)} assigned users carry` : `${users(profile.total)} carry`} a health-risk flag; the largest factor is ${profile.dominant.label} (${number(profile.dominant.value)}).` +
                `${profile.overlapping ? " Factors overlap, so they sum to more than the total." : ""}` +
                " These are not a count of inactive users" +
                `${isNumber(account.active_28d) ? `; ${users(account.active_28d)} were active in the last 28 days` : ""}.`);
        }
        add({
            type: "health", lane: renewing || newlyRed ? "now" : "watch",
            shortLabel: `${newlyRed ? "newly red" : "red health"}${account.declining && isNumber(account.trend_4w) ? `, ${percent(account.trend_4w)} down` : ""}`,
            title: newlyRed ? `Copilot health moved ${account.health_prev || "up"} \u2192 Red` : "Copilot health is red",
            evidence, nextStep: "Review the dominant risk factor and retention with the customer success team before expanding seats.",
            sources: ["06", ...(hasSources(model, "03") ? ["03"] : []), ...(renewing ? ["02"] : [])],
            affected: isNumber(account.at_risk) ? account.at_risk : null,
            change: isNumber(account.trend_4w) ? account.trend_4w : null,
        });
    }

    if (trustworthy && account.consuming && hasSources(model, "06") &&
        isNumber(account.wow_at_risk) && account.wow_at_risk >= 2 && account.health_cat !== "Red") {
        const profile = riskProfile(account);
        add({
            type: "risk", lane: "watch", shortLabel: `+${number(account.wow_at_risk)} flagged`,
            title: "More users carry health-risk flags",
            evidence: [dated(model, `Flagged users rose by ${number(account.wow_at_risk)} week over week to ${number(account.at_risk)}${isNumber(account.assigned) ? ` of ${number(account.assigned)} assigned` : ""}.`, "06"),
                ...(profile ? [`The largest factor is ${profile.dominant.label} (${number(profile.dominant.value)}); flags are not a count of inactive users.`] : [])],
            nextStep: "Review the risk-factor breakdown and target the cohort losing engagement.",
            sources: ["06"], affected: account.wow_at_risk, change: null,
        });
    }

    if (trustworthy && account.consuming && activityComplete && account.low_utilization && hasSources(model, "06")) {
        add({
            type: "utilization", lane: "watch", shortLabel: "low activation",
            title: "Assigned seats are not being activated",
            evidence: [dated(model, `${number(account.active_28d)} of ${number(account.assigned)} assigned seats were active in the last 28 days (${percent(account.utilization)}).`, "06")],
            nextStep: "Ask the admin which assigned users need first-use enablement or seat reallocation.",
            sources: ["03", "06"],
            affected: isNumber(account.assigned) && isNumber(account.active_28d)
                ? account.assigned - account.active_28d : null,
            change: null,
        });
    }

    // The health play already states the activity trend, so a separate decline card would repeat
    // the same evidence and make one deterioration look like two independent findings.
    if (trustworthy && account.consuming && activityComplete && account.declining && !renewing &&
        !plays.some(play => ["licence", "movement", "stopped", "health"].includes(play.type))) {
        const recent = average((account.a7 || []).slice(0, 4));
        const prior = average((account.a7 || []).slice(4, 8));
        add({
            type: "decline", lane: "watch", shortLabel: `trend ${percent(account.trend_4w)} down`,
            title: "Sustained decline in weekly usage",
            evidence: [dated(model, `Weekly active users average ${number(recent)} over the last four weeks, down ${percent(account.trend_4w)} from ${number(prior)} in the four weeks before.`, "03")],
            nextStep: "Inspect the weekly trend and identify the cohort and workflow behind the change.",
            sources: ["03"],
            affected: isNumber(recent) && isNumber(prior) ? Math.round(prior - recent) : null,
            change: account.trend_4w,
        });
    }

    if (trustworthy && account.consuming && hasSources(model, "06", "09") &&
        isNumber(account.contracted_seats) && isNumber(account.assigned) &&
        account.contracted_seats - account.assigned >= 10 &&
        account.assigned / account.contracted_seats < 0.7 && !integrity.length) {
        add({
            type: "rollout", lane: "grow", shortLabel: "seats unassigned",
            title: "Contracted seats are not fully assigned",
            evidence: [`${number(account.assigned)} seats are assigned against ${number(account.contracted_seats)} contracted, taken from differently dated source snapshots.`],
            nextStep: "Validate seat timing and allocation with the admin before planning a wider rollout.",
            sources: ["06", "09"],
            affected: account.contracted_seats - account.assigned, change: null,
        });
    }

    const advanced = ["Copilot CLI", "Code review", "Coding agent", "Copilot app"]
        .filter(surface => hasSurfaceSignal(account, surface));
    if (trustworthy && account.consuming && hasSources(model, "03", "08") && isNumber(account.active_28d) &&
        account.active_28d >= 10 && !advanced.length && (account.integrations || []).length) {
        add({
            type: "workflow", lane: "grow", shortLabel: "single surface",
            title: "Room to widen Copilot workflows",
            evidence: [dated(model, `${users(account.active_28d)} were active in 28 days with no recorded UBB for the CLI, code review, the coding agent or the Copilot app.`, "08"),
                "Recorded UBB only covers billable usage, so this does not prove those features are unused."],
            nextStep: "Ask which workflows matter most, then confirm current feature use before proposing enablement.",
            sources: ["03", "08"], affected: account.active_28d, change: null,
        });
    }

    // Usage-based Copilot consumption with no seats at all. The seat and health model only covers
    // seat-based deployments, so these accounts carry real spend while reporting no active users,
    // which left them matching neither an adoption rule nor whitespace.
    const noCopilotSeats = ["cb_seats", "ce_seats", "contracted_seats"]
        .every(key => !isNumber(account[key]) || account[key] === 0);
    if (trustworthy && account.consuming && noCopilotSeats && !isNumber(account.active_28d) &&
        isNumber(account.gross_28d) && account.gross_28d > 0 && hasSources(model, "06", "07", "09")) {
        add({
            type: "payg", lane: "grow", shortLabel: "usage without licences",
            title: "Copilot usage with no Copilot licences",
            evidence: [
                dated(model, `${money(account.gross_28d)} of 28-day Copilot UBB with no contracted, Business or Enterprise seats recorded.`, "07"),
                "The seat and health model covers seat-based deployments only, so this account reports no active users or health rating. That is expected for usage-based consumption, not a data fault.",
                ...(isNumber(account.ghe_seats) && account.ghe_seats > 0
                    ? [`${number(account.ghe_seats)} GitHub Enterprise seats${isNumber(account.total_arr) && account.total_arr > 0 ? ` and ${money(account.total_arr)} total GitHub ARR` : ""} indicate the size of the developer base, not a predicted purchase.`]
                    : []),
            ],
            nextStep: "Confirm this is deliberate usage-based consumption, then size a licensed plan against the developer base.",
            sources: ["06", "07", "09"],
            affected: isNumber(account.ghe_seats) ? account.ghe_seats : null, change: null,
        });
    }

    if (!account.consuming && hasSources(model, "02", "03", "06", "07", "12")) {
        add({
            type: "whitespace", lane: "grow", shortLabel: "no Copilot signal",
            title: "Copilot whitespace",
            evidence: [`No Copilot active users, assigned seats or UBB spend in the 90-day window.`,
                `${number(account.ghe_seats)} GitHub Enterprise seats${isNumber(account.total_arr) ? ` and ${money(account.total_arr)} total GitHub ARR` : ""} indicate the size of the developer base, not a predicted purchase.`],
            nextStep: "Validate developer needs and find a sponsor for a small, measurable pilot.",
            sources: ["02", "03", "06", "07", "12"],
            affected: isNumber(account.ghe_seats) ? account.ghe_seats : null, change: null,
        });
    }

    return plays.sort((a, b) => LANE_RANK[a.lane] - LANE_RANK[b.lane] ||
        PLAY_ORDER.indexOf(a.type) - PLAY_ORDER.indexOf(b.type));
}

/**
 * One row per account, grouped into lanes. Every matched signal stays visible on the row so a
 * second or third finding is never hidden behind the first.
 */
export function getQueue(model) {
    const items = model.accounts.flatMap(account => {
        const plays = accountPlays(model, account);
        if (!plays.length) return [];
        const lead = plays[0];
        return [{
            account, play: lead, plays,
            lane: lead.lane,
            scenario: plays.map(play => play.shortLabel),
            integrity: integrityIssues(model, account),
            sort: {
                affected: isNumber(lead.affected) ? lead.affected : -1,
                change: isNumber(lead.change) ? Math.abs(lead.change) : -1,
                renewal: isNumber(account.renewing_in_days) && account.renewing_in_days >= 0
                    ? account.renewing_in_days : Number.MAX_SAFE_INTEGER,
                arr: account.total_arr || 0,
            },
        }];
    });
    return items.sort((a, b) =>
        LANE_RANK[a.lane] - LANE_RANK[b.lane] ||
        b.sort.affected - a.sort.affected ||
        b.sort.change - a.sort.change ||
        a.sort.renewal - b.sort.renewal ||
        b.sort.arr - a.sort.arr ||
        a.account.name.localeCompare(b.account.name));
}

export function queueByLane(model, queue = getQueue(model)) {
    return LANES.map(lane => ({ ...lane, items: queue.filter(item => item.lane === lane.id) }));
}

export function summarize(model) {
    const coverage = hasSources(model, "02", "03", "06", "07", "12");
    const queue = getQueue(model);
    const gross = model.portfolio.gross?.[0];
    const count = lane => queue.filter(item => item.lane === lane).length;
    return {
        dataDate: model.data_date,
        generatedAt: model.generated_at,
        owned: model.portfolio.owned,
        consuming: coverage ? model.portfolio.consuming : null,
        active: hasSources(model, "03", "07")
            ? model.accounts.filter(account => account.consuming && account.active_this_week).length : null,
        whitespace: coverage ? model.accounts.filter(account => !account.consuming).length : null,
        flaggedUsers: hasSources(model, "06") ? model.portfolio.at_risk?.[0] ?? null : null,
        weeklyActiveUsers: hasSources(model, "03") ? model.portfolio.a7?.[0] ?? null : null,
        gross7d: hasSources(model, "07") && isNumber(gross) ? Math.round(gross * 100) / 100 : null,
        verifyAccounts: count("verify"),
        urgentAccounts: count("now"),
        watchAccounts: count("watch"),
        growAccounts: count("grow"),
        missingSources: model.missing_sources,
    };
}
