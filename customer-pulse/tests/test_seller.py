"""Synthetic internal-feed contracts; never reads the published seller homes."""
import copy
import datetime as dt
import os
import unittest

import fixtures as fx
from pulse import seller
from pulse.seller_queries import FEEDS, render
from pulse.util import atomic_write_json

A = "001XXXXXXXXXXXXAAA"
B = "001XXXXXXXXXXXXBBB"
TODAY = "2026-11-27"


def task(feed, **args):
    return {"feed": feed, "account_ids": [A], "args": args}


def entry(rows, state="ok"):
    return {"rows": rows, "state": state, "collected_at": TODAY + "T00:00:00Z", "as_of": TODAY}


class SellerTests(fx.HomeCase):
    def test_plans_bound_owned_scope_and_rotate_fifty_without_writes(self):
        model = {"accounts": [{"id": f"001{i:012d}AAA", "name": f"Synthetic {i:02}", "arr": i} for i in range(60)]}
        sets = []
        for i in range(5):
            day = (dt.date.fromisoformat(TODAY) + dt.timedelta(days=i)).isoformat()
            plan = seller.plan(model, {}, day, "activity")
            self.assertEqual(len(plan["accounts"]), 10)
            self.assertTrue(all(t["tool"] in {*seller.TOOLS.values(), "query_salesforce"} for t in plan["tasks"]))
            sets.extend(plan["accounts"])
        self.assertEqual(len(set(sets)), 50)
        self.assertNotIn(model["accounts"][0]["id"], sets)
        with self.assertRaisesRegex(ValueError, "outside"):
            seller.plan(model, {}, TODAY, account_id="001XXXXXXXXXXXXZZZ")
        analytic = seller.plan(model, {}, TODAY)
        self.assertEqual(len(analytic["tasks"]), len(FEEDS) * 3)
        self.assertTrue(all(len(t["account_ids"]) <= 20 for t in analytic["tasks"]))
        self.assertFalse(os.path.exists(os.path.join(self.home, "state", "actioned.json")))

    def test_queries_reject_injected_identity_and_preserve_source_grains(self):
        with self.assertRaises(ValueError):
            render("invoices", ["'); take 1"], TODAY, "synthetic")
        invoice = render("invoices", [A], TODAY, "synthetic")["query"]
        self.assertIn("uom, billing_source", invoice)
        self.assertIn("amount_n > 0", invoice)
        self.assertNotIn("distinct", invoice)
        self.assertIn("arg_max", render("risks", [A], TODAY, "synthetic")["query"])
        self.assertNotIn("sum(total_distinct_actor_count)", render("model_cost", [A], TODAY, "synthetic")["query"])

    def test_analytics_preserve_charge_rows_units_nulls_and_reject_caps(self):
        records = [{"account_id": A, "date": "2026-10-01", "product": "actions", "amount": amount, "units": 1, "uom": unit}
                   for amount, unit in [(8, "minute"), (8, "minute"), (None, "job")]]
        result = fx.kusto(["account_id", "row_count", "data", "as_of"], [[A, 3, fx.b64(records), TODAY]])
        decoded = seller.decode(task("invoices"), result)[A]
        self.assertEqual(decoded["rows"], records)
        broken = copy.deepcopy(result)
        broken["rows"][0]["row_count"] = 4
        with self.assertRaisesRegex(ValueError, "Truncated"):
            seller.decode(task("invoices"), broken)
        records[0]["account_id"] = B
        result["rows"][0]["data"] = records
        with self.assertRaisesRegex(ValueError, "out-of-scope"):
            seller.decode(task("invoices"), result)
        self.assertEqual(seller.decode(task("invoices"), fx.kusto(["account_id"], []))[A]["rows"], [])

    def test_revenue_contracts_and_internal_poc_stack(self):
        issue = seller.decode(task("issues"), {"issues": [{"id": "issue", "name": "<Synthetic>", "created_date": "2026-10-01",
            "github_state": "Closed", "github_issue_url": "https://example.invalid/issue"}]})[A]["rows"][0]
        self.assertEqual((issue["date"], issue["status"]), ("2026-10-01", "Closed"))
        call = seller.decode(task("gong"), {"calls": [{"id": "call", "name": "Synthetic call", "primary_account_id": A,
            "call_start": "2026-10-02T12:00:00Z", "participants_emails": "person@example.invalid, second@example.invalid"}]})[A]["rows"][0]
        self.assertEqual(call["date"], "2026-10-02")
        self.assertEqual(len(call["participants"]), 2)
        poc = {"account_id": A, "salesforce_id": "", "ghas_poc_status": "Open", "end_date": TODAY,
               "existing_sca_tooling": "Synthetic scanner", "evaluating_other_sast_tools": "Not installed"}
        pocs = seller.decode(task("pocs"), {"proofs_of_concept": [poc, poc]})[A]["rows"]
        self.assertNotEqual(pocs[0]["id"], pocs[1]["id"])
        self.assertEqual(len(pocs[0]["stack"]), 1)
        self.assertEqual(seller.decode(task("notes"), {"documents": {"Synthetic note": "Escaped <note>"}, "total_documents": 1})[A]["rows"][0]["description"], "Escaped <note>")
        self.assertEqual(seller.decode(task("gong"), {"calls": None})[A]["rows"], [])
        with self.assertRaisesRegex(ValueError, "outside"):
            seller.decode(task("gong"), {"calls": [{"id": "call", "primary_account_id": B}]})
        account = {"id": A}
        seller.enrich([account], {}, TODAY, dashboard={}, store={"accounts": {A: {"pocs": entry(pocs)}}})
        stack = account["seller"]["feeds"]["tech_stack"]
        self.assertEqual(stack["state"], "ok")
        self.assertEqual(stack["records"][0]["value"], "Synthetic scanner")

    def test_workiq_envelope_exact_correspondents_and_no_email_bodies(self):
        planned = task("emails")
        planned["contact_emails"] = ["person@example.invalid"]
        records = [{"id": "email", "subject": "Synthetic", "body": {"content": "Do not retain"},
                    "receivedDateTime": TODAY, "conversationId": "thread",
                    "from": {"emailAddress": {"address": "person@example.invalid"}}},
                   {"id": "other", "from": {"emailAddress": {"address": "unrelated@example.invalid"}}}]
        payload = {"results": [{"statusCode": 200, "data": {"value": records}}]}
        rows = seller.decode(planned, payload)[A]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("body", rows[0])
        payload["results"][0]["data"]["@odata.nextLink"] = "https://graph.microsoft.com/v1.0/me/messages?skip=100"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            seller.decode(planned, payload)
        with self.assertRaises(ValueError):
            seller.decode(planned, {"results": [{"statusCode": 403, "data": {"value": []}}]})

    def test_no_broad_mail_search_without_known_contacts(self):
        plan = seller.plan({"accounts": [{"id": A, "arr": None}]}, {}, TODAY, "mail", A)
        self.assertEqual(plan["tasks"], [])
        self.assertIn("collect contacts first", plan["skipped"][0]["reason"])

    def test_verified_enterprise_mapping_and_budget_contract(self):
        model = {"accounts": [{"id": A, "arr": None}]}
        empty = seller.plan(model, {}, TODAY, "budgets", A)
        self.assertEqual(empty["tasks"], [])
        rows = seller.decode(task("github_accounts"), {"account": {"Id": A, "github_accounts": [
            {"slug": "synthetic-enterprise", "namespace": "enterprise"},
            {"slug": "synthetic-residency.ghe.com", "namespace": "proxima"},
            {"slug": "synthetic-org", "namespace": "organization"}]}})[A]["rows"]
        atomic_write_json(seller.store_path(), {"schema": 1, "accounts": {A: {"github_accounts": entry(rows)}}})
        planned = seller.plan(model, {}, TODAY, "budgets", A)
        self.assertEqual([t["args"]["namespace"] for t in planned["tasks"]], ["enterprise", "proxima"])
        record = {"budget_name": "Synthetic product", "target_name": "Synthetic scope", "target_amount": 100.25,
                  "current_amount": 25.5, "resource_type": "Ent", "budget_limit_type": "PreventFurtherUsage"}
        payload = {"budgets": [record, {**record, "resource_type": "User"}], "omitted_results": False}
        budget = seller.decode(planned["tasks"][0], payload)[A]["rows"]
        self.assertEqual(len(budget), 1)
        self.assertEqual((budget[0]["target"], budget[0]["spend"], budget[0]["currency"]), (100.25, 25.5, None))
        payload["omitted_results"] = True
        with self.assertRaisesRegex(ValueError, "paginated"):
            seller.decode(planned["tasks"][0], payload)
        with self.assertRaisesRegex(ValueError, "outside"):
            seller.decode(task("github_accounts"), {"Id": B, "github_accounts": []})

    def test_provenance_stale_errors_and_retry_never_replace_good_history(self):
        model = {"accounts": [{"id": A, "arr": None}]}
        plan = seller.plan(model, {}, TODAY, "activity", A)
        planned = next(t for t in plan["tasks"] if t["feed"] == "issues")
        plan["tasks"] = [planned]
        plan["skipped"] = []
        atomic_write_json(seller.plan_path("activity"), plan)
        atomic_write_json(seller.store_path(), {"schema": 1, "accounts": {A: {"issues": entry([{"id": "old"}])}}})
        good = {"issues": [{"id": "new", "name": "Synthetic"}]}
        results = [(plan["started_at"], planned["tool"], {"account_id": B}, good)]
        seller.collect_plan({}, "activity", "test", results=results, use_sf=False)
        self.assertEqual(seller.load()["accounts"][A]["issues"]["rows"][0]["id"], "old")
        results = [(plan["started_at"], planned["tool"], planned["args"], good),
                   (plan["started_at"], planned["tool"], planned["args"], {"error": "Source failed"})]
        seller.collect_plan({}, "activity", "test", results=results, use_sf=False)
        self.assertEqual(seller.load()["accounts"][A]["issues"]["state"], "failed")
        self.assertEqual(seller.load()["accounts"][A]["issues"]["rows"][0]["id"], "old")
        seller.collect_plan({}, "activity", "test", results=results[:1], use_sf=False)
        self.assertEqual(seller.load()["accounts"][A]["issues"]["rows"][0]["id"], "new")
        seller.collect_plan({}, "activity", "test", statuses={planned["id"]: "not_authorized"}, results=[], use_sf=False)
        self.assertEqual(seller.load()["accounts"][A]["issues"]["state"], "not_authorized")

    def test_nulls_freshness_and_missing_dashboard_source(self):
        self.assertIsNone(seller.known_sum([None, True, "unknown"]))
        self.assertEqual(seller.known_sum([0, None]), 0)
        self.assertEqual(seller.coverage("invoices", entry([], "no_rows"), TODAY)["state"], "no_rows")
        old = entry([{}])
        old["as_of"] = old["collected_at"] = "2025-01-01"
        self.assertEqual(seller.coverage("actions", old, TODAY)["state"], "stale")
        ready = seller.ready_fields({"id": A}, {"copilot_aiu_billed_lcm": 100}, {"missing_sources": ["02"]})
        self.assertIsNone(ready["copilot_aiu_billed_lcm"])
        self.assertEqual(ready["enterprises"], [])

    def test_read_only_signals_exclude_closed_unknown_and_failed_evidence(self):
        account = {"id": A, "name": "Synthetic", "seller": {"feeds": {
            "risks": {"state": "ok", "as_of": TODAY, "records": [{"id": "risk", "date": TODAY, "status": "Open"}]},
            "support": {"state": "ok", "as_of": TODAY, "records": [
                {"id": "closed", "date": TODAY, "status": "solved", "priority": "urgent"},
                {"id": "unknown", "date": TODAY, "priority": "urgent"},
                {"id": "urgent", "date": TODAY, "status": "open", "priority": "urgent"}]},
        }}}
        self.assertEqual(len(seller.internal_signals([account])), 2)
        self.assertTrue(all(r["read_only"] for r in seller.internal_signals([account])))
        account["seller"]["feeds"]["support"]["state"] = "stale"
        self.assertEqual(len(seller.internal_signals([account])), 1)


if __name__ == "__main__":
    unittest.main()
