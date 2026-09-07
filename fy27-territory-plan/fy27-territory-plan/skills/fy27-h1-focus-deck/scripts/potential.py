#!/usr/bin/env python3
"""Size the FY27 H1 opportunity per account.

Every dollar this module emits carries a `basis` of "observed", "list" or "derived":

  observed  the account's own effective price, computed from what they actually pay
  list      GitHub public pricing
  derived   a median across the install base, used only where no public price exists

That tag travels all the way to the slide, so any number in the deck can be defended
or discounted on the spot. Nothing here invents a price.

Reads:  fy27-territory-plan.json  (accounts, plays, engagement)
        raw-actuals.json          (optional Kusto ARR + consumption)
        pricing.json              (rates)
Writes: potential.json
"""

import json
import os
import sys

MONTHS = 12

# SuperDash columns that drive sizing. Kept as constants so a column rename in the
# export fails loudly here rather than silently zeroing a headline number.
COL_COPILOT_POTENTIAL = "copilotWhitespace"
COL_ADO = "adoWhitespace"
COL_SECURITY = "securityWhitespace"
COL_METERED = "meteredConsumption"


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def money(value):
    return round(float(value or 0), 2)


class Rates:
    """Resolves a price for a product, preferring what the account actually pays."""

    def __init__(self, pricing):
        self.pricing = pricing
        self.list = pricing.get("list", {})
        self.derived = pricing.get("derived", {})
        self.assumptions = pricing.get("assumptions", {})

    def copilot_seat_year(self, observed=None):
        if observed and observed > 0:
            return observed, "observed"
        tier = self.assumptions.get("copilotTierForSizing", "copilotBusiness")
        return money(self.list[tier]["perUserMonth"] * MONTHS), "list"

    def ghas_committer_year(self, observed=None):
        if observed and observed > 0:
            return observed, "observed"
        sku = self.assumptions.get("ghasSkuForSizing", "codeSecurity")
        return money(self.list[sku]["perCommitterMonth"] * MONTHS), "list"

    def ghe_seat_year(self, observed=None):
        if observed and observed > 0:
            return observed, "observed"
        # No public per-seat list price for GitHub Enterprise, so this is the one
        # place a derived median is unavoidable. It is labelled as such.
        return money(self.derived.get("githubEnterprisePerSeatYear", 0)), "derived"

    def credit(self):
        return float(self.list["aiCredit"]["perCredit"])

    def included_credits_per_seat_year(self):
        """Credits bundled with each Copilot seat, per year.

        Contractual, not modelled: it comes straight from the tier's published
        included allowance, so the capacity figure it feeds can be defended.
        """
        tier = self.assumptions.get("copilotTierForSizing", "copilotBusiness")
        return float(self.list[tier].get("includedCreditsPerUserMonth", 0)) * MONTHS


def observed_prices(actuals_for_account):
    """Effective unit prices from what this account actually pays.

    ARR/seats gives a per-seat price; charge/units gives a per-unit consumption rate.
    Zero denominators are skipped rather than defaulted, so a missing price falls back
    to list rather than silently becoming 0.
    """
    prices = {}
    if not actuals_for_account:
        return prices

    for row in actuals_for_account.get("arr", []):
        seats = float(row.get("license_seats") or 0)
        arr = float(row.get("total_arr") or 0)
        if seats > 0 and arr > 0:
            prices.setdefault(row.get("product_type", ""), money(arr / seats))

    for row in actuals_for_account.get("consumption", []):
        units = float(row.get("billed_units") or 0)
        charge = float(row.get("charge_amt") or 0)
        if units > 0 and charge > 0:
            prices.setdefault("consumption:" + str(row.get("product_name", "")),
                              round(charge / units, 6))
    return prices


def current_state(actuals_for_account):
    """What the account already has: ARR, seats, and annualised consumption."""
    arr_total = 0.0
    seats = {}
    products = []
    for row in (actuals_for_account or {}).get("arr", []):
        arr_total += float(row.get("total_arr") or 0)
        product = row.get("product_type", "")
        seats[product] = seats.get(product, 0) + int(float(row.get("license_seats") or 0))
        products.append(product)

    consumption = {}
    window_months = (actuals_for_account or {}).get("consumptionMonths") or 0
    for row in (actuals_for_account or {}).get("consumption", []):
        name = str(row.get("product_name", ""))
        consumption[name] = consumption.get(name, 0.0) + float(row.get("charge_amt") or 0)

    # Annualise the observed consumption window so ACR is comparable to ARR.
    factor = (12.0 / window_months) if window_months else 0.0
    acr = {k: money(v * factor) for k, v in consumption.items()} if factor else {}

    return {
        "arr": money(arr_total),
        "seatsByProduct": seats,
        "products": sorted(set(p for p in products if p)),
        "consumptionObserved": {k: money(v) for k, v in consumption.items()},
        "acrAnnualised": acr,
        "consumptionWindowMonths": window_months,
        "greenfield": arr_total <= 0,
    }


