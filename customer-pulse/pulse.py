#!/usr/bin/env python3
"""Customer Pulse command line. See README.md for setup and REFRESH.md for the daily run."""
import argparse
import datetime as dt
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pulse import collect, digest, model, paths, privacy, queries  # noqa: E402
from pulse.util import LockBusy, RunLock, atomic_write_json, read_json  # noqa: E402


def _today(a):
    return getattr(a, "today", None) or dt.date.today().isoformat()


def _run(session):
    run = queries.load_run(session)
    if not run:
        raise SystemExit("No run for this session. Run `python3 customer-pulse/pulse.py render --new-run` first.")
    return run


def cmd_init_config(a):
    p = os.path.join(paths.home(), "config.json")
    cfg = read_json(p) or {}
    for k in ("owner_sfdc_id", "owner_name", "digest_to", "sf_target_org", "workflow_id", "copilot_home"):
        v = getattr(a, k)
        if v:
            cfg[k] = v
    atomic_write_json(p, cfg, indent=2)
    print(f"Wrote {p}")
    paths.load_config()


def cmd_render(a):
    cfg = paths.load_config()
    session = queries.session_id(a.session)
    run, created = queries.current_run(session, new=a.new_run)
    qs = queries.load(cfg, run["run_id"])
    folder = queries.write_rendered(run, qs)
    out = {"run_id": run["run_id"], "date": run["date"], "session": session, "new_run": created, "folder": folder,
           "queries": []}
    for q in qs:
        ext = "soql" if q["tool"] == "query_salesforce" else "kql"
        out["queries"].append({"qid": q["qid"], "tool": q["tool"], "database": q["db"],
                               "file": os.path.join(folder, f"{q['qid']}.{ext}"), "chars": len(q["query"])})
    print(json.dumps(out, indent=2))


def cmd_collect(a):
    cfg = paths.load_config()
    session = queries.session_id(a.session)
    run = _run(session)
    qs = queries.load(cfg, run["run_id"])
    with RunLock(paths.lock_path()):
        rep = collect.collect(cfg, run, qs, session, use_sf=not a.no_sf)
    print(json.dumps(rep, indent=2))
    return 2 if rep["missing"] else 0


def cmd_ingest_file(a):
    cfg = paths.load_config()
    run = _run(queries.session_id(a.session))
    qs = {q["qid"]: q for q in queries.load(cfg, run["run_id"])}
    if a.qid not in qs:
        raise SystemExit(f"Unknown query id {a.qid}; expected one of {', '.join(sorted(qs))}.")
    with RunLock(paths.lock_path()):
        collect.ingest_file(cfg, run, qs[a.qid], a.file)
    print(f"Ingested {a.qid} for run {run['run_id']}.")


def cmd_build(a):
    cfg = paths.load_config()
    today = _today(a)
    run = queries.load_run(queries.session_id(a.session))
    run_id = run["run_id"] if run else None
    try:
        with RunLock(paths.lock_path()):
            m, st = model.build(cfg, today, run_id)
            model.write(m, st)
            if not a.no_prune:
                model.prune_raw(cfg, today)
    except LockBusy as exc:
        print(exc, file=sys.stderr)
        return 3
    except Exception as exc:  # keep the previous model and record the failure for the banner and email
        model.write_build_error(f"{type(exc).__name__}: {exc}", today, run_id)
        traceback.print_exc()
        return 1
    k = m["kpis"]["overall"]
    new_events = sum(1 for e in m["events"] if e["new"])
    print(json.dumps({
        "status": m["status"], "baseline": m["baseline"], "book_source": m["book_source"],
        "accounts": k["accounts"], "with_products": k["with_products"],
        "events": len(m["events"]), "new_events": new_events, "actions": k["actions"], "new_high": k["new_high"],
        "feeds": {f["feed"]: f"{f['state']} as of {f['as_of']}" for f in m["sources"]},
        "model": paths.model_path(),
    }, indent=2))
    return 0


