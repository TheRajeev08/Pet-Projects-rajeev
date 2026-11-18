"""Read-only Salesforce recommendation snapshots, separate from Pulse signals."""
import json
import os
from datetime import date

OWNER = "therajeev08"
STATUSES = ("New", "Working", "Saved", "Stale")
LIMIT = 100
SIGNAL_FIELDS = ("company_name", "ml_driven_insights", "recommended_product",
                 "revenue_play", "type", "signal_level", "recommended_outreach_sequence")


def matches(tool, args, result, timestamp):
    return (tool in ("get_account_recommendations", "revenue-mcp-server-get_account_recommendations")
            and args.get("owner") == OWNER and args.get("limit") == LIMIT
            and isinstance(args.get("status"), list)
            and len(args["status"]) == len(STATUSES) and set(args["status"]) == set(STATUSES)
            and isinstance(timestamp, str) and bool(timestamp) and not result.get("error")
            and isinstance(result.get("recommendations"), list)
            and all(isinstance(item, dict) and all(isinstance(item.get(key), str) and item[key]
                    for key in ("id", "account_id", "status"))
                    and item["status"] in STATUSES
                    and item.get("owner_github_username", OWNER) == OWNER
                    and isinstance(item.get("signals"), list)
                    and all(isinstance(signal, dict) and all(
                        signal.get(key) is None or isinstance(signal[key], str)
                        for key in SIGNAL_FIELDS) for signal in item["signals"])
                    for item in result["recommendations"]))


def load(data, days, accounts):
    for day in days:
        path = os.path.join(data, "raw", day, "recommendations.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            snapshot = json.load(f)
        if not matches(snapshot.get("tool"), snapshot.get("arguments", {}),
                       snapshot.get("result", {}), snapshot.get("collected_from")):
            raise ValueError("Invalid Salesforce recommendations snapshot")
        owned = {account["id"]: account.get("name") for account in accounts}
        items = []
        for item in snapshot["result"]["recommendations"]:
            account_id = item.get("account_id")
            items.append({
                "id": item.get("id"), "status": item.get("status"),
                "created_date": item.get("created_date"), "action_by": item.get("action_by"),
                "account_id": account_id, "account_name": owned.get(account_id),
                "in_book": account_id in owned, "url": item.get("url"),
                "signals": [{key: signal.get(key) for key in SIGNAL_FIELDS}
                    for signal in (item.get("signals") or []) if isinstance(signal, dict)],
            })
        return {"status": "available", "collected_from": snapshot["collected_from"],
                "source_date": day, "carried": day != days[0],
                "age_days": (date.fromisoformat(days[0]) - date.fromisoformat(day)).days,
                "limit": LIMIT, "possibly_truncated": len(items) >= LIMIT,
                "items": items}
    return {"status": "unknown", "collected_from": None, "items": None}
