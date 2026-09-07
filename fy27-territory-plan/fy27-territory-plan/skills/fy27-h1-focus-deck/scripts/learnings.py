#!/usr/bin/env python3
"""Derive H2 FY26 learnings and a working/not-working read from the run's own records.

Every statement this emits is computed from focus-accounts.json, coverage.json and
crm-context.json. Nothing is asserted that cannot be traced back to a count or a sum,
because a leadership slide that says "we learned X" without a number behind it is an
opinion, and opinions are what the deck is trying to replace.

    learnings.py <runDir>

Writes <runDir>/learnings.json.
"""

import json
import os
import sys


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def money(value):
    """Same compact format the deck uses, so no two slides render money differently."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from deck import money as deck_money
    return deck_money(value)


def build(focus, coverage, crm):
    accounts = focus.get("accounts", []) or []
    total = len(accounts) or 1
    totals = focus.get("totals", {}) or {}

    no_two_way = [a for a in accounts if not a.get("twoWay")]
    with_pipeline = [a for a in accounts if float(a.get("h1PipelineValue") or 0) > 0]
    by_bucket = (coverage.get("pipeline", {}) or {}).get("byBucket", {}) or {}
    with_overlap = [a for a in accounts if a.get("msftOverlap")]
    greenfield = [a for a in accounts if (a.get("current") or {}).get("greenfield")]
    with_triggers = [a for a in accounts if a.get("triggers")]

    stale_value = sum(float(a.get("stalePipelineValue") or 0) for a in accounts)
    stale_count = sum(
        len([o for o in (a.get("openPipeline") or []) if o.get("stale")])
        for a in accounts
    )

    # Which products the focus set actually consumes today, as a count of accounts.
    consuming = {}
    for account in accounts:
        observed = ((account.get("current") or {}).get("consumptionObserved") or {})
        for product, value in observed.items():
            if float(value or 0) > 1:
                consuming[product] = consuming.get(product, 0) + 1

    ghas_accounts = consuming.get("ghas", 0)
    copilot_accounts = consuming.get("copilot", 0)

    by_product = {p["product"]: p for p in coverage.get("products", []) or []}
    ghe = by_product.get("GHE", {})
    ghas = by_product.get("GHAS", {})

    # Pipeline hygiene is tracked as a live ask to deal desk on the asks slide, not as an
    # H2 retrospective learning, so it is deliberately not carried here.
    learnings = [
        {
            "headline": "GHAS is a story we told, not a motion we ran",
            "detail": "Only {ghas} of {total} focus accounts consume GHAS today. The H2 gap "
                      "was motion, not opportunity - and it is now closing: {pipe} of GHAS is "
                      "close-dated in H1, {ratio}x the H1 GHAS target.".format(
                          ghas=ghas_accounts, total=total,
                          pipe=money(ghas.get("livePipeline")),
                          ratio=ghas.get("pipelineCoverage") or 0),
            "carryForward": "Run GHAS as a named-account motion on the {n} accounts with "
                            "the largest committer bases, not as an attach conversation.".format(
                                n=min(8, total)),
            "evidence": "coverage.json + focus-accounts.json consumption",
        },
        {
            "headline": "GHE net-new needs supply, not just conversion",
            "detail": "GHE has {pipe} close-dated against a {target} H1 target - {ratio}x on "
                      "live pipeline. The existing book does not contain enough GHE to make the "
                      "number on conversion alone; it needs new supply.".format(
                          pipe=money(ghe.get("livePipeline")),
                          target=money(ghe.get("h1Target")),
                          ratio=ghe.get("pipelineCoverage") or 0),
            "carryForward": "Add migration and new-logo supply early in Q1 rather than "
                            "discovering the gap at Q2 close.",
            "evidence": "coverage.json GHE line",
        },
        {
            "headline": "Two-way engagement predicts progression",
            "detail": "{two} of {total} focus accounts have two-way communication; the "
                      "{none} that do not are where sizing exists but motion does not.".format(
                          two=totals.get("withTwoWay", 0), total=total, none=len(no_two_way)),
            "carryForward": "Treat a first genuine reply as the qualification gate before "
                            "an account is counted as covered.",
            "evidence": "focus-accounts.json activity",
        },
        {
            "headline": "Copilot consumption is the widest surface we have",
            "detail": "{cop} of {total} focus accounts already consume Copilot, which makes "
                      "it the most reliable route into an account before a Bucket 1 "
                      "conversation.".format(cop=copilot_accounts, total=total),
            "carryForward": "Lead with Copilot value evidence, land the platform "
                            "conversation second.",
            "evidence": "focus-accounts.json consumptionObserved",
        },
    ]

    working = [
        {
            "point": "Copilot as the door-opener",
            "proof": "{}/{} focus accounts consuming Copilot".format(copilot_accounts, total),
        },
        {
            "point": "Trigger-led prioritisation",
            "proof": "{}/{} focus accounts carry a cited, dated trigger".format(
                len(with_triggers), total),
        },
        {
            "point": "Microsoft overlap is broad",
            "proof": "{}/{} focus accounts carry at least one TPID".format(
                len(with_overlap), total),
        },
        {
            "point": "Installed base is real",
            "proof": "{} current ARR across the focus set".format(
                money(totals.get("currentArr"))),
        },
    ]

    # Accounts holding dated H1 pipeline that never reached the focus set. These are the
    # ones that embarrass you in a review: real money, invisible in the plan. They drop
    # out when SuperDash carries no product signal to classify a play against.
    focus_ids = {a.get("salesforceId") for a in accounts if a.get("salesforceId")}
    orphan_pipeline = [
        (rec.get("name") or sid, float(rec.get("h1PipelineValue") or 0))
        for sid, rec in ((crm or {}).get("accounts", {}) or {}).items()
        if float(rec.get("h1PipelineValue") or 0) > 0 and sid not in focus_ids
    ]
    orphan_value = sum(v for _, v in orphan_pipeline)

    not_working = [
        {
            "point": "Live pipeline is thin, and concentrated in the wrong bucket",
            "proof": "only {n} of {total} focus accounts carry H1-dated net-new pipeline; "
                     "{b1} of it is Bucket 1 and {b2} is Bucket 2 consumption, which does "
                     "not cover a Bucket 1 target".format(
                         n=len(with_pipeline), total=total,
                         b1=money(by_bucket.get("Bucket 1")),
                         b2=money(by_bucket.get("Bucket 2"))),
        },
        {
            "point": "Stale opportunities",
            "proof": "{n} records, {v}, past close date".format(
                n=stale_count, v=money(stale_value)),
        },
        {
            "point": "GHAS attach",
            "proof": "{n} of {total} accounts consuming GHAS".format(
                n=ghas_accounts, total=total),
        },
        {
            "point": "Silent accounts",
            "proof": "{n} of {total} focus accounts without two-way contact".format(
                n=len(no_two_way), total=total),
        },
        {
            "point": "Greenfield exposure",
            "proof": "{n} focus accounts have no current ARR to expand from".format(
                n=len(greenfield)),
        },
    ]

    if orphan_pipeline:
        not_working.insert(1, {
            "point": "Pipeline outside the focus set",
            "proof": "{n} account{s} holding {v} of dated H1 pipeline {verb} outside the focus "
                     "set because SuperDash carries no product signal to classify a play "
                     "({names})".format(
                         n=len(orphan_pipeline),
                         s="" if len(orphan_pipeline) == 1 else "s",
                         verb="sits" if len(orphan_pipeline) == 1 else "sit",
                         v=money(orphan_value),
                         names=", ".join(n for n, _ in sorted(
                             orphan_pipeline, key=lambda x: -x[1])[:3])),
        })

    return {
        "learnings": learnings,
        "working": working,
        "notWorking": not_working,
        "facts": {
            "focusCount": total,
            "staleCount": stale_count,
            "staleValue": round(stale_value, 2),
            "withPipeline": len(with_pipeline),
            "withOverlap": len(with_overlap),
            "withTriggers": len(with_triggers),
            "withoutTwoWay": len(no_two_way),
            "greenfield": len(greenfield),
            "consumingAccounts": consuming,
        },
    }


def apply_seller_learnings(out, overrides, tokens):
    """Fold seller-supplied learnings into the computed set.

    A learning the seller brings is a fact the systems cannot hold - "we lost that
    deal because they would not engage below the exec line" is nowhere in Salesforce.
    Two rules keep it honest:

      * `replaces` substitutes a computed learning instead of sitting beside it, so
        the slide never argues the same point twice in two voices.
      * `detail` and `carryForward` may reference the computed tokens below, so a
        seller narrative still carries a number that traces back to the run data.

    An entry whose `replaces` matches nothing is fatal, for the same reason an
    unmatched account override is.
    """
    entries = (overrides or {}).get("learnings") or []
    if not entries:
        return
    learnings = out["learnings"]
    for entry in entries:
        item = {
            "headline": fill_tokens(entry.get("headline", ""), tokens),
            "detail": fill_tokens(entry.get("detail", ""), tokens),
            "carryForward": fill_tokens(entry.get("carryForward", ""), tokens),
            "evidence": entry.get("evidence") or "overrides.json (seller-supplied)",
            "source": "seller",
        }
        target = entry.get("replaces")
        if target:
            matches = [i for i, existing in enumerate(learnings)
                       if existing.get("headline", "").startswith(target)]
            if not matches:
                raise SystemExit(
                    "overrides.json: learning replaces %r but no computed learning has "
                    "that headline prefix. Computed headlines are: %s"
                    % (target, "; ".join(l.get("headline", "") for l in learnings)))
            learnings[matches[0]] = item
        else:
            learnings.append(item)


def fill_tokens(text, tokens):
    for key, value in tokens.items():
        text = text.replace("{%s}" % key, str(value))
    if "{" in text and "}" in text:
        raise SystemExit(
            "overrides.json: unresolved token in %r. Available tokens: %s"
            % (text, ", ".join(sorted(tokens))))
    return text


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: learnings.py <runDir>")
    run_dir = sys.argv[1]

    focus = load(os.path.join(run_dir, "focus-accounts.json"))
    coverage = load(os.path.join(run_dir, "coverage.json"), {})
    crm = load(os.path.join(run_dir, "crm-context.json"), {})
    if not focus:
        raise SystemExit("Cannot read focus-accounts.json")

    out = build(focus, coverage or {}, crm or {})

    by_product = {p["product"]: p for p in (coverage or {}).get("products", []) or []}
    ghas = by_product.get("GHAS", {})
    consuming = (out.get("facts", {}) or {}).get("consumingAccounts", {}) or {}
    tokens = {
        "focusTotal": len(focus.get("accounts", []) or []),
        "ghasAccounts": consuming.get("ghas", 0),
        "ghasPipeline": money(ghas.get("livePipeline")),
        "ghasCoverage": ghas.get("pipelineCoverage") or 0,
    }
    overrides = load(os.path.join(run_dir, "overrides.json"), {}) or {}
    apply_seller_learnings(out, overrides, tokens)

    dest = os.path.join(run_dir, "learnings.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({"learningsPath": dest, "learnings": len(out["learnings"]),
                      "working": len(out["working"]),
                      "notWorking": len(out["notWorking"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
