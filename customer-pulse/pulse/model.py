"""Assemble model.json (what the canvas and digest read) from sources, seat events and rules."""
import datetime as dt
import glob
import os
import shutil

from . import paths, rules, seats, sources, status
from .util import atomic_write_json, date_range, iso_now, read_json

SCHEMA = 1
SOURCE_OF = {("ghe", "contracted"): "contracted", ("ghas", "contracted"): "contracted", ("ghcp", "contracted"): "seats",
             ("ghe", "metered"): "metered", ("ghe", "ubu"): "volume", ("ghas", "committers"): "ghas",
             ("ghcp", "assigned"): "health", ("book", "owner"): None}
FAMILY_LABEL = {"ghe": "GHE", "ghas": "GHAS", "ghcp": "Copilot", "book": "Book", "all": "All products"}
MEASURE_LABEL = {"contracted": "contracted seats", "metered": "metered users", "ubu": "billable users",
                 "committers": "active committers", "assigned": "assigned seats", "owner": "owner"}
RAW_KEEP_DAYS = 30


def _num_text(n):
    return "—" if n is None else f"{n:,.0f}"


def event_text(e):
    fam, meas, k = FAMILY_LABEL.get(e["family"], e["family"]), MEASURE_LABEL.get(e["measure"], e["measure"]), e["kind"]
    a, b = _num_text(e.get("from")), _num_text(e.get("to"))
    pt = sources.TYPE_LABELS.get(e.get("product_type"), e.get("product_type"))
    what = f"{pt} seats" if pt else f"{fam} {meas}"
    if k == "migration":
        moves = ", ".join(f"{sources.TYPE_LABELS.get(s['type'], s['type'])} {_num_text(s['from'])} → {_num_text(s['to'])}"
                          for s in e.get("detail") or [])
        text = f"{fam} seats moved between products ({moves})"
    elif k == "new_product":
        text = f"New {what}: {b}"
    elif k == "product_to_zero":
        text = f"{what[0].upper() + what[1:]} went to zero ({a} → 0)"
    elif k in ("added", "dropped"):
        sign = "+" if (e.get("delta") or 0) > 0 else "−"
        n = abs(e.get("delta") or 0)
        text = f"{sign}{_num_text(n)} {what[:-1] if n == 1 and what.endswith('s') else what} ({a} → {b})"
    elif k == "stopped":
        text = f"{fam} {meas} stopped reporting (last {a})"
    elif k == "started":
        text = f"{fam} {meas} started: {b}"
    elif k == "resumed":
        text = f"{fam} {meas} reporting again ({a} → {b})"
    elif k in ("trend_up", "trend_down"):
        sign = "+" if k == "trend_up" else "−"
        text = f"{fam} {meas} {sign}{_num_text(abs(e.get('delta') or 0))} over 7+ days ({a} → {b})"
    elif k == "entered_book":
        text = "Entered your book in Salesforce"
    elif k == "left_book":
        text = "Left your book in Salesforce"
    else:
        text = f"{fam} {meas} {k}"
    if e.get("gap_days") and e["gap_days"] > 1 and k not in ("trend_up", "trend_down"):
        text += f" since {e['since']}"
    return text


def _daily_series(days, by_day):
    """Calendar-day series from the first to the last healthy day; None where unknown."""
    if not days:
        return None
    return {"start": days[0], "values": [by_day.get(d.isoformat()) for d in date_range(days[0], days[-1])]}


def _points(days, by_day):
    return {"points": [[d, by_day[d]] for d in days if by_day.get(d) is not None]}


def _at_or_before(days, by_day, day):
    best = None
    for d in days:
        if d <= day and by_day.get(d) is not None:
            best = d
    return best


