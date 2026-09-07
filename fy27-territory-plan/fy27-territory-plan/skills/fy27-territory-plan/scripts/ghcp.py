"""GHCP segmentation for the sprint queue.

The sprint queue is prioritised around GitHub Copilot, which splits into two motions that
need different conversations, different personas and different measures:

  Seat expansion  - attach Copilot to GHE licences the customer already pays for.
                    Addressable = installed GHE seats - Copilot seats.
  AIU activation  - get individual users consuming more tokens. Measured per user against
                    the credits bundled with every Copilot seat.

Only installed seats create headroom. Agreed-but-unlanded GHE does not, because there is
nothing yet to attach a Copilot seat to; those accounts sit in a third group until the GHE
lands.

Every number here is derived from licensing and billing data. Nothing is asserted.
"""

INCLUDED_CREDITS_PER_SEAT_MONTH = 1900
INCLUDED_CREDITS_PER_SEAT_YEAR = INCLUDED_CREDITS_PER_SEAT_MONTH * 12  # 22,800
AI_CREDIT_USD = 0.01
COPILOT_BUSINESS_PER_USER_MONTH = 19.0
COPILOT_ENTERPRISE_PER_USER_MONTH = 39.0
# An observed rate is believed only within this band around the real SKUs. Outside it, the
# figure is an artefact of annualisation rather than a price.
RATE_FLOOR = COPILOT_BUSINESS_PER_USER_MONTH * 0.75
RATE_CEILING = COPILOT_ENTERPRISE_PER_USER_MONTH * 1.25

SEG_SEAT = "Copilot seat expansion"
SEG_AIU = "AIU activation"
SEG_GHE = "Land GHE first"

SEGMENT_ORDER = [SEG_SEAT, SEG_AIU, SEG_GHE]

SEGMENT_BLURB = {
    SEG_SEAT: (
        "GHE licences already paid for with no Copilot attached. Addressable = GHE seats "
        "minus Copilot seats. Ordered by the annual value of the headroom."
    ),
    SEG_AIU: (
        "Copilot seats already sold where users are not yet consuming their bundled "
        f"{INCLUDED_CREDITS_PER_SEAT_MONTH:,} credits a month. Ordered by dormant seats - the "
        "seats not yet returning the value they were bought for."
    ),
    SEG_GHE: (
        "No GHE landed, so Copilot has nothing to attach to. Land the platform first; the "
        "Copilot headroom follows automatically."
    ),
}


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def seat_facts(licensing_entry):
    """Installed seat counts for one account."""
    lic = licensing_entry or {}
    enterprise = int(_num(lic.get("enterpriseSeatsConsumed")))
    team = int(_num(lic.get("teamSeatsConsumed")))
    copilot = int(_num(lic.get("copilotSeats")))
    return {
        "gheSeats": enterprise + team,
        "enterpriseSeats": enterprise,
        "teamSeats": team,
        "copilotSeats": copilot,
        "planType": lic.get("planType") or "",
        "onTeamPlan": team > 0,
    }


def copilot_rate(account, seats):
    """Per-user-month Copilot price used to value *new* seats.

    The account's own observed price wins, but only when it is plausible. Observed rate is
    annualised ACR divided by current seats, so an account that added seats recently shows a
    rate well below what it actually pays - the revenue has not caught up with the seat count
    yet. Pricing headroom off that lagged figure would understate the prize. Anything outside
    a band around the real SKUs ($19 Business, $39 Enterprise) falls back to list.
    """
    acr = ((account.get("current") or {}).get("acrAnnualised") or {})
    observed = _num(acr.get("copilot"))
    if seats > 0 and observed > 0:
        rate = round(observed / seats / 12, 2)
        if RATE_FLOOR <= rate <= RATE_CEILING:
            return rate, "observed"
        return (
            COPILOT_BUSINESS_PER_USER_MONTH,
            f"list - observed ${rate:,.2f} outside plausible band",
        )
    return COPILOT_BUSINESS_PER_USER_MONTH, "list (Copilot Business)"


def aiu_facts(account, copilot_seats):
    """Per-user credit consumption against the bundled allowance.

    Utilisation is deliberately measured per user, because that is the level at which
    consumption is actually driven. Overage - and therefore incremental revenue - only
    exists above the included allowance.
    """
    aiu = account.get("aiu") or {}
    credits = _num(aiu.get("currentAnnualisedCredits"))
    spend = _num(aiu.get("currentAnnualisedSpend"))
    if copilot_seats <= 0:
        return {
            "annualCredits": credits,
            "annualSpend": spend,
            "creditsPerUserMonth": None,
            "allowanceUsed": None,
            "dormantSeats": 0.0,
            "overageCredits": 0.0,
            "overageValue": 0.0,
        }
    allowance = copilot_seats * INCLUDED_CREDITS_PER_SEAT_YEAR
    used = credits / allowance if allowance else 0.0
    overage = max(credits - allowance, 0.0)
    return {
        "annualCredits": credits,
        "annualSpend": spend,
        "creditsPerUserMonth": round(credits / copilot_seats / 12),
        "allowanceUsed": used,
        # Seats not yet returning their bundled value. Fractional on purpose: it is a
        # measure of unrealised capacity, not a count of named people.
        "dormantSeats": round(copilot_seats * (1 - min(used, 1.0)), 1),
        "overageCredits": overage,
        "overageValue": round(overage * AI_CREDIT_USD, 2),
    }