def cmd_status(a):
    st = read_json(paths.status_path())
    if not st:
        print("No status yet. Run `pulse.py build`.")
        return 1
    print(f"{st['overall'].upper()} · built {st['generated_at']} for {st['date']}")
    if st.get("build_error"):
        print(f"  build error: {st['build_error']}")
    for f in st.get("feeds") or []:
        msg = "; ".join(f["messages"])
        print(f"  {f['label']:<20} {f['state']:<8} as of {f.get('as_of') or '—'}{'  ' + msg if msg else ''}")
    return 0 if st["overall"] == "ok" else 2


def cmd_digest(a):
    cfg = paths.load_config()
    if a.mark_sent:
        meta = read_json(digest.digest_paths(a.date)[1]) if a.date else digest.latest_meta()
        if not meta:
            raise SystemExit("No digest found. Run `pulse.py digest` first.")
        with RunLock(paths.lock_path()):
            at = digest.mark_sent(meta, queries.session_id(a.session), force=a.force)
        print(f"Recorded {len(meta['ids'])} items as notified for {meta['date']} (sent {at}).")
        return 0
    m = read_json(paths.model_path())
    if not m:
        raise SystemExit("No model.json yet. Run `pulse.py build` first.")
    body, meta = digest.build_digest(m, cfg, read_json(paths.status_path()), today=_today(a))
    body_path, _ = digest.write_digest(body, meta)
    out = {"file": body_path, "subject": meta["subject"], "to": meta["to"], "counts": meta["counts"],
           "send": digest.needs_send(meta)}
    if out["send"]:
        out["send_with"] = {"tool": "workiq do_action", "actionUrl": "/me/sendMail",
                            "jsonBody": f"the JSON object in {body_path}"}
    else:
        out["reason"] = "Today's email was already sent and nothing new has appeared since. Don't send it again."
    print(json.dumps(out, indent=2))
    return 0


def cmd_privacy_scan(a):
    cfg = paths.load_config(required=False)
    findings, n_terms = privacy.scan(cfg, all_files=a.all, targets=a.paths)
    if n_terms == 0:
        print("Warning: no local account names found (no collected data yet); only ids are checked.", file=sys.stderr)
    for path, no, kind, match in findings:
        print(f"{path}:{no}: {kind}: {match}")
    print(f"privacy-scan: {len(findings)} finding(s) using {n_terms} local account terms.")
    return 1 if findings else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pulse.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-config", help="create or update ~/CustomerPulse/config.json")
    s.add_argument("--owner-id", dest="owner_sfdc_id")
    s.add_argument("--owner-name", dest="owner_name")
    s.add_argument("--digest-to", dest="digest_to")
    s.add_argument("--sf-target-org", dest="sf_target_org")
    s.add_argument("--workflow-id", dest="workflow_id")
    s.add_argument("--copilot-home", dest="copilot_home")
    s.set_defaults(fn=cmd_init_config)

    s = sub.add_parser("render", help="start (or reuse) a run and write the queries to run")
    s.add_argument("--new-run", action="store_true")
    s.add_argument("--session")
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("collect", help="collect this run's results (Salesforce CLI + this session's MCP results)")
    s.add_argument("--session")
    s.add_argument("--no-sf", action="store_true", help="do not run p01 through the Salesforce CLI")
    s.set_defaults(fn=cmd_collect)

    s = sub.add_parser("ingest-file", help="developer fallback: ingest a saved tool result file")
    s.add_argument("--qid", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--session")
    s.set_defaults(fn=cmd_ingest_file)

    s = sub.add_parser("build", help="compute model.json and status.json")
    s.add_argument("--today")
    s.add_argument("--session")
    s.add_argument("--no-prune", action="store_true")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("status", help="print source freshness")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("digest", help="write today's email body, or record it as sent")
    s.add_argument("--mark-sent", action="store_true")
    s.add_argument("--force", action="store_true", help="with --mark-sent: skip the session-log check")
    s.add_argument("--date")
    s.add_argument("--today")
    s.add_argument("--session")
    s.set_defaults(fn=cmd_digest)

    s = sub.add_parser("privacy-scan", help="block staged lines containing local account names or Salesforce ids")
    s.add_argument("--all", action="store_true", help="scan every tracked and untracked file instead of the staged diff")
    s.add_argument("paths", nargs="*")
    s.set_defaults(fn=cmd_privacy_scan)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
