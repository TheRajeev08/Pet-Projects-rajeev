"""Bridge Salesforce CRM context (Microsoft TPID overlap + open pipeline) into the plan.

    python3 crm_context.py query <report.json> [runDir]
    python3 crm_context.py ingest <raw.json> <runDir> [--as-of YYYY-MM-DD]

Two modes, matching the actuals.py contract, because this agent cannot reach
Salesforce directly: `query` prints the SOQL to run through the Revenue MCP
`query_salesforce` tool, and `ingest` folds whatever came back into
`crm-context.json` for rank.py and exec_deck.py to consume.

`raw.json` is {"accounts": <tool output>, "opportunities": <tool output>} where
each value is whatever `query_salesforce` returned — the row extraction is
tolerant of the usual envelopes.

Output shape:
    {"accounts": {"<salesforceId>": {
        "tpids": ["54839153"],
        "msftOverlap": true,
        "openPipeline": [{"name","stage","amount","closeDate","type","forecast","stale","inH1"}],
        "h1PipelineValue": 46381.0,      # non-renewal, dated inside H1, only
        "h1RenewalValue": 0.0,
        "stalePipelineValue": 0.0,
        "bestStage": "Determined Need"}},
     "coverage": {...}, "window": {...}}

Renewals are tracked but held apart from `h1PipelineValue`: the targets this plan
is measured against are net-new/expansion, so counting a renewal towards them
would overstate coverage.
"""
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from actuals import num, pick, rows_of  # noqa: E402  (shared envelope handling)
import overrides  # noqa: E402
from potential import Rates  # noqa: E402

CHUNK = 200

# GitHub's fiscal year mirrors Microsoft's: FY27 H1 = Jul-Dec 2026. Deriving the
# window from a date rather than hardcoding it keeps the script usable next half.
def fiscal_h1(as_of):
    """(fyLabel, h1Start, h1End, q1End) for the fiscal half that `as_of` sits in."""
    fy = as_of.year + 1 if as_of.month >= 7 else as_of.year
    start_year = fy - 1
    if as_of.month >= 7:
        h1 = (date(start_year, 7, 1), date(start_year, 12, 31), date(start_year, 9, 30))
    else:
        # Jan-Jun is H2; the "current" half for planning purposes is still the next H1.
        h1 = (date(fy, 7, 1), date(fy, 12, 31), date(fy, 9, 30))
    return "FY%d" % (fy % 100), h1[0], h1[1], h1[2]


ACCOUNT_QUERY = """SELECT Id, Name, Industry, MSFT_TPID__c, MSFT_All_TPIDs__c, MS_Sales_TPID_Best_Match__c,
MsftOwnerName__c, MsftOwnerRole__c, Microsoft_Involvement__c
FROM Account WHERE Id IN (%s)"""

OPPORTUNITY_QUERY = """SELECT Id, Name, AccountId, StageName, Amount, CloseDate, Type,
ForecastCategoryName, NextStep
FROM Opportunity WHERE IsClosed = false AND AccountId IN (%s)
ORDER BY Amount DESC NULLS LAST"""

RENEWAL_TYPES = {"renewal"}

# Opportunity name -> product. Salesforce carries no product field on the Opportunity
# here, so the name is the only signal, and it is a good one: the naming convention is
# consistent enough to classify on. Order matters - GHAS before GHE, because "GitHub
# Advanced Security" contains neither "GHE" nor "Enterprise" but a combined name might.
PRODUCT_PATTERNS = (
    ("GHAS", r"\bghas\b|advanced\s+security|code\s+security|secret\s+protection"),
    ("Copilot", r"copilot|\bghcp\b"),
    ("Actions", r"\bactions?\b"),
    ("Codespaces", r"codespace"),
    ("Code Quality", r"code\s+quality"),
    ("GHE", r"\bghe\b|\bghec\b|github\s+enterprise|enterprise\s+(cloud|server)"),
)

# Opportunity Type -> product, used only when the name says nothing. "Metered" is
# consumption by definition, so it is safe to place in Bucket 2. Services carries no
# product ARR at all and must never land in either bucket.
TYPE_PRODUCT = {
    "metered": "Consumption",
    "services": "Services",
}

