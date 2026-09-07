"""Meeting-booking sprint prioritizer.

Reads the current territory-plan report (already built by workbook.py) plus
optional enrichment caches (Salesforce activity, contacts, and sprint signals
= open opportunities + Gong calls + GitHub account recommendations), scores
every account for "chance of booking a meeting this sprint," and writes
artifacts/sprint-focus.json for serve.py's /sprint view and the Excel export.

This is personal-app-only, same as activity/contacts enrichment: the team
app has no CRM access, so it never sees this file.

Score model (0-100, weighted, each weight documented so the "why" line is
never a black box):

  two_way_recency      25   verified two-way comms, weighted by recency
  open_opportunity      20   has an open Salesforce opportunity (more weight
                              if it has a near-term CloseDate or NextStep)
  renewal_proximity     15   renewal inside the next ~180 days = natural
                              reason to meet
  recommendation        15   GitHub account recommendation flagged "New"
                              for this rep (a system already surfaced intent)
  gong_recency          10   a Gong call in the last 90 days = live momentum
  potential             10   whitespace/revenue-potential proxy (upside
                              worth a meeting even without a warm signal)
  named_contact          5   we already have someone to invite

External market signals (funding/expansion/leadership news) are NOT scored
here -- they're gathered only for the shortlist by a separate enrichment
pass (sprint_news.py) and attached as cited evidence, because unverified web
signals shouldn't move a rep's numeric ranking, only their talking points.
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(ROOT, "artifacts")


def clean(v):
    return str(v or "").strip()


def load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def days_between(a, b):
    try:
        return (b - a).days
    except Exception:
        return None


def parse_date(s):
    s = clean(s)[:10]
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def score_account(account, sprint_signal, recommendation, today):
    triggers = []
    total = 0.0

    # -- two-way communication recency (0-25) --------------------------
    act = account.get("activity") or {}
    if act.get("status") == "enriched" and act.get("twoWay"):
        last = parse_date(act.get("lastActivity"))
        age = days_between(last, today) if last else None
        if age is not None and age <= 30:
            pts, note = 25, f"two-way contact {age}d ago"
        elif age is not None and age <= 90:
            pts, note = 18, f"two-way contact {age}d ago"
        elif age is not None:
            pts, note = 10, f"two-way contact {age}d ago (aging)"
        else:
            pts, note = 12, "two-way contact, date unknown"
        total += pts
        triggers.append({"type": "engagement", "label": "Two-way comms", "detail": note, "points": pts})

    # -- open Salesforce opportunity (0-20) ------------------------------
    opp = (sprint_signal or {}).get("openOpportunities")
    if opp and opp.get("count"):
        pts = 14
        close = parse_date(opp.get("earliestCloseDate"))
        age_close = days_between(today, close) if close else None
        note = f"{opp['count']} open opp"
        if opp.get("maxAmount"):
            note += f", up to ${opp['maxAmount']:,.0f}"
        if age_close is not None and 0 <= age_close <= 90:
            pts = 20
            note += f", closing in {age_close}d"
        elif age_close is not None and age_close < 0:
            note += " (close date has slipped)"
        stages = opp.get("stages") or []
        if stages:
            note += f" · {', '.join(stages[:2])}"
        total += pts
        triggers.append({"type": "opportunity", "label": "Open opportunity", "detail": note, "points": pts})

    # -- renewal proximity (0-15) ----------------------------------------
    renewal = parse_date(account.get("renewal"))
    if renewal:
        age = days_between(today, renewal)
        if age is not None and 0 <= age <= 90:
            pts, note = 15, f"renews in {age}d"
        elif age is not None and 90 < age <= 180:
            pts, note = 9, f"renews in {age}d"
        else:
            pts, note = 0, ""
        if pts:
            total += pts
            triggers.append({"type": "renewal", "label": "Renewal window", "detail": note, "points": pts})

    # -- GitHub account recommendation (0-15) -----------------------------
    if recommendation:
        pts = 15
        total += pts
        sig = (recommendation.get("signals") or [{}])[0]
        note = sig.get("recommended_product", "") 
        play = sig.get("revenue_play", "")
        insight = sig.get("ml_driven_insights", "")
        detail = f"{note} · {play}".strip(" ·")
        if insight:
            detail += f" — {insight[:140]}{'…' if len(insight) > 140 else ''}"
        triggers.append({"type": "recommendation", "label": "GitHub-flagged signal", "detail": detail, "points": pts})

    # -- Gong recency (0-10) ------------------------------------------------
    gong = (sprint_signal or {}).get("gong")
    if gong and gong.get("mostRecentCallDate"):
        d = parse_date(gong["mostRecentCallDate"])
        age = days_between(d, today) if d else None
        if age is not None and age <= 90:
            pts = 10
            note = f"Gong call {age}d ago"
            if gong.get("mostRecentCallTitle"):
                note += f' ("{gong["mostRecentCallTitle"]}")'
            total += pts
            triggers.append({"type": "gong", "label": "Recent call activity", "detail": note, "points": pts})

    # -- whitespace / potential (0-10) --------------------------------------
    potential = account.get("revenuePotential")
    if potential:
        pts = round(min(10, potential / 10), 1)
        if pts >= 3:
            total += pts
            triggers.append({"type": "potential", "label": "Whitespace potential", "detail": f"potential proxy {potential}", "points": pts})

    # -- named contact ready (0-5) --------------------------------------------
    contacts = account.get("contacts") or []
    if contacts:
        pts = 5
        total += pts
        c = contacts[0]
        triggers.append({"type": "contact", "label": "Contact on file", "detail": f"{c['name']} · {c['title']}", "points": pts})

    return round(min(100, total), 1), triggers


def build(report, activity_signals, sprint_signals, recommendations):
    today = datetime.date.today()
    rec_by_id = {r["account_id"]: r for r in (recommendations.get("recommendations") or [])}
    by_id = (sprint_signals or {}).get("accounts", {})

    rows = []
    for a in report["accounts"]:
        sid = a["salesforceId"]
        s, triggers = score_account(a, by_id.get(sid), rec_by_id.get(sid), today)
        if not triggers:
            continue
        why = "; ".join(t["detail"] for t in triggers if t.get("detail"))
        rows.append({
            "name": a["name"],
            "salesforceId": sid,
            "primaryPlay": a["primaryPlay"],
            "sprintScore": s,
            "triggers": triggers,
            "whyNow": why,
            "contacts": a.get("contacts") or [],
            "renewal": a.get("renewal"),
            "revenuePotential": a.get("revenuePotential"),
            "dashboards": a.get("dashboards") or [],
            "news": [],  # filled in by sprint_news.py, optional
        })

    rows.sort(key=lambda r: (-r["sprintScore"], r["name"].lower()))
    return {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "candidateCount": len(rows),
        "totalAccounts": report["accountCount"],
        "accounts": rows,
    }


def main():
    report_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ART, "fy27-territory-plan.json")
    # Enrichment caches live next to the report so each run directory is self-contained.
    run_dir = os.path.dirname(os.path.abspath(report_path)) or ART
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(run_dir, "sprint-focus.json")
    report = load(report_path)
    if not report:
        raise SystemExit(f"No report at {report_path}; run workbook.py first.")
    sprint_signals = load(os.path.join(run_dir, "salesforce-sprint-signals.json"))
    recommendations = load(os.path.join(run_dir, "github-recommendations.json"))
    activity_signals = load(os.path.join(run_dir, "salesforce-activity.json"))
    out = build(report, activity_signals, sprint_signals, recommendations)

    # Merge in news enrichment if sprint_news.py already ran and left a cache.
    news_cache = load(os.path.join(run_dir, "sprint-news.json"))
    news_by_id = news_cache.get("accounts", {}) if news_cache else {}
    for row in out["accounts"]:
        row["news"] = news_by_id.get(row["salesforceId"], [])

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(json.dumps({"path": out_path, "candidates": out["candidateCount"], "totalAccounts": out["totalAccounts"]}))


if __name__ == "__main__":
    main()
