#!/usr/bin/env python3
"""Normalize collected query results, compute week-over-week changes, render dashboard.html.

  python3 build_dashboard.py                 # latest data/raw/<date>/
  python3 build_dashboard.py --date 2026-09-25
  python3 build_dashboard.py --summary       # also print the headline changes (for the automation run log)

Standard library only (Python 3.9+).
"""
import argparse
import base64
import datetime as dt
import glob
import json
import math
import os
import re
import sys

from paths import ROOT, dashboard_path, data_dir

DATA = data_dir()
WEEKS = 13
ACTIVE_LOOKBACK_WEEKS = 13  # ~90 days

SURFACES = {
    "vscode-chat": "IDE chat", "visualstudio-chat": "IDE chat", "jetbrains-chat": "IDE chat",
    "xcode-chat": "IDE chat", "eclipse-chat": "IDE chat", "zed": "IDE chat", "copilot-language-server": "IDE chat",
    "copilot-chat": "GitHub.com chat", "copilot-mobile-ios": "Mobile", "copilot-mobile-android": "Mobile",
    "copilot-pr-reviews": "Code review",
    "copilot-developer-cli": "Copilot CLI", "copilot-4-cli": "Copilot CLI",
    "copilot-developer": "Coding agent", "copilot-developer-app": "Copilot app", "copilot-desktop": "Copilot app",
    "copilot-sdk": "SDK / integrations", "copilot-slack-integration": "SDK / integrations",
    "ghas-code-scanning-agentic": "Security agents",
    "claude-code": "Third-party agents", "claude-code-cloud": "Third-party agents",
    "codex-cloud": "Third-party agents", "opencode": "Third-party agents",
}


def surface(integration):
    return SURFACES.get(integration or "", "Other")


# ---------------------------------------------------------------- loading
def dec(v):
    """Kusto dynamic columns arrive base64-encoded JSON through the MCP server."""
    if isinstance(v, str) and v and v[0] not in "{[" and len(v) % 4 == 0:
        try:
            return json.loads(base64.b64decode(v, validate=True))
        except (ValueError, json.JSONDecodeError):
            return v
    return v


def num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def chunk(flat, cols):
    names = cols.split(",")
    flat = dec(flat) or []
    return [dict(zip(names, flat[i:i + len(names)])) for i in range(0, len(flat), len(names))]


def weekly(points, key):
    """wk-keyed dicts -> list indexed by wk (0 = latest week), None where missing."""
    out = [None] * WEEKS
    for p in points:
        wk = p.get("wk")
        if isinstance(wk, (int, float)) and 0 <= int(wk) < WEEKS:
            out[int(wk)] = p.get(key)
    return out


