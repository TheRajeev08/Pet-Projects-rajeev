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
import recommendations

QDIR = os.path.join(ROOT, "queries")
DATA = data_dir()
STATE = os.path.expanduser("~/.copilot/session-state")
ENTERPRISE_TOOLS = {"get_copilot_impact": "impact", "get_copilot_feature_settings": "settings"}
NOT_FOUND = object()


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


def session_log(session):
    """The events log of the one session that ran the queries.

    Only that session is read: scanning every recent session would mix in results from other
    runs (another refresh, a manual session), so a missing session is an error, not a fallback.
    """
    session = session or os.environ.get("COPILOT_AGENT_SESSION_ID")
    if not session:
        sys.exit("No Copilot session to collect from. Run this inside the session that ran the queries, "
                 "or pass --session <id>.")
    path = os.path.join(STATE, session, "events.jsonl")
    if not os.path.exists(path):
        sys.exit(f"No session log at {path}. Pass --session <id> for the session that ran the queries.")
    return path


def iter_events(session):
    with open(session_log(session)) as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def enterprise_feed(tool, args):
    if args.get("namespace") == "enterprise" and isinstance(args.get("slug"), str) and args["slug"]:
        return ENTERPRISE_TOOLS.get(tool.removeprefix("revenue-mcp-server-"))


def enterprise_not_found(detail, slug):
    """Recognize a resource-not-found reply, not e.g. 'tool not found' or unavailable data."""
    if isinstance(detail, list):
        return any(enterprise_not_found(item.get("text"), slug) for item in detail
                   if isinstance(item, dict) and item.get("type") == "text")
    if isinstance(detail, dict):
        return any(enterprise_not_found(detail.get(key), slug) for key in ("error", "message", "content"))
    if not isinstance(detail, str):
        return False
    # Revenue reports generic code "failure"; the message identifies the actual resource.
    message = detail.strip().removeprefix("MCP server 'revenue-mcp-server': ")
    return message.casefold() == f"not found: {slug} (github.com)".casefold()


def enterprise_payload(feed, payload):
    if not isinstance(payload, dict) or payload.get("error") or payload.get("isError"):
        return False
    if feed == "settings":
        return isinstance(payload.get("settings"), list)
    return any(key in payload for key in ("dataAvailable", "noDataReason", "impact", "cohorts"))


def tool_results(events):
    """Normalize successful results and definitive enterprise lookup gaps from one session."""
    starts = {}
    for e in events:
        d = e.get("data") or {}
        if e.get("type") == "tool.execution_start":
            starts[d.get("toolCallId")] = d
            continue
        if e.get("type") == "tool.execution_complete":
            s = starts.pop(d.get("toolCallId"), None)
            if not s:
                continue
            tool = s.get("mcpToolName") or s.get("toolName", "")
            args_in = s.get("arguments") or {}
            res = d.get("result") or {}
            text = res.get("content")
            success = d.get("success")
            error = d.get("error") or res.get("error")
        elif e.get("type") == "hook.start" and d.get("hookType") == "postToolUse":
            inp = d.get("input") or {}
            res = inp.get("toolResult") or {}
            success = res.get("resultType") == "success"
            error = res.get("error")
            tool = inp.get("toolName") or ""
            args_in = inp.get("toolArgs") or {}
            text = res.get("textResultForLlm")
        else:
            continue
        if isinstance(args_in, str):
            try:
                args_in = json.loads(args_in)
            except json.JSONDecodeError:
                continue
        if not isinstance(args_in, dict):
            continue
        payload = res.get("structuredContent")
        if payload is None:
            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                payload = text
        feed = enterprise_feed(tool, args_in)
        if feed and any(enterprise_not_found(detail, args_in["slug"]) for detail in (error, payload)):
            yield e.get("timestamp"), tool, args_in, NOT_FOUND
            continue
        if not success or not isinstance(payload, dict) or payload.get("isError") or res.get("isError"):
            continue
        if feed and not enterprise_payload(feed, payload):
            continue
        yield e.get("timestamp"), tool, args_in, payload