def _delta(days, by_day, span):
    """Change from the value `span` days before the latest healthy day (nearest earlier healthy day)."""
    if not days or by_day.get(days[-1]) is None:
        return None, None
    last = days[-1]
    if span == 1:
        prev = days[-2] if len(days) > 1 else None
    else:
        prev = _at_or_before(days, by_day, (dt.date.fromisoformat(last) - dt.timedelta(days=span)).isoformat())
    if not prev or by_day.get(prev) is None:
        return None, None
    return by_day[last] - by_day[prev], prev


def _by_type_at(runs, day, fam):
    out = {}
    if not day:
        return out
    for pt, lst in (runs or {}).items():
        if sources.family_of(pt) != fam:
            continue
        for s, e, seats_, arr, skus in lst:
            if s <= day <= e:
                cur = out.setdefault(pt, {"seats": 0.0, "arr": 0.0, "skus": []})
                cur["seats"] += seats_ or 0.0
                cur["arr"] += arr or 0.0
                cur["skus"] = sorted(set(cur["skus"]) | set(skus))
                cur["since"] = s
    return out


def _value_map(rows, days, col=1):
    keep = set(days)
    return {r[0]: r[col] for r in rows or [] if r[0] in keep and r[col] is not None}


def _last_reported(rows, col=1):
    for r in reversed(rows or []):
        if r[col] is not None:
            return [r[0], r[col]]
    return None


def _frac(v):
    if v is None:
        return None
    return v / 100.0 if v > 1.5 else v


