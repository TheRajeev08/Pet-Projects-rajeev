"""Seat change events, recomputed from source history on every run.

Two kinds of measure:

* contracted (seats on contract): a missing row on a healthy day means 0, changes are diffed at
  family grain (GHE, GHAS, GHCP) and product-type shifts that net to ~0 are a migration.
* consumed (metered users, billable users, committers, assigned seats): a missing value means
  "not reported", never 0.

Only healthy source days (see sources.healthy_days) are compared, so partial loads cannot invent
churn. Events carry `since` (the previous healthy day) so gaps read as "since <date>".
"""
import datetime as dt
import hashlib

from .sources import account_days, family_of

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Notify": 2, "Info": 3}


def event_id(*parts):
    return "e_" + hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:12]


def _days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def _event(acct, fam, measure, kind, priority, day, since, a, b, **extra):
    delta = None if a is None or b is None else b - a
    ev = {
        "id": event_id(acct, fam, measure, extra.get("product_type"), kind, day),
        "account_id": acct, "family": fam, "measure": measure, "kind": kind, "priority": priority,
        "date": day, "since": since, "gap_days": _days_between(since, day) if since else None,
        "from": a, "to": b, "delta": delta,
        "pct": (delta / a) if delta is not None and a else None,
        "reverted": False,
    }
    ev.update({k: v for k, v in extra.items() if v is not None})
    return ev


def family_total(fam, by_type):
    if fam == "ghas":
        return by_type.get("Advanced Security", 0.0) + max(by_type.get("GHSP", 0.0), by_type.get("GHCS", 0.0))
    return sum(by_type.values())


def contracted_daily(runs_by_type, days, fam):
    """Expand run-length rows to {day: {product_type: seats}} for the healthy days of one family."""
    want = set(days)
    per_day = {d: {} for d in days}
    for pt, runs in runs_by_type.items():
        if family_of(pt) != fam:
            continue
        for s, e, seats, _arr, _skus in runs:
            cur = dt.date.fromisoformat(s)
            end = dt.date.fromisoformat(e)
            while cur <= end:
                d = cur.isoformat()
                if d in want:
                    per_day[d][pt] = per_day[d].get(pt, 0.0) + (seats or 0.0)
                cur += dt.timedelta(days=1)
    return per_day


def contracted_events(acct, fam, days, by_type_per_day, th, measure="contracted"):
    """Diff consecutive healthy days of a contracted family. `by_type_per_day[d]` maps product type -> seats."""
    events = []
    for i in range(1, len(days)):
        p, d = days[i - 1], days[i]
        before, after = by_type_per_day.get(p) or {}, by_type_per_day.get(d) or {}
        a, b = family_total(fam, before), family_total(fam, after)
        shifts = [{"type": t, "from": before.get(t, 0.0), "to": after.get(t, 0.0)}
                  for t in sorted(set(before) | set(after)) if before.get(t, 0.0) != after.get(t, 0.0)]
        if not shifts:
            continue
        tol = max(th["migration_abs"], th["migration_pct"] * a)
        ups = [s for s in shifts if s["to"] > s["from"]]
        downs = [s for s in shifts if s["to"] < s["from"]]
        if abs(b - a) <= tol and a > 0 and b > 0 and ups and downs:
            events.append(_event(acct, fam, measure, "migration", "Info", d, p, a, b, detail=shifts))
        elif abs(b - a) <= tol and a > 0 and b > 0:
            # Same-direction product-type change that does not move the family total (GHAS counts
            # max(Secret Protection, Code Security)): report it per product type.
            for s in shifts:
                kind, pri = _contracted_kind(s["from"], s["to"], th)
                events.append(_event(acct, fam, measure, kind, pri, d, p, s["from"], s["to"],
                                     product_type=s["type"], detail=[s]))
        else:
            kind, pri = _contracted_kind(a, b, th)
            events.append(_event(acct, fam, measure, kind, pri, d, p, a, b, detail=shifts))
    _mark_reverted(events, th, exact=True)
    return events


def _contracted_kind(a, b, th):
    if a == 0:
        return "new_product", "Notify"
    if b == 0:
        return "product_to_zero", "High" if a >= th["contracted_high_min_abs"] else "Medium"
    if b > a:
        return "added", "Notify"
    drop = a - b
    high = drop >= th["contracted_high_abs"] or (drop >= th["contracted_high_pct"] * a
                                                 and drop >= th["contracted_high_min_abs"])
    return "dropped", "High" if high else "Medium"


def _flag(a, delta, th):
    return abs(delta) >= max(th["consumed_abs"], th["consumed_pct"] * (a or 0))


def _consumed_kind(a, b, th):
    delta = b - a
    if delta > 0:
        return "added", "Notify"
    high = abs(delta) >= max(th["consumed_high_abs"], th["consumed_high_pct"] * a)
    return "dropped", "High" if high else "Medium"


def consumed_events(acct, fam, measure, days, values, th, trend=True):
    """Diff a consumed measure over healthy days. `values[i]` is None when the account did not report.
    Reporting that starts at 0, or stops after 0, is not a change."""
    events = []
    last = None  # (day, value) of the last reported value
    for i, d in enumerate(days):
        v = values[i]
        pv = values[i - 1] if i else None
        if i == 0:
            if v is not None:
                last = (d, v)
            continue
        if v is None:
            if pv:
                events.append(_event(acct, fam, measure, "stopped", "Medium", d, days[i - 1], pv, None))
            continue
        if last is None:
            if v:
                events.append(_event(acct, fam, measure, "started", "Notify", d, days[i - 1], None, v))
        elif pv is None:
            ld, lv = last
            if _flag(lv, v - lv, th):
                kind, pri = _consumed_kind(lv, v, th)
                events.append(_event(acct, fam, measure, kind, pri, d, ld, lv, v, after_gap=True))
            elif lv or v:
                events.append(_event(acct, fam, measure, "resumed", "Info", d, ld, lv, v))
        elif _flag(pv, v - pv, th):
            kind, pri = _consumed_kind(pv, v, th)
            events.append(_event(acct, fam, measure, kind, pri, d, days[i - 1], pv, v))
        last = (d, v)
    if trend and days and values[-1] is not None:
        events.extend(_trend(acct, fam, measure, days, values, events, th))
    _mark_reverted(events, th, exact=False)
    return events


