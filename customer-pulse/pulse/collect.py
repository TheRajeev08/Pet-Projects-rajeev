"""Collect this run's query results without anyone re-typing them.

* p01 (the SFDC book) runs directly through the Salesforce CLI (`sf`) when it is logged in.
* Revenue MCP results are read from *this* session's events.jsonl, from either
  `tool.execution_start`/`tool.execution_complete` pairs or `postToolUse` hook events
  (both carry the full structuredContent). Only events after the run started whose query
  carries this run's nonce and matches the rendered text exactly (whitespace-normalized)
  are accepted, so results from other sessions, older runs or edited queries are ignored.
"""
import datetime as dt
import json
import os
import shutil
import subprocess

from . import paths
from .queries import RUN_HEADER, norm
from .util import atomic_write_json, read_json

STATE = os.path.expanduser("~/.copilot/session-state")
KUSTO_ROW_CAP = 1000


def events_path(session):
    return os.path.join(STATE, session, "events.jsonl")


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def since_filter(events, since):
    """Drop events stamped before `since` (or without a usable timestamp)."""
    for e in events:
        if since is not None:
            ts = _parse_ts(e.get("timestamp"))
            if ts is None or ts < since:
                continue
        yield e


def iter_events(path, since=None):
    if not os.path.exists(path):
        return

    def lines():
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    yield from since_filter(lines(), since)


def _args(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return {}
    return v if isinstance(v, dict) else {}


def _from_text(text):
    if not isinstance(text, str) or text.startswith("[copilot:elided"):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def tool_results(events):
    """Yield (timestamp, tool_name, args, result) for successful tool calls."""
    starts = {}
    for e in events:
        kind = e.get("type")
        d = e.get("data") or {}
        if kind == "tool.execution_start":
            starts[d.get("toolCallId")] = d
        elif kind == "tool.execution_complete":
            s = starts.pop(d.get("toolCallId"), None)
            if not s or not d.get("success"):
                continue
            res = d.get("result") or {}
            result = res.get("structuredContent")
            if result is None:
                result = _from_text(res.get("content"))
            if result is not None:
                yield e.get("timestamp"), s.get("mcpToolName") or s.get("toolName") or "", _args(s.get("arguments")), result
        elif kind == "hook.start" and d.get("hookType") == "postToolUse":
            inp = d.get("input") or {}
            res = inp.get("toolResult") or {}
            if res.get("resultType") != "success":
                continue
            result = res.get("structuredContent")
            if result is None:
                result = _from_text(res.get("textResultForLlm"))
            if result is not None:
                yield e.get("timestamp"), inp.get("toolName") or "", _args(inp.get("toolArgs")), result


def validate_result(q, result):
    """Return an error string when a result is unusable, else None."""
    if q["tool"] == "query_kusto":
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list) or not isinstance(result.get("columns"), list):
            return "not a Kusto result (columns/rows missing)"
        if len(result["rows"]) >= KUSTO_ROW_CAP:
            return f"hit the {KUSTO_ROW_CAP}-row cap; the result is truncated"
        return None
    if not isinstance(result, dict) or not isinstance(result.get("records"), list):
        return "not a SOQL result (records missing)"
    if result.get("done") is False:
        return "SOQL result is incomplete (done=false)"
    total = result.get("totalSize")
    if isinstance(total, int) and total != len(result["records"]):
        return f"SOQL returned {len(result['records'])} of {total} records"
    return None


