"""Turn raw Salesforce/Gong query results into the governed FY27 enrichment contracts.

The agent runs the pinned SOQL from the fy27-crm-enrichment skill and writes the raw
rows to one JSON file. This script does every piece of arithmetic, so two runs over
the same CRM state always produce the same numbers. Nothing here calls out to a
network: it is a pure transform, which is what makes a run reproducible and auditable.

Input (single JSON file):

    {
      "windowDays": 90,
      "asOf": "2026-08-07",                  # optional, defaults to today
      "accountIds": ["001..."],              # REQUIRED scope: the uploaded book
      "sources": {"salesforce": {"status": "ok"},
                  "gong": {"status": "unavailable", "error": "..."}},
      "taskGroups":  [{"AccountId","TaskSubtype","CallType","Type","count","lastActivity"}],
      "eventGroups": [{"AccountId","count","lastActivity"}],
      "contacts":    [{"AccountId","Name","Title","Email"}],
      "opportunities":[{"AccountId","Name","StageName","Amount","CloseDate","NextStep"}],
      "gongCalls":   {"001...": [{"title","date","url"}]}
    }

Aggregate SOQL returns unaliased columns as expr0/expr1; those are accepted as
synonyms for count/lastActivity so the agent can paste tool output verbatim.

Outputs, written into the run directory:
    salesforce-activity.json         engagement scores/tiers consumed by workbook.py
    salesforce-contacts.json         named personas per account
    salesforce-sprint-signals.json   open pipeline + Gong, consumed by sprint_score.py

Directionality is taken from real Salesforce picklists rather than inferred:
  inbound  = CallType 'Inbound', or Type beginning 'Connected'/'Answered'
             (both are recorded evidence that the customer responded)
  outbound = every other logged, non-internal seller touch
  meetings = Events, plus Tasks whose Type is one of the 'Connected - Meeting *' values
  twoWay   = inbound > 0
CallType 'Internal' is excluded entirely: internal chatter is not engagement.

Score (fixed absolute scale so uploads stay comparable, capped at 100):

    score = min(100, 6 * total**0.75 * (1+meetings)**0.9 * (2.2 if twoWay else 1)
                     * 0.5 ** (days_since_last_activity / 180))

Tier is evidence-first, not score-first, because a score alone cannot distinguish
"customer talks to us" from "we send a lot of email":

    Priority  two-way AND at least one meeting
    High      two-way OR at least one meeting
    Medium    neither, but score >= 15
    Low       neither, and score < 15
    Unranked  no matched activity (Unknown, NOT cold)
"""
import datetime
import json
import os
import re
import sys

HALF_LIFE_DAYS = 180.0
MEDIUM_SCORE_THRESHOLD = 15.0
MEETING_TYPES = {
    "Connected - Meeting Set",
    "Connected - Meeting Confirmed",
    "Connected - Meeting Rescheduled",
}
RESPONSE_PREFIXES = ("Connected", "Answered")
# Titles that map to each play's economic buyer, used to rank contacts.
PLAY_TITLE_FIT = {
    "Innovate": ("developer experience", "platform", "engineering", "innovation", "ai", "cto"),
    "Trust": ("security", "appsec", "compliance", "risk", "ciso", "governance"),
    "Scale": ("devops", "platform", "infrastructure", "sre", "operations", "cloud"),
}


def clean(value):
    return str(value if value is not None else "").strip()


def as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def looks_like_date(value):
    return bool(DATE_RE.match(clean(value)))


def row_count(row):
    for key in ("count", "expr0", "cnt", "total"):
        if key in row and not looks_like_date(row[key]):
            return as_int(row[key])
    return 0


def row_last(row):
    for key in ("lastActivity", "expr1", "last", "maxDate"):
        if row.get(key):
            return clean(row[key])[:10]
    # ActivityDate is not aggregatable, so recency is fetched in its own
    # MAX(CreatedDate) query where the date lands in expr0.
    if looks_like_date(row.get("expr0")):
        return clean(row["expr0"])[:10]
    return ""


def parse_date(value):
    try:
        return datetime.date.fromisoformat(clean(value)[:10])
    except ValueError:
        return None


