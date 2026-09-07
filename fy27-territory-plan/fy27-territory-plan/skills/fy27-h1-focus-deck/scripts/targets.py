"""Compute target coverage: what the number is, what is committed, and what is missing.

    python3 targets.py <potential.json> <focus-accounts.json> <runDir>
        [--targets targets.json] [--crm crm-context.json]

Writes coverage.json. This is the arithmetic behind the "how will you achieve it"
slide, and it is deliberately conservative:

  * Renewals never count towards attainment or gap. The targets are net-new, so
    folding renewals in would show coverage the plan has not earned. They are
    reported separately because leadership will ask.
  * Sized potential is TAM, not commit. It is reported in its own column and never
    added to pipeline, because summing an aspiration with a dated opportunity
    produces a number that means nothing.
  * A null target renders as TBD with the percentage suppressed. Inventing a
    denominator to make a chart look complete would misstate attainment.

Bucket 1 is GHE + GHAS. Bucket 2 is consumption: Copilot/AIU, Actions, Codespaces
and Code Quality. The sized lines in potential.json use the product vocabulary
"Copilot" / "GHAS" / "GHE", so the mapping below is what joins the two worlds.
"""
import json
import os
import sys

QUARTERS = ("Q1", "Q2")

# potential.json product -> bucket. Anything unmapped falls to Bucket 2 (consumption),
# which is the safer default: it keeps unknown products out of the quota-bearing
# Bucket 1 number rather than silently inflating it.
PRODUCT_BUCKET = {
    "GHE": "Bucket 1",
    "GHAS": "Bucket 1",
    "Copilot": "Bucket 2",
    "AIU": "Bucket 2",
    "Actions": "Bucket 2",
    "Codespaces": "Bucket 2",
    "Code Quality": "Bucket 2",
    "GHAzDO": "Bucket 2",
}


def run_rate_projection(targets):
    """Run-rate carry for the consumption bucket, per quarter.

    Bucket 2 is recurring revenue. Last month's consumption repeats unless something
    churns, so the honest denominator question is not "how much of the quarter have we
    booked" but "how much does the half land at if nothing changes, and how much growth
    closes the rest".

    Q1 is held flat at the measured base: the quarter is already under way and the base
    is measured now, so claiming growth inside it would be inventing revenue. Q2 applies
    `growthPerQuarter` once. That growth rate is the vehicle through which seat landings
    appear - which is exactly why landings are never *also* added to the carry. Adding
    them would count the same consumption twice, the error this model exists to avoid.

    Returns (q1 per-product, q2 per-product, months, growth). Empty dicts when no run
    rate is configured, so a book without one falls back to the booked-attainment view.
    """
    cfg = targets.get("runRate") or {}
    products = cfg.get("products") or {}
    months = float(cfg.get("monthsInQuarter") or 3)
    growth = float(cfg.get("growthPerQuarter") or 0.0)
    q1 = {p: round(float(v or 0) * months, 2) for p, v in products.items()}
    q2 = {p: round(float(v or 0) * (1.0 + growth) * months, 2) for p, v in products.items()}
    return q1, q2, months, growth


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def bucket_of(product):
    return PRODUCT_BUCKET.get(product, "Bucket 2")


def sized_by_product(focus):
    """Sized potential for the focus set, per product, from the selected accounts only."""
    out = {}
    for acct in focus.get("accounts", []):
        for line in acct.get("lines", []):
            out[line["product"]] = out.get(line["product"], 0.0) + float(line.get("value") or 0)
    return out


