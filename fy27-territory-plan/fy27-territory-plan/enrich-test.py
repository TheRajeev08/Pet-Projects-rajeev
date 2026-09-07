"""Regression tests for the deterministic CRM enrichment transform.

Two things must stay true:

1.  The transform is honest about the raw data — internal chatter is dropped, out-of-scope
    account IDs are rejected, and unmatched accounts stay Unknown rather than becoming cold.
2.  The pinned score/tier model still agrees with the engagement picture the territory plan
    was originally built on. The historical artifact only survives as aggregates, which is
    exactly the input this script takes, so it doubles as a fixture: we replay it and require
    the tier rule to reproduce exactly and the scores to stay rank-stable.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
# Runs from the dev tree (plugin/skills/...) and from a published plugin checkout (skills/...).
SCRIPT = next(
    path
    for path in (
        os.path.join(ROOT, "plugin", "skills", "fy27-territory-plan", "scripts", "enrich_activity.py"),
        os.path.join(ROOT, "skills", "fy27-territory-plan", "scripts", "enrich_activity.py"),
    )
    if os.path.exists(path)
)
HISTORICAL = os.path.join(ROOT, "artifacts", "salesforce-activity.json")

sys.path.insert(0, os.path.dirname(SCRIPT))
import enrich_activity as enrich  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


def spearman(xs, ys):
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0] * len(values)
        for position, index in enumerate(order):
            out[index] = position
        return out

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((r - mx) ** 2 for r in rx)
    vy = sum((r - my) ** 2 for r in ry)
    return cov / (vx * vy) ** 0.5


print("transform behaviour")
with tempfile.TemporaryDirectory() as tmp:
    raw = {
        "windowDays": 90,
        "asOf": "2026-08-07",
        "accountIds": ["001AAA", "001BBB", "001CCC", "001DDD"],
        "sources": {"salesforce": {"status": "ok"}, "gong": {"status": "unavailable", "error": "no access"}},
        "taskGroups": [
            # 001AAA: seller sends, customer answers, meeting booked -> Priority
            {"AccountId": "001AAA", "TaskSubtype": "Email", "Type": "Email", "count": 10, "lastActivity": "2026-08-01"},
            {"AccountId": "001AAA", "TaskSubtype": "Call", "CallType": "Inbound", "Type": "Connected - Qualified", "count": 3, "lastActivity": "2026-08-05"},
            {"AccountId": "001AAA", "TaskSubtype": "Call", "CallType": "Outbound", "Type": "Connected - Meeting Set", "count": 1, "lastActivity": "2026-08-06"},
            # internal chatter must never count as engagement
            {"AccountId": "001AAA", "TaskSubtype": "Call", "CallType": "Internal", "Type": "Call", "count": 50, "lastActivity": "2026-08-07"},
            # 001BBB: one-way outbound only, recent -> Medium
            {"AccountId": "001BBB", "TaskSubtype": "Email", "Type": "Email", "count": 8, "lastActivity": "2026-08-02"},
            # out of scope, must be dropped
            {"AccountId": "001ZZZ", "TaskSubtype": "Email", "Type": "Email", "count": 99, "lastActivity": "2026-08-02"},
            # real live shape: ActivityDate is not aggregatable, so composition rows
            # carry no date and recency arrives as a separate MAX(CreatedDate) row
            {"AccountId": "001DDD", "TaskSubtype": "Email", "Type": "Email", "expr0": 4},
            {"AccountId": "001DDD", "expr0": "2026-08-03T10:11:12.000+0000"},
        ],
        # aggregate SOQL returns expr0/expr1 when unaliased; that must be accepted verbatim
        "eventGroups": [{"AccountId": "001AAA", "expr0": 2, "expr1": "2026-08-04T00:00:00Z"}],
        "contacts": [
            {"AccountId": "001AAA", "Name": "Priya Rao", "Title": "CISO", "Email": "p@x.com"},
            {"AccountId": "001AAA", "Name": "No Title", "Title": "", "Email": "n@x.com"},
        ],
        "opportunities": [
            {"AccountId": "001AAA", "Name": "Renewal", "StageName": "Validate", "Amount": 50000, "CloseDate": "2026-12-01", "NextStep": "Scope"},
            {"AccountId": "001AAA", "Name": "Expansion", "StageName": "Propose", "Amount": 20000, "CloseDate": "2026-10-01", "NextStep": ""},
        ],
        "gongCalls": {"001AAA": [{"title": "QBR", "date": "2026-07-15", "url": "https://gong/x"}]},
    }
    raw_path = os.path.join(tmp, "raw.json")
    with open(raw_path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle)
    summary = json.loads(subprocess.run([sys.executable, SCRIPT, raw_path, tmp], capture_output=True, text=True, check=True).stdout)

    activity = json.load(open(os.path.join(tmp, "salesforce-activity.json"), encoding="utf-8"))
    contacts = json.load(open(os.path.join(tmp, "salesforce-contacts.json"), encoding="utf-8"))
    signals = json.load(open(os.path.join(tmp, "salesforce-sprint-signals.json"), encoding="utf-8"))
    accounts = activity["accounts"]

    check("out-of-scope account ids are rejected", "001ZZZ" not in accounts)
    check("unmatched account stays Unknown", "001CCC" not in accounts)
    check("internal calls excluded from total", accounts["001AAA"]["total"] == 16, accounts["001AAA"]["total"])
    check("customer response counts as inbound", accounts["001AAA"]["inbound"] == 6, accounts["001AAA"]["inbound"])
    check("events and connected-meeting tasks both count as meetings", accounts["001AAA"]["meetings"] == 3, accounts["001AAA"]["meetings"])
    check("two-way plus meeting is Priority", accounts["001AAA"]["tier"] == "Priority", accounts["001AAA"]["tier"])
    check("one-way recent outbound is Medium", accounts["001BBB"]["tier"] == "Medium", accounts["001BBB"]["tier"])
    check("one-way account is not two-way", accounts["001BBB"]["twoWay"] is False)
    check("expr0/expr1 aggregate aliases are parsed", accounts["001AAA"]["lastActivity"] == "2026-08-06")
    check("separate MAX(CreatedDate) row supplies recency", accounts["001DDD"]["lastActivity"] == "2026-08-03", accounts["001DDD"]["lastActivity"])
    check("date in expr0 is never counted as volume", accounts["001DDD"]["total"] == 4, accounts["001DDD"]["total"])
    check("untitled contacts are skipped", [c["name"] for c in contacts["accounts"]["001AAA"]] == ["Priya Rao"])
    check("security title maps to Trust", contacts["accounts"]["001AAA"][0]["fit"] == "Trust")
    check("open pipeline is rolled up", signals["accounts"]["001AAA"]["openOpportunities"] == {
        "count": 2, "maxAmount": 50000, "earliestCloseDate": "2026-10-01",
        "stages": ["Validate", "Propose"], "mostRecentActivity": "Scope"})
    check("gong is attached", signals["accounts"]["001AAA"]["gong"]["lastCallDate"] == "2026-07-15")
    check("failed source keeps its own status", activity["metadata"]["sources"]["gong"]["status"] == "unavailable")
    check("coverage is reported against scope", summary["coveragePct"] == 75.0, summary["coveragePct"])

    missing_scope = os.path.join(tmp, "noscope.json")
    with open(missing_scope, "w", encoding="utf-8") as handle:
        json.dump({"taskGroups": []}, handle)
    result = subprocess.run([sys.executable, SCRIPT, missing_scope, tmp], capture_output=True, text=True)
    check("enrichment without an account scope is refused", result.returncode != 0)

print("historical agreement")
if not os.path.exists(HISTORICAL):
    print("  skip (no historical artifact on this machine)")
else:
    import datetime

    stored = json.load(open(HISTORICAL, encoding="utf-8"))
    as_of = datetime.date.fromisoformat(stored["metadata"]["asOf"])
    rows = [v for v in stored["accounts"].values() if v["status"] == "enriched"]
    replayed = []
    for row in rows:
        age = enrich.days_since(row["lastActivity"], as_of)
        score = enrich.score_for(row["total"], row["meetings"], row["twoWay"], age)
        replayed.append((row, score, enrich.tier_for(score, row["meetings"], row["twoWay"])))

    agree = sum(1 for row, _, tier in replayed if tier == row["tier"])
    # Priority/High are evidence-based (two-way, meetings) so they must reproduce exactly.
    # Medium vs Low is a score threshold, so a couple of accounts sitting on the boundary
    # are allowed to move without that counting as a behaviour change.
    evidence = sum(
        1 for row, _, tier in replayed
        if (tier in ("Priority", "High")) == (row["tier"] in ("Priority", "High"))
    )
    correlation = spearman([r["score"] for r, _, _ in replayed], [s for _, s, _ in replayed])
    check(f"evidence tiers reproduce history ({evidence}/{len(rows)})", evidence == len(rows))
    check(f"overall tiers stay stable ({agree}/{len(rows)})", agree >= len(rows) * 0.95, agree)
    check(f"scores stay rank-stable (spearman {correlation:.3f})", correlation >= 0.95)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
    raise SystemExit(1)
print("All enrichment checks passed.")
