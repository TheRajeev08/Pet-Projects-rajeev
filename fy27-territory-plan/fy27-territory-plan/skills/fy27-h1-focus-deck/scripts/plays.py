#!/usr/bin/env python3
"""Refine play classification once Salesforce industry is known.

    plays.py <runDir>

`workbook.py` assigns plays from the upload alone, and one rung of that ladder needs
a fact the upload does not carry: whether the account operates in a regulated
industry. An account already on GitHub Enterprise with neither Copilot nor GHAS is a
Trust conversation if it is regulated and an Innovate conversation if it is not, and
nothing in SuperDash distinguishes the two. Those accounts are marked
`playPendingIndustry` at build time and settled here.

This step only ever moves an account between Trust and Innovate, with one deliberate
exception: an override carrying `sellerAsserted: true` and a `play` may also give a
play to an account the ladder left "Unclassified". SuperDash has no row for a pure
prospect, so "Unclassified" there means "no data", not "no opportunity". That
exception does not confer a rank - rank.py still requires potential or engagement -
so the focus set cannot grow just because a seller named an account.

Two sources of truth, in order:

  1. `crm-context.json` - Salesforce `Account.Industry`, tested against REGULATED.
  2. `overrides.json` - seller corrections, because the Salesforce picklist is coarse
     and often blank. A medical-device manufacturer is filed under "Manufacturing"
     and a fintech under "Software & Internet"; both are regulated and neither reads
     that way.

Writes the refined play back into fy27-territory-plan.json, which rank.py stage 2
reads, so there is one source of truth for the play rather than two that can drift.
"""

import json
import os
import sys

# Industries where security and governance are a compliance obligation rather than an
# engineering preference. Matched as case-insensitive substrings, because the
# Salesforce picklist varies ("Financial Services", "Banking & Financial Services").
REGULATED = (
    "financial service", "banking", "insurance", "capital market", "fintech",
    "health", "life science", "pharma", "medical", "biotech",
    "energy", "utilit", "oil & gas",
    "government", "public sector", "defense", "defence", "aerospace",
    "telecommunication", "telecom",
)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def is_regulated(industry):
    text = (industry or "").strip().lower()
    if not text:
        return None
    return any(token in text for token in REGULATED)


