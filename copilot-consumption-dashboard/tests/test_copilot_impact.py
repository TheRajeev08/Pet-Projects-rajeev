"""Synthetic tests for CCR (q19), CCA (q20/q21) and AIU-by-feature (q10/q14/q22) model payloads."""
import importlib.util
import os
import sys
import tempfile
import json
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("impact_builder", os.path.join(ROOT, "build_dashboard.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
import refresh

DAY = "2030-03-10"
ENTERPRISES = [{"enterprise_id": "901", "slug": "synthetic-alpha"},
               {"enterprise_id": "902", "slug": "synthetic-beta"},
               {"enterprise_id": "903", "slug": "synthetic-gamma"}]
ACCOUNTS = [{"Id": "syn-acct-1", "Name": "Synthetic One"}, {"Id": "syn-acct-2", "Name": "Synthetic Two"}]
CCR_COLS = builder.CCR_COLS.split(",")


def flat(cols, rows):
    """Mimic Kusto make_list(pack_array(...)): one flat list that chunk() regroups by column count."""
    return [r.get(c) for r in rows for c in cols]


def ccr_point(month, reviewed, ccr_prs, **extra):
    base = {"month": month, "prs": reviewed + 10, "reviewed": reviewed, "ccr_prs": ccr_prs, "ccr_users": 4,
            "coverage": ccr_prs / reviewed, "penetration": 0.25, "rate": 0.3, "benchmark": 0.2,
            "vs_industry": "Above", "variants": 1, "orgs": 2, "ccr_avg": 1.5, "ccr_p50": 1.0, "ccr_p90": 3.0,
            "gen_avg": 2.5, "gen_p50": 2.0, "gen_p90": 5.0, "lead_labels": "Faster|Faster", "lead_conflict": False}
    base.update({f"{side}_{s}_label": "Faster" for side in ("ccr", "gen") for s in ("avg", "p50", "p90")})
    base.update(extra)
    return base


def ccr_row(eid, points, sf_ids=("syn-acct-1",)):
    return {"slug": f"slug-{eid}", "enterprise_id": eid, "sf_ids": list(sf_ids), "owner_keys": [],
            "industry": "Synthetic", "s": flat(CCR_COLS, points), "asof": DAY, "cols": builder.CCR_COLS}


def cca_row(owner_id, owner_type, sf_id, months, tiers=()):
    cols, tier_cols = builder.CCA_COLS.split(","), builder.CCA_TIER_COLS.split(",")
    return {"owner_id": owner_id, "owner_type": owner_type, "sf_id": sf_id, "interpretation": "Synthetic note",
            "s": flat(cols, months), "epi_month": "2030-02-28", "tiers": flat(tier_cols, tiers),
            "asof": "2030-02-28", "cols": builder.CCA_COLS, "tier_cols": builder.CCA_TIER_COLS}


def cca_month(month, cca_prs, prs, cca_repos, repos, **extra):
    return {"month": month, "cca_prs": cca_prs, "prs": prs, "cca_repos": cca_repos, "repos": repos,
            "cca_days": 10, "days": 20, "share": cca_prs / prs, "penetration": cca_repos / repos,
            "frequency": 0.5, "rate": 0.1, "stage": "Expanding", "dup": 1, **extra}


def tier(name, lift, comparison=True):
    return {"tier": name, "cca_prs": 5, "non_cca_prs": 9 if comparison else 0, "cca_epi_avg": 0.6,
            "non_cca_epi_avg": 0.5, "cca_epi_p50": 0.6, "non_cca_epi_p50": 0.5, "cca_epi_p90": 0.9,
            "non_cca_epi_p90": 0.8, "lift_avg": lift, "lift_p50": lift, "lift_p90": lift,
            "comparison": comparison, "has_cca": True, "has_non_cca": comparison,
            "summary": "Synthetic summary", "interpretation": "Observational", "action": "Synthetic action"}


def build(raw):
    base = {"01": ACCOUNTS, "13": ENTERPRISES,
            "15": [{"slug": "synthetic-alpha", "sf_id": "syn-acct-1"},
                   {"slug": "synthetic-beta", "sf_id": "syn-acct-1"},
                   {"slug": "synthetic-gamma", "sf_id": "syn-acct-2"}]}
    with patch.object(builder, "load_enterprise_payloads", return_value={}), \
            patch.object(builder.recommendations, "load", return_value=[]):
        return builder.build(DAY, {**base, **raw}, {})


def by_id(model):
    return {a["id"]: a for a in model["accounts"]}, {e["enterprise_id"]: e for e in model["enterprises"]}


class CcrTests(unittest.TestCase):
    def test_mtd_flags_labels_and_latest_complete_month(self):
        rows = [ccr_row("901", [ccr_point("2030-01-01", 100, 40, benchmark=None),
                                ccr_point("2030-02-01", 100, 50, variants=2, lead_conflict=True),
                                ccr_point("2030-03-01", 20, 5)])]
        out = builder.build_ccr(rows, DAY)["901"]
        self.assertEqual(out["status"], "observed")
        self.assertEqual([p["mtd"] for p in out["series"]], [False, False, True])
        self.assertEqual(out["latest_month"], "2030-02-01")
        self.assertIsNone(out["series"][0]["benchmark"])
        self.assertEqual(out["series"][1]["label_basis"], ["Faster", "Faster"])
        self.assertEqual(out["series"][1]["lead_days"]["ccr"]["p50"], 1.0)
        self.assertEqual(out["flags"], ["lead_time_rows_disagree", "org_rows_disagree"])

    def test_account_rollup_recomputes_coverage_from_counts(self):
        model = build({"19": [ccr_row("901", [ccr_point("2030-02-01", 100, 90)]),
                              ccr_row("902", [ccr_point("2030-02-01", 900, 90)])]})
        accounts, _ = by_id(model)
        point = accounts["syn-acct-1"]["ccr"]["series"][0]
        self.assertEqual((point["reviewed"], point["ccr_prs"], point["enterprises"]), (1000, 180, 2))
        self.assertAlmostEqual(point["coverage"], 0.18)  # not the 0.5 mean of 0.9 and 0.1
        self.assertNotIn("penetration", point)
        self.assertNotIn("rate", point)
        self.assertEqual(model["portfolio"]["ccr"]["summary"]["median_lead"]["faster"], 2)
        self.assertEqual(model["asof"]["19"], "2030-02-01")

    def test_salesforce_mismatch_is_withheld_and_not_rolled_up(self):
        model = build({"19": [ccr_row("903", [ccr_point("2030-02-01", 100, 40)], sf_ids=["syn-other"])]})
        accounts, enterprises = by_id(model)
        self.assertEqual(enterprises["903"]["ccr"]["status"], "withheld")
        self.assertIn("different Salesforce account", enterprises["903"]["ccr"]["reason"])
        self.assertEqual(accounts["syn-acct-2"]["ccr"]["status"], "withheld")
        self.assertEqual(accounts["syn-acct-2"]["ccr"]["series"], [])
        self.assertEqual(model["portfolio"]["ccr"]["withheld"], 1)
        self.assertEqual(model["portfolio"]["ccr"]["series"], [])

    def test_enterprise_without_rows_and_source_not_collected(self):
        model = build({"19": [ccr_row("901", [ccr_point("2030-02-01", 10, 1)])]})
        accounts, enterprises = by_id(model)
        self.assertIsNone(enterprises["903"]["ccr"])
        self.assertEqual(accounts["syn-acct-2"]["ccr"]["status"], "no_rows")
        old = build({})
        accounts, enterprises = by_id(old)
        self.assertIsNone(accounts["syn-acct-1"]["ccr"])
        self.assertIsNone(enterprises["901"]["ccr"])
        self.assertNotIn("ccr", old["portfolio"])
        self.assertNotIn("19", old["asof"])


class CcaTests(unittest.TestCase):
    def rows(self):
        return [cca_row("901", "Enterprise", "syn-acct-1",
                        [cca_month("2030-01-31", 1, 100, 1, 10), cca_month("2030-02-28", 5, 100, 2, 10)],
                        [tier("high", -0.2), tier("low", 0.3), tier("medium", None, comparison=False)]),
                cca_row("org-77", "Organization", "syn-acct-1",
                        [cca_month("2030-02-28", 45, 900, 8, 90, dup=2, stage="Broad")])]

    def test_org_owned_attaches_to_account_not_enterprise(self):
        accounts, enterprises = by_id(build({"20": self.rows()}))
        self.assertEqual(enterprises["901"]["cca"]["owner_id"], "901")
        self.assertIsNone(enterprises["902"]["cca"])
        acct = accounts["syn-acct-1"]["cca"]
        self.assertEqual([o["owner_id"] for o in acct["owners"]], ["901", "org-77"])
        latest = acct["series"][-1]
        self.assertAlmostEqual(latest["share"], 50 / 1000)
        self.assertAlmostEqual(latest["penetration"], 10 / 100)
        self.assertNotIn("frequency", latest)
        self.assertEqual(accounts["syn-acct-2"]["cca"]["status"], "no_rows")
        self.assertEqual(next(o for o in acct["owners"] if o["owner_id"] == "org-77")["flags"],
                         ["duplicate_owner_rows"])

    def test_negative_lift_no_comparison_and_tier_order(self):
        owner = builder.build_cca(self.rows())[0]
        tiers = owner["epi"]["tiers"]
        self.assertEqual([t["tier"] for t in tiers], ["low", "medium", "high"])
        self.assertEqual(tiers[2]["lift"]["avg"], -0.2)
        self.assertIsNone(tiers[1]["lift"]["avg"])
        self.assertFalse(tiers[1]["comparison"])
        summary = builder.cca_summary(builder.build_cca(self.rows()))
        self.assertEqual(summary["stages"], {"Expanding": 1, "Broad": 1})
        counts = {t["tier"]: t for t in summary["epi_tiers"]}
        self.assertEqual((counts["high"]["negative"], counts["low"]["positive"], counts["medium"]["no_comparison"]),
                         (1, 1, 1))

    def test_source_not_collected_is_none(self):
        accounts, enterprises = by_id(build({}))
        self.assertIsNone(accounts["syn-acct-1"]["cca"])
        self.assertIsNone(enterprises["901"]["cca"])


class OpportunityTests(unittest.TestCase):
    def test_sorted_named_and_band_validated(self):
        rows = [{"asof": "2030-02-28", "expansion_month": "2030-02-28", "readiness_month": "2030-02-28", "rows": [
            {"kind": "expansion", "owner_id": "901", "owner_type": "Enterprise", "sf_id": "syn-acct-1",
             "month": "2030-02-28", "d": {"score": 22.5, "band": "B - Moderate", "share": 0.1, "action": "Do X"}},
            {"kind": "expansion", "owner_id": "o-2", "owner_type": "Organization", "sf_id": "syn-unowned",
             "month": "2030-02-28", "d": {"score": 61, "band": "Z?"}},
            {"kind": "new_logo", "owner_id": "o-3", "owner_type": "Organization", "sf_id": "syn-acct-2",
             "month": "2030-02-28", "d": {"score": 12, "pr_volume_score": 0.5, "lead_time_score": 0.4,
                                          "repo_score": 0.6, "opportunity_summary": "Synthetic"}},
        ]}]
        model = build({"21": rows})
        opp = model["cca_opportunities"]
        self.assertEqual([e["score"] for e in opp["expansion"]], [61, 22.5])
        self.assertIsNone(opp["expansion"][0]["band_letter"])
        self.assertIsNone(opp["expansion"][0]["account"])
        self.assertEqual((opp["expansion"][1]["band_letter"], opp["expansion"][1]["account"]), ("B", "Synthetic One"))
        self.assertEqual(opp["new_logo"][0]["components"]["repo"], 0.6)
        self.assertIsNone(build({})["cca_opportunities"])


class AiuTests(unittest.TestCase):
    def test_feature_weekly_zero_fill_and_not_collected(self):
        rows = [{"id": "syn-acct-1", "asof": "2030-03-09", "cols": builder.FEATURE_COLS,
                 "s": [0, "copilot-pr-reviews", 2.0, 20.0, 2, "copilot-pr-reviews", 1.0, 10.0,
                       0, "copilot-developer", 5.0, 0.0]}]
        accounts, _ = by_id(build({"22": rows}))
        review = accounts["syn-acct-1"]["aiu_by_feature"]["Code review"]
        self.assertEqual(review["aiu"][:3], [20.0, 0.0, 10.0])
        self.assertEqual(accounts["syn-acct-1"]["aiu_by_feature"]["Coding agent"]["gross"][0], 5.0)
        self.assertEqual(accounts["syn-acct-2"]["aiu_by_feature"], {})
        self.assertIsNone(by_id(build({}))[0]["syn-acct-1"]["aiu_by_feature"])

    def test_portfolio_and_enterprise_aiu_old_rows_are_unknown(self):
        new = build({"10": [{"integration": "copilot-pr-reviews", "wk": 0, "gross": 3, "aiu": 30}],
                     "14": [{"slug": "synthetic-alpha", "integration": "copilot-pr-reviews",
                             "s": [0, 3, 30], "cols": "wk,gross,aiu"}]})
        self.assertEqual(new["portfolio"]["surface_weekly_aiu"]["Code review"][0], 30)
        self.assertEqual(by_id(new)[1]["901"]["integrations"][0]["weekly_aiu"][0], 30)
        old = build({"10": [{"integration": "copilot-pr-reviews", "wk": 0, "gross": 3}],
                     "14": [{"slug": "synthetic-alpha", "integration": "copilot-pr-reviews",
                             "s": [0, 3], "cols": "wk,gross"}]})
        self.assertIsNone(old["portfolio"]["surface_weekly_aiu"])
        integration = by_id(old)[1]["901"]["integrations"][0]
        self.assertIsNone(integration["weekly_aiu"])
        self.assertEqual(integration["weekly"][0], 3)


class QueryTests(unittest.TestCase):
    def queries(self):
        with patch.object(refresh, "load_config", return_value={
                "owner_sfdc_id": "ownerIdAAAAAAAA", "owner_name": "Synthetic Owner"}):
            return {q["qid"]: q for q in refresh.queries()}

    def test_new_sources_are_owner_scoped_and_registered(self):
        qs = self.queries()
        dbs = {"10": "rev_source", "14": "rev_source", "19": "rev_source_dependencies",
               "20": "service_sales_analytics", "21": "service_sales_analytics", "22": "rev_source"}
        for qid, db in dbs.items():
            self.assertEqual(qs[qid]["db"], db, qid)
            self.assertNotIn("{{", qs[qid]["query"])
            self.assertTrue("Synthetic Owner" in qs[qid]["query"] or "ownerIdAAAAAAAA" in qs[qid]["query"], qid)
        self.assertIn("strcat_array(lead_labels", qs["19"]["query"])  # nested arrays would break chunk()
        for qid in ("19", "20", "21", "22"):
            self.assertIn(qid, builder.SLOW_QUERIES)

    def test_carry_forward_reuses_last_cca_result(self):
        with tempfile.TemporaryDirectory() as data:
            folder = os.path.join(data, "raw", "2030-03-01")
            os.makedirs(folder)
            with open(os.path.join(folder, "20.json"), "w") as f:
                json.dump({"collected_from": "2030-03-01T00:00:00Z", "result": {"records": [{"owner_id": "x"}]}}, f)
            raw, meta = {}, {}
            with patch.object(builder, "DATA", data):
                self.assertEqual(builder.carry_forward(DAY, raw, meta), {"20": "2030-03-01"})


class SyntheticFixtureTests(unittest.TestCase):
    def test_review_fixture_matches_builder_and_omits_free_text_pitches(self):
        import synthetic_impact_fixture as fixture
        with open(fixture.OUT) as f:
            committed = f.read()
        self.assertEqual(committed, fixture.render(), "regenerate with tests/synthetic_impact_fixture.py")
        for field in ("sales_pitch", "csm_field_notes", "expansion_pitch"):
            self.assertNotIn(field, committed)
        data = json.loads(committed)
        self.assertEqual({a["ccr"]["status"] for a in data["accounts"].values()},
                         {"observed", "withheld", "no_rows", "no_enterprise"})
        self.assertTrue(all(k.startswith("synthetic-") for k in data["accounts"]))


if __name__ == "__main__":
    unittest.main()
