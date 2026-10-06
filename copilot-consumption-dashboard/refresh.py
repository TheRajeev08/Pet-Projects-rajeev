#!/usr/bin/env python3
"""Query rendering + result collection for the Copilot consumption dashboard.

  python3 refresh.py render            # print every query (db + text) ready to paste into the Revenue MCP tools
  python3 refresh.py collect           # pull the MCP results out of the current Copilot session log into data/raw/<date>/
  python3 refresh.py collect --session <id>

The agent runs each rendered query verbatim with revenue-mcp-server query_kusto /
query_salesforce. `collect` then finds those tool results (full structuredContent) in
~/.copilot/session-state/<session>/events.jsonl, so results never need to be re-typed.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

from paths import ROOT, data_dir, load_config

QDIR = os.path.join(ROOT, "queries")
DATA = data_dir()
STATE = os.path.expanduser("~/.copilot/session-state")


def queries():
    cfg = load_config()
    out = []
    for path in sorted(glob.glob(os.path.join(QDIR, "*"))):
        name = os.path.basename(path)
        qid = name.split("_", 1)[0]
        with open(path) as f:
            text = f.read()
        text = (text.replace("{{OWNER_ID}}", cfg["owner_sfdc_id"])
                    .replace("{{OWNER_NAME}}", cfg["owner_name"])).strip()
        if name.endswith(".soql"):
            out.append({"qid": qid, "name": name, "tool": "query_salesforce", "db": None, "query": text})
        else:
            m = re.match(r"// qid:(\d+) db:(\w+)", text)
            if not m or m.group(1) != qid:
                sys.exit(f"{name}: first line must be '// qid:{qid} db:<database> — ...'")
            out.append({"qid": qid, "name": name, "tool": "query_kusto", "db": m.group(2), "query": text})
    return out


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def cmd_render(_):
    for q in queries():
        hdr = f"=== {q['qid']} · {q['tool']}" + (f" · database={q['db']}" if q["db"] else "") + " ==="
        print(hdr)
        print(q["query"])
        print()


def iter_events(session):
    files = []
    if session:
        files = [os.path.join(STATE, session, "events.jsonl")]
    else:
        env = os.environ.get("COPILOT_AGENT_SESSION_ID")
        if env and os.path.exists(os.path.join(STATE, env, "events.jsonl")):
            files = [os.path.join(STATE, env, "events.jsonl")]
        else:
            cutoff = dt.datetime.now().timestamp() - 12 * 3600
            files = [p for p in glob.glob(os.path.join(STATE, "*", "events.jsonl")) if os.path.getmtime(p) > cutoff]
            files.sort(key=os.path.getmtime)
    for path in files:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def cmd_collect(args):
    qs = queries()
    by_kql = {q["qid"]: q for q in qs if q["tool"] == "query_kusto"}
    by_soql = {norm(q["query"]): q for q in qs if q["tool"] == "query_salesforce"}
    starts, found, impact, settings = {}, {}, {}, {}
    for e in iter_events(args.session):
        d = e.get("data") or {}
        if e.get("type") == "tool.execution_start":
            starts[d.get("toolCallId")] = d
        elif e.get("type") == "tool.execution_complete" and d.get("success"):
            s = starts.get(d.get("toolCallId")) or {}
            tool = s.get("mcpToolName") or s.get("toolName", "")
            args_in = s.get("arguments") or {}
            if isinstance(args_in, str):
                try:
                    args_in = json.loads(args_in)
                except json.JSONDecodeError:
                    args_in = {}
            if not isinstance(args_in, dict):
                args_in = {}
            qtext = args_in.get("query", "")
            # Copilot Impact and feature settings are fetched per enterprise slug, not by query text.
            if tool.endswith("get_copilot_impact") or tool.endswith("get_copilot_feature_settings"):
                slug = args_in.get("slug")
                if slug:
                    payload = (d.get("result") or {}).get("structuredContent")
                    if payload is None:
                        try:
                            payload = json.loads((d.get("result") or {}).get("content", ""))
                        except (json.JSONDecodeError, TypeError):
                            continue
                    bucket = settings if tool.endswith("feature_settings") else impact
                    bucket[slug] = {"collected_from": e.get("timestamp"), "slug": slug,
                                    "namespace": args_in.get("namespace"), "result": payload}
                continue
            q = None
            if tool.endswith("query_kusto"):
                m = re.match(r"\s*// qid:(\d+)", qtext)
                q = by_kql.get(m.group(1)) if m else None
                if q and norm(qtext) != norm(q["query"]):
                    q = None  # stale/edited query text — ignore
            elif tool.endswith("query_salesforce"):
                q = by_soql.get(norm(qtext))
            if not q:
                continue
            result = (d.get("result") or {}).get("structuredContent")
            if result is None:
                try:
                    result = json.loads((d.get("result") or {}).get("content", ""))
                except (json.JSONDecodeError, TypeError):
                    continue
            found[q["qid"]] = {"collected_from": e.get("timestamp"), "query_file": q["name"], "result": result}

    missing = [q["qid"] for q in qs if q["qid"] not in found]
    day = args.date or dt.date.today().isoformat()
    out = os.path.join(DATA, "raw", day)
    os.makedirs(out, exist_ok=True)
    for qid, payload in found.items():
        with open(os.path.join(out, f"{qid}.json"), "w") as f:
            json.dump(payload, f)
    print(f"Collected {len(found)}/{len(qs)} query results into {out}")
    # A query collected earlier today is not missing; this keeps the Impact step re-runnable.
    missing = [qid for qid in missing if not os.path.exists(os.path.join(out, f"{qid}.json"))]

    for name, bucket in (("impact", impact), ("settings", settings)):
        if not bucket:
            continue
        folder = os.path.join(out, name)
        os.makedirs(folder, exist_ok=True)
        for slug, payload in bucket.items():
            with open(os.path.join(folder, f"{slug_file(slug)}.json"), "w") as f:
                json.dump(payload, f)
        print(f"Collected Copilot {name} for {len(bucket)} enterprises into {folder}")

    slugs = owned_slugs(day)
    for name, bucket in (("Impact", impact), ("feature settings", settings)):
        pending = [s for s in slugs if s not in bucket and
                   not os.path.exists(os.path.join(out, "impact" if name == "Impact" else "settings", f"{slug_file(s)}.json"))]
        if pending:
            print(f"Copilot {name} still missing for {len(pending)} enterprises "
                  f"(run `python3 refresh.py slugs` for the list).")

    if missing:
        print("MISSING: " + ", ".join(missing) + " — run these queries (verbatim from `render`) and collect again.")
        sys.exit(2)


def slug_file(slug):
    """Enterprise slugs are user-supplied; keep them to a safe flat filename."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", slug)[:120] or "unnamed"