def norm_name(value):
    return " ".join(str(value or "").lower().replace("&", "and").split())


# Override pipeline products -> the sizing-line product label they correspond to.
# A seller-asserted line REPLACES the modelled line for the same product rather than
# adding to it: a signed or verbally agreed quantity is a better fact than a whitespace
# model, and summing the two would count the same seats twice.
OVERRIDE_PRODUCT_LINES = {
    "copilot": ("Copilot", "seats"),
    "ghe": ("GHE", "seats"),
    "ghas": ("GHAS", "committers"),
}


def override_lines(record, rates, prices):
    """Turn seller-asserted pipeline into sizing lines.

    The whitespace model only sees installed base, so an account that has already
    committed to seats it has not yet provisioned sizes at zero and never enters the
    ranking pool at all. Reading the same overrides the rest of the pipeline already
    trusts lets a committed deal earn its rank on merit instead of being invisible.
    """
    lines = []
    for entry in (record or {}).get("pipeline", []) or []:
        product = str(entry.get("product", "")).strip().lower()
        mapped = OVERRIDE_PRODUCT_LINES.get(product)
        if not mapped:
            continue
        label, metric = mapped
        quantity = int(float(entry.get("seats") or entry.get("committers") or 0))
        if quantity <= 0:
            continue

        rate_month = entry.get("rateMonth")
        if rate_month:
            unit = money(float(rate_month) * MONTHS)
            basis = "observed"
        elif product == "copilot":
            unit, basis = rates.copilot_seat_year(prices.get("Copilot"))
        elif product == "ghe":
            unit, basis = rates.ghe_seat_year(prices.get("GitHub Enterprise"))
        else:
            unit, basis = rates.ghas_committer_year(prices.get("Advanced Security"))

        lines.append({
            "product": label,
            "metric": metric,
            "quantity": quantity,
            "rate": unit,
            "basis": "seller-asserted",
            "priceBasis": basis,
            "value": money(quantity * unit),
            "note": entry.get("note") or "Seller-asserted; agreed with the customer, not yet raised in Salesforce",
        })
    return lines


def corrected_signals(account, override):
    """Apply seller-verified corrections to the telemetry a sizing line is built from.

    SuperDash reports some signals org-wide, so a figure can be internally consistent
    and still be the wrong basis for a quote - active committers counted across every
    cloud user, for instance, when only a subset is in scope for GHAS. A seller who has
    verified the real number with the customer needs a way to say so that survives the
    next run.

    This is deliberately NOT the `pipeline` override. Pipeline asserts a deal the
    customer has agreed to and flows into H1 coverage; this only corrects an input to
    the whitespace model, so it must not be counted as pipeline. Corrections are
    recorded on the line's note so the number stays traceable to whoever asserted it.
    """
    signals = dict(account.get("revenueSignals", {}) or {})
    applied = {}
    for key, value in ((override or {}).get("signals") or {}).items():
        if key not in signals:
            # An unknown key is a typo, not a correction. Silently adding it would
            # size off a field nothing reads.
            continue
        try:
            corrected = float(value)
        except (TypeError, ValueError):
            continue
        if corrected < 0:
            continue
        applied[key] = {"was": signals.get(key), "now": corrected}
        signals[key] = corrected
    return signals, applied


# An account whose committer count runs far ahead of the seats it actually licenses is
# not necessarily wrong - contractors and monorepo bots inflate the cloud-wide figure -
# but it is the single largest distortion available to this model, because GHAS bills
# per committer. Surfacing the ratio makes the assumption arguable before it is quoted.
COMMITTER_SEAT_RATIO = 1.5


def data_quality_flags(signals, corrections):
    """Flag sizing inputs a seller should confirm before the number leaves the room."""
    flags = []
    if "activeCommitters" in corrections:
        return flags  # already corrected by the seller; nothing left to challenge

    committers = float(signals.get("activeCommitters") or 0)
    seats = max(
        float(signals.get("gheSeats") or 0),
        float(signals.get("vsBundleSeats") or 0),
    )
    if seats > 0 and committers > seats * COMMITTER_SEAT_RATIO:
        flags.append({
            "signal": "activeCommitters",
            "severity": "check",
            "detail": (
                f"{committers:,.0f} active committers against {seats:,.0f} licensed seats "
                f"({committers / seats:.1f}x). GHAS is sized per committer, so if the "
                f"committer count includes people outside the GHAS scope the sizing is "
                f"overstated. Confirm the number, then correct it with a `signals` override."
            ),
        })
    return flags


