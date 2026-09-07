"""Seller-known facts that Salesforce and SuperDash do not carry.

    overrides.json  (lives in the run directory, alongside crm-context.json)

A territory plan built only from systems of record is wrong in a specific,
predictable way: it knows every deal that has been *entered* and nothing about the
conversation that has not been entered yet. That gap is not noise. The largest
Bucket 1 opportunity in this book existed as a live customer conversation weeks
before it existed as a Salesforce record.

This module is how that knowledge enters the plan without anybody hand-editing an
output file. Two rules make it safe to use:

1. **Overrides supply facts, never rankings.** There is deliberately no "pin this
   account to rank 3". The deck tells leadership its accounts are ranked on
   potential, pipeline, communication and triggers; a forced rank would make that
   sentence a lie. Correct the input and let the ranking move.

2. **An override that matches nothing is an error.** Silent no-ops are how a
   renamed account quietly drops a correction and nobody notices until the number
   is wrong in front of leadership.

Seller-sourced pipeline is tagged `source: "seller"` all the way through to the
deck, because leadership will go looking for it in Salesforce and it will not be
there yet.

Shape:

    {
      "asOf": "2026-08-13",
      "accounts": {
        "Example Account Ltd": {
          "reason": "GHE + GHAS deal agreed in conversation, not yet in Salesforce",
          "pipeline": [
            {"product": "GHE",  "seats": 335,      "quarter": "Q1",
             "stage": "Qualified", "note": "..."},
            {"product": "GHAS", "committers": 285, "quarter": "Q1",
             "stage": "Qualified", "note": "..."}
          ]
        },
        "Direct Customer Inc": {
          "msftOverlap": false,
          "reason": "GitHub direct - no Microsoft involvement"
        },
        "Prospect Co": {
          "engagement": {"twoWay": true, "lastActivity": "2026-08-11",
                         "note": "GHAS conversation in progress"},
          "reason": "..."
        }
      }
    }

Quantities are sized through pricing.json rather than entered as dollars, so a
seller-sourced line is priced on exactly the same basis as a modelled one and
carries the same `basis` label.
"""
import json
import os
import re
import sys
from datetime import date

MONTHS = 12
QUARTERS = ("Q1", "Q2")


