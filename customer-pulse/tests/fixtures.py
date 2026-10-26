"""Synthetic data for the Customer Pulse tests.

Ids look like "acc1" and every name is invented, so nothing here can match a real account. Results
mirror what the Revenue MCP returns: column names as strings, each row an object keyed by column,
dynamic columns base64-encoded and '|'-delimited points.
"""
import base64
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pulse import paths  # noqa: E402

TH = dict(paths.DEFAULTS["thresholds"])
DAY1 = "2026-10-26"
DAY2 = "2026-10-27"


def b64(v):
    return base64.b64encode(json.dumps(v).encode()).decode()


def d8(day):
    return day.replace("-", "")


def days(start, end):
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out = []
    while a <= b:
        out.append(a.isoformat())
        a += dt.timedelta(days=1)
    return out


def pipe(*vals):
    return "|".join("" if v is None else str(v) for v in vals)


def kusto(columns, rows):
    return {"columns": list(columns), "rows": [dict(zip(columns, r)) for r in rows]}


def p02(runs, meta):
    """runs: {acct: [(product_type, start, end, seats, arr, skus)]}; meta: [(day, n, s)]."""
    rows = [[acct, b64([pipe(pt, d8(s), d8(e), seats, arr, skus) for pt, s, e, seats, arr, skus in lst])]
            for acct, lst in runs.items()]
    rows.append(["__meta__", b64([pipe("__rows__", d8(d), d8(d), n, s, "") for d, n, s in meta])])
    return kusto(["account_id", "runs"], rows)


def series(items):
    """items: [(acct, series, [(day, v1, v2, ...)])]."""
    rows = [[acct, s, b64([pipe(d8(p[0]), *p[1:]) for p in pts])] for acct, s, pts in items]
    return kusto(["salesforce_account_id", "series", "pts"], rows)


def attributes(recs):
    cols = sorted({k for r in recs for k in r})
    return kusto(cols, [[r.get(c) for c in cols] for r in recs])


def universe(recs):
    return {"done": True, "totalSize": len(recs), "records": recs}


def sf_account(acct, name, parent=None):
    return {"Id": acct, "Name": name, "Type": "Customer", "Industry": "Software", "BillingCountry": "India",
            "Parent": {"Name": parent} if parent else None, "CreatedDate": "2020-01-01T00:00:00.000+0000",
            "LastActivityDate": "2026-10-20"}


def write_raw(home, day, qid, result, run_id="run1", via="mcp"):
    path = os.path.join(home, "data", "raw", day, f"{qid}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"qid": qid, "query_file": qid, "run_id": run_id, "collected_from": f"{day}T01:00:00+00:00",
                   "via": via, "result": result}, f)


def meta(day_list, n=1000, s=100000):
    return [(d, n, s) for d in day_list]


NAMES = {
    "acc1": "Quillfeather Analytics Pvt Ltd",
    "acc2": "Nimbus & <Orbit> Ltd",
    "acc3": "Larkspur Freight",
    "acc4": "Tessellate Robotics Inc",
    "acc5": "Driftwood Ceramics",
    "acc6": "Orchid Lattice Systems",
    "acc9": "Sandbox Trial Account",
}
W1 = days("2026-10-12", "2026-10-25")


def write_day1(home):
    """A book of six accounts on 2026-10-26 (one is a test account).

    acc1: volume GHE 100 seats with 120 billable users (overage), legacy GHAS moved to Secret Protection +
          Code Security on 10-21 (migration), 20 GHAS committers on 50 seats, Code Quality in use.
    acc2: GHEC 200 -> 150 on 10-25 (High drop), renews 2026-12-25.
    acc3: metered GHE, 80 -> 95 users on 10-21; 10-25 is a partial load (held back).
    acc4: GHE 400, Copilot Business 50 with health Red and 20 of 50 users at risk.
    acc5: in Salesforce, no product data. acc9: test account.
    """
    write_raw(home, DAY1, "p01", universe([sf_account(a, NAMES[a]) for a in ("acc1", "acc2", "acc3", "acc4", "acc5", "acc9")]),
              via="sf-cli")
    write_raw(home, DAY1, "p02", p02({
        "acc1": [("GitHub Enterprise", "2026-10-12", "2026-10-25", 100, 21000, "GHE Seat"),
                 ("Advanced Security", "2026-10-12", "2026-10-20", 50, 24000, "GHAS Seat"),
                 ("GHSP", "2026-10-21", "2026-10-25", 50, 9000, "Secret Protection"),
                 ("GHCS", "2026-10-21", "2026-10-25", 50, 15000, "Code Security")],
        "acc2": [("GitHub Enterprise Cloud", "2026-10-12", "2026-10-24", 200, 42000, "GHEC Seat"),
                 ("GitHub Enterprise Cloud", "2026-10-25", "2026-10-25", 150, 31500, "GHEC Seat")],
        "acc4": [("GitHub Enterprise", "2026-10-12", "2026-10-25", 400, 84000, "GHE Seat")],
        "acc9": [("GitHub Enterprise", "2026-10-12", "2026-10-25", 10, 2100, "GHE Seat")],
    }, meta(W1)))
    metered = [(d, 80 if d < "2026-10-21" else 95, 60, 35, 95) for d in W1]
    write_raw(home, DAY1, "p03", series([
        ("acc1", "volume", [("2026-10-15", 110), ("2026-10-22", 120)]),
        ("acc1", "engagement", [("2026-10-25", 90, 100)]),
        ("acc3", "metered", metered),
        ("__meta__", "metered_rows", meta(W1[:-1]) + [("2026-10-25", 200, 20000)]),
        ("__meta__", "volume_rows", [("2026-10-15", 1000, 100000), ("2026-10-22", 1000, 100000)]),
    ]))
    write_raw(home, DAY1, "p04", series([
        ("acc4", "health", [(d, 50, 20) for d in W1]),
        ("acc4", "health_latest", [("2026-10-25", 50, 20, 5, 4, 3, 2, 0.9, 60, 0.8, 40, 50, 45, "Red")]),
        ("acc4", "seats", [("2026-08-31", 50, 0, 0), ("2026-09-30", 50, 0, 0)]),
        ("__meta__", "health_rows", meta(W1)),
        ("__meta__", "seat_totals", [("2026-08-31", 1000, 100000), ("2026-09-30", 1000, 100000)]),
    ]))
    write_raw(home, DAY1, "p05", series([
        ("acc1", "ghas", [(d, 20, 15, 18) for d in W1]),
        ("acc1", "code_quality", [("2026-10-24", 5, 3, 1, 40, 12, 4, 6, 2)]),
        ("__meta__", "ghas_rows", meta(W1)),
    ]))
    base = {"measurement_date": "2026-10-24T00:00:00Z", "is_test_account": False, "sales_segment": "Enterprise"}
    write_raw(home, DAY1, "p06", attributes([
        dict(base, salesforce_account_id="acc1", salesforce_account_name=NAMES["acc1"], latest_total_arr=69000,
             next_renewal_date="2027-06-30T00:00:00Z", latest_license_adoption_pct=120.0),
        dict(base, salesforce_account_id="acc2", salesforce_account_name=NAMES["acc2"], latest_total_arr=31500,
             next_renewal_date="2026-12-25T00:00:00Z"),
        dict(base, salesforce_account_id="acc3", salesforce_account_name=NAMES["acc3"], latest_total_arr=12000),
        dict(base, salesforce_account_id="acc4", salesforce_account_name=NAMES["acc4"], latest_total_arr=110000,
             latest_number_copilot_assigned_users=50, latest_number_copilot_engaged_1d28_users=22),
        dict(base, salesforce_account_id="acc9", salesforce_account_name=NAMES["acc9"], is_test_account=True),
    ]))


