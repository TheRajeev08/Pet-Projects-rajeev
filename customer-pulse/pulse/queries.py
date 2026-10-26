"""Query files: load, validate headers and render them with config plus a per-run nonce.

Kusto files start with `// qid:pNN db:<database> — <description>`. Rendering inserts
`run:<nonce>` into that header so `collect` only accepts results produced for this run.
SOQL has no comment syntax, so p01 is matched on its exact normalized text instead.
"""
import datetime as dt
import glob
import os
import re
import secrets

from . import paths
from .util import atomic_write_json, atomic_write_text, read_json

HEADER = re.compile(r"^// qid:(p\d\d) db:(\w+) — .+$")
RUN_HEADER = re.compile(r"^\s*// qid:(p\d\d) run:([A-Za-z0-9]+) db:(\w+) ")
SESSION_REUSE_SECONDS = 12 * 3600
SFDC_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
SAFE_NAME = re.compile(r"^[^\"'\\\n\r{}]+$")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _runs_dir():
    return os.path.join(paths.state_dir(), "runs")


def session_id(explicit=None):
    return explicit or os.environ.get("COPILOT_AGENT_SESSION_ID") or "manual"


def current_run(session, new=False, now=None):
    """Return (run, created). A run is reused by the same session for 12h unless `new`."""
    now = now or dt.datetime.now(dt.timezone.utc)
    path = os.path.join(_runs_dir(), f"{_safe(session)}.json")
    run = read_json(path)
    if run and not new:
        started = dt.datetime.fromisoformat(run["started_at"])
        if (now - started).total_seconds() < SESSION_REUSE_SECONDS:
            return run, False
    run = {
        "run_id": secrets.token_hex(4),
        "session": session,
        "started_at": now.replace(microsecond=0).isoformat(),
        "date": now.astimezone().date().isoformat(),
    }
    atomic_write_json(path, run, indent=2)
    atomic_write_json(os.path.join(paths.state_dir(), "last_run.json"), run, indent=2)
    return run, True


def load_run(session):
    return read_json(os.path.join(_runs_dir(), f"{_safe(session)}.json"))


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def _check_config(cfg):
    if not SFDC_ID.match(cfg.get("owner_sfdc_id") or ""):
        raise SystemExit("owner_sfdc_id must be a 15/18-character Salesforce id.")
    if not SAFE_NAME.match(cfg.get("owner_name") or ""):
        raise SystemExit("owner_name must not contain quotes, backslashes, braces or newlines.")
    days = int(cfg.get("history_days") or 35)
    if not 7 <= days <= 90:
        raise SystemExit("history_days must be between 7 and 90.")
    return days


def load(cfg, run_id, query_dir=None):
    query_dir = query_dir or paths.QUERY_DIR
    days = _check_config(cfg)
    with open(os.path.join(query_dir, "_owned.kql"), encoding="utf-8") as f:
        owned = f.read().strip()
    out = []
    for path in sorted(glob.glob(os.path.join(query_dir, "p*"))):
        name = os.path.basename(path)
        qid = name.split("_", 1)[0]
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        if name.endswith(".soql"):
            tool, db = "query_salesforce", None
        elif name.endswith(".kql"):
            m = HEADER.match(text.splitlines()[0])
            if not m or m.group(1) != qid:
                raise SystemExit(f"{name}: first line must be '// qid:{qid} db:<database> — <description>'")
            tool, db = "query_kusto", m.group(2)
            text = text.replace(f"// qid:{qid} db:", f"// qid:{qid} run:{run_id} db:", 1)
            text = text.replace("{{OWNED_LET}}", owned)
        else:
            continue
        text = (text.replace("{{OWNER_ID}}", cfg["owner_sfdc_id"])
                    .replace("{{OWNER_NAME}}", cfg["owner_name"])
                    .replace("{{DAYS}}", str(days)))
        if "{{" in text:
            raise SystemExit(f"{name}: unresolved placeholder in rendered query")
        if len(text) > 10000:
            raise SystemExit(f"{name}: rendered query is {len(text)} characters; the Revenue MCP limit is 10000")
        out.append({"qid": qid, "name": name, "tool": tool, "db": db, "query": text})
    return out


def write_rendered(run, qs):
    folder = os.path.join(paths.state_dir(), "rendered", run["run_id"])
    for q in qs:
        ext = "soql" if q["tool"] == "query_salesforce" else "kql"
        atomic_write_text(os.path.join(folder, f"{q['qid']}.{ext}"), q["query"] + "\n")
    return folder