def pipeline_totals(crm, focus):
    """H1 open pipeline for the focus set, split by bucket as well as by kind.

    The bucket split is the point. A single blended "net-new pipeline" figure sitting
    beside Bucket 1 coverage implies it supports that target; in this book every
    CRM-sourced net-new deal is Metered consumption, which supports Bucket 2 and
    nothing else. Splitting makes an empty Bucket 1 visible instead of flattering.
    """
    empty = {"netNew": 0.0, "renewal": 0.0, "stale": 0.0, "accounts": 0,
             "staleCount": 0, "byAccount": {}, "byBucket": {}, "byProduct": {},
             "seller": 0.0, "crm": 0.0, "unclassified": 0.0, "nonProduct": 0.0,
             "inferredProduct": 0.0,
             "q1NetNew": 0.0, "q1ByBucket": {}, "q1ByProduct": {}}
    if not crm:
        return empty
    accounts = crm.get("accounts", {})
    ids = {a.get("salesforceId") for a in focus.get("accounts", []) if a.get("salesforceId")}
    net = ren = stale = 0.0
    q1_net = 0.0
    stale_count = 0
    by_account, by_bucket, by_product = {}, {}, {}
    q1_by_bucket, q1_by_product = {}, {}
    seller = crm_sourced = unclassified = non_product = inferred = 0.0

    for sid in ids:
        rec = accounts.get(sid)
        if not rec:
            continue
        net += rec.get("h1PipelineValue", 0.0)
        q1_net += rec.get("q1PipelineValue", 0.0)
        ren += rec.get("h1RenewalValue", 0.0)
        stale += rec.get("stalePipelineValue", 0.0)
        stale_count += sum(1 for o in rec.get("openPipeline", []) if o.get("stale"))
        if rec.get("h1PipelineValue", 0.0) > 0:
            by_account[sid] = rec["h1PipelineValue"]

        for opp in rec.get("openPipeline", []):
            if opp.get("stale") or opp.get("isRenewal") or not opp.get("inH1"):
                continue
            amount = float(opp.get("amount") or 0)
            product = opp.get("product") or "Unclassified"
            in_q1 = bool(opp.get("inQ1"))
            by_product[product] = by_product.get(product, 0.0) + amount
            if in_q1:
                q1_by_product[product] = q1_by_product.get(product, 0.0) + amount
            if opp.get("source") == "seller":
                seller += amount
            else:
                crm_sourced += amount
            # Product read off a naming convention rather than an explicit product
            # field. Tracked so the deck can footnote how much of Bucket 1 rests on it.
            if opp.get("productBasis") == "inferred-seat-naming":
                inferred += amount
            # Services carries no product ARR, so it belongs to no quota bucket.
            # Unclassified is held out too rather than defaulted into one.
            if product == "Services":
                non_product += amount
                continue
            if product == "Unclassified":
                unclassified += amount
                continue
            bucket = bucket_of(product) if product != "Consumption" else "Bucket 2"
            by_bucket[bucket] = by_bucket.get(bucket, 0.0) + amount
            if in_q1:
                q1_by_bucket[bucket] = q1_by_bucket.get(bucket, 0.0) + amount

    return {"netNew": round(net, 2), "renewal": round(ren, 2), "stale": round(stale, 2),
            "accounts": len(by_account), "staleCount": stale_count, "byAccount": by_account,
            "byBucket": {k: round(v, 2) for k, v in by_bucket.items()},
            "byProduct": {k: round(v, 2) for k, v in by_product.items()},
            "seller": round(seller, 2), "crm": round(crm_sourced, 2),
            "unclassified": round(unclassified, 2), "nonProduct": round(non_product, 2),
            "inferredProduct": round(inferred, 2),
            "q1NetNew": round(q1_net, 2),
            "q1ByBucket": {k: round(v, 2) for k, v in q1_by_bucket.items()},
            "q1ByProduct": {k: round(v, 2) for k, v in q1_by_product.items()}}