def summarize_account(acct, src, cfg, today, hd):
    th = cfg["thresholds"]
    u = (src["universe"] or {}).get(acct) or {}
    a = src["attrs"].get(acct) or {}
    have = src["files"]
    renewal = a.get("next_renewal_date")
    renew_days = (dt.date.fromisoformat(renewal) - dt.date.fromisoformat(today)).days if renewal else None
    runs = src["contracted"].get(acct) or {}
    cdays = hd["contracted"]
    last_c = cdays[-1] if cdays else None
    daily = {}

    def contracted(fam):
        per_day = seats.contracted_daily(runs, cdays, fam)
        tot = {d: seats.family_total(fam, v) for d, v in per_day.items()}
        daily[(fam, "contracted")] = tot
        return (tot.get(last_c, 0.0) if last_c else None), tot

    # GHE
    ghe_c, ghe_tot = contracted("ghe")
    mdays, vdays = hd["metered"], hd["volume"]
    mmap = _value_map(src["metered"].get(acct), mdays)
    vmap = _value_map(src["volume"].get(acct), vdays)
    daily[("ghe", "metered")], daily[("ghe", "ubu")] = mmap, vmap
    metered = mmap.get(mdays[-1]) if mdays else None
    mrow = {r[0]: r for r in src["metered"].get(acct) or []}.get(mdays[-1]) if mdays else None
    ubu = vmap.get(vdays[-1]) if vdays else None
    eng = (src["engagement"].get(acct) or [None])[-1]
    by_type = _by_type_at(runs, last_c, "ghe")
    billing = None
    if ghe_c and (metered or acct in src["metered"]):
        billing = "mixed"
    elif ghe_c:
        billing = "volume"
    elif acct in src["metered"]:
        billing = "metered"
    adoption = (ubu / ghe_c) if ghe_c and ubu is not None else _frac(a.get("latest_license_adoption_pct"))
    ghe = {
        "contracted": ghe_c if "p02" in have else None, "as_of": last_c,
        "arr": sum(v["arr"] for v in by_type.values()) if by_type else None,
        "by_type": by_type, "billing": billing,
        "metered": metered, "metered_as_of": mdays[-1] if mdays else None,
        "metered_last": _last_reported(src["metered"].get(acct)),
        "metered_detail": dict(zip(("cloud", "server", "users"), mrow[2:5])) if mrow else None,
        "ubu": ubu, "ubu_as_of": vdays[-1] if vdays else None, "ubu_last": _last_reported(src["volume"].get(acct)),
        "meu": eng[1] if eng else None, "mau": eng[2] if eng else None, "engagement_as_of": eng[0] if eng else None,
        "adoption": adoption, "c360_adoption": _frac(a.get("latest_license_adoption_pct")),
        "series": {"contracted": _daily_series(cdays, ghe_tot) if ghe_c or any(ghe_tot.values()) else None,
                   "metered": _daily_series(mdays, mmap) if mmap else None,
                   "ubu": _points(vdays, vmap) if vmap else None},
    }

    # GHAS
    ghas_c, ghas_tot = contracted("ghas")
    gdays = hd["ghas"]
    grows = src["ghas"].get(acct)
    gmap = _value_map(grows, gdays)
    daily[("ghas", "committers")] = gmap
    glast = {r[0]: r for r in grows or []}.get(gdays[-1]) if gdays else None
    ghas = {
        "contracted": ghas_c if "p02" in have else None, "as_of": last_c,
        "by_type": _by_type_at(runs, last_c, "ghas"),
        "committers": gmap.get(gdays[-1]) if gdays else None, "committers_as_of": gdays[-1] if gdays else None,
        "code_scanning": glast[2] if glast else None, "secret_scanning": glast[3] if glast else None,
        "committers_last": _last_reported(grows),
        "code_quality": src["code_quality"].get(acct),
        "licensing_model": a.get("ghas_licensing_model"), "plan": a.get("final_ghas_plan"),
        "series": {"contracted": _daily_series(cdays, ghas_tot) if ghas_c or any(ghas_tot.values()) else None,
                   "committers": _daily_series(gdays, gmap) if gmap else None},
    }
    ghas["arr"] = sum(v["arr"] for v in ghas["by_type"].values()) if ghas["by_type"] else None

    # Copilot
    sdays, hdays = hd["seats"], hd["health"]
    srows = {r[0]: r for r in src["seats"].get(acct) or []}
    ctot = {d: sum(x or 0.0 for x in srows[d][1:4]) if d in srows else 0.0
            for d in sources.account_days(src, "seats", acct, sdays)} if acct in src["seats"] else {}
    daily[("ghcp", "contracted")] = ctot
    srow = srows.get(sdays[-1]) if sdays else None
    hrows = src["health"].get(acct)
    amap = _value_map(hrows, hdays)
    rmap = _value_map(hrows, hdays, col=2)
    daily[("ghcp", "assigned")] = amap
    cp_contracted = (ctot.get(sdays[-1], 0.0) if ctot else 0.0) if sdays and "p04" in have else None
    ghcp = {
        "contracted": cp_contracted, "contracted_as_of": sdays[-1] if sdays else None,
        "business": srow[1] if srow else None, "enterprise": srow[2] if srow else None, "standalone": srow[3] if srow else None,
        "assigned": amap.get(hdays[-1]) if hdays else None, "at_risk": rmap.get(hdays[-1]) if hdays else None,
        "as_of": hdays[-1] if hdays else None, "assigned_last": _last_reported(hrows),
        "health": src["health_latest"].get(acct),
        "funnel": {"assigned": a.get("latest_number_copilot_assigned_users"),
                   "installed_1d28": a.get("latest_number_copilot_installed_authenticated_1d28_users"),
                   "active_1d28": a.get("latest_number_copilot_active_1d28_users"),
                   "engaged_1d28": a.get("latest_number_copilot_engaged_1d28_users")},
        "billed_ltm": a.get("latest_copilot_billed_dollars_ltm"),
        "series": {"contracted": _points(sdays, ctot) if ctot else None,
                   "assigned": _daily_series(hdays, amap) if amap else None,
                   "at_risk": _daily_series(hdays, rmap) if rmap else None},
    }
    other = _by_type_at(runs, last_c, "other")
    rec = {
        "id": acct, "name": u.get("name") or a.get("salesforce_account_name") or acct,
        "parent": u.get("parent") or a.get("salesforce_parent_account_name"),
        "segment": a.get("sales_segment") or a.get("business_segment"),
        "industry": u.get("industry") or a.get("industry"), "country": u.get("country") or a.get("billing_country"),
        "type": u.get("type") or a.get("account_type"),
        "arr": a.get("latest_total_arr"), "renewal": renewal, "renewing_in_days": renew_days,
        "open_renewal_arr": a.get("latest_open_renewal_total_expected_license_arr"),
        "open_pipeline_net_arr": a.get("latest_open_pipeline_license_net_change_in_arr"),
        "in_book": (acct in src["universe"]) if src["universe"] is not None else None,
        "has_data": bool(a or runs or acct in src["metered"] or acct in src["volume"] or acct in src["seats"]
                         or acct in src["health"] or acct in src["ghas"]),
        "sfdc_url": f"{cfg['sfdc_base_url'].rstrip('/')}/lightning/r/Account/{acct}/view",
        "flags": {k: a.get(k) for k in ("g2k", "has_actions", "has_codespaces", "has_ghec_metered", "has_cloud_orgs",
                                         "has_server_orgs", "ghas_enabled")},
        "billing_ltm": {"actions": a.get("latest_actions_billed_dollars_ltm"), "ghas": a.get("latest_ghas_billed_dollars_ltm"),
                        "ghec": a.get("latest_ghec_billed_dollars_ltm"), "copilot": a.get("latest_copilot_billed_dollars_ltm")},
        "support": {"tickets_28d": a.get("created_tickets_past28days"), "csat_ltm": a.get("latest_rounded_avg_support_experience_csat_ltm")},
        "products": {"ghe": ghe, "ghas": ghas, "ghcp": ghcp, "other": {"by_type": other}},
    }
    rec["has_products"] = bool((ghe["contracted"] or 0) or ghe["metered"] or ghe["ubu"] or (ghas["contracted"] or 0)
                               or ghas["committers"] or (ghcp["contracted"] or 0) or ghcp["assigned"])
    return rec, daily


