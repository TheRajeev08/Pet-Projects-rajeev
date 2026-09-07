#!/usr/bin/env python3
"""Merge one gathered batch into the run-local licensing cache.

Widening licensing to the whole book means the gather happens in batches, and a batch
that overwrote `raw.json` would throw away every earlier batch - turning a resumable
pass back into an all-or-nothing one. This script only ever adds.

It also classifies the misses, because "we did not get a reading" covers three quite
different situations and they lead to different actions:

| status | what it means | what the seller should do |
|---|---|---|
| `ok` | a licensing summary came back | nothing |
| `no-salesforce-match` | the row never resolved to an account | fix the account name in SuperDash |
| `no-github-account` | resolved, but the record lists no GitHub tenant | get the tenant linked in Salesforce |
| `error` | tenant known, lookup failed | retry; it is transient far more often than not |

Collapsing those into one bucket would tell a seller their book is uncovered when what
is actually wrong is three unlinked Salesforce records.

The cache stays in the run directory. It carries customer entitlement data, so it is
under the same never-leaves rule as `overrides.json` and `conversations.json` - it must
never be written to a shared path where one teammate's book could surface in another's.

Reads:  <batch.json>                 {"accounts": {"<sfid>": {...}}}
Writes: <runDir>/licensing/raw.json  (merged, never replaced)
"""

import argparse
import datetime
import json
import os
import sys

VALID_STATUS = ("ok", "no-salesforce-match", "no-github-account", "error")


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def classify(entry):
    """Give every entry an explicit status, inferring only where one is absent.

    Inference is deliberately narrow: it fills a gap, it never overrules a status the
    gatherer set. The gatherer knows things this script cannot see - that a lookup
    threw, for instance, rather than simply returning nothing.
    """
    status = entry.get("status")
    if status in VALID_STATUS:
        return status
    if entry.get("error"):
        return "error"
    if entry.get("summaries"):
        return "ok"
    if not entry.get("githubAccounts"):
        return "no-github-account"
    return "error"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("batch", help="JSON file holding this batch's gathered accounts")
    parser.add_argument("--source", default="revenue-mcp/get_licensing_summary")
    args = parser.parse_args()

    batch = load(args.batch)
    if not batch:
        raise SystemExit("cannot read batch: %s" % args.batch)

    incoming = batch.get("accounts") if isinstance(batch, dict) else None
    if incoming is None:
        raise SystemExit("batch has no 'accounts' object: %s" % args.batch)

    cache_dir = os.path.join(args.run_dir, "licensing")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "raw.json")

    raw = load(cache_path) or {}
    accounts = raw.get("accounts") or {}

    added = replaced = 0
    for sid, entry in incoming.items():
        entry = dict(entry)
        entry["status"] = classify(entry)
        existing = accounts.get(sid)
        # A real reading always beats a recorded miss, so a retry that finally succeeds
        # is kept. The reverse is not true: a fresh miss must not erase a good reading
        # just because one batch happened to fail.
        if existing and existing.get("status") == "ok" and entry["status"] != "ok":
            continue
        if existing:
            replaced += 1
        else:
            added += 1
        accounts[sid] = entry

    raw["accounts"] = accounts
    raw["source"] = args.source
    raw["gatheredAt"] = datetime.datetime.now().strftime("%Y-%m-%d")

    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2, ensure_ascii=False)

    counts = {}
    for entry in accounts.values():
        key = entry.get("status") or "unknown"
        counts[key] = counts.get(key, 0) + 1

    print("merged %d new, %d updated -> %d cached accounts" % (added, replaced, len(accounts)))
    for status in sorted(counts):
        print("  %-22s %d" % (status, counts[status]))
    print("wrote %s" % cache_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
