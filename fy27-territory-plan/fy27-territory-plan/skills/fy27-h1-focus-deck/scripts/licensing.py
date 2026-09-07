#!/usr/bin/env python3
"""Normalise raw Revenue MCP licensing responses into a sizing-ready shape.

The SuperDash upload reports signals org-wide, so it overstates the population an
account is actually billed for - GHAS most of all, because GHAS bills per active
committer and the upload counts every committer in the cloud tenant. Live licensing
tells us what GitHub itself thinks the billable population is.

This module does no arithmetic beyond summing across an account's GitHub tenants. It
reshapes; `potential.py` prices.

Reads:  <runDir>/licensing/raw.json   (written by the gathering pass)
Writes: <runDir>/licensing.json       (keyed by Salesforce Account ID)
"""

import json
import os
import sys

# Product names as they appear in get_licensing_summary responses. Kept as constants so
# a rename upstream fails loudly here rather than silently zeroing a headline number.
PRODUCT_ENTERPRISE = "github enterprise"
PRODUCT_TEAM = "github team organization"
PRODUCT_GHAS_PREFIX = "advanced security"

COPILOT_FEATURES = ("copilot business", "copilot enterprise")


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def plan_type(product_type):
    """Classify a tenant as enterprise or team.

    This matters more than it looks. An account on a Team plan cannot buy GHAS - the
    product is not available to them - so sizing one as a GHAS play prices something
    they could never be invoiced for. The correct motion is GHE consolidation.
    """
    text = (product_type or "").lower()
    if "ghec" in text or "enterprise" in text:
        return "enterprise"
    if "business plan" in text or "organization" in text or "team" in text:
        return "team"
    return "unknown"


def read_summary(summary):
    """Pull the four numbers sizing needs out of one tenant's licensing summary."""
    out = {
        "planType": plan_type(summary.get("product_type")),
        "productType": summary.get("product_type"),
        "maxCommitters": int(summary.get("maximum_committers") or 0),
        "enterpriseSeatsConsumed": 0,
        "teamSeatsConsumed": 0,
        "copilotSeats": 0,
        "ghasMeteredCommitters": 0,
    }

    for feature in summary.get("product_features") or []:
        if str(feature.get("name", "")).lower() in COPILOT_FEATURES:
            out["copilotSeats"] += int(feature.get("seats") or 0)

    for product in summary.get("products") or []:
        name = str(product.get("name", "")).lower()
        licences = product.get("licenses") or {}
        if name == PRODUCT_ENTERPRISE:
            out["enterpriseSeatsConsumed"] += int(licences.get("consumed") or 0)
        elif name == PRODUCT_TEAM:
            out["teamSeatsConsumed"] += int(licences.get("consumed") or 0)
        elif name.startswith(PRODUCT_GHAS_PREFIX):
            # GHAS appears under several names and under both licensing models: as
            # "Advanced Security" on a Volume contract, and split into Code Security /
            # Secret Protection when metered. Reading only one shape would report an
            # account's existing coverage as zero and size the whole population as gap.
            held = max(int(licences.get("metered_consumed") or 0),
                       int(licences.get("consumed") or 0))
            # Either SKU consumes a committer licence, and an account is not billed
            # twice for the same person, so take the larger rather than the sum.
            out["ghasMeteredCommitters"] = max(out["ghasMeteredCommitters"], held)

    return out


