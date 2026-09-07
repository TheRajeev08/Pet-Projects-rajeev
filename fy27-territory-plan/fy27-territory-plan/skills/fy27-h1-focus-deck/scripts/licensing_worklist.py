#!/usr/bin/env python3
"""Work out which accounts still need a live licensing reading, and in what order.

Licensing used to be gathered for the ranked focus set only, which left the play
sheets - the ones carrying every account - sized on the SuperDash upload alone. That
is exactly the org-wide signal live licensing exists to correct, so the widest sheets
were resting on the least trustworthy numbers.

Widening the gather turns ~40 lookups into several hundred, which is only tractable if
the pass is resumable. This script is the resumable half: it reads what has already
been gathered into the run-local cache and emits only what is still outstanding. Run
it, gather a batch, merge it, run it again. It shrinks until it is empty.

The cache lives in `<runDir>/licensing/raw.json` and never leaves the run directory -
it names customers and carries their entitlement data, so it is under the same rule as
`overrides.json` and `conversations.json`.

Reads:  <runDir>/crm-context.json      (the enrichment pass's resolved book)
        <runDir>/licensing/raw.json    (whatever has been gathered so far)
        <runDir>/focus-accounts.json   (optional - only to order the queue)
Writes: nothing. Prints the outstanding worklist as JSON on stdout.
"""

import argparse
import json
import os
import sys


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def resolved_book(run_dir):
    """Every account the enrichment pass resolved to a Salesforce ID.

    A licensing lookup is keyed by GitHub slug, and slugs are read off the Salesforce
    account record. SuperDash rows arrive keyed by name, so the Salesforce resolution
    the enrichment pass performs is the only thing that makes a row licensable at all.
    No enrichment means no worklist - not a short one.
    """
    context = load(os.path.join(run_dir, "crm-context.json")) or {}
    accounts = context.get("accounts") or {}
    if accounts:
        return {sid: (entry.get("name") or "") for sid, entry in accounts.items()}

    # Fall back to the activity pass, which is keyed the same way but carries no name.
    activity = load(os.path.join(run_dir, "salesforce-activity.json")) or {}
    return {sid: "" for sid in (activity.get("accounts") or {})}


def already_gathered(run_dir):
    """Salesforce IDs already in the run-local cache, gathered or explicitly missed.

    A recorded miss counts as done. Re-asking for an account we already know has no
    GitHub tenant burns an API call every single run and never returns anything new.
    """
    raw = load(os.path.join(run_dir, "licensing", "raw.json")) or {}
    return set(raw.get("accounts") or {})


def focus_order(run_dir):
    """Rank order, used only to decide what to gather first."""
    focus = load(os.path.join(run_dir, "focus-accounts.json")) or {}
    rows = focus.get("accounts") if isinstance(focus, dict) else focus
    order = {}
    for index, row in enumerate(rows or []):
        sid = row.get("salesforceId") or row.get("accountId") or row.get("id")
        if sid and sid not in order:
            order[sid] = index
    return order


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--batch", type=int, default=25,
                        help="how many accounts to return this time (0 = all)")
    args = parser.parse_args()

    book = resolved_book(args.run_dir)
    if not book:
        print(json.dumps({
            "outstanding": [],
            "bookSize": 0,
            "gathered": 0,
            "remaining": 0,
            "note": "no enrichment output in this run - licensing cannot be widened, "
                    "and every licence field will stay blank rather than zero",
        }, indent=2))
        return 0

    done = already_gathered(args.run_dir)
    order = focus_order(args.run_dir)

    outstanding = [{"salesforceId": sid, "name": name}
                   for sid, name in book.items() if sid not in done]
    # Ranked accounts first, so a gather that is interrupted half way through still
    # produced readings for the accounts the deck leans on hardest.
    outstanding.sort(key=lambda row: (order.get(row["salesforceId"], 10 ** 6),
                                      row["name"].lower()))

    batch = outstanding if args.batch <= 0 else outstanding[:args.batch]

    print(json.dumps({
        "outstanding": batch,
        "bookSize": len(book),
        "gathered": len(done & set(book)),
        "remaining": len(outstanding),
        "cache": os.path.join(args.run_dir, "licensing", "raw.json"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