# A Team-plan account cannot buy GHAS - the product is not sold on that plan - so any
# GHAS line against one prices something the customer could never be invoiced for. The
# real motion is consolidation onto GHE, sized off the Team seats they already run.
TEAM_PLAN_NOTE = ("On a GitHub Team plan, not Enterprise. GHAS is not available on Team, "
                  "so the motion is platform consolidation onto GHE, sized off the Team "
                  "seats already in use")


def size_account(account, actuals, rates, override=None, licensing=None):
    """Convert an account's whitespace signals into dollars, with basis tags."""
    signals, corrections = corrected_signals(account, override)
    prices = observed_prices(actuals)
    state = current_state(actuals)

    copilot_seats = max(0, int(float(signals.get(COL_COPILOT_POTENTIAL) or 0)))
    ado_seats = max(0, int(float(signals.get(COL_ADO) or 0)))

    # GHAS licenses are consumed by unique ACTIVE COMMITTERS (L90d), not by seats.
    # Sizing off seat whitespace would price a product the customer would never be
    # billed for, so committers are the basis and existing GHAS coverage is netted off.
    committers_total = max(0, int(float(signals.get("activeCommitters") or 0)))
    ghas_covered = max(0, int(float(signals.get("ghasSeats") or 0)))
    committers = max(0, committers_total - ghas_covered)

    # Live licensing outranks the upload, because it is what GitHub actually bills.
    # It never outranks the seller: an explicit correction or an agreed deal is a
    # harder fact than a telemetry reading, so both are left alone below.
    live = licensing or {}
    live_plan = live.get("planType")
    team_plan = live_plan == "team"
    copilot_basis = None
    ghas_basis = None
    rebased = {}

    if live:
        live_seats = int(live.get("enterpriseSeatsConsumed") or 0) + int(live.get("teamSeatsConsumed") or 0)
        live_copilot = int(live.get("copilotSeats") or 0)
        live_gap = max(0, live_seats - live_copilot)
        if live_seats > 0:
            rebased["copilot"] = {"was": copilot_seats, "now": live_gap,
                                  "seats": live_seats, "existing": live_copilot}
            copilot_seats = live_gap
            copilot_basis = "live"

        if not team_plan and "activeCommitters" not in corrections:
            live_committers = int(live.get("maxCommitters") or 0)
            live_ghas = int(live.get("ghasMeteredCommitters") or 0)
            if live_committers > 0:
                gap = max(0, live_committers - live_ghas)
                rebased["ghas"] = {"was": committers, "now": gap,
                                   "committers": live_committers, "existing": live_ghas}
                committers = gap
                ghas_basis = "live"

    if team_plan:
        # Drop the modelled GHAS line entirely and replace it with the conversion.
        rebased["ghas"] = {"was": committers, "now": 0, "reason": "team plan"}
        committers = 0
        team_seats = int(live.get("teamSeatsConsumed") or 0) or int(live.get("enterpriseSeatsConsumed") or 0)
        if team_seats > 0:
            ado_seats = team_seats

    lines = []

    if copilot_seats > 0:
        rate, basis = rates.copilot_seat_year(prices.get("Copilot"))
        note = "GHE/VS seats without Copilot today"
        if copilot_basis == "live":
            r = rebased["copilot"]
            note = ("%d licensed seats live, %d already on Copilot; upload said %d"
                    % (r["seats"], r["existing"], r["was"]))
        lines.append({
            "product": "Copilot",
            "metric": "seats",
            "quantity": copilot_seats,
            "rate": rate,
            "basis": copilot_basis or basis,
            "priceBasis": basis,
            "value": money(copilot_seats * rate),
            "note": note,
        })

    if committers > 0:
        rate, basis = rates.ghas_committer_year(prices.get("Advanced Security"))
        sku = rates.assumptions.get("ghasSkuForSizing", "codeSecurity")
        note = ("%d active committers (L90d) not covered by GHAS today; GHAS bills "
                "per active committer - sized as %s" % (committers, sku))
        if "activeCommitters" in corrections:
            was = corrections["activeCommitters"]["was"]
            note += ("; seller-corrected from %d reported by the upload%s"
                     % (int(float(was or 0)),
                        (" - " + str((override or {}).get("signalsReason"))
                         if (override or {}).get("signalsReason") else "")))
        elif ghas_basis == "live":
            r = rebased["ghas"]
            note = ("%d billable committers live, %d already licensed; upload implied %d"
                    % (r["committers"], r["existing"], r["was"]))
        if "activeCommitters" in corrections:
            line_basis = "seller-corrected"
        else:
            line_basis = ghas_basis or basis
        lines.append({
            "product": "GHAS",
            "metric": "committers",
            "quantity": committers,
            "rate": rate,
            "basis": line_basis,
            "priceBasis": basis,
            "value": money(committers * rate),
            "note": note,
        })

    if ado_seats > 0:
        rate, basis = rates.ghe_seat_year(prices.get("GitHub Enterprise"))
        lines.append({
            "product": "GHE",
            "metric": "seats",
            "quantity": ado_seats,
            "rate": rate,
            "basis": "live" if team_plan else basis,
            "priceBasis": basis,
            "value": money(ado_seats * rate),
            "note": TEAM_PLAN_NOTE if team_plan else "Azure DevOps TAM available to migrate",
        })

    # Seller-asserted lines replace the modelled line for the same product. A quantity
    # the customer has already agreed to is a harder fact than a whitespace estimate,
    # and adding the two would count the same seats twice.
    asserted = override_lines(override, rates, prices)
    if asserted:
        replaced = {line["product"] for line in asserted}
        lines = [line for line in lines if line["product"] not in replaced] + asserted

    # AIU deliberately does NOT contribute to potential ARR.
    #
    # Two things are true and must not be conflated:
    #   1. Credits already invoiced are revenue we ALREADY earn. Annualising them into
    #      "potential" would double-count the run-rate and inflate the number.
    #   2. Every Copilot seat sold ships with included credits (1,900/mo Business,
    #      3,900/mo Enterprise). Those are bundled, so they are capacity, not new revenue.
    #      Overage beyond the included pool is real incremental revenue, but nothing in the
    #      data supports forecasting it without inventing a consumption curve.
    #
    # So AIU is reported as measured run-rate plus contractual capacity unlocked by the
    # Copilot seats in this plan, and both are kept out of potentialArr.
    aiu_spend = state["acrAnnualised"].get("copilot aiu", 0.0)
    aiu_units_billed = 0.0
    for row in (actuals or {}).get("consumption", []):
        if str(row.get("product_name", "")) == "copilot aiu":
            aiu_units_billed += float(row.get("billed_units") or 0)
    window = state["consumptionWindowMonths"] or 0
    factor = (12.0 / window) if window else 0.0
    included_per_seat_year = rates.included_credits_per_seat_year()
    aiu = {
        "currentAnnualisedSpend": money(aiu_spend),
        "currentAnnualisedCredits": int(aiu_units_billed * factor) if factor else int(aiu_units_billed),
        "includedCreditCapacityFromPlan": int(copilot_seats * included_per_seat_year),
        "includedCreditsPerSeatYear": included_per_seat_year,
        "basis": "observed" if aiu_units_billed > 0 else "none",
        "note": ("Credits already invoiced are existing revenue, not upside; included credits "
                 "ship bundled with Copilot seats. Neither is counted in potential ARR."),
    }

    total = money(sum(line["value"] for line in lines))
    bases = sorted(set(line["basis"] for line in lines))

    return {
        "potentialArr": total,
        "lines": lines,
        "bases": bases,
        "aiu": aiu,
        "current": state,
        "signalCorrections": corrections,
        "dataQualityFlags": data_quality_flags(signals, corrections),
        "liveBasis": {
            "available": bool(live),
            "planType": live_plan,
            "rebased": rebased,
        },
        "sizingCoverage": "sized" if lines else "unsized",
    }


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: potential.py <report.json> <runDir> [raw-actuals.json] [pricing.json] [overrides.json]")

    report_path, run_dir = sys.argv[1], sys.argv[2]
    actuals_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(run_dir, "raw-actuals.json")
    pricing_path = sys.argv[4] if len(sys.argv) > 4 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "pricing.json")
    overrides_path = sys.argv[5] if len(sys.argv) > 5 else os.path.join(run_dir, "overrides.json")

    report = load(report_path)
    if not report:
        raise SystemExit("Cannot read territory report: %s" % report_path)
    pricing = load(pricing_path)
    if not pricing:
        raise SystemExit("Cannot read pricing config: %s" % pricing_path)

    raw = load(actuals_path, {}) or {}
    by_account = raw.get("accounts", {})
    rates = Rates(pricing)

    # Live licensing is optional. Where it is absent the model falls back to the upload
    # and says so on the line, because an absent reading is not evidence of zero.
    licensing = (load(os.path.join(run_dir, "licensing.json"), {}) or {}).get("accounts", {}) or {}

    sized = {}
    overrides = (load(overrides_path, {}) or {}).get("accounts", {}) or {}
    by_norm = {norm_name(k): v for k, v in overrides.items()}
    matched = set()
    for account in report.get("accounts", []):
        sid = account.get("salesforceId") or ""
        name = account.get("name", "")
        record = overrides.get(sid) or overrides.get(name) or by_norm.get(norm_name(name))
        if record:
            matched.add(name)
        sized[sid or name] = size_account(
            account, by_account.get(sid), rates, record, licensing.get(sid))

    unmatched = sorted(k for k in overrides if norm_name(k) not in {norm_name(m) for m in matched})

    totals = {}
    for entry in sized.values():
        for line in entry["lines"]:
            key = line["product"]
            bucket = totals.setdefault(key, {"quantity": 0, "value": 0.0, "accounts": 0})
            bucket["quantity"] += line["quantity"]
            bucket["value"] = money(bucket["value"] + line["value"])
            bucket["accounts"] += 1

    # Kept separate from `totals` on purpose: AIU is run-rate plus bundled capacity,
    # not incremental ARR, and merging the two is exactly the error to avoid.
    aiu_totals = {
        "currentAnnualisedSpend": money(sum(e["aiu"]["currentAnnualisedSpend"] for e in sized.values())),
        "currentAnnualisedCredits": sum(e["aiu"]["currentAnnualisedCredits"] for e in sized.values()),
        "includedCreditCapacityFromPlan": sum(e["aiu"]["includedCreditCapacityFromPlan"] for e in sized.values()),
        "accountsConsumingAiu": sum(1 for e in sized.values() if e["aiu"]["currentAnnualisedCredits"] > 0),
    }

    installed = {
        "arr": money(sum(e["current"]["arr"] for e in sized.values())),
        "acrAnnualised": {},
    }
    for entry in sized.values():
        for product, value in entry["current"]["acrAnnualised"].items():
            installed["acrAnnualised"][product] = money(
                installed["acrAnnualised"].get(product, 0.0) + value)

    out = {
        "generatedFrom": os.path.basename(report_path),
        "pricingBasis": {
            "order": "seller-asserted > seller-corrected > live > observed > list > derived",
            "derivedUsedFor": "GitHub Enterprise per-seat (no public list price)",
            "liveUsedFor": ("Copilot seat gap and GHAS billable committers, read from "
                            "GitHub licensing rather than the upload"),
        },
        "assumptions": pricing.get("assumptions", {}),
        "accounts": sized,
        "totals": totals,
        "aiuTotals": aiu_totals,
        "installed": installed,
        "accountsSized": sum(1 for e in sized.values() if e["sizingCoverage"] == "sized"),
        "accountsTotal": len(sized),
        "accountsWithArr": sum(1 for e in sized.values() if not e["current"]["greenfield"]),
        "sellerAssertedAccounts": sorted(matched),
        "signalCorrections": {
            key: entry["signalCorrections"]
            for key, entry in sized.items() if entry.get("signalCorrections")
        },
        "dataQualityFlags": {
            key: entry["dataQualityFlags"]
            for key, entry in sized.items() if entry.get("dataQualityFlags")
        },
        "liveBasis": {
            "accountsRebased": sum(1 for e in sized.values()
                                   if e.get("liveBasis", {}).get("rebased")),
            "accountsWithLiveData": sum(1 for e in sized.values()
                                        if e.get("liveBasis", {}).get("available")),
            "teamPlanAccounts": sorted(
                key for key, e in sized.items()
                if e.get("liveBasis", {}).get("planType") == "team"),
        },
        "overridesUnmatched": unmatched,
    }

    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "potential.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)

    print(json.dumps({
        "potentialPath": path,
        "accountsSized": out["accountsSized"],
        "accountsTotal": out["accountsTotal"],
        "accountsWithArr": out["accountsWithArr"],
        "sellerAssertedAccounts": out["sellerAssertedAccounts"],
        "overridesUnmatched": out["overridesUnmatched"],
        "totals": {k: v["value"] for k, v in totals.items()},
    }))


if __name__ == "__main__":
    main()
