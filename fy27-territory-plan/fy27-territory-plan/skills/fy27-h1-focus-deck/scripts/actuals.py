"""Build the Kusto actuals contract that gives the deck its observed dollars.

Sizing potential from list price alone is easy to challenge in a leadership review
("you assumed list; we discount"). So wherever an account already buys something we
prefer *their* effective price. That requires two facts out of `rev_source`:

    salesforce_account_monthly_product_arr_seats_fact   ARR + seats, per product_type
    salesforce_account_consumption_invoices_fact        metered spend, incl. Copilot AIU

Copilot is deliberately read from the consumption fact and not the seat fact: Copilot
is consumption-billed, so it does not appear as a seat product, and `copilot aiu` is
the only place AIU volume is observable.

This script never calls Kusto. It has two modes, which keeps the network step in the
agent's hands and every transform reproducible here:

    actuals.py query <report.json> [runDir]   -> prints the two KQL queries to run
    actuals.py ingest <raw.json> <runDir>     -> writes raw-actuals.json for potential.py

`ingest` accepts the tool output pasted verbatim, in any of the shapes query_kusto
returns (a bare list of rows, {"rows": [...]}, or {"data"/"results"/"Rows": [...]}),
because forcing the agent to reshape JSON by hand is exactly where silent errors get in.

Output (consumed by potential.py):

    {"asOf": "...", "windowMonths": 12,
     "accounts": {"<salesforce_id>": {
         "arr":         [{"product_type","total_arr","license_seats"}],
         "consumption": [{"product_name","charge_amt","billed_units"}],
         "consumptionMonths": 12}},
     "coverage": {...}}
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date

WINDOW_MONTHS = 12
# Kusto rejects very large inline literals, and 251 ids already run ~5.3k chars.
# Chunking keeps the query well inside limits if a bigger book is uploaded later.
CHUNK = 200

ARR_QUERY = """let ids = dynamic([%s]);
salesforce_account_monthly_product_arr_seats_fact
| where salesforce_account_id in (ids)
| where measurement_date >= startofmonth(datetime(%s))
| summarize arg_max(measurement_date, total_arr, license_seats)
    by salesforce_account_id, product_type
| where total_arr > 0 or license_seats > 0
| project salesforce_account_id, product_type, total_arr, license_seats"""

CONSUMPTION_QUERY = """let ids = dynamic([%s]);
let products = dynamic(["copilot","copilot aiu","actions","ghec","ghas","ghsp","ghcs","codespaces","packages","git_lfs","shared_storage"]);
salesforce_account_consumption_invoices_fact
| where salesforce_account_id in (ids)
| where service_month >= startofmonth(datetime(%s))
| where tolower(product_name) in (products)
| summarize charge_amt = sum(charge_amt), billed_units = sum(billed_units),
            months = dcount(service_month)
    by salesforce_account_id, product_name
