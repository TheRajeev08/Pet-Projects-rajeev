"""Per-feed freshness: ok, stale, suspect (breaker held recent days), failed (not collected this run) or missing."""
import datetime as dt

from .sources import trailing_unhealthy

# (feed, query id, label, healthy-day key, expected lag in days)
FEEDS = (
    ("book", "p01", "Salesforce book", None, 0),
    ("contracts", "p02", "Contracts", "contracted", 1),
    ("metered", "p03", "GHE metered users", "metered", 2),
    ("volume", "p03", "GHE billable users", "volume", 31),
    ("copilot", "p04", "Copilot health", "health", 3),
    ("copilot_seats", "p04", "Copilot seats", "seats", 45),
    ("ghas", "p05", "GHAS committers", "ghas", 2),
    ("attributes", "p06", "Account attributes", None, 3),
)
RANK = {"ok": 0, "stale": 1, "suspect": 2, "failed": 3, "missing": 4}


def feed_status(src, today, th):
    """Return a list of feed dicts with state, as_of and a short message."""
    out = []
    for feed, qid, label, key, lag in FEEDS:
        f = src["files"].get(qid)
        st = {"feed": feed, "qid": qid, "label": label, "state": "ok", "as_of": None, "messages": [],
              "raw_date": f and f["raw_date"], "via": f and f["via"], "rows": f and f["rows"], "held": []}
        if not f:
            st["state"] = "missing"
            st["messages"].append("never collected")
            out.append(st)
            continue
        if f["raw_date"] < today:
            st["state"] = "failed"
            st["messages"].append(f"not collected today; using data collected {f['raw_date']}")
        if key:
            healthy = src["healthy"].get(key) or set()
            meta = src["meta"].get(key) or []
            st["as_of"] = max(healthy) if healthy else None
            n = trailing_unhealthy(meta, healthy)
            if n:
                held = [d for d, _, _ in meta[-n:]]
                st["held"] = held
                why = src["unhealthy_why"][key].get(held[-1], "incomplete")
                days = "latest day" if n == 1 else f"latest {n} days"
                st["messages"].append(f"{days} held back by the circuit breaker ({why})")
                if n >= th["suspect_alert_days"] and st["state"] == "ok":
                    st["state"] = "suspect"
            if not healthy:
                st["messages"].append("no complete days in the window")
                if st["state"] == "ok":
                    st["state"] = "suspect"
        elif feed == "book":
            st["as_of"] = f["raw_date"]
        elif feed == "attributes":
            dates = [a.get("measurement_date") for a in src["attrs"].values() if a.get("measurement_date")]
            st["as_of"] = max(dates) if dates else None
        if st["as_of"] and st["state"] == "ok":
            age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(st["as_of"])).days
            if age > lag + 2:
                st["state"] = "stale"
                st["messages"].append(f"data is {age} days old (expected about {lag})")
        out.append(st)
    return out


def overall(feeds, build_error=None):
    if build_error:
        return "failed"
    worst = max((RANK[f["state"]] for f in feeds), default=0)
    return {v: k for k, v in RANK.items()}[worst]