# This book's opportunity names follow "<Account> <N> Seat <FY_Q> <Type>". Seat-based
# business here is GitHub Enterprise: of the 34 seat-priced opportunities, 32 land at
# or below GHE list ($252/seat/yr), and the accounts carrying them hold no other
# product contract. Inferring GHE is therefore evidence-based rather than a guess --
# but it is a weaker signal than an explicit product name, so it is recorded with its
# own basis label and footnoted wherever the number appears.
SEAT_NAMING = re.compile(r"\b\d+\s+seats?\b", re.I)


def classify_product(name, otype):
    """(product, basis) for an opportunity. Never guesses into a quota-bearing bucket.

    An opportunity that cannot be classified returns "Unclassified" rather than
    defaulting to a product. Defaulting is how a deal quietly becomes coverage for a
    target it has nothing to do with.
    """
    text = str(name or "")
    for product, pattern in PRODUCT_PATTERNS:
        if re.search(pattern, text, re.I):
            return product, "name"
    mapped = TYPE_PRODUCT.get(str(otype or "").strip().lower())
    if mapped:
        return mapped, "type"
    if SEAT_NAMING.search(text):
        return "GHE", "inferred-seat-naming"
    return "Unclassified", "none"


# Stages late enough that the deal is a credible half-commit rather than an aspiration.
# Used only to rank, never to inflate value.
ADVANCED_STAGES = {
    "verbal agreement": 1.0,
    "proposal & negotiation": 0.9,
    "business selected": 0.8,
    "determined need": 0.6,
    "manage & optimize": 0.5,
    "qualified": 0.4,
    "problem identification": 0.3,
}