def cmd_collect(args):
    qs = queries()
    by_kql = {q["qid"]: q for q in qs if q["tool"] == "query_kusto"}
    by_soql = {norm(q["query"]): q for q in qs if q["tool"] == "query_salesforce"}
    found, impact, settings = {}, {}, {}
    recommendation_feed = None
    for timestamp, tool, args_in, result in tool_results(iter_events(args.session)):
        qtext = args_in.get("query", "")
        feed = enterprise_feed(tool, args_in)
        if feed:
            slug = args_in["slug"]
            bucket = settings if feed == "settings" else impact
            bucket[slug] = {"collected_from": timestamp, "slug": slug, "namespace": "enterprise",
                            **({"status": "not_found"} if result is NOT_FOUND else {"result": result})}
            continue
        if recommendations.matches(tool, args_in, result, timestamp):
            recommendation_feed = {"collected_from": timestamp, "tool": tool,
                                   "arguments": args_in, "result": result}
            continue
        q = None
        if tool.endswith("query_kusto"):
            m = re.match(r"\s*// qid:(\d+)", qtext)
            q = by_kql.get(m.group(1)) if m else None
            if q and (norm(qtext) != norm(q["query"]) or args_in.get("database") != q["db"]):
                q = None  # stale/edited query text — ignore
        elif tool.endswith("query_salesforce"):
            q = by_soql.get(norm(qtext))
        if not q:
            continue
        if q["tool"] == "query_kusto":
            if not isinstance(result.get("columns"), list) or not isinstance(result.get("rows"), list):
                print(f"WARNING {q['qid']}: invalid Kusto result; not collected")
                continue
            if len(result["rows"]) >= 1000:
                print(f"WARNING {q['qid']}: hit 1000-row cap; not collected")
                continue
        found[q["qid"]] = {"collected_from": timestamp, "query_file": q["name"], "result": result}

    missing = [q["qid"] for q in qs if q["qid"] not in found]
    day = args.date or dt.date.today().isoformat()
    out = os.path.join(DATA, "raw", day)
    os.makedirs(out, exist_ok=True)
    for qid, payload in found.items():
        with open(os.path.join(out, f"{qid}.json"), "w") as f:
            json.dump(payload, f)
    print(f"Collected {len(found)}/{len(qs)} query results into {out}")
    if recommendation_feed:
        with open(os.path.join(out, "recommendations.json"), "w") as f:
            json.dump(recommendation_feed, f)
        count = len(recommendation_feed["result"]["recommendations"])
        print(f"Collected {count} Salesforce recommendations" +
              ("; WARNING: possible truncation at the limit" if count >= recommendations.LIMIT else ""))
    else:
        print("Salesforce recommendations not found in this session; feed remains unknown or carried forward.")
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
    report_enterprises(owned_slugs(day), out)

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
    if day:
        days = [d for d in days if d <= day]
    for d in reversed(days):
        path = os.path.join(base, d, "13.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            rows = (json.load(f).get("result") or {}).get("rows") or []
        return [r["slug"] for r in rows if r.get("slug")]
    return []


def report_enterprises(slugs, base, list_pending=False):
    collected = {"impact": 0, "settings": 0}
    pending = {}
    for slug in slugs:
        for feed in collected:
            path = os.path.join(base, feed, f"{slug_file(slug)}.json")
            if os.path.isfile(path):
                with open(path) as f:
                    snapshot = json.load(f)
                if snapshot.get("slug") == slug and snapshot.get("namespace") == "enterprise":
                    if snapshot.get("status") == "not_found":
                        print(f"Terminal gap: {slug} ({feed} not found)")
                        continue
                    if enterprise_payload(feed, snapshot.get("result")):
                        collected[feed] += 1
                        continue
            pending.setdefault(slug, []).append(feed)
    print(f"{len(slugs)} owned enterprises · Impact collected {collected['impact']} · "
          f"feature settings collected {collected['settings']} · outstanding enterprises {len(pending)}")
    if list_pending and pending:
        print('Call only the outstanding feeds below (namespace "enterprise"):')
        for slug, feeds in pending.items():
            print(f"{slug}\t{'+'.join(feeds)}")


def cmd_slugs(args):
    day = args.date or dt.date.today().isoformat()
    slugs = owned_slugs(day)
    if not slugs:
        sys.exit("No enterprise slugs collected yet — run query 13, then `collect`.")
    report_enterprises(slugs, os.path.join(DATA, "raw", day), list_pending=True)


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
