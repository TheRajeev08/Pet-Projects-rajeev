"""Generate the synthetic CCR/CCA/AIU-by-feature review fixture by running the real builder over synthetic rows.

Every ID, slug and name is invented. The output is the impact subset of a dashboard model that
customer-pulse/canvas/tests/workspace-fixture.mjs merges into its synthetic Copilot model.

    python3 copilot-consumption-dashboard/tests/synthetic_impact_fixture.py          # rewrite the fixture
    python3 copilot-consumption-dashboard/tests/synthetic_impact_fixture.py --check  # fail if it drifted
"""
import json
import os
import sys
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_copilot_impact import builder, ccr_point, ccr_row, cca_month, cca_row, flat, tier  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "customer-pulse", "canvas", "tests", "fixtures",
                   "copilot-impact.json")
DAY = "2026-11-27"
NAMES = ["Alder Labs", "Beacon Systems", "Cedar Works", "Drift Engineering", "Elm Software",
         "Fieldstone Research", "Grove Robotics", "Harbor Platforms", "Iris Manufacturing", "Juniper Digital"]
WEEKS = 13
CCR_MONTHS = [f"{2025 + (m > 12)}-{(m - 1) % 12 + 1:02d}-01" for m in range(12, 24)]  # 2025-12 .. 2026-11 (MTD)
CCA_MONTHS = ["2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-31"]


def account_name(i):
    if i == 12:
        return "Synthetic long account name for International Platform Engineering and Infrastructure Services"
    return f"Synthetic {NAMES[i % len(NAMES)]}{f' {i + 1}' if i >= 10 else ''}"


ENTERPRISES = [  # (enterprise_id, slug, account index)
    ("9001", "synthetic-alder", 0), ("9002", "synthetic-alder-eu", 0), ("9003", "synthetic-beacon", 1),
    ("9004", "synthetic-cedar", 2), ("9005", "synthetic-drift", 3), ("9006", "synthetic-elm", 4),
    ("9007", "synthetic-fieldstone", 5),
]


def ccr_series(scale, start, step, benchmark=0.32, gap=None):
    points = []
    for m, month in enumerate(CCR_MONTHS):
        mtd = month == CCR_MONTHS[-1]
        reviewed = int(scale * (0.4 if mtd else 1 + 0.03 * m))
        coverage = min(0.92, start + step * m)
        lead = 30 - m * 1.2
        points.append(ccr_point(month, reviewed, int(reviewed * coverage), ccr_users=int(scale / 20 + m),
                                coverage=coverage, penetration=min(0.8, 0.18 + 0.03 * m),
                                rate=(coverage + min(0.8, 0.18 + 0.03 * m)) / 2,
                                benchmark=None if gap == month else benchmark,
                                vs_industry="Above" if (coverage + 0.18 + 0.03 * m) / 2 >= benchmark else "Below",
                                ccr_avg=round(lead * 0.8, 1), ccr_p50=round(lead * 0.6, 1), ccr_p90=round(lead * 1.9, 1),
                                gen_avg=round(lead, 1), gen_p50=round(lead * 0.75, 1), gen_p90=round(lead * 2.3, 1)))
    return points