def match_events(qs, run, session, events=None):
    """Find this run's results in the session log. Returns {qid: payload} and {qid: error}."""
    since = _parse_ts(run["started_at"])
    events = since_filter(events, since) if events is not None else iter_events(events_path(session), since)
    kusto = {q["qid"]: q for q in qs if q["tool"] == "query_kusto"}
    soql = {norm(q["query"]): q for q in qs if q["tool"] == "query_salesforce"}
    found, errors = {}, {}
    for ts, name, args, result in tool_results(events):
        text = args.get("query") or ""
        q = None
        if name.endswith("query_kusto"):
            m = RUN_HEADER.match(text)
            if not m or m.group(2) != run["run_id"]:
                continue
            q = kusto.get(m.group(1))
            if not q or norm(text) != norm(q["query"]) or (args.get("database") or q["db"]) != q["db"]:
                if q:
                    errors[q["qid"]] = "query text was changed; run the rendered text verbatim"
                continue
        elif name.endswith("query_salesforce"):
            q = soql.get(norm(text))
        if not q:
            continue
        err = validate_result(q, result)
        if err:
            errors[q["qid"]] = err
            continue
        found[q["qid"]] = {"collected_from": ts, "via": "mcp", "result": result}
        errors.pop(q["qid"], None)
    return found, errors


def sf_binary():
    for cand in (shutil.which("sf"), os.path.expanduser("~/.local/node/bin/sf")):
        if cand and os.path.exists(cand):
            return cand
    return None


def run_sf(cfg, soql, timeout=180):
    """Run p01 through the Salesforce CLI. Returns (result, error)."""
    sf = sf_binary()
    if not sf:
        return None, "Salesforce CLI (sf) not installed"
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(sf) + os.pathsep + env.get("PATH", "")
    env.update({"SF_DISABLE_TELEMETRY": "true", "SF_AUTOUPDATE_DISABLE": "true", "SF_HIDE_RELEASE_NOTES": "true"})
    cmd = [sf, "data", "query", "--query", soql, "--json"]
    if cfg.get("sf_target_org"):
        cmd += ["--target-org", cfg["sf_target_org"]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"sf failed: {exc}"
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None, f"sf returned non-JSON output (exit {p.returncode})"
    if out.get("status") != 0:
        return None, f"sf query failed: {out.get('message') or out.get('name') or 'unknown error'}"
    res = out.get("result") or {}
    records = res.get("records") or []
    result = {"done": bool(res.get("done", True)), "totalSize": res.get("totalSize", len(records)), "records": records}
    return result, None


def raw_path(cfg, run, qid):
    return os.path.join(paths.raw_dir(cfg), run["date"], f"{qid}.json")


def write_raw(cfg, run, q, payload):
    doc = {"qid": q["qid"], "query_file": q["name"], "run_id": run["run_id"], **payload}
    atomic_write_json(raw_path(cfg, run, q["qid"]), doc)


def already_collected(cfg, run, qid):
    doc = read_json(raw_path(cfg, run, qid))
    return bool(doc and doc.get("run_id") == run["run_id"])


def collect(cfg, run, qs, session, use_sf=True, events=None):
    """Collect every query for `run`. Returns a report dict."""
    found, errors = match_events(qs, run, session, events=events)
    report = {"run_id": run["run_id"], "date": run["date"], "collected": [], "missing": [], "errors": {}}
    for q in qs:
        payload = found.get(q["qid"])
        if q["tool"] == "query_salesforce" and use_sf and not payload and not already_collected(cfg, run, q["qid"]):
            result, err = run_sf(cfg, q["query"])
            if result is not None:
                err = validate_result(q, result)
            if result is not None and not err:
                payload = {"collected_from": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                           "via": "sf-cli", "result": result}
            else:
                errors[q["qid"]] = err
        if payload:
            write_raw(cfg, run, q, payload)
            report["collected"].append(q["qid"])
        elif already_collected(cfg, run, q["qid"]):
            report["collected"].append(q["qid"])
        else:
            report["missing"].append(q["qid"])
            if q["qid"] in errors:
                report["errors"][q["qid"]] = errors[q["qid"]]
    return report


def ingest_file(cfg, run, q, path):
    """Developer fallback: ingest a result file written by the tool harness (never hand-typed)."""
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    if isinstance(result, dict) and "result" in result and "rows" not in result and "records" not in result:
        result = result["result"]
    err = validate_result(q, result)
    if err:
        raise SystemExit(f"{q['qid']}: {err}")
    write_raw(cfg, run, q, {"collected_from": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                            "via": "file", "result": result})