def load_raw(day):
    base = os.path.join(DATA, "raw")
    if day is None:
        days = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
        if not days:
            sys.exit("No data/raw/<date>/ folder — run the refresh first (see REFRESH.md).")
        day = days[-1]
    folder = os.path.join(base, day)
    raw, meta = {}, {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        qid = os.path.basename(path)[:-5]
        with open(path) as f:
            payload = json.load(f)
        res = payload["result"]
        rows = res.get("records") if "records" in res else res.get("rows", [])
        raw[qid] = [{k: dec(v) for k, v in r.items()} for r in rows]
        meta[qid] = {"rows": len(rows), "collected_from": payload.get("collected_from")}
        if len(rows) >= 1000:
            print(f"WARNING: query {qid} returned {len(rows)} rows — Kusto 1000-row cap may have truncated results.", file=sys.stderr)
    return day, raw, meta


# ---------------------------------------------------------------- model

def period(d):
    """ARR facts land on month-ends plus a 'latest' day that differs by product; bucket non-month-end dates as current."""
    day = dt.date.fromisoformat(d)
    return d if (day + dt.timedelta(days=1)).day == 1 else "9999-current"


def arr_series(row, dates):
    pts, cur_dates = {}, []
    for p in (chunk(row["s"], row["cols"]) if row else []):
        k = period(p["date"])
        if k.startswith("9999"):
            cur_dates.append(p["date"])
        agg = pts.setdefault(k, {"copilot_arr": 0.0, "total_arr": 0.0, "cb": 0.0, "ce": 0.0, "cs": 0.0})
        agg["copilot_arr"] += num(p["copilot_arr"]) or 0
        agg["total_arr"] += num(p["total_arr"]) or 0
        agg["cb"] += num(p["cb_seats"]) or 0
        agg["ce"] += num(p["ce_seats"]) or 0
        agg["cs"] += num(p["standalone_seats"]) or 0
    # Each product's latest snapshot lands on its own day, and right after a month closes it moves onto the new
    # month-end. When that month-end is newer than the account's 'current' days, fill any field that is empty in
    # 'current' from it instead of reading it as zero.
    month_ends = [d for d in dates if not d.startswith("9999")]
    if dates and dates[-1].startswith("9999") and month_ends and month_ends[-1] in pts and max(cur_dates, default="") < month_ends[-1]:
        cur = pts.setdefault(dates[-1], {"copilot_arr": 0.0, "total_arr": 0.0, "cb": 0.0, "ce": 0.0, "cs": 0.0})
        for k, v in pts[month_ends[-1]].items():
            if not cur[k]:
                cur[k] = v
    out = []
    for d in dates:  # otherwise a missing period means no contracted products then -> zero
        g = pts.get(d, {"copilot_arr": 0.0, "total_arr": 0.0, "cb": 0.0, "ce": 0.0, "cs": 0.0})
        out.append({"date": "current" if d.startswith("9999") else d, "copilot_arr": g["copilot_arr"], "total_arr": g["total_arr"],
                    "seats": g["cb"] + g["ce"] + g["cs"], "cb": g["cb"], "ce": g["ce"]})
    return out

def build(day, raw, meta):
    sf = {r["Id"]: r for r in raw.get("01", [])}
    snap = {r["id"]: r for r in raw.get("02", [])}
    by = {q: {r["id"]: r for r in raw.get(q, []) if r.get("id")} for q in ("03", "04", "05", "06", "07", "09")}
    integ, models = {}, {}
    for r in raw.get("08", []):
        integ.setdefault(r["id"], []).append(r)
    for r in raw.get("11", []):
        models.setdefault(r["id"], []).append(r)
    linked = {}
    for r in raw.get("12", []):
        linked.setdefault(r["id"], []).append({"id": r["linked_id"], "name": r["linked_name"], "gross_7d": num(r["gross_7d"]) or 0,
                                               "gross_p7": num(r["gross_p7"]) or 0, "gross_28d": num(r["gross_28d"]) or 0,
                                               "users_7d": r.get("users_7d") or 0})

    asof = {}
    arr_dates = sorted({period(p["date"]) for r in raw.get("09", []) for p in chunk(r["s"], r["cols"])})
    for q in ("03", "04", "06", "07", "08", "10", "12"):
        rows = raw.get(q) or []
        if rows and rows[0].get("asof"):
            asof[q] = rows[0]["asof"][:10]
    if raw.get("02"):
        asof["02"] = (raw["02"][0].get("measurement_date") or "")[:10]

    accounts = []
    for aid, s in sf.items():
        c = snap.get(aid, {})
        a = {
            "id": aid, "name": s.get("Name"), "type": s.get("Type"), "industry": s.get("Industry") or c.get("industry"),
            "country": s.get("BillingCountry"), "parent": (s.get("Parent") or {}).get("Name"),
            "segment": c.get("segment"), "region": c.get("region"), "territory": c.get("territory"),
            "csm": c.get("csm") or None, "csa": c.get("csa") or None,
            "renewal": (c.get("next_renewal_date") or "")[:10] or None,
            "deployment": c.get("deployment") or None, "emu": c.get("is_emu"),
            "total_arr": num(c.get("total_arr")), "ghe_seats": num(c.get("license_seats")), "ghe_meu": num(c.get("ghe_meu")),
            "copilot_est_arr": num(c.get("copilot_est_arr")),
            "copilot_billed_ltm": num(c.get("copilot_billed_ltm")), "copilot_aiu_billed_ltm": num(c.get("copilot_aiu_billed_ltm")),
            "copilot_billed_lcm": num(c.get("copilot_billed_lcm")), "copilot_aiu_billed_lcm": num(c.get("copilot_aiu_billed_lcm")),
            "c360_assigned": num(c.get("assigned")), "installed_1d28": num(c.get("installed_1d28")),
            "engaged_1d28": num(c.get("engaged_1d28")), "c360_active_1d28": num(c.get("active_1d28")),
            "hwm_assigned_180d": num(c.get("hwm_assigned_180d")), "pct_users_at_risk": num(c.get("pct_users_at_risk")),
            "open_renewal_arr": num(c.get("open_renewal_license_arr")), "open_pipeline_net": num(c.get("open_pipeline_license_net")),
            "mover": c.get("mover") or None,
            "sf_url_id": aid,
        }
        # active users
        u = chunk(by["03"].get(aid, {}).get("s"), by["03"].get(aid, {}).get("cols", "wk,a7,a28"))
        a["a7"], a["a28"] = weekly(u, "a7"), weekly(u, "a28")
        # health
        h = by["06"].get(aid)
        hp = chunk(h["s"], h["cols"]) if h else []
        for k in ("assigned", "at_risk", "health", "health_cat", "grr_30d"):
            a["h_" + k] = weekly(hp, k)
        latest_h = next((p for p in sorted(hp, key=lambda p: p["wk"])), None)
        a["risk"] = {k: latest_h.get(k) for k in ("risk_mau", "risk_intracohort_90d", "risk_no_activity_180d", "risk_pru_mau_retention")} if latest_h else None
        a["product_health"] = latest_h.get("product_health") if latest_h else None
        a["commercial_health"] = latest_h.get("commercial_health") if latest_h else None
        # UBB
        b = by["07"].get(aid)
        bp = chunk(b["s"], b["cols"]) if b else []
        a["gross"], a["billable"] = weekly(bp, "gross"), weekly(bp, "billable")
        a["aiu"], a["ubb_users"] = weekly(bp, "ai_units"), weekly(bp, "max_daily_users")
        cur_month = b and b.get("last_day") and b.get("month_start") and b["last_day"] >= b["month_start"]
        a["mtd_gross"] = num(b.get("mtd_gross")) if cur_month else None
        a["mtd_billable"] = num(b.get("mtd_billable")) if cur_month else None
        a["projected_month_spend"] = num(b.get("projected_month_end_spend")) if cur_month else None
        a["promo_util"] = num(b.get("promo_util")) if cur_month else None
        a["standard_util"] = num(b.get("standard_util")) if cur_month else None
        a["promo_pool"] = num(b.get("promo_pool")) if cur_month else None
        a["gross_run_rate_month"] = None
        if cur_month and a["mtd_gross"] is not None:
            ld = dt.date.fromisoformat(str(b["last_day"])[:10])
            dim = ((ld.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day
            a["gross_run_rate_month"] = round(a["mtd_gross"] / ld.day * dim, 2)
        # telemetry
        t = by["04"].get(aid)
        tp = chunk(t["s"], t["cols"]) if t else []
        a["accepted"], a["shown"] = weekly(tp, "accepted"), weekly(tp, "shown")
        a["editors"] = {k: {"accepted": v[0], "shown": v[1]} for k, v in (dec(t.get("editors")) or {}).items()} if t else {}
        # engagement / retention
        e = by["05"].get(aid, {})
        eng = e.get("eng") or {}
        def ratio(name):
            v = eng.get(name)
            return (v[0] / v[1]) if v and v[1] else None
        a["eng_active"], a["eng_active_prev"] = ratio("Copilot Engaged / Active"), ratio("Copilot Engaged / Active (prev)")
        a["eng_assigned"], a["eng_assigned_prev"] = ratio("Copilot Engaged / Assigned"), ratio("Copilot Engaged / Assigned (prev)")
        ret = (e.get("retention") or {}).get("30_day")
        a["mau_grr"], a["mau_nrr"] = (num(ret[0]), num(ret[1])) if ret else (None, None)
        a["mau_retained"], a["mau_churned"], a["mau_new"] = (ret[2], ret[3], ret[4]) if ret else (None, None, None)
        a["adoption_phase"] = e.get("adoption_phase") or None
        # contracted seats / ARR
        a["arr_series"] = arr_series(by["09"].get(aid), arr_dates)
        last9 = a["arr_series"][-1] if a["arr_series"] else {}
        a["copilot_arr"] = last9.get("copilot_arr")
        a["contracted_seats"] = last9.get("seats")
        a["cb_seats"], a["ce_seats"] = last9.get("cb"), last9.get("ce")
        # surfaces & models (last 28d)
        a["integrations"] = sorted([{"integration": r["integration"], "surface": surface(r["integration"]),
                                     "l7": num(r["l7"]) or 0, "p7": num(r["p7"]) or 0, "l28": num(r["l28"]) or 0,
                                     "aiu28": num(r.get("aiu28")) or 0, "u7": r.get("u7") or 0, "up7": r.get("up7") or 0,
                                     "u28": r.get("u28") or 0} for r in integ.get(aid, [])], key=lambda r: -r["l28"])
        a["models"] = sorted([{"model": r["model"], "l7": num(r["l7"]) or 0, "p7": num(r["p7"]) or 0, "l28": num(r["l28"]) or 0}
                              for r in models.get(aid, [])], key=lambda r: -r["l28"])
        a["linked_usage"] = sorted(linked.get(aid, []), key=lambda r: -r["gross_28d"])
        derive(a)
        accounts.append(a)

    link_billed_to_siblings(accounts)

    orphans = []
    kusto_ids = set(snap) | set(by["03"]) | set(by["06"]) | set(by["07"])
    for aid in sorted(kusto_ids - set(sf)):
        orphans.append({"id": aid, "name": (snap.get(aid) or {}).get("name") or aid})

    portfolio = build_portfolio(accounts, raw.get("10", []))
    return {"generated_at": dt.datetime.now().isoformat(timespec="minutes"), "data_date": day, "asof": asof,
            "sources": meta, "accounts": accounts, "portfolio": portfolio, "orphans": orphans}


NAME_SUFFIXES = (
    "privatelimited",
    "technologies",
    "corporation",
    "consulting",
    "technology",
    "solutions",
    "software",
    "services",
    "systems",
    "pvtltd",
    "limited",
    "india",
    "group",
    "gmbh",
    "labs",
    "corp",
    "pvt",
    "ltd",
    "inc",
    "llc",
    "llp",
    "co",
)


def name_key(n):
    """Same normalisation as query 12: lowercase alphanumerics with trailing corporate suffixes removed."""
    k = re.sub(r"[^a-z0-9]", "", (n or "").lower())
    while True:
        for suffix in NAME_SUFFIXES:
            if k.endswith(suffix):
                k = k[:-len(suffix)]
                break
        else:
            return k


def names_match(k1, k2):
    return len(k1) >= 5 and len(k2) >= 5 and (k1 == k2 or (len(k1) >= 8 and k2.startswith(k1)) or (len(k2) >= 8 and k1.startswith(k2)))


def link_billed_to_siblings(accounts):
    """A billed-only account whose usage is recorded on another owned look-alike account (e.g. after Sales Ops
    reassigns the slug-created account) is covered, not unlinked."""
    direct = [b for b in accounts if set(b["signals"]) & {"active users", "seats assigned", "UBB spend"}]
    for a in accounts:
        a["usage_on_sibling"] = []
        if not a["usage_unlinked"] or a.get("linked_usage"):
            continue
        k = name_key(a["name"])
        sib = [{"id": b["id"], "name": b["name"], "gross_28d": b.get("gross_28d")} for b in direct if b["id"] != a["id"] and names_match(k, name_key(b["name"]))]
        if sib:
            a["usage_on_sibling"] = sib
            a["usage_unlinked"] = False
            a["signals"].append("usage on sibling account")


def v0(series):
    return series[0] if series else None


def any_pos(series, weeks=ACTIVE_LOOKBACK_WEEKS):
    return any((num(x) or 0) > 0 for x in series[:weeks])


def derive(a):
    a["active_7d"] = v0(a["a7"])
    a["active_28d"] = v0(a["a28"]) if v0(a["a28"]) is not None else a["c360_active_1d28"]
    a["assigned"] = v0(a["h_assigned"]) if v0(a["h_assigned"]) is not None else a["c360_assigned"]
    a["health_cat"] = v0(a["h_health_cat"]) or None
    a["health"] = v0(a["h_health"])
    a["at_risk"] = v0(a["h_at_risk"])
    a["gross_7d"] = v0(a["gross"]) or 0
    a["gross_28d"] = sum(num(x) or 0 for x in a["gross"][:4])
    a["gross_90d"] = sum(num(x) or 0 for x in a["gross"])
    acc4 = sum(num(x) or 0 for x in a["accepted"][:4]); sh4 = sum(num(x) or 0 for x in a["shown"][:4])
    a["acceptance_28d"] = acc4 / sh4 if sh4 else None
    a["utilization"] = (a["active_28d"] / a["assigned"]) if a["assigned"] and a["active_28d"] is not None else None
    a["top_surface"] = None
    if a["integrations"]:
        agg = {}
        for r in a["integrations"]:
            agg[r["surface"]] = agg.get(r["surface"], 0) + r["l28"]
        a["surface_mix"] = dict(sorted(agg.items(), key=lambda kv: -kv[1]))
        a["top_surface"] = next(iter(a["surface_mix"]))
    else:
        a["surface_mix"] = {}
    a["top_model"] = a["models"][0]["model"] if a["models"] else None
    signals = []
    if any_pos(a["a7"]) or any_pos(a["a28"]):
        signals.append("active users")
    if any_pos(a["h_assigned"]) or (a["c360_assigned"] or 0) > 0 or any((p["seats"] or 0) > 0 for p in a["arr_series"]):
        signals.append("seats assigned")
    if any_pos(a["gross"]):
        signals.append("UBB spend")
    if (a.get("copilot_billed_lcm") or 0) > 0:
        signals.append("Copilot billed")
    if a.get("linked_usage"):
        signals.append("UBB on unowned account")
    a["signals"] = signals
    # No usage telemetry under this Salesforce ID even though the customer is billed or has usage on an
    # unowned look-alike account (typically auto-created from a GitHub enterprise slug).
    a["usage_unlinked"] = bool(signals) and set(signals) <= {"Copilot billed", "UBB on unowned account"}
    a["linked_gross_7d"] = round(sum(r["gross_7d"] for r in a.get("linked_usage") or []), 2) if a.get("linked_usage") else None
    a["linked_gross_28d"] = round(sum(r["gross_28d"] for r in a.get("linked_usage") or []), 2) if a.get("linked_usage") else None
    a["consuming"] = bool(signals)
    # 13-week trend of active users: last 4 weeks avg vs weeks 4-7 avg
    def avg(xs):
        xs = [num(x) for x in xs if num(x) is not None]
        return sum(xs) / len(xs) if xs else None
    r, p = avg(a["a7"][:4]), avg(a["a7"][4:8])
    a["trend_4w"] = ((r - p) / p) if r is not None and p else None
    for k, s in (("a7", "a7"), ("a28", "a28"), ("assigned", "h_assigned"), ("gross", "gross"), ("aiu", "aiu"), ("ubb_users", "ubb_users"), ("at_risk", "h_at_risk"), ("health", "h_health")):
        cur, prev = num(a[s][0]), num(a[s][1])
        a["wow_" + k] = (cur - prev) if cur is not None and prev is not None else None
    acc = [(num(x), num(y)) for x, y in zip(a["accepted"][:2], a["shown"][:2])]
    rates = [x / y if x is not None and y else None for x, y in acc]
    a["wow_acceptance"] = (rates[0] - rates[1]) if None not in rates else None


def build_portfolio(accounts, rows10):
    cons = [a for a in accounts if a["consuming"]]
    def series(key):
        return [sum(num(a[key][w]) or 0 for a in cons) for w in range(WEEKS)]
    p = {"owned": len(accounts), "consuming": len(cons),
         "a7": series("a7"), "a28": series("a28"), "assigned": series("h_assigned"), "gross": series("gross"),
         "billable": series("billable"), "aiu": series("aiu"), "at_risk": series("h_at_risk"),
         "accepted": series("accepted"), "shown": series("shown"),
         "consuming_by_week": [sum(1 for a in cons if (num(a["a7"][w]) or 0) > 0 or (num(a["gross"][w]) or 0) > 0) for w in range(WEEKS)],
         "copilot_arr": sum(a["copilot_arr"] or 0 for a in cons), "contracted_seats": sum(a["contracted_seats"] or 0 for a in cons),
         "mtd_gross": sum(a["mtd_gross"] or 0 for a in cons), "projected_month_spend": sum(a["projected_month_spend"] or 0 for a in cons),
         "gross_run_rate_month": sum(a["gross_run_rate_month"] or 0 for a in cons),
         "copilot_billed_ltm": sum((a["copilot_billed_ltm"] or 0) + (a["copilot_aiu_billed_ltm"] or 0) for a in cons)}
    hc = {}
    for a in cons:
        hc[a["health_cat"] or "n/a"] = hc.get(a["health_cat"] or "n/a", 0) + 1
    p["health_mix"] = hc
    hc_prev = {}
    for a in cons:
        k = (a["h_health_cat"][1] if a["h_health_cat"][1] else None) or "n/a"
        hc_prev[k] = hc_prev.get(k, 0) + 1
    p["health_mix_prev"] = hc_prev
    surf = {}
    for r in rows10:
        s = surface(r.get("integration"))
        surf.setdefault(s, [0.0] * WEEKS)
        wk = r.get("wk")
        if isinstance(wk, (int, float)) and 0 <= wk < WEEKS:
            surf[s][int(wk)] += num(r.get("gross")) or 0
    p["surface_weekly"] = dict(sorted(surf.items(), key=lambda kv: -sum(kv[1])))
    integ = {}
    for r in rows10:
        integ.setdefault(r["integration"], [0.0] * WEEKS)
        if 0 <= r["wk"] < WEEKS:
            integ[r["integration"]][int(r["wk"])] += num(r["gross"]) or 0
    p["integration_weekly"] = dict(sorted(integ.items(), key=lambda kv: -sum(kv[1])))
    mdl = {}
    for a in cons:
        for m in a["models"]:
            d = mdl.setdefault(m["model"], {"l7": 0, "p7": 0, "l28": 0, "accounts": 0})
            d["l7"] += m["l7"]; d["p7"] += m["p7"]; d["l28"] += m["l28"]; d["accounts"] += 1
    p["models"] = dict(sorted(mdl.items(), key=lambda kv: -kv[1]["l28"]))
    return p


# ---------------------------------------------------------------- changes
def pct(cur, prev):
    return (cur - prev) / prev if prev else None


def changes(model, prev_snapshot):
    ev = []
    today = dt.date.fromisoformat(model["data_date"])
    def add(kind, sev, a, text, value=None):
        ev.append({"kind": kind, "severity": sev, "id": a["id"] if a else None, "account": a["name"] if a else None, "text": text, "value": value})

    for a in model["accounts"]:
        a7, g = a["a7"], a["gross"]
        act = lambda w: (num(a7[w]) or 0) > 0 or (num(g[w]) or 0) > 0
        if a["consuming"]:
            if act(0) and not any(act(w) for w in range(1, 5)):
                add("new", "good", a, "Started consuming Copilot this week", a["active_7d"])
            if not act(0) and any(act(w) for w in range(1, 5)):
                add("stopped", "bad", a, "No Copilot activity this week (active last month)")
        c, p = num(a7[0]), num(a7[1])
        if c is not None and p is not None and abs(c - p) >= 3 and (not p or abs(c - p) / p >= 0.2):
            add("active_users", "good" if c > p else "bad", a, f"Weekly active users {int(p)} → {int(c)} ({fmt_pct(pct(c, p))})", c - p)
        c, p = num(a["h_assigned"][0]), num(a["h_assigned"][1])
        if c is not None and p is not None and c != p:
            add("assigned", "good" if c > p else "bad", a, f"Assigned Copilot seats {int(p)} → {int(c)}", c - p)
        ser = a["arr_series"]
        if len(ser) >= 2 and ser[-1]["seats"] != ser[-2]["seats"]:
            add("contract", "good" if ser[-1]["seats"] > ser[-2]["seats"] else "bad", a,
                f"Contracted Copilot seats {int(ser[-2]['seats'])} → {int(ser[-1]['seats'])} (since {ser[-2]['date']})", ser[-1]["seats"] - ser[-2]["seats"])
        hc, hp = a["h_health_cat"][0], a["h_health_cat"][1]
        if hc and hp and hc != hp:
            order = {"Red": 0, "Yellow": 1, "Green": 2}
            add("health", "good" if order.get(hc, 1) > order.get(hp, 1) else "bad", a, f"Copilot health {hp} → {hc}")
        c, p = num(a["h_at_risk"][0]), num(a["h_at_risk"][1])
        if c is not None and p is not None and c - p >= 2:
            add("risk", "bad", a, f"Users at risk up {int(p)} → {int(c)}", c - p)
        c, p = num(g[0]) or 0, num(g[1]) or 0
        if abs(c - p) >= 50 and (not p or abs(c - p) / p >= 0.3):
            add("spend", "good" if c > p else "warn", a, f"UBB gross spend ${p:,.0f} → ${c:,.0f} WoW ({fmt_pct(pct(c, p))})", c - p)
        for r in a["integrations"]:
            if r["l7"] > 0 and r["p7"] == 0 and r["l28"] == r["l7"] and r["surface"] not in ("IDE chat",):
                add("surface", "good", a, f"New surface adopted: {r['integration']} ({r['surface']}), ${r['l7']:,.0f} this week", r["l7"])
        if a["promo_util"] and a["promo_util"] > 1:
            add("overage", "warn", a, f"Projected to use {a['promo_util']*100:.0f}% of included pool this month (${a['projected_month_spend'] or 0:,.0f})", a["promo_util"])
        if a["renewal"] and a["consuming"]:
            days = (dt.date.fromisoformat(a["renewal"]) - today).days
            if 0 <= days <= 90 and a["trend_4w"] is not None and a["trend_4w"] <= -0.1:
                add("renewal", "bad", a, f"Renews in {days} days with active users down {fmt_pct(a['trend_4w'])} (4-wk avg)", days)
            elif 0 <= days <= 90:
                add("renewal", "info", a, f"Renews in {days} days ({a['renewal']})", days)
        for r in a.get("linked_usage") or []:
            add("linked", "warn", a, f"Copilot usage on unowned Salesforce account '{r['name']}': ${r['gross_7d']:,.0f} this week, ${r['gross_28d']:,.0f} over 28 days "
                f"({r['users_7d']} users). Ask Sales Ops to merge it into this account", r["gross_28d"])
        if a["utilization"] is not None and a["assigned"] and a["assigned"] >= 5 and a["utilization"] < 0.5:
            add("utilization", "warn", a, f"Low utilization: {a['utilization']*100:.0f}% of {int(a['assigned'])} assigned seats active (28d)", a["utilization"])

    if prev_snapshot:
        prev_ids = {x["id"]: x for x in prev_snapshot.get("accounts", [])}
        cur_ids = {x["id"]: x for x in model["accounts"]}
        for aid in set(cur_ids) - set(prev_ids):
            add("ownership", "info", cur_ids[aid], f"Account newly owned in Salesforce (since {prev_snapshot['data_date']})")
        for aid in set(prev_ids) - set(cur_ids):
            x = prev_ids[aid]
            ev.append({"kind": "ownership", "severity": "info", "id": aid, "account": x["name"], "text": f"No longer owned in Salesforce (was owned on {prev_snapshot['data_date']})", "value": None})
    rank = {"bad": 0, "warn": 1, "good": 2, "info": 3}
    ev.sort(key=lambda e: (rank.get(e["severity"], 9), -abs(e["value"]) if isinstance(e["value"], (int, float)) else 0))
    return ev


def fmt_pct(x):
    return "n/a" if x is None else f"{x*100:+.0f}%"


# ---------------------------------------------------------------- snapshots / output
def previous_snapshot(day):
    snaps = sorted(glob.glob(os.path.join(DATA, "snapshots", "*.json")))
    target = dt.date.fromisoformat(day) - dt.timedelta(days=7)
    older = [s for s in snaps if os.path.basename(s)[:-5] < day]
    if not older:
        return None
    # prefer the snapshot closest to 7 days ago, else the latest earlier one
    best = min(older, key=lambda s: abs((dt.date.fromisoformat(os.path.basename(s)[:-5]) - target).days))
    with open(best) as f:
        return json.load(f)


DEFINITIONS = {
    "consuming": "Any Copilot active users, seats assigned, or UBB spend in the last 90 days, Copilot billed last month, or UBB on an unowned look-alike account",
    "linked_usage": "UBB spend (28d) on Salesforce accounts with no real owner (e.g. Data Syncer, auto-created from a GitHub enterprise slug) whose name matches this account. Shown for context; not added to this account's totals",
    "active_this_week": "Active users > 0 or UBB gross spend > 0 in the latest 7 days",
    "usage_unlinked": "Billed for Copilot or has usage on an unowned look-alike account, but no usage telemetry under this Salesforce account; excluded from inactive_this_week",
    "usage_on_sibling": "Billed for Copilot with no telemetry of its own, but another owned account with a matching name carries the usage (e.g. a reassigned slug-created account); excluded from inactive_this_week",
    "active_last_week": "Same as active_this_week, for the 7 days before that",
    "inactive_this_week": "Consuming (90d) but not active this week",
    "trend_4w": "Avg weekly active users, last 4 weeks vs the 4 weeks before",
    "declining": "trend_4w <= -10%",
    "growing": "trend_4w >= +10%",
    "low_utilization": "Consuming, >= 10 assigned seats and 28-day active / assigned < 30%",
    "renewing_in_days": "Days from the data date to next renewal (negative = past)",
    "health_moved": "Copilot health category differs from one week earlier",
    "utilization": "28-day active users / assigned seats",
    "wow_*": "Latest 7 days minus the prior 7 days",
}


def add_flags(model):
    """Shared, question-friendly flags used by both the dashboard Ask box and ask.py."""
    today = dt.date.fromisoformat(model["data_date"])
    for a in model["accounts"]:
        def pos(key, i):
            v = num((a.get(key) or [None, None])[i])
            return v is not None and v > 0
        a["active_this_week"] = pos("a7", 0) or pos("gross", 0)
        a["active_last_week"] = pos("a7", 1) or pos("gross", 1)
        a["inactive_this_week"] = a["consuming"] and not a["active_this_week"] and not a.get("usage_unlinked") and not a.get("usage_on_sibling")
        t = a.get("trend_4w")
        a["declining"] = t is not None and t <= -0.1
        a["growing"] = t is not None and t >= 0.1
        u = a.get("utilization")
        a["low_utilization"] = bool(a["consuming"] and u is not None and (a.get("assigned") or 0) >= 10 and u < 0.3)
        a["renewing_in_days"] = (dt.date.fromisoformat(a["renewal"]) - today).days if a.get("renewal") else None
        hc = a.get("h_health_cat") or [None, None]
        a["health_prev"] = hc[1] if len(hc) > 1 else None
        a["health_moved"] = bool(a.get("health_cat") and a["health_prev"] and a["health_cat"] != a["health_prev"])
    model["definitions"] = DEFINITIONS


SNAP_FIELDS = ("id", "name", "consuming", "active_this_week", "active_7d", "active_28d", "assigned", "contracted_seats", "utilization",
               "at_risk", "health", "health_cat", "gross_7d", "gross_28d", "mtd_gross", "top_surface", "top_model")


def slim(model):
    return {"data_date": model["data_date"], "accounts": [{k: a.get(k) for k in SNAP_FIELDS} for a in model["accounts"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--out", default=dashboard_path())
    args = ap.parse_args()

    day, raw, meta = load_raw(args.date)
    missing = [q for q in ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12") if q not in raw]
    if "01" in missing:
        sys.exit("Query 01 (owned accounts) is required.")
    model = build(day, raw, meta)
    add_flags(model)
    model["missing_sources"] = missing
    prev = previous_snapshot(day)
    model["compared_to"] = prev["data_date"] if prev else None
    model["changes"] = changes(model, prev)

    os.makedirs(os.path.join(DATA, "snapshots"), exist_ok=True)
    with open(os.path.join(DATA, "snapshots", f"{day}.json"), "w") as f:
        json.dump(slim(model), f)

    with open(os.path.join(ROOT, "template.html")) as f:
        tpl = f.read()
    with open(os.path.join(os.path.dirname(args.out), "model.json"), "w") as f:
        json.dump(model, f, separators=(",", ":"))
    blob = json.dumps(model, separators=(",", ":")).replace("</", "<\\/")
    with open(os.path.join(ROOT, "ask_engine.js")) as f:
        engine = f.read().replace("</", "<\\/")
    with open(args.out, "w") as f:
        f.write(tpl.replace("/*__ASK_ENGINE__*/", engine).replace("/*__DATA__*/null", blob))

    p = model["portfolio"]
    print(f"Dashboard written: {args.out}")
    print(f"Data date {day} · {p['consuming']} consuming of {p['owned']} owned accounts · "
          f"weekly active users {p['a7'][0]:.0f} (WoW {p['a7'][0]-p['a7'][1]:+.0f}) · UBB gross last 7d ${p['gross'][0]:,.0f} (WoW {fmt_pct(pct(p['gross'][0], p['gross'][1]))})")
    if missing:
        print("Missing sources: " + ", ".join(missing))
    if args.summary:
        print("\nTop changes this week:")
        for e in model["changes"][:15]:
            print(f"  [{e['severity']}] {e['account'] or ''}: {e['text']}")


if __name__ == "__main__":
    main()