def raw_rows():
    accounts = [{"Id": f"synthetic-{i}", "Name": account_name(i)} for i in range(64)]
    ccr = [
        ccr_row("9001", ccr_series(600, 0.22, 0.04), sf_ids=["synthetic-0"]),
        ccr_row("9002", ccr_series(180, 0.10, 0.02), sf_ids=["synthetic-0"]),
        ccr_row("9003", ccr_series(320, 0.35, 0.03, gap="2026-03-01"), sf_ids=["synthetic-1"]),
        # Warehouse Salesforce link disagrees with the confirmed match: withheld, never rolled up.
        ccr_row("9004", ccr_series(250, 0.30, 0.02), sf_ids=["synthetic-other"]),
        # 9005 has no CCR rows at all: the linked account reads "no rows", not zero.
        ccr_row("9006", ccr_series(90, 0.05, 0.01, benchmark=0.28), sf_ids=["synthetic-4"]),
        ccr_row("9007", ccr_series(140, 0.40, 0.025), sf_ids=["synthetic-5"]),
    ]
    for row in ccr:
        row["slug"] = next(slug for eid, slug, _ in ENTERPRISES if eid == row["enterprise_id"])
        row["industry"] = "Synthetic industry"
        row["asof"] = DAY

    def cca_months(prs, start, step, repos=40):
        return [cca_month(month, int(prs * (start + step * m)), prs, int(repos * (0.1 + 0.05 * m)), repos,
                          cca_days=4 + 2 * m, days=21, frequency=round(0.2 + 0.08 * m, 3),
                          rate=round(((start + step * m) * (0.1 + 0.05 * m) * (0.2 + 0.08 * m)) ** (1 / 3), 4),
                          stage=["Emerging", "Emerging", "Early expansion", "Early expansion", "Expanding", "Expanding"][m])
                for m, month in enumerate(CCA_MONTHS)]

    cca = [
        cca_row("9001", "Enterprise", "synthetic-0", cca_months(900, 0.02, 0.012),
                [tier("high", -0.08), tier("medium", None, comparison=False), tier("low", 0.21)]),
        cca_row("synthetic-org-11", "Organization", "synthetic-0", cca_months(240, 0.01, 0.006, repos=12),
                [tier("low", 0.12)]),
        cca_row("9003", "Enterprise", "synthetic-1", cca_months(500, 0.04, 0.01),
                [tier("high", 0.05), tier("low", 0.18)]),
        cca_row("synthetic-org-18", "Organization", "synthetic-8", cca_months(150, 0.03, 0.004, repos=9)),
    ]
    for row in cca:
        row["asof"] = row["epi_month"] = CCA_MONTHS[-1]
        row["interpretation"] = "Synthetic observational guidance: compare like-for-like repositories before acting."

    opp = [{"asof": "2026-11-20", "expansion_month": "2026-10-31", "readiness_month": "2026-10-31", "rows": [
        {"kind": "expansion", "owner_id": "9001", "owner_type": "Enterprise", "sf_id": "synthetic-0", "month": "2026-10-31",
         "d": {"score": 44.5, "band": "A - Strong", "share": 0.08, "action": "Synthetic: pilot coding agent on two more teams."}},
        {"kind": "expansion", "owner_id": "9003", "owner_type": "Enterprise", "sf_id": "synthetic-1", "month": "2026-10-31",
         "d": {"score": 23, "band": "B - Moderate", "share": 0.09, "action": "Synthetic: review agent task backlog."}},
        {"kind": "expansion", "owner_id": "synthetic-org-18", "owner_type": "Organization", "sf_id": "synthetic-8",
         "month": "2026-10-31", "d": {"score": 63, "band": "S - High signal", "share": 0.05}},
        {"kind": "expansion", "owner_id": "synthetic-org-99", "owner_type": "Organization", "sf_id": "synthetic-unowned",
         "month": "2026-10-31", "d": {"score": 7, "band": "unrecognised"}},
        {"kind": "new_logo", "owner_id": "synthetic-org-21", "owner_type": "Organization", "sf_id": "synthetic-9",
         "month": "2026-10-31", "d": {"score": 31, "pr_volume_score": 0.7, "lead_time_score": 0.4, "repo_score": 0.55,
                                       "opportunity_summary": "Synthetic: high PR volume, slow lead time."}},
        {"kind": "new_logo", "owner_id": "synthetic-org-22", "owner_type": "Organization", "sf_id": "synthetic-10",
         "month": "2026-10-31", "d": {"score": 12, "pr_volume_score": 0.3, "lead_time_score": 0.2, "repo_score": 0.25,
                                       "opportunity_summary": "Synthetic: small repository estate."}},
    ]}]

    features = {"copilot-pr-reviews": (40, 0.06), "copilot-developer": (55, 0.09), "vscode-chat": (120, 0.01),
                "copilot-developer-cli": (18, 0.05)}
    rows22 = []
    for i in range(24):
        s = []
        for wk in range(WEEKS):
            for p, (integration, (base, growth)) in enumerate(features.items()):
                if integration == "copilot-developer" and i % 4 == 3:
                    continue  # never used: the surface is absent for this account, distinct from "not collected"
                aiu = round(base * (1 + i / 30) * (1 + growth) ** (WEEKS - 1 - wk) * (0.9 + 0.1 * ((wk + p) % 3)), 1)
                s.extend([wk, integration, round(aiu * 0.04, 2), aiu])
        rows22.append({"id": f"synthetic-{i}", "asof": "2026-11-23", "cols": builder.FEATURE_COLS, "s": s})

    portfolio = [{"integration": integration, "wk": wk, "gross": round(base * 24 * (1 + growth) ** (WEEKS - 1 - wk) * 0.04, 2),
                  "aiu": round(base * 24 * (1 + growth) ** (WEEKS - 1 - wk), 1), "asof": "2026-11-23"}
                 for integration, (base, growth) in features.items() for wk in range(WEEKS)]
    integrations = [{"slug": slug, "integration": integration, "asof": "2026-11-23", "cols": "wk,gross,aiu",
                     "s": [v for wk in range(WEEKS) for v in (wk, round(base * (1 + growth) ** (WEEKS - 1 - wk) * 0.04, 2),
                                                               round(base * (1 + growth) ** (WEEKS - 1 - wk), 1))]}
                    for _, slug, _ in ENTERPRISES[:3] for integration, (base, growth) in features.items()]
    return {
        "01": accounts,
        "10": portfolio,
        "13": [{"enterprise_id": eid, "slug": slug, "asof": DAY} for eid, slug, _ in ENTERPRISES],
        "14": integrations,
        "15": [{"slug": slug, "sf_id": f"synthetic-{i}"} for _, slug, i in ENTERPRISES],
        "19": ccr, "20": cca, "21": opp, "22": rows22,
    }