def days_since(last, as_of):
    date = parse_date(last)
    if not date:
        return 3650
    return max((as_of - date).days, 0)


def score_for(total, meetings, two_way, age_days):
    if total <= 0:
        return 0.0
    raw = 6.0 * (total ** 0.75) * ((1 + meetings) ** 0.9)
    raw *= 2.2 if two_way else 1.0
    raw *= 0.5 ** (age_days / HALF_LIFE_DAYS)
    return round(min(100.0, raw), 2)


def tier_for(score, meetings, two_way):
    if two_way and meetings > 0:
        return "Priority"
    if two_way or meetings > 0:
        return "High"
    return "Medium" if score >= MEDIUM_SCORE_THRESHOLD else "Low"


def build_activity(payload, scope, as_of, window_days):
    buckets = {}

    def bucket(account_id):
        return buckets.setdefault(
            account_id,
            {"total": 0, "inbound": 0, "outbound": 0, "meetings": 0, "lastActivity": ""},
        )

    for row in payload.get("taskGroups") or []:
        account_id = clean(row.get("AccountId"))
        if account_id not in scope:
            continue
        call_type = clean(row.get("CallType"))
        if call_type == "Internal":
            continue
        count = row_count(row)
        entry = bucket(account_id)
        last = row_last(row)
        if last > entry["lastActivity"]:
            entry["lastActivity"] = last
        if count <= 0:
            continue
        task_type = clean(row.get("Type"))
        entry["total"] += count
        responded = call_type == "Inbound" or task_type.startswith(RESPONSE_PREFIXES)
        entry["inbound" if responded else "outbound"] += count
        if task_type in MEETING_TYPES:
            entry["meetings"] += count

    for row in payload.get("eventGroups") or []:
        account_id = clean(row.get("AccountId"))
        if account_id not in scope:
            continue
        count = row_count(row)
        if count <= 0:
            continue
        entry = bucket(account_id)
        entry["total"] += count
        entry["meetings"] += count
        # A held meeting is bilateral by definition.
        entry["inbound"] += count
        last = row_last(row)
        if last > entry["lastActivity"]:
            entry["lastActivity"] = last

    accounts = {}
    for account_id, entry in buckets.items():
        if entry["total"] <= 0:
            # a recency-only row is not evidence of engagement
            continue
        two_way = entry["inbound"] > 0
        age = days_since(entry["lastActivity"], as_of)
        score = score_for(entry["total"], entry["meetings"], two_way, age)
        accounts[account_id] = {
            "status": "enriched",
            "total": entry["total"],
            "inbound": entry["inbound"],
            "outbound": entry["outbound"],
            "meetings": entry["meetings"],
            "lastActivity": entry["lastActivity"],
            "twoWay": two_way,
            "score": score,
            "tier": tier_for(score, entry["meetings"], two_way),
            "reason": (
                f'{entry["total"]} activities in {window_days}d; '
                f'{entry["inbound"]} responded / {entry["outbound"]} outbound; '
                f'{entry["meetings"]} meetings; '
                f'{"verified two-way communication" if two_way else "no verified customer response"}; '
                f'last activity {entry["lastActivity"] or "unknown"}.'
            ),
        }
    return accounts


def build_contacts(payload, scope):
    def fit_for(title):
        lowered = title.lower()
        for play, needles in PLAY_TITLE_FIT.items():
            if any(needle in lowered for needle in needles):
                return play
        return ""

    accounts = {}
    for row in payload.get("contacts") or []:
        account_id = clean(row.get("AccountId"))
        title = clean(row.get("Title"))
        if account_id not in scope or not title:
            continue
        accounts.setdefault(account_id, []).append(
            {
                "name": clean(row.get("Name")),
                "title": title,
                "email": clean(row.get("Email")),
                "fit": fit_for(title),
            }
        )
    # Personas with a play fit first, then alphabetically, capped so the workbook stays readable.
    for account_id, people in accounts.items():
        people.sort(key=lambda c: (c["fit"] == "", c["name"]))
        accounts[account_id] = people[:5]
    return accounts