def account_ids(report):
    seen, ids = set(), []
    for acct in report.get("accounts", []):
        sid = (acct.get("salesforceId") or "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def emit_queries(report):
    ids = account_ids(report)
    out = []
    for i in range(0, len(ids), CHUNK):
        lit = ",".join("'%s'" % s for s in ids[i:i + CHUNK])
        out.append(("accounts", ACCOUNT_QUERY % lit))
        out.append(("opportunities", OPPORTUNITY_QUERY % lit))
    return ids, out


MSFT_TIERS = {
    1: "Co-sell led",
    2: "Partner led",
    3: "Direct",
}


def msft_tier(has_tpid, owner_name):
    """Three tiers of Microsoft engagement, replacing a bare TPID boolean.

    A TPID on its own is close to the default state of this book - 142 of 251 accounts
    carry one - so reporting "has TPID" as Microsoft overlap overstates co-sell badly.
    A *named* Microsoft seller is the scarce signal: only 13 accounts in the book have
    one, and every one of those also carries a TPID.

      tier 1  TPID + named AM/Specialist  -> a person on the Microsoft side to work with
      tier 2  TPID only                   -> partner-led, no named Microsoft counterpart
      tier 3  neither                      -> GitHub direct

    Tiering keys off the *presence* of a named owner rather than parsing the role,
    because MsftOwnerRole__c is free text and dirty: it holds "AE", "ACCOUNT EXECUTIVE",
    "Account Exective", and on one record an email address. Role is carried for display
    only and never drives the tier.
    """
    if has_tpid and owner_name:
        return 1
    if has_tpid:
        return 2
    return 3


def split_tpids(*values):
    """MSFT_All_TPIDs__c is a comma-joined string; the others are single ids."""
    found = []
    for value in values:
        if not value:
            continue
        for part in re.split(r"[,;\s]+", str(value)):
            part = part.strip()
            if part and part.isdigit() and part not in found:
                found.append(part)
    return found


def stage_weight(stage):
    return ADVANCED_STAGES.get(str(stage or "").strip().lower(), 0.3)


def apply_overrides(accounts, ov, rates, h1_start, h1_end):
    """Fold seller-known facts into the CRM picture.

    Runs after the Salesforce rows are in place so an override always wins over the
    system of record - that is the point of it. Every change is recorded on the
    account so the workbook can show what was corrected and why.
    """
    if not ov:
        return {"pipelineAccounts": 0, "pipelineValue": 0.0, "overlapCleared": 0,
                "unpriced": []}

    by_name = {norm_name(rec.get("name")): sid for sid, rec in accounts.items()}
    summary = {"pipelineAccounts": 0, "pipelineValue": 0.0, "overlapCleared": 0,
               "unpriced": []}

    for key in list(ov.accounts):
        record = ov.accounts[key]
        sid = key if key in accounts else by_name.get(norm_name(key))
        if not sid:
            continue
        ov.for_account(sid, accounts[sid].get("name", ""))
        rec = accounts[sid]
        reason = record.get("reason") or ""

        if record.get("msftOverlap") is False and rec.get("msftOverlap"):
            rec["msftOverlap"] = False
            rec["suppressedTpids"] = rec.get("tpids", [])
            rec["tpids"] = []
            rec["msftTier"] = 3
            rec["msftTierSource"] = "seller"
            rec.setdefault("overrides", []).append(
                {"field": "msftOverlap", "value": False, "reason": reason})
            summary["overlapCleared"] += 1

        if record.get("msftOverlap") is True and not rec.get("msftOverlap"):
            # Salesforce carries no TPID for this account but the seller is working it
            # jointly with the Microsoft team. Assert the overlap and label its source,
            # so the deck never implies a TPID exists where one does not.
            rec["msftOverlap"] = True
            rec["msftOverlapSource"] = "seller"
            asserted_tpid = record.get("tpid")
            rec["tpids"] = [asserted_tpid] if asserted_tpid else []
            rec["msftTier"] = msft_tier(True, rec.get("msftOwner"))
            rec["msftTierSource"] = "seller"
            rec.setdefault("overrides", []).append(
                {"field": "msftOverlap", "value": True, "reason": reason})
            summary["overlapAsserted"] = summary.get("overlapAsserted", 0) + 1

        if record.get("msftCoSell") is True:
            # Named on the co-sell slide regardless of focus-set membership. Used for
            # accounts the seller is working with Microsoft that carry no product
            # signal yet, so they cannot earn a rank.
            rec["msftCoSell"] = True
            rec["msftCoSellReason"] = record.get("coSellReason") or reason
            # A seller working an account jointly with a named Microsoft counterpart is
            # tier 1 by definition, whatever Salesforce holds. The tier is asserted, the
            # *source* is recorded as seller, and the Salesforce gap is kept visible so
            # it can be raised as a data-quality fix rather than quietly papered over.
            rec["msftTier"] = 1
            rec["msftTierSource"] = "seller"
            gaps = []
            if not rec.get("tpids"):
                gaps.append("no TPID on the Salesforce record")
            if not rec.get("msftOwner"):
                gaps.append("no Microsoft owner named on the Salesforce record")
            if gaps:
                rec["msftDataGap"] = " and ".join(gaps)
                summary["msftDataGaps"] = summary.get("msftDataGaps", 0) + 1

        # A Salesforce opportunity filed against the wrong account is not pipeline for
        # that account. Suppression removes it and backs its value out of the H1
        # figure, so the deal can be re-stated on the account that actually owns it
        # without being counted twice.
        for fragment in record.get("suppressOpportunities", []) or []:
            needle = str(fragment).strip().lower()
            if not needle:
                continue
            kept, dropped = [], []
            for opp in rec.get("openPipeline", []):
                (dropped if needle in str(opp.get("name", "")).lower() else kept).append(opp)
            if not dropped:
                continue
            rec["openPipeline"] = kept
            removed = sum(float(o.get("amount") or 0) for o in dropped if o.get("inH1")
                          and not o.get("isRenewal"))
            removed_q1 = sum(float(o.get("amount") or 0) for o in dropped if o.get("inQ1")
                             and not o.get("isRenewal"))
            rec["h1PipelineValue"] = max(0.0, rec.get("h1PipelineValue", 0.0) - removed)
            rec["q1PipelineValue"] = max(0.0, rec.get("q1PipelineValue", 0.0) - removed_q1)
            rec["suppressedOpportunities"] = rec.get("suppressedOpportunities", []) + [
                {"name": o.get("name"), "amount": o.get("amount")} for o in dropped]
            # bestStage may have come from a dropped opportunity, so recompute it.
            rec["bestStageWeight"], rec["bestStage"] = 0.0, ""
            for opp in kept:
                weight = stage_weight(opp.get("stage") or "")
                if weight > rec["bestStageWeight"]:
                    rec["bestStageWeight"], rec["bestStage"] = weight, opp.get("stage") or ""
            rec.setdefault("overrides", []).append(
                {"field": "suppressOpportunities", "value": round(removed, 2),
                 "reason": reason})
            summary["opportunitiesSuppressed"] = (
                summary.get("opportunitiesSuppressed", 0) + len(dropped))

        entries, unpriced = overrides.pipeline_entries(record, rates, h1_start, h1_end)
        summary["unpriced"].extend(
            {"account": rec.get("name", ""), "line": line} for line in unpriced)
        if not entries:
            continue
        rec["openPipeline"].extend(entries)
        added = sum(e["amount"] for e in entries)
        rec["h1PipelineValue"] += added
        rec["q1PipelineValue"] = rec.get("q1PipelineValue", 0.0) + sum(
            e["amount"] for e in entries if e.get("inQ1"))
        rec["sellerPipelineValue"] = rec.get("sellerPipelineValue", 0.0) + added
        for entry in entries:
            weight = stage_weight(entry["stage"])
            if weight > rec["bestStageWeight"]:
                rec["bestStageWeight"] = weight
                rec["bestStage"] = entry["stage"]
        rec.setdefault("overrides", []).append(
            {"field": "pipeline", "value": round(added, 2), "reason": reason})
        summary["pipelineAccounts"] += 1
        summary["pipelineValue"] += added

    summary["pipelineValue"] = round(summary["pipelineValue"], 2)
    return summary


def norm_name(value):
    return overrides.norm(value)


def ingest(raw, as_of, ov=None, rates=None):
    _, h1_start, h1_end, q1_end = fiscal_h1(as_of)
    accounts = {}

    for row in rows_of(raw.get("accounts")):
        sid = pick(row, "Id", "id")
        if not sid:
            continue
        tpids = split_tpids(
            pick(row, "MSFT_TPID__c"),
            pick(row, "MSFT_All_TPIDs__c"),
            pick(row, "MS_Sales_TPID_Best_Match__c"),
        )
        owner = str(pick(row, "MsftOwnerName__c") or "").strip()
        role = str(pick(row, "MsftOwnerRole__c") or "").strip()
        accounts[str(sid)] = {
            "name": pick(row, "Name", "name") or "",
            "industry": pick(row, "Industry", "industry") or "",
            "tpids": tpids,
            "msftOverlap": bool(tpids),
            "msftOwner": owner,
            "msftOwnerRole": role,
            "msftInvolvement": str(pick(row, "Microsoft_Involvement__c") or "").strip(),
            "msftTier": msft_tier(bool(tpids), owner),
            "msftTierSource": "salesforce" if (tpids or owner) else "",
            "openPipeline": [],
            "h1PipelineValue": 0.0,
            "q1PipelineValue": 0.0,
            "h1RenewalValue": 0.0,
            "stalePipelineValue": 0.0,
            "bestStage": "",
            "bestStageWeight": 0.0,
        }

    stale_count = 0
    for row in rows_of(raw.get("opportunities")):
        sid = pick(row, "AccountId", "accountId")
        if not sid:
            continue
        rec = accounts.setdefault(str(sid), {
            "name": "", "tpids": [], "msftOverlap": False, "openPipeline": [],
            "msftOwner": "", "msftOwnerRole": "", "msftInvolvement": "",
            "msftTier": 3, "msftTierSource": "",
            "h1PipelineValue": 0.0, "q1PipelineValue": 0.0, "h1RenewalValue": 0.0,
            "stalePipelineValue": 0.0,
            "bestStage": "", "bestStageWeight": 0.0,
        })
        close = str(pick(row, "CloseDate", "closeDate") or "")[:10]
        amount = num(pick(row, "Amount", "amount"))
        otype = str(pick(row, "Type", "type") or "").strip()
        stage = str(pick(row, "StageName", "stageName") or "").strip()
        # A close date in the past means the record is not a forecastable deal, whatever
        # its stage says. It is surfaced as hygiene, never scored as live.
        stale = bool(close) and close < as_of.isoformat()
        in_h1 = bool(close) and h1_start.isoformat() <= close <= h1_end.isoformat()
        # Q1 is a strict subset of H1. It is carried separately because the quota is set
        # per quarter: a deal dated in Q2 supports the half but does nothing for the Q1
        # number, and blending the two overstates how covered the current quarter is.
        in_q1 = bool(close) and h1_start.isoformat() <= close <= q1_end.isoformat()
        is_renewal = otype.lower() in RENEWAL_TYPES
        product, product_basis = classify_product(pick(row, "Name", "name"), otype)

        rec["openPipeline"].append({
            "name": pick(row, "Name", "name") or "",
            "stage": stage,
            "amount": amount,
            "closeDate": close,
            "type": otype,
            "forecast": pick(row, "ForecastCategoryName") or "",
            "stale": stale,
            "inH1": in_h1,
            "inQ1": in_q1,
            "isRenewal": is_renewal,
            "product": product,
            "productBasis": product_basis,
            "source": "crm",
        })
        if stale:
            rec["stalePipelineValue"] += amount
            stale_count += 1
            continue
        if in_h1:
            if is_renewal:
                rec["h1RenewalValue"] += amount
            else:
                rec["h1PipelineValue"] += amount
                if in_q1:
                    rec["q1PipelineValue"] = rec.get("q1PipelineValue", 0.0) + amount
                weight = stage_weight(stage)
                if weight > rec["bestStageWeight"]:
                    rec["bestStageWeight"] = weight
                    rec["bestStage"] = stage

    for rec in accounts.values():
        rec["openPipeline"].sort(key=lambda o: -o["amount"])

    applied = apply_overrides(accounts, ov, rates, h1_start, h1_end)

    with_tpid = sum(1 for r in accounts.values() if r["msftOverlap"])
    with_pipe = sum(1 for r in accounts.values() if r["h1PipelineValue"] > 0)
    return {
        "accounts": accounts,
        "window": {"h1Start": h1_start.isoformat(), "h1End": h1_end.isoformat(),
                   "asOf": as_of.isoformat()},
        "overrides": applied,
        "coverage": {
            "accounts": len(accounts),
            "accountsWithTpid": with_tpid,
            "accountsWithH1Pipeline": with_pipe,
            "h1PipelineValue": round(sum(r["h1PipelineValue"] for r in accounts.values()), 2),
            "sellerPipelineValue": round(
                sum(r.get("sellerPipelineValue", 0.0) for r in accounts.values()), 2),
            "h1RenewalValue": round(sum(r["h1RenewalValue"] for r in accounts.values()), 2),
            "stalePipelineValue": round(sum(r["stalePipelineValue"] for r in accounts.values()), 2),
            "staleOpportunities": stale_count,
        },
    }


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    mode = sys.argv[1]

    if mode == "query":
        with open(sys.argv[2], "r", encoding="utf-8") as fh:
            report = json.load(fh)
        run_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(sys.argv[2])
        ids, queries = emit_queries(report)
        out = os.path.join(run_dir, "crm-queries.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump([{"kind": k, "soql": q} for k, q in queries], fh, indent=1)
        print(json.dumps({"queryPath": out, "accounts": len(ids),
                          "queries": len(queries)}))
        for kind, soql in queries:
            print("\n--- %s ---\n%s" % (kind, soql))
        return

    if mode == "ingest":
        with open(sys.argv[2], "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        run_dir = sys.argv[3]
        as_of = date.today()
        if "--as-of" in sys.argv:
            as_of = date.fromisoformat(sys.argv[sys.argv.index("--as-of") + 1])

        here = os.path.dirname(os.path.abspath(__file__))
        ov_path = (sys.argv[sys.argv.index("--overrides") + 1]
                   if "--overrides" in sys.argv
                   else os.path.join(run_dir, "overrides.json"))
        pricing_path = (sys.argv[sys.argv.index("--pricing") + 1]
                        if "--pricing" in sys.argv
                        else os.path.join(os.path.dirname(here), "pricing.json"))
        ov_data = overrides.load(ov_path)
        ov = overrides.Overrides(ov_data) if ov_data else None
        rates = Rates(json.load(open(pricing_path, encoding="utf-8")))

        result = ingest(raw, as_of, ov=ov, rates=rates)
        if ov:
            ov.check("overrides file: %s" % ov_path)
        unpriced = result.get("overrides", {}).get("unpriced") or []
        if unpriced:
            raise SystemExit(
                "overrides.json: could not price %d pipeline line(s) - give each a "
                "seats/committers count for a known product, or an explicit amount: %s"
                % (len(unpriced), json.dumps(unpriced)))

        out = os.path.join(run_dir, "crm-context.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        summary = {"crmContextPath": out}
        summary.update(result["coverage"])
        summary["overridesApplied"] = {
            k: v for k, v in result["overrides"].items() if k != "unpriced"}
        print(json.dumps(summary))
        return

    raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