def _trend(acct, fam, measure, days, values, events, th):
    latest = days[-1]
    cutoff = (dt.date.fromisoformat(latest) - dt.timedelta(days=7)).isoformat()
    base = None
    for d, v in zip(days, values):
        if d <= cutoff and v is not None:
            base = (d, v)
    if not base:
        return []
    d7, v7 = base
    delta = values[-1] - v7
    if abs(delta) < max(th["consumed_7d_abs"], th["consumed_7d_pct"] * v7):
        return []
    up = delta > 0
    if any(e["date"] > d7 and _direction(e) == up for e in events):
        return []
    week = dt.date.fromisoformat(latest).isocalendar()
    ev = _event(acct, fam, measure, "trend_up" if up else "trend_down", "Notify" if up else "Medium",
                latest, d7, v7, values[-1])
    ev["id"] = event_id(acct, fam, measure, "7d", "up" if up else "down", f"{week[0]}-W{week[1]:02d}")
    return [ev]


def _direction(e):
    """True for an increase, False for a decrease, None for events without a direction."""
    if e["kind"] in ("started", "new_product"):
        return True
    if e["kind"] in ("stopped", "product_to_zero"):
        return False
    if e["kind"] in ("added", "dropped"):
        return (e.get("delta") or 0) > 0
    return None


def _mark_reverted(events, th, exact):
    """X -> Y -> X within `revert_days` marks both changes as reverted (data flaps)."""
    changes = sorted((e for e in events if e["kind"] not in ("migration", "trend_up", "trend_down")),
                     key=lambda e: (e.get("product_type") or "", e["date"]))
    for e1, e2 in zip(changes, changes[1:]):
        if (e1.get("product_type") or "") != (e2.get("product_type") or ""):
            continue
        if _days_between(e1["date"], e2["date"]) > th["revert_days"]:
            continue
        a, b = e1["from"], e2["to"]
        if exact:
            same = (a or 0) == (b or 0)
        elif a is None or b is None:
            same = a is None and b is None
        else:
            same = not _flag(a, b - a, th)
        if same and (not exact or e1["to"] == e2["from"]):
            e1["reverted"] = e2["reverted"] = True
            e1["reverted_by"], e2["reverts"] = e2["id"], e1["id"]


def series_values(rows, days, col=1, missing=None):
    """rows: sorted tuples (day, v1, v2...). Returns the column aligned to `days` (missing -> `missing`)."""
    m = {r[0]: r[col] for r in rows if r[col] is not None}
    return [m.get(d, missing) for d in days]


def all_events(src, accounts, th):
    """Compute every seat event for the given account ids. Returns a list sorted newest first."""
    hd = {k: sorted(v) for k, v in src["healthy"].items()}
    out = []
    for acct in accounts:
        runs = src["contracted"].get(acct)
        if runs:
            for fam in ("ghe", "ghas"):
                if any(family_of(pt) == fam for pt in runs):
                    per_day = contracted_daily(runs, hd["contracted"], fam)
                    out += contracted_events(acct, fam, hd["contracted"], per_day, th)
        seats = src["seats"].get(acct)
        if seats:
            days = account_days(src, "seats", acct, hd["seats"])
            per_day = {}
            have = {r[0]: r for r in seats}
            for d in days:
                r = have.get(d)
                per_day[d] = {"Copilot Business": (r[1] or 0.0), "Copilot Enterprise": (r[2] or 0.0),
                              "Copilot Standalone": (r[3] or 0.0)} if r else {}
                per_day[d] = {k: v for k, v in per_day[d].items() if v}
            out += contracted_events(acct, "ghcp", days, per_day, th)
        if acct in src["metered"]:
            days = hd["metered"]
            out += consumed_events(acct, "ghe", "metered", days, series_values(src["metered"][acct], days), th)
        if acct in src["volume"]:
            days = account_days(src, "volume", acct, hd["volume"])
            out += consumed_events(acct, "ghe", "ubu", days, series_values(src["volume"][acct], days), th, trend=False)
        if acct in src["ghas"]:
            days = hd["ghas"]
            out += consumed_events(acct, "ghas", "committers", days, series_values(src["ghas"][acct], days), th)
        if acct in src["health"]:
            days = hd["health"]
            out += consumed_events(acct, "ghcp", "assigned", days, series_values(src["health"][acct], days), th)
    out.sort(key=lambda e: (e["date"], -PRIORITY_ORDER[e["priority"]]), reverse=True)
    return out


def book_events(today_universe, prev_universe, prev_date, today):
    """Entered/left book events from two SFDC universe snapshots."""
    if not prev_universe or today_universe is None:
        return []
    out = []
    for acct in sorted(set(today_universe) - set(prev_universe)):
        out.append(_event(acct, "book", "owner", "entered_book", "Notify", today, prev_date, None, None,
                          name=today_universe[acct].get("name")))
    for acct in sorted(set(prev_universe) - set(today_universe)):
        out.append(_event(acct, "book", "owner", "left_book", "Notify", today, prev_date, None, None,
                          name=(prev_universe[acct] or {}).get("name")))
    return out