def norm(name):
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def refine(report, crm, overrides, licensing=None):
    # workbook.py owns the ladder and the play copy; import it rather than restate it,
    # so the rule cannot drift between the app build and this refinement. It ships as
    # a sibling skill in both the dev tree and the published plugin.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.normpath(
        os.path.join(here, "..", "..", "fy27-territory-plan", "scripts")))
    from workbook import classify_play, play_reason, guidance, next_action, dashboards

    crm_accounts = (crm or {}).get("accounts", {}) or {}
    ov_accounts = (overrides or {}).get("accounts", {}) or {}
    by_norm = {norm(k): v for k, v in ov_accounts.items()}

    changes, unknown = [], []
    for account in report.get("accounts", []) or []:
        sid = account.get("salesforceId") or ""
        override = ov_accounts.get(sid) or by_norm.get(norm(account.get("name")))
        # A seller-asserted play is the one case where this step may touch an account
        # the ladder left outside the play set. SuperDash carries no row signal for a
        # pure prospect, so "Unclassified" there means "no data", not "no opportunity".
        # It is gated behind an explicit flag so it can never happen by accident, and
        # it does NOT confer a rank: rank.py still requires potential or engagement,
        # so an asserted account earns focus-set membership on evidence or not at all.
        asserted = bool(override and override.get("sellerAsserted")
                        and override.get("play"))
        if not account.get("playPendingIndustry") and not asserted:
            continue
        record = crm_accounts.get(sid, {}) or {}

        industry = record.get("industry") or ""
        source = "salesforce"
        if override and override.get("industry"):
            industry = override["industry"]
            source = "override"

        regulated = is_regulated(industry)
        if override and override.get("regulated") is not None:
            regulated = bool(override["regulated"])
            source = "override"

        signals = account.get("revenueSignals", {}) or {}
        ghe = float(signals.get("gheSeats") or 0)
        copilot = float(signals.get("copilotSeats") or 0)
        ghas = float(signals.get("ghasSeats") or 0)

        if override and override.get("play"):
            new_play = override["play"]
            basis = "Seller-asserted play. %s" % (
                override.get("playReason") or override.get("reason") or "")
            source = "seller"
        else:
            new_play = classify_play(ghe, copilot, ghas, regulated)
            basis = play_reason(ghe, copilot, ghas, regulated, industry)

        if regulated is None and source == "salesforce":
            unknown.append(account.get("name"))

        old_play = account.get("primaryPlay")
        account["primaryPlay"] = new_play
        account["plays"] = [new_play] + [p for p in account.get("plays", [])
                                         if p != new_play and p != "Unclassified"]
        if asserted:
            # The account now has a play, so it is no longer unclassified. Record the
            # assertion so every downstream surface can label it as seller conviction
            # rather than observed product signal.
            account["classified"] = True
            account["sellerAsserted"] = True
        account["playBasis"] = basis.strip()
        account["industry"] = industry
        account["industrySource"] = source
        account["regulated"] = regulated
        account["winPlan"] = " ".join(guidance(p) for p in account["plays"][:2])
        account["nextAction"] = next_action(new_play)
        account["dashboards"] = dashboards(new_play, sid)
        if old_play != new_play:
            changes.append((account.get("name"), old_play, new_play, basis,
                            "signal"))

    # Team-plan accounts are a ladder blind spot. SuperDash reports their committers, so
    # the ladder reads them as a security play, but GHAS is not sold on Team - the motion
    # is consolidation onto GHE. Live licensing is the only place this is visible, so the
    # correction lands here. An explicit seller play override still wins.
    team_plan = set(licensing or ())
    for account in report.get("accounts", []) or []:
        sid = account.get("salesforceId") or ""
        if sid not in team_plan:
            continue
        override = ov_accounts.get(sid) or by_norm.get(norm(account.get("name")))
        if override and override.get("play"):
            continue
        old_play = account.get("primaryPlay")
        if old_play == "Scale":
            continue
        account["primaryPlay"] = "Scale"
        account["plays"] = ["Scale"] + [p for p in account.get("plays", [])
                                        if p not in ("Scale", "Unclassified")]
        account["playBasis"] = ("On a GitHub Team plan, not Enterprise. GHAS is not "
                                "available on Team, so the motion is consolidation onto "
                                "GHE before any security or Copilot expansion.")
        account["winPlan"] = " ".join(guidance(p) for p in account["plays"][:2])
        account["nextAction"] = next_action("Scale")
        account["dashboards"] = dashboards("Scale", sid)
        changes.append((account.get("name"), old_play, "Scale", account["playBasis"],
                        "licensing"))

    summary = {}
    for account in report.get("accounts", []) or []:
        play = account.get("primaryPlay") or "Unclassified"
        summary[play] = summary.get(play, 0) + 1
    report["playSummary"] = [{"play": p, "accounts": summary[p]}
                             for p in sorted(summary, key=lambda k: -summary[k])]
    return changes, unknown


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: plays.py <runDir>")
    run_dir = sys.argv[1]
    report_path = os.path.join(run_dir, "fy27-territory-plan.json")

    report = load(report_path)
    if not report:
        raise SystemExit("Cannot read %s" % report_path)
    crm = load(os.path.join(run_dir, "crm-context.json"), {}) or {}
    overrides = load(os.path.join(run_dir, "overrides.json"), {}) or {}
    live = (load(os.path.join(run_dir, "licensing.json"), {}) or {}).get("accounts", {}) or {}
    team_plan = {sid for sid, v in live.items() if v.get("planType") == "team"}

    changes, unknown = refine(report, crm, overrides, team_plan)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    # Widening licensing to the whole book means this correction now reaches every
    # account, not just the ranked ones, so play counts shift on the first run after
    # that change. A shifted mix with no named cause reads as the tool drifting. Write
    # the before/after down so it reads as a correction instead.
    #
    # It names accounts, so it stays in the run directory under the same rule as
    # overrides.json and conversations.json.
    licence_moves = [c for c in changes if c[4] == "licensing"]
    reclass_path = os.path.join(run_dir, "reclassification.json")
    with open(reclass_path, "w", encoding="utf-8") as fh:
        json.dump({
            "reclassified": len(changes),
            "licenceDriven": len(licence_moves),
            "signalDriven": len(changes) - len(licence_moves),
            "accounts": [{"name": n, "from": old, "to": new, "cause": cause,
                          "reason": basis}
                         for n, old, new, basis, cause in changes],
        }, fh, indent=2, ensure_ascii=False)

    print(json.dumps({
        "reportPath": report_path,
        "reclassificationPath": reclass_path,
        "reclassified": len(changes),
        "licenceDriven": len(licence_moves),
        "industryUnknown": len(unknown),
        "playSummary": report.get("playSummary"),
    }))
    for name, old, new, basis, cause in changes:
        print("  %-44s %-10s -> %-9s [%s] %s" % (name[:44], old, new, cause, basis))
    if unknown:
        print("  industry unknown (defaulted to Innovate): %s"
              % ", ".join(sorted(unknown)[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
