"""Synthetic internal-feed contracts; never reads the published seller homes."""
import copy
import datetime as dt
import json
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
    def _simulate_rotation(self, model, days, reshuffle=None):
        seen, daily = {}, []
        for i in range(days):
            day = (dt.date.fromisoformat(TODAY) + dt.timedelta(days=i)).isoformat()
            if reshuffle:
                reshuffle(model, i)
            plan = seller.plan(model, {}, day, "activity")
            daily.append(plan)
            for group in ("budgets", "mail"):
                self.assertEqual(seller.plan(model, {}, day, group)["accounts"], plan["accounts"])
            doc = seller.load()
            for aid in plan["accounts"]:
                seen.setdefault(aid, i)
                doc["accounts"].setdefault(aid, {})["contacts"] = {"state": "failed", "attempted_at": day + "T00:00:00Z"}
            atomic_write_json(seller.store_path(), doc)
        return seen, daily

    def test_activity_rotation_prioritises_top_fifty_without_starving_the_book(self):
        # Ties at the rank-50 boundary and missing ARR exercise the deterministic ordering.
        arr = [None if i % 7 == 0 else (100 - i // 3) for i in range(64)]
        model = {"accounts": [{"id": f"001{i:012d}AAA", "name": "Synthetic", "arr": v} for i, v in enumerate(arr)]}
        ranked = sorted(model["accounts"], key=lambda a: (a["arr"] is None, -(a["arr"] or 0), a["name"], a["id"]))
        top = {a["id"] for a in ranked[:50]}
        seen, daily = self._simulate_rotation(model, 7)
        for plan in daily:
            self.assertEqual(len(plan["accounts"]), 10)
            self.assertEqual(sum(a not in top for a in plan["accounts"]), 2)
            self.assertTrue(all(t["tool"] in {*seller.TOOLS.values(), "query_salesforce"} for t in plan["tasks"]))
        self.assertEqual(daily[0]["rotation"]["top_cycle_days"], 7)
        self.assertEqual(daily[0]["rotation"]["other_cycle_days"], 7)
        self.assertEqual(daily[0]["rotation"]["never_attempted"], 64)
        self.assertTrue(top <= set(seen))
        self.assertEqual(len(seen), 64)
        # Missing-ARR accounts ranked beyond fifty are still reached.
        self.assertTrue(any(a["arr"] is None and a["id"] not in top and a["id"] in seen for a in model["accounts"]))

    def test_rotation_survives_rank_churn_and_budgets_need_todays_activity(self):
        model = {"accounts": [{"id": f"001{i:012d}AAA", "name": f"Synthetic {i:02}", "arr": i} for i in range(80)]}
        with self.assertRaisesRegex(ValueError, "activity"):
            seller.plan(model, {}, TODAY, "budgets")
        churn = lambda m, i: [a.update(arr=(int(a["id"][3:15]) * (i + 3)) % 97) for a in m["accounts"]]
        seen, _ = self._simulate_rotation(model, 15, churn)
        self.assertEqual(len(seen), 80)

    def test_small_books_use_every_daily_slot(self):
        model = {"accounts": [{"id": f"001{i:012d}AAA", "name": "Synthetic", "arr": i} for i in range(12)]}
        plan = seller.plan(model, {}, TODAY, "activity")
        self.assertEqual(len(plan["accounts"]), 10)
        self.assertEqual(plan["rotation"]["other_slots"], 0)

    def test_plans_bound_owned_scope_without_writes(self):
        model = {"accounts": [{"id": f"001{i:012d}AAA", "name": f"Synthetic {i:02}", "arr": i} for i in range(60)]}
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
        self.assertEqual(stack["state"], "not_connected")
        self.assertEqual(stack["records"], [])
        self.assertEqual(account["seller"]["feeds"]["pocs"]["records"], pocs)

    def test_workiq_envelope_exact_correspondents_and_no_email_bodies(self):
        planned = task("emails")
        planned["contact_emails"] = ["person@example.invalid"]
        records = [{"id": "email", "subject": "Synthetic", "body": {"content": "Do not retain"},
                    "receivedDateTime": TODAY, "conversationId": "thread",
                    "from": {"emailAddress": {"address": "person@example.invalid"}}},
                   {"id": "other", "from": {"emailAddress": {"address": "unrelated@example.invalid"}}}]
        payload = {"results": [{"statusCode": 200, "data": {"value": records}}]}
        records[0]["toRecipients"] = [{"emailAddress": {"address": "Bystander@example.invalid", "name": "Not a contact"}}]
        records[0]["ccRecipients"] = [{"emailAddress": {"address": "PERSON@example.invalid", "name": "Synthetic"}}]
        rows = seller.decode(planned, payload)[A]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("body", rows[0])
        self.assertEqual(rows[0]["people"], [{"email": "person@example.invalid", "name": "Synthetic"}])
        payload["results"][0]["data"]["@odata.nextLink"] = "https://graph.microsoft.com/v1.0/me/messages?skip=100"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            seller.decode(planned, payload)
        with self.assertRaises(ValueError):
            seller.decode(planned, {"results": [{"statusCode": 403, "data": {"value": []}}]})

    def test_no_broad_mail_search_without_known_contacts(self):
        plan = seller.plan({"accounts": [{"id": A, "arr": None}]}, {}, TODAY, "mail", A)
        self.assertEqual(plan["tasks"], [])
        self.assertIn("collect contacts first", plan["skipped"][0]["reason"])

    def test_mail_uses_only_current_contacts_and_never_reads_calendar(self):
        model = {"accounts": [{"id": A, "arr": None}]}
        contact = [{"id": "c", "email": "Person@example.invalid"}]
        for state, collected in (("failed", TODAY), ("ok", "2026-01-01")):
            stored = {**entry(contact, state), "collected_at": collected + "T00:00:00Z", "as_of": None}
            atomic_write_json(seller.store_path(), {"schema": 1, "accounts": {A: {"contacts": stored}}})
            self.assertEqual(seller.plan(model, {}, TODAY, "mail", A)["tasks"], [])
        stored = {**entry(contact), "as_of": None}
        atomic_write_json(seller.store_path(), {"schema": 1, "accounts": {A: {"contacts": stored, "meetings": entry([{"id": "m"}])}}})
        tasks = seller.plan(model, {}, TODAY, "mail", A)["tasks"]
        self.assertEqual([t["feed"] for t in tasks], ["emails"])
        self.assertEqual(tasks[0]["contact_emails"], ["person@example.invalid"])
        self.assertNotIn("calendar", json.dumps(tasks).lower())
        account = {"id": A}
        seller.enrich([account], {}, TODAY, dashboard={}, store=seller.load())
        self.assertNotIn("meetings", account["seller"]["feeds"])

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