def build_sprint_signals(payload, scope):
    accounts = {}
    for row in payload.get("opportunities") or []:
        account_id = clean(row.get("AccountId"))
        if account_id not in scope:
            continue
        entry = accounts.setdefault(account_id, {}).setdefault(
            "openOpportunities",
            {"count": 0, "maxAmount": 0, "earliestCloseDate": "", "stages": [], "mostRecentActivity": ""},
        )
        entry["count"] += 1
        entry["maxAmount"] = max(entry["maxAmount"], as_int(row.get("Amount")))
        close = clean(row.get("CloseDate"))[:10]
        if close and (not entry["earliestCloseDate"] or close < entry["earliestCloseDate"]):
            entry["earliestCloseDate"] = close
        stage = clean(row.get("StageName"))
        if stage and stage not in entry["stages"]:
            entry["stages"].append(stage)
        next_step = clean(row.get("NextStep"))
        if next_step and not entry["mostRecentActivity"]:
            entry["mostRecentActivity"] = next_step

    for account_id, calls in (payload.get("gongCalls") or {}).items():
        account_id = clean(account_id)
        if account_id not in scope or not calls:
            continue
        normalized = [
            {
                "title": clean(call.get("title")),
                "date": clean(call.get("date"))[:10],
                "url": clean(call.get("url")),
            }
            for call in calls
        ]
        normalized.sort(key=lambda c: c["date"], reverse=True)
        accounts.setdefault(account_id, {})["gong"] = {
            "count": len(normalized),
            "lastCallDate": normalized[0]["date"],
            "calls": normalized[:5],
        }
    return accounts


def source_status(payload):
    declared = payload.get("sources") or {}
    statuses = {}
    for name in ("salesforce", "gong"):
        entry = declared.get(name) or {}
        statuses[name] = {
            "status": clean(entry.get("status")) or "not requested",
            "error": clean(entry.get("error")),
        }
    return statuses


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: enrich_activity.py <raw-crm.json> <run-dir>")
    raw_path, run_dir = sys.argv[1], sys.argv[2]
    with open(raw_path, encoding="utf-8") as handle:
        payload = json.load(handle)

    scope = {clean(a) for a in payload.get("accountIds") or [] if clean(a)}
    if not scope:
        raise SystemExit("accountIds is required: enrichment is scoped to the uploaded book only.")

    as_of = parse_date(payload.get("asOf")) or datetime.date.today()
    window_days = as_int(payload.get("windowDays")) or 90
    sources = source_status(payload)

    activity_accounts = build_activity(payload, scope, as_of, window_days)
    contacts = build_contacts(payload, scope)
    sprint_signals = build_sprint_signals(payload, scope)

    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    os.makedirs(run_dir, exist_ok=True)

    activity = {
        "metadata": {
            "windowDays": window_days,
            "asOf": as_of.isoformat(),
            "generatedAt": generated_at,
            "accountCount": len(scope),
            "matchedCount": len(activity_accounts),
            "sources": sources,
            "source": f"Salesforce Task and Event (AccountId join, {window_days}-day window)",
            "unmatchedMeaning": "Unknown, not cold: no matched activity in the declared window.",
        },
        "accounts": activity_accounts,
    }
    outputs = {
        "salesforce-activity.json": activity,
        "salesforce-contacts.json": {
            "generatedAt": generated_at,
            "accountsQueried": len(scope),
            "accountsWithContacts": len(contacts),
            "sources": sources,
            "accounts": contacts,
        },
        "salesforce-sprint-signals.json": {
            "generatedAt": generated_at,
            "accountsQueried": len(scope),
            "sources": sources,
            "accounts": sprint_signals,
        },
    }
    for name, body in outputs.items():
        with open(os.path.join(run_dir, name), "w", encoding="utf-8") as handle:
            json.dump(body, handle)

    print(
        json.dumps(
            {
                "runDir": run_dir,
                "accountsInScope": len(scope),
                "activityMatched": len(activity_accounts),
                "coveragePct": round(len(activity_accounts) / len(scope) * 100, 1),
                "twoWay": sum(1 for a in activity_accounts.values() if a["twoWay"]),
                "contactsMatched": len(contacts),
                "sprintSignals": len(sprint_signals),
                "sources": sources,
            }
        )
    )


if __name__ == "__main__":
    main()