W2 = days("2026-10-12", "2026-10-26")


def write_day2(home):
    """2026-10-27: acc5 left the book, acc6 joined, acc4 GHE 400 -> 420 on 10-26. Only p01/p02 refreshed."""
    write_raw(home, DAY2, "p01", universe([sf_account(a, NAMES[a]) for a in ("acc1", "acc2", "acc3", "acc4", "acc6", "acc9")]),
              run_id="run2", via="sf-cli")
    write_raw(home, DAY2, "p02", p02({
        "acc1": [("GitHub Enterprise", "2026-10-12", "2026-10-26", 100, 21000, "GHE Seat"),
                 ("Advanced Security", "2026-10-12", "2026-10-20", 50, 24000, "GHAS Seat"),
                 ("GHSP", "2026-10-21", "2026-10-26", 50, 9000, "Secret Protection"),
                 ("GHCS", "2026-10-21", "2026-10-26", 50, 15000, "Code Security")],
        "acc2": [("GitHub Enterprise Cloud", "2026-10-12", "2026-10-24", 200, 42000, "GHEC Seat"),
                 ("GitHub Enterprise Cloud", "2026-10-25", "2026-10-26", 150, 31500, "GHEC Seat")],
        "acc4": [("GitHub Enterprise", "2026-10-12", "2026-10-25", 400, 84000, "GHE Seat"),
                 ("GitHub Enterprise", "2026-10-26", "2026-10-26", 420, 88200, "GHE Seat")],
        "acc9": [("GitHub Enterprise", "2026-10-12", "2026-10-26", 10, 2100, "GHE Seat")],
    }, meta(W2)), run_id="run2")


def write_config(home, **extra):
    cfg = {"owner_sfdc_id": "ownerIdAAAAAAAA", "owner_name": "Test Owner", "digest_to": "me@example.com",
           "copilot_home": os.path.join(home, "copilot")}
    cfg.update(extra)
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


class HomeCase(unittest.TestCase):
    """Runs each test against a fresh temporary CP_HOME."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cp-test-")
        self._old_home = os.environ.get("CP_HOME")
        os.environ["CP_HOME"] = self.home
        write_config(self.home)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("CP_HOME", None)
        else:
            os.environ["CP_HOME"] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def cfg(self):
        return paths.load_config()


def start_complete(call_id, ts, tool, args, result, success=True):
    """The tool.execution_start / tool.execution_complete pair shape."""
    return [
        {"type": "tool.execution_start", "timestamp": ts,
         "data": {"toolCallId": call_id, "toolName": f"revenue-mcp-server-{tool}", "mcpToolName": tool, "arguments": args}},
        {"type": "tool.execution_complete", "timestamp": ts,
         "data": {"toolCallId": call_id, "success": success,
                  "result": {"content": json.dumps(result), "structuredContent": result}}},
    ]


def hook(ts, tool_name, args, result, ok=True, elided=False):
    """The postToolUse hook shape."""
    text = "[copilot:elided 12345 chars]" if elided else json.dumps(result)
    return {"type": "hook.start", "timestamp": ts,
            "data": {"hookType": "postToolUse",
                     "input": {"toolName": tool_name, "toolArgs": args,
                               "toolResult": {"resultType": "success" if ok else "failure",
                                              "structuredContent": None if elided else result,
                                              "textResultForLlm": text}}}}