def book_ids(src):
    """SFDC universe minus test accounts; falls back to every account Kusto attributes to the owner."""
    tests = {k for k, v in src["attrs"].items() if v.get("is_test_account")}
    if src["universe"] is not None:
        return sorted(set(src["universe"]) - tests), "salesforce"
    ids = set(src["attrs"]) | set(src["contracted"]) | set(src["metered"]) | set(src["volume"]) \
        | set(src["seats"]) | set(src["health"]) | set(src["ghas"])
    return sorted(ids - tests - {"__meta__", None}), "kusto"


def _kpi(days, dailies, kind="daily"):
    tot = {}
    for d in days:
        vals = [m[d] for m in dailies if m.get(d) is not None]
        if vals:
            tot[d] = sum(vals)
    if not days or days[-1] not in tot:
        return {"value": None, "as_of": days[-1] if days else None}
    d1, p1 = _delta(days, tot, 1)
    d7, p7 = _delta(days, tot, 7)
    out = {"value": tot[days[-1]], "as_of": days[-1], "d1": d1, "d1_since": p1, "d7": d7, "d7_since": p7}
    out["series"] = _points(days, tot) if kind == "points" else _daily_series(days, tot)
    return out


def build_kpis(accs, dailies, hd, events, actions):
    def col(fam, meas):
        return [dailies[a["id"]][(fam, meas)] for a in accs]

    p = lambda fam: [a["products"][fam] for a in accs]
    ghe_acc = [x for x in p("ghe") if (x["contracted"] or 0) or x["metered"] or x["ubu"]]
    vol = [x for x in p("ghe") if x["contracted"] and x["ubu"] is not None]
    ghas_acc = [x for x in p("ghas") if (x["contracted"] or 0) or x["committers"]]
    cp = p("ghcp")
    cp_acc = [x for x in cp if (x["contracted"] or 0) or x["assigned"]]
    cats = {}
    for x in cp_acc:
        c = (x.get("health") or {}).get("category")
        if c:
            cats[c] = cats.get(c, 0) + 1
    ghas_seats = sum(x["contracted"] or 0 for x in ghas_acc)
    ghas_comm = sum(x["committers"] or 0 for x in ghas_acc)
    recent = [e for e in events if e["recent"] and not e["reverted"]]
    return {
        "overall": {
            "accounts": len(accs), "with_products": sum(1 for a in accs if a["has_products"]),
            "arr": sum(a["arr"] or 0 for a in accs),
            "renewing_120": sum(1 for a in accs if a["renewing_in_days"] is not None and 0 <= a["renewing_in_days"] <= 120),
            "actions": {k: sum(1 for x in actions if x["priority"] == k) for k in ("High", "Medium", "Notify")},
            "changes": {k: sum(1 for e in recent if e["priority"] == k) for k in ("High", "Medium", "Notify", "Info")},
            "new_high": sum(1 for x in actions + events if x.get("new") and x["priority"] == "High"),
        },
        "ghe": {
            "accounts": len(ghe_acc),
            "contracted": _kpi(hd["contracted"], col("ghe", "contracted")),
            "metered": _kpi(hd["metered"], col("ghe", "metered")),
            "ubu": _kpi(hd["volume"], col("ghe", "ubu"), "points"),
            "adoption": (sum(x["ubu"] for x in vol) / sum(x["contracted"] for x in vol)) if vol else None,
            "metered_accounts": sum(1 for x in p("ghe") if x["billing"] in ("metered", "mixed")),
        },
        "ghas": {
            "accounts": len(ghas_acc),
            "contracted": _kpi(hd["contracted"], col("ghas", "contracted")),
            "committers": _kpi(hd["ghas"], col("ghas", "committers")),
            "utilization": (ghas_comm / ghas_seats) if ghas_seats else None,
            "code_quality_accounts": sum(1 for x in p("ghas") if x.get("code_quality")),
        },
        "ghcp": {
            "accounts": len(cp_acc),
            "contracted": _kpi(hd["seats"], col("ghcp", "contracted"), "points"),
            "assigned": _kpi(hd["health"], col("ghcp", "assigned")),
            "at_risk": sum(x["at_risk"] or 0 for x in cp_acc),
            "categories": cats,
        },
    }