def load(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def norm(value):
    """Loose account-name key: case, punctuation and spacing are not identity."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


class Overrides:
    """Resolves seller corrections onto accounts, and refuses to miss silently."""

    def __init__(self, data):
        self.data = data or {}
        self.accounts = self.data.get("accounts", {}) or {}
        self._used = set()

    def __bool__(self):
        return bool(self.accounts)

    def for_account(self, salesforce_id="", name=""):
        """Override record for an account, by Salesforce ID first, then by name."""
        for candidate in (salesforce_id, name):
            if not candidate:
                continue
            if candidate in self.accounts:
                self._used.add(candidate)
                return self.accounts[candidate]
        target = norm(name)
        if not target:
            return None
        for key, value in self.accounts.items():
            if norm(key) == target:
                self._used.add(key)
                return value
        return None

    def unmatched(self):
        return sorted(set(self.accounts) - self._used)

    def check(self, context=""):
        """Raise if any override never matched an account.

        Deliberately fatal. An override exists because somebody knew something the
        systems did not; dropping it quietly is worse than failing the build.
        """
        missing = self.unmatched()
        if missing:
            raise SystemExit(
                "overrides.json: no account matched %s%s. Check the name against the "
                "report, or use the Salesforce ID as the key."
                % (", ".join(repr(m) for m in missing),
                   " (%s)" % context if context else ""))


def size_line(line, rates):
    """Price a seller-sourced pipeline line the same way potential.py prices a modelled one.

    Returns (product, amount, basis, quantity, unit) or None when the line carries
    no quantity this module knows how to price - an unpriceable line is reported,
    never guessed at.
    """
    product = str(line.get("product") or "").strip()
    if line.get("amount") is not None:
        return product, float(line["amount"]), "stated", None, ""

    seats = line.get("seats")
    committers = line.get("committers")

    # A seller may price a SKU this module does not carry a rate for - Secret
    # Protection, for example, sits below the Code Security list price. An explicit
    # per-unit monthly rate keeps the quantity visible on the slide instead of
    # collapsing the line to a bare dollar amount.
    rate_month = line.get("rateMonth")
    if rate_month and (committers or seats):
        qty = float(committers or seats)
        unit = "committers" if committers else "seats"
        return (product, round(qty * float(rate_month) * 12, 2), "stated", qty, unit)

    if product == "GHE" and seats:
        rate, basis = rates.ghe_seat_year()
        return product, round(float(seats) * rate, 2), basis, float(seats), "seats"
    if product == "GHAS" and (committers or seats):
        qty = float(committers or seats)
        rate, basis = rates.ghas_committer_year()
        return product, round(qty * rate, 2), basis, qty, "committers"
    if product == "Copilot" and seats:
        rate, basis = rates.copilot_seat_year()
        return product, round(float(seats) * rate, 2), basis, float(seats), "seats"
    return None


def pipeline_entries(record, rates, h1_start, h1_end):
    """Seller-sourced pipeline for one account, shaped like a CRM opportunity.

    The shape matches crm_context's openPipeline entries exactly so that everything
    downstream - bucket splitting, ranking, the workbook - treats them uniformly and
    only the `source` tag distinguishes them.
    """
    out, unpriced = [], []
    for line in record.get("pipeline", []) or []:
        sized = size_line(line, rates)
        if not sized:
            unpriced.append(line)
            continue
        product, amount, basis, qty, unit = sized
        quarter = str(line.get("quarter") or "Q1").upper()
        close = quarter_close(quarter, h1_start, h1_end)
        qty_text = ("%s %s " % (int(qty), unit)) if qty else ""
        out.append({
            "name": "%s %s(seller-sourced, %s)" % (product, qty_text, quarter),
            "stage": line.get("stage") or "Qualified",
            "amount": amount,
            "closeDate": close,
            "type": line.get("type") or "Upgrade",
            "forecast": line.get("forecast") or "Pipeline",
            "stale": False,
            "inH1": True,
            "inQ1": quarter == "Q1",
            "isRenewal": False,
            "product": product,
            "productBasis": "seller",
            "priceBasis": basis,
            "quantity": qty,
            "unit": unit,
            "quarter": quarter,
            "source": "seller",
            "note": line.get("note") or record.get("reason") or "",
        })
    return out, unpriced


def _activity_scorer():
    """The real activity scorer from the territory-plan skill.

    Imported rather than reimplemented so an overridden account is scored on exactly
    the same curve as every account that came through enrichment normally. A local
    copy of the formula would drift the moment either side changed.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    sibling = os.path.normpath(
        os.path.join(here, "..", "..", "fy27-territory-plan", "scripts"))
    if sibling not in sys.path:
        sys.path.insert(0, sibling)
    try:
        from enrich_activity import HALF_LIFE_DAYS, score_for, tier_for
    except ImportError as exc:
        raise SystemExit(
            "overrides.json: engagement override needs enrich_activity.py from the "
            "fy27-territory-plan skill (looked in %s): %s" % (sibling, exc))
    return score_for, tier_for, HALF_LIFE_DAYS


def apply_engagement(activity, record, as_of):
    """Re-score an account's engagement from a seller-confirmed conversation.

    Counts are only nudged to the minimum consistent with the stated fact: if the
    seller says the customer is talking to us, at least one inbound response exists.
    Anything more specific has to be stated explicitly, because inflating activity
    counts to move a rank is exactly what this module exists to avoid.
    """
    engagement = record.get("engagement") or {}
    if not engagement:
        return None
    score_for, tier_for, _ = _activity_scorer()

    current = dict(activity or {})
    total = int(engagement.get("total") or current.get("total") or 0)
    inbound = int(engagement.get("inbound") or current.get("inbound") or 0)
    meetings = int(engagement.get("meetings") or current.get("meetings") or 0)
    two_way = bool(engagement.get("twoWay", True))

    if two_way and inbound <= 0:
        inbound = 1
        total = max(total + 1, 1)

    last = str(engagement.get("lastActivity") or current.get("lastActivity") or "")
    age = 0
    if last:
        try:
            age = max(0, (as_of - date.fromisoformat(last)).days)
        except ValueError:
            age = 0

    score = score_for(total, meetings, two_way, age)
    return {
        **current,
        "status": "enriched",
        "total": total,
        "inbound": inbound,
        "outbound": max(0, total - inbound),
        "meetings": meetings,
        "lastActivity": last,
        "twoWay": two_way,
        "score": score,
        "tier": tier_for(score, meetings, two_way),
        "source": "seller",
        "reason": (
            "Seller-confirmed: %s. Re-scored on the standard activity curve with "
            "%d activities, %d responded, %d meetings; last activity %s."
            % (engagement.get("note") or record.get("reason") or "active conversation",
               total, inbound, meetings, last or "unknown")),
    }


def quarter_close(quarter, h1_start, h1_end):
    """Last day of the requested fiscal quarter inside the H1 window."""
    if quarter == "Q1":
        year = h1_start.year
        month = h1_start.month + 2
        if month > 12:
            month -= 12
            year += 1
        return "%04d-%02d-30" % (year, month)
    return h1_end.isoformat()


def _main():
    """Validate an overrides file against the account universe before a run uses it.

        python3 overrides.py check <overrides.json> <fy27-territory-plan.json> [focus-accounts.json]

    Catches the failure that matters: a key that matches no account anywhere. The rest
    of the pipeline is fatal on that too, but finding it here costs seconds instead of
    a full re-run, and reports every bad key at once rather than the first.

    Validation is against the full report, not the focus set. An override may correctly
    target an account that did not make the focus 40 - suppressing a misfiled
    opportunity, or asserting a play on a prospect - and that is not an error. Pass the
    focus set as well and those accounts are listed as `outsideFocusSet`, so they are
    visible without being fatal.
    """
    if len(sys.argv) < 4 or sys.argv[1] != "check":
        raise SystemExit(_main.__doc__)

    ov = Overrides(load(sys.argv[2]))
    universe = load(sys.argv[3]) or {}
    rows = universe.get("accounts") or universe.get("focusAccounts") or []
    if not rows:
        raise SystemExit("no accounts found in %s" % sys.argv[3])

    for row in rows:
        ov.for_account(row.get("salesforceId", ""), row.get("name", ""))
    ov.check("overrides.py check")

    outside = []
    if len(sys.argv) > 4:
        focus = load(sys.argv[4]) or {}
        focus_ov = Overrides(load(sys.argv[2]))
        for row in focus.get("accounts") or focus.get("focusAccounts") or []:
            focus_ov.for_account(row.get("salesforceId", ""), row.get("name", ""))
        outside = focus_ov.unmatched()

    matched = []
    for key in sorted(ov.accounts):
        record = ov.accounts[key]
        matched.append({
            "key": key,
            "name": record.get("name") or key,
            "pipelineLines": len(record.get("pipeline") or []),
            "engagement": bool(record.get("engagement")),
            "msftOverlap": record.get("msftOverlap"),
            "inFocusSet": key not in outside,
            "assumptions": [
                v.get("assumption") for v in
                ([record.get("engagement") or {}] + list(record.get("pipeline") or []))
                if isinstance(v, dict) and v.get("assumption")
            ],
        })
    print(json.dumps({"overrides": len(ov.accounts), "allMatched": True,
                      "outsideFocusSet": outside, "accounts": matched}, indent=1))


if __name__ == "__main__":
    _main()
