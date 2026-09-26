#!/usr/bin/env python3
"""Answer questions from the dashboard's own data (never live sources).

  python3 ask.py schema                 tables, columns, definitions, data date
  python3 ask.py sql "SELECT ..."       run a read-only query, print a markdown table
  python3 ask.py install-skill          install the Copilot skill to ~/.copilot/skills
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys

from paths import HOME, ROOT, data_dir

MODEL = os.path.join(HOME, "model.json")
WEEKLY_METRICS = {"a7": "active_7d", "a28": "active_28d", "h_assigned": "assigned", "h_at_risk": "at_risk", "h_health": "health",
                  "gross": "ubb_gross", "billable": "ubb_billable", "aiu": "ai_units", "ubb_users": "ubb_users",
                  "accepted": "accepted", "shown": "shown"}
LIST_KEYS = {"a7", "a28", "h_assigned", "h_at_risk", "h_health", "h_health_cat", "h_grr_30d", "gross", "billable", "aiu", "ubb_users",
             "accepted", "shown", "signals", "arr_series", "integrations", "models", "editors", "risk", "surface_mix", "linked_usage"}


def scalar(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def load():
    if not os.path.exists(MODEL):
        sys.exit(f"No dashboard data at {MODEL}. Run the daily refresh (REFRESH.md) first.")
    with open(MODEL) as f:
        m = json.load(f)
    db = sqlite3.connect(":memory:")
    accts = m["accounts"]
    cols = sorted({k for a in accts for k in a if k not in LIST_KEYS})
    cols += ["signals", "risk_factors", "editors"]
    db.execute(f"CREATE TABLE accounts ({', '.join(f'[{c}]' for c in cols)})")
    for a in accts:
        row = [scalar(a.get(c)) for c in cols[:-3]] + [", ".join(a.get("signals") or []), scalar(a.get("risk")), scalar(a.get("editors"))]
        db.execute(f"INSERT INTO accounts VALUES ({','.join('?' * len(cols))})", row)

    db.execute("CREATE TABLE weekly (id, name, week_ago INTEGER, metric, value)")
    db.execute("CREATE TABLE weekly_health (id, name, week_ago INTEGER, health_cat)")
    db.execute("CREATE TABLE integrations (id, name, integration, surface, spend_7d, spend_prev_7d, spend_28d, ai_units_28d, users_7d, users_prev_7d, users_28d)")
    db.execute("CREATE TABLE models (id, name, model, spend_7d, spend_prev_7d, spend_28d)")
    db.execute("CREATE TABLE contract_history (id, name, period, copilot_seats, business_seats, enterprise_seats, total_arr)")
    db.execute("CREATE TABLE linked_usage (id, name, linked_id, linked_name, spend_7d, spend_prev_7d, spend_28d, users_7d)")
    for a in accts:
        for r in a.get("linked_usage") or []:
            db.execute("INSERT INTO linked_usage VALUES (?,?,?,?,?,?,?,?)", (a["id"], a["name"], r.get("id"), r.get("name"), r.get("gross_7d"), r.get("gross_p7"), r.get("gross_28d"), r.get("users_7d")))
        for k, metric in WEEKLY_METRICS.items():
            for i, v in enumerate(a.get(k) or []):
                if v is not None:
                    db.execute("INSERT INTO weekly VALUES (?,?,?,?,?)", (a["id"], a["name"], i, metric, v))
        for i, v in enumerate(a.get("h_health_cat") or []):
            if v:
                db.execute("INSERT INTO weekly_health VALUES (?,?,?,?)", (a["id"], a["name"], i, v))
        for r in a.get("integrations") or []:
            db.execute("INSERT INTO integrations VALUES (?,?,?,?,?,?,?,?,?,?,?)", (a["id"], a["name"], r.get("integration"), r.get("surface"), r.get("l7"), r.get("p7"),
                                                                                r.get("l28"), r.get("aiu28"), r.get("u7"), r.get("up7"), r.get("u28")))
        for r in a.get("models") or []:
            db.execute("INSERT INTO models VALUES (?,?,?,?,?,?)", (a["id"], a["name"], r.get("model"), r.get("l7"), r.get("p7"), r.get("l28")))
        for r in a.get("arr_series") or []:
            db.execute("INSERT INTO contract_history VALUES (?,?,?,?,?,?,?)", (a["id"], a["name"], r.get("date"), r.get("seats"), r.get("cb"), r.get("ce"), r.get("total_arr")))

    db.execute("CREATE TABLE changes (id, account, kind, severity, text, value)")
    for c in m.get("changes") or []:
        db.execute("INSERT INTO changes VALUES (?,?,?,?,?,?)", (c.get("id"), c.get("account"), c.get("kind"), c.get("severity"), c.get("text"), scalar(c.get("value"))))

    snap_rows = []
    for p in sorted(glob.glob(os.path.join(data_dir(), "snapshots", "*.json"))):
        with open(p) as f:
            s = json.load(f)
        for a in s.get("accounts", []):
            snap_rows.append(dict(a, data_date=s["data_date"]))
    scols = sorted({k for r in snap_rows for k in r}) or ["data_date", "id", "name"]
    db.execute(f"CREATE TABLE snapshots ({', '.join(f'[{c}]' for c in scols)})")
    for r in snap_rows:
        db.execute(f"INSERT INTO snapshots VALUES ({','.join('?' * len(scols))})", [scalar(r.get(c)) for c in scols])

    db.execute("CREATE TABLE meta (key, value)")
    asof = max((m.get("asof") or {}).values(), default=m["data_date"])
    for k, v in (("data_date", m["data_date"]), ("data_as_of", asof), ("generated_at", m.get("generated_at")),
                 ("missing_sources", ", ".join(m.get("missing_sources") or []) or "none")):
        db.execute("INSERT INTO meta VALUES (?,?)", (k, v))
    db.commit()
    return db, m


def md(cur, limit=200):
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(limit + 1)
    def cell(v):
        if isinstance(v, float):
            v = round(v, 3)
        return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows[:limit]]
    out.append(f"\n{min(len(rows), limit)} row(s)" + (f" (truncated at {limit})" if len(rows) > limit else ""))
    return "\n".join(out)


def cmd_schema():
    db, m = load()
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    print(f"Data date {meta['data_date']} (sources as of {meta['data_as_of']}); missing sources: {meta['missing_sources']}\n")
    notes = {
        "accounts": "one row per owned account; booleans are 0/1; *_7d = latest 7 days; wow_* = latest 7d minus prior 7d",
        "weekly": "13 weeks per account; week_ago 0 = latest 7 days; metric in (" + ", ".join(sorted(WEEKLY_METRICS.values())) + ")",
        "weekly_health": "health category per account per week_ago",
        "integrations": "UBB spend by integration (surface = product area) per account",
        "models": "UBB spend by model per account",
        "contract_history": "contracted Copilot seats and total ARR at month-ends ('current' = latest)",
        "linked_usage": "Copilot UBB on unowned Salesforce accounts (e.g. Data Syncer, created from a GitHub enterprise slug) whose name matches an owned account; not included in account totals",
        "changes": "this week's change feed shown on the dashboard",
        "snapshots": "one row per account per saved daily snapshot (history grows daily)",
        "meta": "data_date, data_as_of, generated_at, missing_sources",
    }
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info([{t}])")]
        n = db.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        print(f"## {t} ({n} rows) — {notes.get(t, '')}\n{', '.join(cols)}\n")
    print("## Definitions")
    for k, v in (m.get("definitions") or {}).items():
        print(f"- {k}: {v}")


def cmd_sql(q):
    if not q.lstrip().lower().startswith(("select", "with")):
        sys.exit("Only SELECT / WITH queries are allowed.")
    db, _ = load()
    db.execute("PRAGMA query_only = ON")
    try:
        print(md(db.execute(q)))
    except sqlite3.Error as e:
        sys.exit(f"SQL error: {e}")


def cmd_install_skill():
    src = os.path.join(ROOT, "skill")
    dst = os.path.expanduser("~/.copilot/skills/copilot-dashboard-ask")
    os.makedirs(dst, exist_ok=True)
    # copy the scripts too, so the skill keeps working when this checkout/worktree goes away
    for name in ("ask.py", "paths.py"):
        shutil.copy2(os.path.join(ROOT, name), os.path.join(dst, name))
    with open(os.path.join(src, "SKILL.md")) as f:
        body = f.read().replace("{{ASK_PY}}", os.path.join(dst, "ask.py"))
    with open(os.path.join(dst, "SKILL.md"), "w") as f:
        f.write(body)
    print(f"Installed skill to {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("schema")
    s = sub.add_parser("sql")
    s.add_argument("query")
    sub.add_parser("install-skill")
    args = ap.parse_args()
    if args.cmd == "schema":
        cmd_schema()
    elif args.cmd == "sql":
        cmd_sql(args.query)
    elif args.cmd == "install-skill":
        cmd_install_skill()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