| where charge_amt > 0 or billed_units > 0
| project salesforce_account_id, product_name, charge_amt, billed_units, months"""


def window_start(as_of):
    """First day of the month WINDOW_MONTHS back, so the window is whole months."""
    y, m = as_of.year, as_of.month - (WINDOW_MONTHS - 1)
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1).isoformat()


def account_ids(report):
    seen, ids = set(), []
    for acct in report.get("accounts", []):
        sid = (acct.get("salesforceId") or "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def emit_queries(report, as_of=None):
    as_of = as_of or date.today()
    start = window_start(as_of)
    ids = account_ids(report)
    out = []
    for i in range(0, len(ids), CHUNK):
        lit = ",".join('"%s"' % s for s in ids[i:i + CHUNK])
        out.append(("arr", ARR_QUERY % (lit, start)))
        out.append(("consumption", CONSUMPTION_QUERY % (lit, start)))
    return ids, start, out


def rows_of(blob):
    """Pull a row list out of whatever envelope the tool handed back."""
    if blob is None:
        return []
    if isinstance(blob, list):
        return [r for r in blob if isinstance(r, dict)]
    if isinstance(blob, dict):
        for key in ("rows", "data", "results", "Rows", "value", "table", "tables"):
            if key in blob:
                return rows_of(blob[key])
        # A dict of column->list (columnar) is the remaining plausible shape.
        cols = {k: v for k, v in blob.items() if isinstance(v, list)}
        if cols and len({len(v) for v in cols.values()}) == 1:
            n = len(next(iter(cols.values())))
            return [{k: cols[k][i] for k in cols} for i in range(n)]
    return []


def pick(row, *names):
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
        for k in row:
            if k.lower() == n.lower() and row[k] is not None:
                return row[k]
    return None


def num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


# The consumption fact carries occasional non-product test rows (e.g. "check :)").
# They are real rows with real charges, so they must be dropped explicitly rather
# than assumed away, or they would land in a leadership deck as a product line.
KNOWN_PRODUCTS = {
    "copilot", "copilot aiu", "actions", "ghec", "ghas", "ghsp", "ghcs",
    "codespaces", "packages", "git_lfs", "shared_storage",
}


def is_product(name):
    return name in KNOWN_PRODUCTS


def ingest(raw):
    """Fold ARR and consumption rows into the per-account contract."""
    accounts = defaultdict(lambda: {"arr": [], "consumption": [], "consumptionMonths": 0})

    for row in rows_of(raw.get("arr")):
        sid = pick(row, "salesforce_account_id", "salesforceAccountId", "salesforce_id")
        if not sid:
            continue
        accounts[str(sid)]["arr"].append({
            "product_type": str(pick(row, "product_type", "productType") or "").strip(),
            "total_arr": num(pick(row, "total_arr", "totalArr", "arr")),
            "license_seats": num(pick(row, "license_seats", "licenseSeats", "seats")),
        })

    skipped = []
    for row in rows_of(raw.get("consumption")):
        sid = pick(row, "salesforce_account_id", "salesforceAccountId", "salesforce_id")
        if not sid:
            continue
        product = str(pick(row, "product_name", "productName", "product") or "").strip().lower()
        if not is_product(product):
            skipped.append(product)
            continue
        rec = accounts[str(sid)]
        rec["consumption"].append({
            "product_name": product,
            "charge_amt": num(pick(row, "charge_amt", "chargeAmt", "charge")),
            "billed_units": num(pick(row, "billed_units", "billedUnits", "units")),
        })
        # Annualising needs the real number of billed months, not the window length:
        # an account that only started consuming in month 10 must not be scaled by 12/12.
        months = int(num(pick(row, "months", "monthCount")) or 0)
        rec["consumptionMonths"] = max(rec["consumptionMonths"], min(months, WINDOW_MONTHS))

    for rec in accounts.values():
        if rec["consumption"] and rec["consumptionMonths"] <= 0:
            rec["consumptionMonths"] = WINDOW_MONTHS

    return dict(accounts), sorted(set(skipped))


def main(argv):
    if len(argv) < 3:
        print(json.dumps({"error": "usage: actuals.py query|ingest <input.json> [runDir]"}))
        return 2
    mode, path = argv[1], argv[2]
    run_dir = argv[3] if len(argv) > 3 else os.path.dirname(os.path.abspath(path))

    with open(path, "r", encoding="utf-8") as fh:
        blob = json.load(fh)

    if mode == "query":
        ids, start, queries = emit_queries(blob)
        payload = {
            "database": "rev_source",
            "accountIds": len(ids),
            "windowStart": start,
            "windowMonths": WINDOW_MONTHS,
            "queries": [{"kind": k, "query": q} for k, q in queries],
        }
        print(json.dumps(payload, indent=2))
        return 0

    if mode == "ingest":
        accounts, skipped = ingest(blob)
        with_arr = sum(1 for a in accounts.values() if any(x["total_arr"] > 0 for x in a["arr"]))
        with_cons = sum(1 for a in accounts.values() if a["consumption"])
        out = {
            "asOf": date.today().isoformat(),
            "windowMonths": WINDOW_MONTHS,
            "accounts": accounts,
            "coverage": {
                "accountsWithData": len(accounts),
                "accountsWithArr": with_arr,
                "accountsWithConsumption": with_cons,
                "skippedProducts": skipped,
            },
        }
        os.makedirs(run_dir, exist_ok=True)
        dest = os.path.join(run_dir, "raw-actuals.json")
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print(json.dumps({"rawActualsPath": dest, **out["coverage"]}))
        return 0

    print(json.dumps({"error": "unknown mode: %s" % mode}))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