def merge(entry):
    """Roll one Salesforce account's tenants into a single sizing input.

    Several accounts own more than one GitHub tenant. Seats and committers add across
    them; the plan type is enterprise if ANY tenant is, because one GHEC tenant makes
    the enterprise motion available to the account.
    """
    tenants = []
    for record in entry.get("summaries") or []:
        summary = record.get("summary")
        if not summary:
            continue
        parsed = read_summary(summary)
        parsed["slug"] = record.get("slug")
        parsed["namespace"] = record.get("namespace")
        tenants.append(parsed)

    if not tenants:
        return None

    plans = {t["planType"] for t in tenants}
    if "enterprise" in plans:
        resolved_plan = "enterprise"
    elif "team" in plans:
        resolved_plan = "team"
    else:
        resolved_plan = "unknown"

    return {
        "name": entry.get("name"),
        "planType": resolved_plan,
        "productTypes": sorted({t["productType"] for t in tenants if t["productType"]}),
        "maxCommitters": sum(t["maxCommitters"] for t in tenants),
        "enterpriseSeatsConsumed": sum(t["enterpriseSeatsConsumed"] for t in tenants),
        "teamSeatsConsumed": sum(t["teamSeatsConsumed"] for t in tenants),
        "copilotSeats": sum(t["copilotSeats"] for t in tenants),
        "ghasMeteredCommitters": sum(t["ghasMeteredCommitters"] for t in tenants),
        "tenants": [
            {
                "slug": t["slug"],
                "namespace": t["namespace"],
                "productType": t["productType"],
                "maxCommitters": t["maxCommitters"],
                "enterpriseSeatsConsumed": t["enterpriseSeatsConsumed"],
                "teamSeatsConsumed": t["teamSeatsConsumed"],
                "copilotSeats": t["copilotSeats"],
                "ghasMeteredCommitters": t["ghasMeteredCommitters"],
            }
            for t in tenants
        ],
    }


def book_size(run_dir):
    """How many accounts the enrichment pass resolved, or 0 if it did not run.

    This is the denominator for licensing coverage. Without it a reading count is just
    a number, and a number with no denominator always sounds like enough.
    """
    context = load(os.path.join(run_dir, "crm-context.json")) or {}
    accounts = context.get("accounts") or {}
    if accounts:
        return len(accounts)
    activity = load(os.path.join(run_dir, "salesforce-activity.json")) or {}
    return len(activity.get("accounts") or {})


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: licensing.py <runDir> [raw.json]")

    run_dir = sys.argv[1]
    raw_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(run_dir, "licensing", "raw.json")

    raw = load(raw_path)
    if not raw:
        raise SystemExit("Cannot read raw licensing: %s" % raw_path)

    accounts = {}
    # An account with no live reading is NOT an account with zero licences. It is
    # recorded as unavailable so sizing can fall back to the upload and say so, rather
    # than silently pricing a zero.
    unavailable = {}
    misses = {}

    for sid, entry in (raw.get("accounts") or {}).items():
        merged = merge(entry)
        if merged:
            accounts[sid] = merged
        else:
            status = entry.get("status") or "error"
            misses[status] = misses.get(status, 0) + 1
            unavailable[sid] = {
                "name": entry.get("name"),
                "status": status,
                "reason": entry.get("error") or status or "no licensing summary returned",
            }

    # Coverage is stated as a fraction of the resolved book, not left implied. "63
    # accounts have live licensing" reads as thorough until you know the book is 251.
    book = book_size(run_dir)

    out = {
        "generatedFrom": os.path.basename(raw_path),
        "gatheredAt": raw.get("gatheredAt"),
        "source": raw.get("source"),
        "accounts": accounts,
        "unavailable": unavailable,
        "accountsWithLiveData": len(accounts),
        "accountsWithoutLiveData": len(unavailable),
        "bookSize": book,
        "notAttempted": max(0, book - len(accounts) - len(unavailable)) if book else None,
        "missesByReason": misses,
        "teamPlanAccounts": sorted(
            v["name"] for v in accounts.values() if v["planType"] == "team"),
    }

    path = os.path.join(run_dir, "licensing.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, ensure_ascii=False)

    print("live licensing: %d accounts | no live data: %d | team-plan: %d"
          % (out["accountsWithLiveData"], out["accountsWithoutLiveData"],
             len(out["teamPlanAccounts"])))
    if book:
        print("coverage: %d/%d of the resolved book (%d not attempted)"
              % (out["accountsWithLiveData"], book, out["notAttempted"]))
    else:
        print("coverage: unknown - enrichment did not run, so there is no book to "
              "measure against and every licence field stays blank")
    for reason in sorted(misses):
        print("  no reading: %-22s %d" % (reason, misses[reason]))
    for name in out["teamPlanAccounts"]:
        print("  team plan -> %s" % name)
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