def owned_slugs(day=None):
    """Enterprise slugs from the most recent collected query 13."""
    base = os.path.join(DATA, "raw")
    days = sorted(d for d in os.listdir(base)) if os.path.isdir(base) else []
    if day and day in days:
        days = [d for d in days if d <= day]
    for d in reversed(days):
        path = os.path.join(base, d, "13.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            rows = (json.load(f).get("result") or {}).get("rows") or []
        return [r["slug"] for r in rows if r.get("slug")]
    return []


def cmd_slugs(args):
    slugs = owned_slugs()
    if not slugs:
        sys.exit("No enterprise slugs collected yet — run query 13, then `collect`.")
    day = args.date or dt.date.today().isoformat()
    base = os.path.join(DATA, "raw", day)
    have = {}
    for name in ("impact", "settings"):
        folder = os.path.join(base, name)
        have[name] = {os.path.splitext(f)[0] for f in os.listdir(folder)} if os.path.isdir(folder) else set()
    pending = [s for s in slugs
               if slug_file(s) not in have["impact"] or slug_file(s) not in have["settings"]]
    print(f"{len(slugs)} owned enterprises · Impact collected {len(have['impact'])} · "
          f"feature settings collected {len(have['settings'])}")
    print("Call get_copilot_impact AND get_copilot_feature_settings (namespace \"enterprise\") for each slug below:")
    for s in pending:
        missing = [n for n in ("impact", "settings") if slug_file(s) not in have[n]]
        print(f"{s}\t{'+'.join(missing)}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render")
    c = sub.add_parser("collect")
    c.add_argument("--session")
    c.add_argument("--date")
    s = sub.add_parser("slugs", help="enterprise slugs still needing a Copilot Impact call today")
    s.add_argument("--date")
    a = p.parse_args()
    {"render": cmd_render, "collect": cmd_collect, "slugs": cmd_slugs}[a.cmd](a)


if __name__ == "__main__":
    main()