def build(targets, potential, focus, crm):
    sized = sized_by_product(focus)
    pipe = pipeline_totals(crm, focus)
    run_rate, run_rate_q2, run_months, run_growth = run_rate_projection(targets)

    buckets = []
    for name, cfg in targets.get("buckets", {}).items():
        lines = []
        bucket_target_h1 = 0.0
        bucket_target_q1 = 0.0
        bucket_known = True
        bucket_q1_known = True
        recurring = bool(cfg.get("recurring"))
        for product, quarters in cfg.get("targets", {}).items():
            q = {qtr: quarters.get(qtr) for qtr in QUARTERS}
            known = all(q[qtr] is not None for qtr in QUARTERS)
            # Q1 is tracked independently of H1. A book can have a firm current-quarter
            # number with next quarter still unset, and forcing the two to be known or
            # unknown together threw away the number the deck is actually presented on.
            q1_known = q["Q1"] is not None
            h1 = sum(q[qtr] for qtr in QUARTERS) if known else None
            if known:
                bucket_target_h1 += h1
            else:
                bucket_known = False
            if q1_known:
                bucket_target_q1 += q["Q1"]
            else:
                bucket_q1_known = False
            lines.append({
                "product": product,
                "quarters": q,
                "h1Target": h1,
                "targetKnown": known,
                "q1Target": q["Q1"],
                "q1TargetKnown": q1_known,
            })

        # Sized TAM for this bucket, from the focus set.
        bucket_sized = round(sum(v for p, v in sized.items() if bucket_of(p) == name), 2)
        attained = cfg.get("attained", {})
        attained_h1 = sum(float(attained.get(qtr) or 0) for qtr in QUARTERS)
        attained_q1 = float(attained.get("Q1") or 0)

        gap = round(bucket_target_h1 - attained_h1, 2) if bucket_known else None
        coverage = (round(bucket_sized / bucket_target_h1, 2)
                    if bucket_known and bucket_target_h1 else None)
        bucket_pipe = round(pipe.get("byBucket", {}).get(name, 0.0), 2)
        bucket_pipe_q1 = round(pipe.get("q1ByBucket", {}).get(name, 0.0), 2)

        # Q1 coverage. For a recurring bucket the carry *is* the coverage: the run rate
        # repeats whether or not anything new is sold, and the elapsed month's booked
        # revenue is the same money as the first month of that carry. The open Q1
        # pipeline in this bucket is metered consumption, which is that same money a
        # third time - so it is reported as context, never added. For a one-off bucket,
        # booked attainment plus dated pipeline is the coverage.
        carry = round(sum(v for p, v in run_rate.items()
                          if bucket_of(p) == name), 2) if recurring else 0.0
        carry_q2 = round(sum(v for p, v in run_rate_q2.items()
                             if bucket_of(p) == name), 2) if recurring else 0.0
        carry_h1 = round(carry + carry_q2, 2) if recurring else 0.0
        if recurring:
            covered_q1 = carry
            covered_basis = "run-rate carry (Q1 pipeline is metered consumption, already in the carry)"
        else:
            covered_q1 = round(attained_q1 + bucket_pipe_q1, 2)
            covered_basis = "attained + Q1 pipeline"

        # H1 coverage follows the same rule as Q1, for the same reason. For the recurring
        # bucket the two quarters of carry are the coverage; open Bucket 2 pipeline is
        # metered consumption already inside it. For the one-off bucket, attainment plus
        # H1-dated pipeline is the coverage.
        if recurring:
            covered_h1 = carry_h1
            covered_h1_basis = (
                "run-rate carry over both quarters (Q1 flat at the measured base, "
                "Q2 grown %d%%; open Bucket 2 pipeline is metered consumption already "
                "inside the carry)" % round(run_growth * 100))
        else:
            covered_h1 = round(attained_h1 + bucket_pipe, 2)
            covered_h1_basis = "attained + H1 pipeline"

        buckets.append({
            "bucket": name,
            "label": cfg.get("label", name),
            "products": cfg.get("products", []),
            "recurring": recurring,
            "lines": lines,
            "h1Target": round(bucket_target_h1, 2) if bucket_known else None,
            "targetKnown": bucket_known,
            "q1Target": round(bucket_target_q1, 2) if bucket_q1_known else None,
            "q1TargetKnown": bucket_q1_known,
            "attained": {qtr: float(attained.get(qtr) or 0) for qtr in QUARTERS},
            "attainedH1": round(attained_h1, 2),
            "attainedQ1": round(attained_q1, 2),
            "gap": gap,
            "attainmentPct": (round(100.0 * attained_h1 / bucket_target_h1, 1)
                              if bucket_known and bucket_target_h1 else None),
            "sizedPotential": bucket_sized,
            "coverageRatio": coverage,
            "livePipeline": bucket_pipe,
            "q1LivePipeline": bucket_pipe_q1,
            "q1RunRateCarry": carry,
            "q2RunRateCarry": carry_q2,
            "h1RunRateCarry": carry_h1,
            "q1Covered": covered_q1,
            "q1CoveredBasis": covered_basis,
            "q1CoveredPct": (round(100.0 * covered_q1 / bucket_target_q1, 1)
                             if bucket_q1_known and bucket_target_q1 else None),
            "q1Gap": (round(bucket_target_q1 - covered_q1, 2)
                      if bucket_q1_known else None),
            "h1Covered": covered_h1,
            "h1CoveredBasis": covered_h1_basis,
            "h1CoveredPct": (round(100.0 * covered_h1 / bucket_target_h1, 1)
                             if bucket_known and bucket_target_h1 else None),
            "h1Gap": (round(bucket_target_h1 - covered_h1, 2)
                      if bucket_known else None),
            # Gap left after attainment and dated pipeline both count. This is the
            # number that has to come from somewhere not yet visible.
            "uncoveredGap": (round(bucket_target_h1 - attained_h1 - bucket_pipe, 2)
                             if bucket_known else None),
        })

    # Per-product coverage inside each bucket is the detail that carries the story: the
    # bucket can look covered in aggregate while one product is badly short.
    product_rows = []
    for bucket in buckets:
        for line in bucket["lines"]:
            product = line["product"]
            tam = round(sized.get(product, 0.0), 2) if product in sized else None
            if product == "Consumption":
                tam = round(sum(v for p, v in sized.items() if bucket_of(p) == "Bucket 2"), 2)
                live = round(pipe.get("byBucket", {}).get("Bucket 2", 0.0), 2)
                live_q1 = round(pipe.get("q1ByBucket", {}).get("Bucket 2", 0.0), 2)
            else:
                live = round(pipe.get("byProduct", {}).get(product, 0.0), 2)
                live_q1 = round(pipe.get("q1ByProduct", {}).get(product, 0.0), 2)
            carry = round(run_rate.get(product, 0.0), 2) if bucket["recurring"] else 0.0
            carry_q2 = round(run_rate_q2.get(product, 0.0), 2) if bucket["recurring"] else 0.0
            carry_h1 = round(carry + carry_q2, 2) if bucket["recurring"] else 0.0
            # Same rule as the bucket: a recurring line is covered by its carry alone.
            covered_q1 = carry if bucket["recurring"] else round(live_q1, 2)
            covered_h1 = carry_h1 if bucket["recurring"] else round(live, 2)
            q1_target = line["q1Target"]
            h1_target = line["h1Target"]
            product_rows.append({
                "bucket": bucket["bucket"],
                "product": product,
                "q1Target": q1_target,
                "q2Target": line["quarters"]["Q2"],
                "h1Target": h1_target,
                "targetKnown": line["targetKnown"],
                "q1TargetKnown": line["q1TargetKnown"],
                "sizedPotential": tam,
                "livePipeline": live,
                "q1LivePipeline": live_q1,
                "q1RunRateCarry": carry,
                "q2RunRateCarry": carry_q2,
                "h1RunRateCarry": carry_h1,
                "q1Covered": covered_q1,
                "q1CoveredPct": (round(100.0 * covered_q1 / q1_target, 1)
                                 if line["q1TargetKnown"] and q1_target else None),
                "q1Gap": (round(q1_target - covered_q1, 2)
                          if line["q1TargetKnown"] else None),
                "h1Covered": covered_h1,
                "h1CoveredPct": (round(100.0 * covered_h1 / h1_target, 1)
                                 if line["targetKnown"] and h1_target else None),
                "h1Gap": (round(h1_target - covered_h1, 2)
                          if line["targetKnown"] else None),
                # Coverage on dated pipeline is the honest read; coverage on TAM is
                # only what the book could theoretically address.
                "pipelineCoverage": (round(live / line["h1Target"], 2)
                                     if line["targetKnown"] and line["h1Target"] else None),
                "coverageRatio": (round(tam / line["h1Target"], 2)
                                  if line["targetKnown"] and line["h1Target"] and tam
                                  else None),
            })

    known_targets = [b for b in buckets if b["targetKnown"]]
    q1_known = [b for b in buckets if b["q1TargetKnown"]]
    q1_target_total = round(sum(b["q1Target"] for b in q1_known), 2)
    q1_covered_total = round(sum(b["q1Covered"] for b in q1_known), 2)
    h1_target_total = round(sum(b["h1Target"] for b in known_targets), 2)
    h1_covered_total = round(sum(b["h1Covered"] for b in known_targets), 2)
    return {
        "fiscalYear": targets.get("fiscalYear", ""),
        "half": targets.get("half", ""),
        "territory": (targets.get("territory") or "").strip(),
        "focusQuarter": targets.get("focusQuarter", "Q1"),
        "basis": targets.get("basis", "net-new"),
        "runRate": {
            "products": (targets.get("runRate") or {}).get("products", {}),
            "monthsInQuarter": run_months,
            "growthPerQuarter": run_growth,
            "quarterProjection": run_rate,
            "q2Projection": run_rate_q2,
            "h1Projection": {p: round(v + run_rate_q2.get(p, 0.0), 2)
                             for p, v in run_rate.items()},
            "total": round(sum(run_rate.values()), 2),
            "h1Total": round(sum(run_rate.values()) + sum(run_rate_q2.values()), 2),
            "growthContribution": round(sum(run_rate_q2.values()) - sum(run_rate.values()), 2),
            "basisNote": ("Q1 held flat at the measured monthly base; Q2 grown %d%% once. "
                          "The growth rate is an assumption, the base is measured."
                          % round(run_growth * 100)),
        },
        "buckets": buckets,
        "products": product_rows,
        "pipeline": pipe,
        "totals": {
            "h1Target": round(sum(b["h1Target"] for b in known_targets), 2),
            # Attainment against the known targets only, so target minus attained
            # equals gap. Mixing in a bucket whose target is TBD made the three
            # numbers on the summary slide fail to reconcile.
            "attainedH1": round(sum(b["attainedH1"] for b in known_targets), 2),
            "attainedAllBuckets": round(sum(b["attainedH1"] for b in buckets), 2),
            "gap": round(sum(b["gap"] for b in known_targets), 2),
            "livePipeline": round(sum(b["livePipeline"] for b in known_targets), 2),
            "sizedPotential": round(sum(b["sizedPotential"] for b in buckets), 2),
            "targetsComplete": len(known_targets) == len(buckets),
            "q1Target": q1_target_total,
            "q1Attained": round(sum(b["attainedQ1"] for b in q1_known), 2),
            "q1RunRateCarry": round(sum(b["q1RunRateCarry"] for b in q1_known), 2),
            "q1LivePipeline": round(sum(b["q1LivePipeline"] for b in q1_known), 2),
            "q1Covered": q1_covered_total,
            "q1CoveredPct": (round(100.0 * q1_covered_total / q1_target_total, 1)
                             if q1_target_total else None),
            "q1Gap": round(q1_target_total - q1_covered_total, 2),
            "h1Covered": h1_covered_total,
            "h1CoveredPct": (round(100.0 * h1_covered_total / h1_target_total, 1)
                             if h1_target_total else None),
            "h1CoveredGap": round(h1_target_total - h1_covered_total, 2),
            "q1TargetsComplete": len(q1_known) == len(buckets),
        },
    }


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    potential_path, focus_path, run_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    args = sys.argv[4:]

    def opt(flag, default):
        return args[args.index(flag) + 1] if flag in args else default

    targets_path = opt("--targets", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "targets.json"))
    crm_path = opt("--crm", os.path.join(run_dir, "crm-context.json"))

    targets = load(targets_path)
    if not targets:
        raise SystemExit("targets file not found: %s" % targets_path)

    result = build(targets, load(potential_path, {}), load(focus_path, {}), load(crm_path))
    out = os.path.join(run_dir, "coverage.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)

    print(json.dumps({
        "coveragePath": out,
        "h1Target": result["totals"]["h1Target"],
        "attainedH1": result["totals"]["attainedH1"],
        "gap": result["totals"]["gap"],
        "targetsComplete": result["totals"]["targetsComplete"],
        "h1NetNewPipeline": result["pipeline"]["netNew"],
        "h1RenewalPipeline": result["pipeline"]["renewal"],
        "coverageByProduct": {r["product"]: r["coverageRatio"] for r in result["products"]},
    }))


if __name__ == "__main__":
    main()