def generate():
    raw = raw_rows()
    meta = {q: {"rows": len(rows), "collected_from": f"{DAY}T05:00:00Z"} for q, rows in raw.items()}
    meta["21"]["carried_from"] = "2026-11-20"  # exercises the "Carried from" stale state
    with patch.object(builder, "load_enterprise_payloads", return_value={}), \
            patch.object(builder.recommendations, "load", return_value=[]):
        model = builder.build(DAY, raw, meta)
    keep = ("10", "14", "19", "20", "21", "22")
    return {
        "_note": "Synthetic review data generated by copilot-consumption-dashboard/tests/synthetic_impact_fixture.py. Not customer data.",
        "asof": {q: model["asof"][q] for q in keep if q in model["asof"]},
        "sources": {q: model["sources"][q] for q in keep},
        "stale_sources": {"21": "2026-11-20"},
        "definitions": {k: v for k, v in builder.DEFINITIONS.items() if k.startswith(("ccr", "cca", "aiu"))},
        "portfolio": {k: model["portfolio"].get(k) for k in ("ccr", "cca", "surface_weekly", "surface_weekly_aiu")},
        "cca_opportunities": model["cca_opportunities"],
        "enterprises": model["enterprises"],
        "accounts": {a["id"]: {k: a[k] for k in ("ccr", "cca", "aiu_by_feature")} for a in model["accounts"]},
    }


def render():
    return json.dumps(generate(), indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        with open(OUT) as f:
            sys.exit(0 if f.read() == text else "Synthetic impact fixture is out of date; regenerate it.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(text)
    print(f"wrote {os.path.relpath(OUT)} ({len(text)} bytes)")