def classify(seats, aiu):
    """Assign exactly one primary play.

    Accounts are assigned on seats at stake rather than dollars. AIU overage revenue is
    legitimately zero until an account exhausts its allowance, so a dollar comparison would
    push a large activation case into the seat segment for the sake of a handful of seats.
    """
    headroom = max(seats["gheSeats"] - seats["copilotSeats"], 0)
    dormant = aiu["dormantSeats"]
    if seats["copilotSeats"] == 0 and headroom == 0:
        return SEG_GHE, headroom
    if headroom >= dormant:
        return SEG_SEAT, headroom
    return SEG_AIU, headroom


def next_step(segment, seats, aiu, headroom):
    """A concrete next step for the segment, grounded in the PAF key actions."""
    ghe = seats["gheSeats"]
    cp = seats["copilotSeats"]
    if segment == SEG_GHE:
        return (
            "No GHE seats live, so there is nothing for Copilot to attach to. Land the "
            "platform first, then the Copilot headroom follows automatically."
        )
    if segment == SEG_SEAT:
        if cp == 0:
            return (
                f"{ghe} GHE seats paid for and no Copilot against any of them. Agree a "
                "phased rollout - name the first cohort, a date, and the measures it will "
                "be judged on - rather than opening the whole estate at once."
            )
        attach = cp / ghe if ghe else 0
        return (
            f"{cp} of {ghe} GHE seats have Copilot ({attach:.0%} attach). Use the first "
            f"cohort's measured results to justify wave two across the remaining {headroom} "
            "seats; a proven internal number carries further than a benchmark."
        )
    used = aiu["allowanceUsed"] or 0
    if used < 0.05:
        return (
            f"{cp} seats live and effectively no credits consumed. These seats are dormant - "
            "a renewal risk today and the reason expansion is hard to argue. Get users off "
            "completions-only and into agentic work: assign issues to the coding agent and "
            "turn on Copilot code review."
        )
    if used < 0.5:
        return (
            f"Users are at {used:.0%} of their bundled allowance "
            f"({aiu['creditsPerUserMonth']:,} of {INCLUDED_CREDITS_PER_SEAT_MONTH:,} credits "
            "a month). Broaden from chat into agent-driven tasks and model choice; premium "
            "models and agent sessions are where consumption actually moves."
        )
    return (
        f"Users are at {used:.0%} of the bundled allowance "
        f"({aiu['creditsPerUserMonth']:,} credits a month). Closest account to genuine AIU "
        "overage - agree the budget and cost-centre approach now, before the allowance is "
        "exhausted, so the first overspend is a planned conversation."
    )


def build(focus_accounts, licensing_accounts, pricing=None):
    """Return the focus list annotated with GHCP facts, segmented and ordered."""
    rows = []
    for account in focus_accounts:
        lic = (licensing_accounts or {}).get(account.get("salesforceId") or "") or {}
        seats = seat_facts(lic)
        aiu = aiu_facts(account, seats["copilotSeats"])
        segment, headroom = classify(seats, aiu)
        rate, basis = copilot_rate(account, seats["copilotSeats"])
        prize = round(headroom * rate * 12, 2)
        rows.append(
            {
                "key": account.get("key"),
                "name": account.get("name"),
                "h1Rank": account.get("rank"),
                "segment": segment,
                "gheSeats": seats["gheSeats"],
                "copilotSeats": seats["copilotSeats"],
                "onTeamPlan": seats["onTeamPlan"],
                "planType": seats["planType"],
                "attachRate": (
                    seats["copilotSeats"] / seats["gheSeats"] if seats["gheSeats"] else None
                ),
                "headroom": headroom,
                "copilotRate": rate,
                "rateBasis": basis,
                "prize": prize,
                "hasLicensing": bool(lic),
                "nextStep": next_step(segment, seats, aiu, headroom),
                **{f"aiu{k[0].upper()}{k[1:]}": v for k, v in aiu.items()},
            }
        )

    def sort_key(row):
        if row["segment"] == SEG_SEAT:
            return (-row["prize"], row["h1Rank"] or 999)
        if row["segment"] == SEG_AIU:
            return (-row["aiuDormantSeats"], row["h1Rank"] or 999)
        return (row["h1Rank"] or 999, 0)

    ordered = []
    for segment in SEGMENT_ORDER:
        group = sorted([r for r in rows if r["segment"] == segment], key=sort_key)
        for i, row in enumerate(group, 1):
            row["segmentRank"] = i
        ordered.extend(group)
    return ordered


def totals(rows):
    """Segment subtotals, plus a book-level roll-up."""
    out = {}
    for segment in SEGMENT_ORDER:
        group = [r for r in rows if r["segment"] == segment]
        out[segment] = {
            "accounts": len(group),
            "gheSeats": sum(r["gheSeats"] for r in group),
            "copilotSeats": sum(r["copilotSeats"] for r in group),
            "headroom": sum(r["headroom"] for r in group),
            "prize": round(sum(r["prize"] for r in group), 2),
            "dormantSeats": round(sum(r["aiuDormantSeats"] for r in group), 1),
            "overageValue": round(sum(r["aiuOverageValue"] for r in group), 2),
        }
    out["_book"] = {
        "accounts": len(rows),
        "headroom": sum(r["headroom"] for r in rows),
        "prize": round(sum(r["prize"] for r in rows), 2),
        "copilotSeats": sum(r["copilotSeats"] for r in rows),
        "dormantSeats": round(sum(r["aiuDormantSeats"] for r in rows), 1),
        "overageValue": round(sum(r["aiuOverageValue"] for r in rows), 2),
    }
    return out