def _event_new(e, notified, baseline, latest_by_source):
    if e["id"] in notified or not e["recent"]:
        return False
    if e["reverted"]:
        return bool(e.get("reverts") and e["reverts"] in notified)
    if baseline:
        src = SOURCE_OF.get((e["family"], e["measure"]))
        return src is None or e["date"] == latest_by_source.get(src)
    return True


def _action_new(a, notified, today, th):
    seen = notified.get(a["id"])
    if not seen:
        return True
    last = seen.get("last_seen") or seen.get("first")
    return bool(last) and (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days >= th["renotify_after_days"]


def notified_path():
    return os.path.join(paths.state_dir(), "notified.json")


def actioned_path():
    return os.path.join(paths.state_dir(), "actioned.json")


def load_notified():
    doc = read_json(notified_path())
    return doc, (doc or {}).get("items") or {}


def _universe_history(cfg, src):
    """Save today's SFDC book snapshot and return (previous snapshot, its date)."""
    folder = os.path.join(paths.history_dir(cfg), "universe")
    f = src["files"].get("p01")
    if src["universe"] is None or not f:
        return None, None
    day = f["raw_date"]
    atomic_write_json(os.path.join(folder, f"{day}.json"), {k: {"name": v.get("name")} for k, v in src["universe"].items()})
    prev = sorted(p for p in glob.glob(os.path.join(folder, "*.json")) if os.path.basename(p)[:-5] < day)
    if not prev:
        return None, None
    return read_json(prev[-1]), os.path.basename(prev[-1])[:-5]


def prune_raw(cfg, today, keep_days=RAW_KEEP_DAYS):
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
    for d in glob.glob(os.path.join(paths.raw_dir(cfg), "*")):
        name = os.path.basename(d)
        if len(name) == 10 and name < cutoff and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


def build(cfg, today=None, run=None):
    """Compute everything and return (model, status). Pure apart from the universe history snapshot."""
    today = today or dt.date.today().isoformat()
    th = cfg["thresholds"]
    src = sources.load_all(cfg, today)
    hd = {k: sorted(v) for k, v in src["healthy"].items()}
    ids, book_source = book_ids(src)
    accs, dailies = [], {}
    for acct in ids:
        rec, daily = summarize_account(acct, src, cfg, today, hd)
        accs.append(rec)
        dailies[acct] = daily
    names = {a["id"]: a["name"] for a in accs}

    events = seats.all_events(src, ids, th)
    prev_u, prev_date = _universe_history(cfg, src)
    events = seats.book_events(src["universe"], prev_u, prev_date, (src["files"].get("p01") or {}).get("raw_date") or today) + events
    latest = {k: (v[-1] if v else None) for k, v in hd.items()}
    window = th["changes_window_days"]
    doc, notified = load_notified()
    baseline = not ((doc or {}).get("sent") or notified)
    for e in events:
        src_key = SOURCE_OF.get((e["family"], e["measure"]))
        ref = latest.get(src_key) if src_key else today
        e["source"] = src_key
        e["recent"] = bool(ref) and (dt.date.fromisoformat(ref) - dt.date.fromisoformat(e["date"])).days < window
        e["account_name"] = e.get("name") or names.get(e["account_id"], e["account_id"])
        e["text"] = event_text(e)
        e["new"] = _event_new(e, notified, baseline, latest)
        e["baseline"] = baseline and not e["new"]

    actions = rules.all_actions(accs, events, th, dt.date.fromisoformat(today))
    actioned = (read_json(actioned_path()) or {}).get("items") or {}
    for a in actions:
        a["new"] = _action_new(a, notified, today, th)
        st = actioned.get(a["id"])
        a["state"] = st.get("state") if st else None
        a["state_at"] = st.get("at") if st else None
        if a["state"] in ("done", "dismissed"):
            a["new"] = False

    from . import signals
    try:
        signal_items, signal_feeds = signals.merge(cfg, today, ids, notified, actioned)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        signal_items = []
        signal_feeds = [{"feed": "signals_store", "label": "Signals store", "state": "failed",
                         "as_of": None, "messages": [f"{type(exc).__name__}: {exc}"],
                         "qid": None, "raw_date": None, "via": "local", "held": [], "rows": 0}]
    feeds = status.feed_status(src, today, th) + signal_feeds
    model = {
        "schema": SCHEMA, "generated_at": iso_now(), "date": today, "run": run,
        "owner": cfg.get("owner_name"), "book_source": book_source, "baseline": baseline,
        "thresholds": th, "changes_window_days": window,
        "sources": feeds, "status": status.overall(feeds),
        "kpis": build_kpis(accs, dailies, hd, events, actions),
        "accounts": accs, "events": events, "actions": actions, "signals": signal_items,
        "copilot": {"model_path": paths.copilot_model_path(cfg), "workflow_id": cfg.get("workflow_id")},
    }
    model["kpis"]["overall"]["new_high"] += sum(1 for s in signal_items if s["new"] and s["verified"] and s["priority"] == "High")
    st = {"generated_at": model["generated_at"], "date": today, "run": run, "overall": model["status"],
          "feeds": feeds, "build_error": None, "baseline": baseline,
          "counts": {"accounts": len(accs), "events": len(events), "actions": len(actions), "signals": len(signal_items)}}
    return model, st


def write(model, st):
    atomic_write_json(paths.model_path(), model)
    atomic_write_json(paths.status_path(), st, indent=2)


def write_build_error(err, today, run=None):
    prev = read_json(paths.status_path()) or {}
    prev.update({"generated_at": iso_now(), "date": today, "run": run, "overall": "failed", "build_error": str(err)[:2000]})
    atomic_write_json(paths.status_path(), prev, indent=2)
