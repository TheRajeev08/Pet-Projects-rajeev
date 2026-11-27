"""Synthetic source-version, scope, pagination and relationship acceptance cases."""
import copy
import os
import unittest
from unittest.mock import patch

import fixtures as fx
from pulse import seller, stack_evidence as stack
from pulse.util import atomic_write_json
from test_seller import A, B, TODAY

ISSUE_URL = "https://github.com/synthetic/se/issues/7"
CALL = "a0B000000000001AAA"


class StackTests(fx.HomeCase):
    def setUp(self):
        super().setUp()
        self.cfg = {"stack_evidence": {"se_repositories": ["synthetic/se"]}}
        self.model = {"accounts": [{"id": A, "arr": 1}]}
        seller.plan(self.model, self.cfg, TODAY, "activity", A)
        stack.plan(self.model, self.cfg, TODAY)
        self.issue = {"Id": "a0A000000000001AAA", "Account__c": A, "GitHub_Issue_URL__c": ISSUE_URL}
        self.call = {"Id": CALL, "Gong__Primary_Account__c": A, "Gong__Is_Private__c": False,
                     "Gong__Call_Start__c": TODAY, "Gong__Call_Brief__c": "We use Jenkins."}
        self.body = "We use Jenkins."
        self.comment_body = "We are evaluating CircleCI."
        self.closed = False
        self.version = TODAY + "T01:00:00Z"

    def sf(self, cfg, query):
        self.assertIn(A, query)
        if "GitHub_Issue__c" in query:
            rows = [self.issue]
            self.assertNotIn("GitHub_State__c =", query)
        else:
            self.assertIn("Gong__Is_Private__c = false", query)
            rows = [self.call]
        return {"records": rows, "done": True, "totalSize": len(rows)}, None

    def gh(self, endpoint):
        if "/comments?" in endpoint:
            return [] if self.comment_body is None else [{"id": 3, "html_url": ISSUE_URL + "#issuecomment-3",
                "issue_url": "https://api.github.com/repos/synthetic/se/issues/7", "body": self.comment_body,
                "updated_at": TODAY}]
        self.assertEqual(endpoint, "repos/synthetic/se/issues/7")
        return {"html_url": ISSUE_URL, "state": "closed" if self.closed else "open",
                "body": self.body, "updated_at": self.version, "comments": 0 if self.comment_body is None else 1}

    def hydrate(self):
        return stack.hydrate(self.cfg, reader=self.gh, sf_reader=self.sf)

    def claim(self, sid="synthetic/se#7", technology="Jenkins", status="reported_in_use"):
        source = stack.load_sources()["accounts"][A]["sources"][sid]
        return {"account_id": A, "technology": technology, "category": "CI/CD", "usage_status": status,
                "source_id": sid, "content_hash": source["content_hash"], "evidence_quote": source["content"]}

    def test_open_closed_comments_and_gong_keep_distinct_provenance(self):
        self.closed = True
        result = self.hydrate()
        self.assertEqual(result["reads"], 4)
        rows = [self.claim(), self.claim("synthetic/se#7:comment:3", "CircleCI", "evaluating"), self.claim(CALL)]
        self.assertEqual(stack.ingest(rows, TODAY), {"accepted": 3, "ambiguous": 0})
        records = seller.load()["accounts"][A]["tech_stack"]["rows"]
        self.assertEqual({r["source_type"] for r in records}, {"se_issue", "se_comment", "gong"})
        self.assertTrue(any("#issuecomment-3" in r["source_url"] for r in records))
        self.assertEqual(stack.load_sources()["accounts"][A]["sources"]["synthetic/se#7"]["issue_state"], "closed")
        self.assertFalse(os.path.exists(os.path.join(self.home, "signals", "signals.json")))

    def test_wrong_account_forged_quote_changed_hash_and_unsafe_url(self):
        self.hydrate()
        good = self.claim()
        for changed in ({"account_id": B}, {"content_hash": "forged"}, {"evidence_quote": "They adopted Jenkins."}):
            with self.assertRaises(ValueError):
                stack.ingest([{**good, **changed}], TODAY)
        with self.assertRaises(ValueError):
            stack.source_record(A, "gong", CALL, "javascript:alert(1)", TODAY, "We use Jenkins.", TODAY)
        with self.assertRaises(ValueError):
            stack._call_url({"id": "not-a-salesforce-record"}, self.cfg)

    def test_relationship_is_bound_to_technology_and_complete_context(self):
        cases = [
            ("We use Jenkins.", "Jenkins", "reported_in_use", "reported_in_use"),
            ("We use Jenkins. CircleCI was also mentioned.", "CircleCI", "reported_in_use", "mention_only"),
            ("We never used Jenkins.", "Jenkins", "historical", "mention_only"),
            ("We do not use Jenkins.", "Jenkins", "reported_in_use", "mention_only"),
            ("We are evaluating Jenkins.", "Jenkins", "reported_in_use", "mention_only"),
            ("We are evaluating Jenkins.", "Jenkins", "evaluating", "evaluating"),
            ("We plan to use Jenkins.", "Jenkins", "planned", "planned"),
            ("We no longer use Jenkins.", "Jenkins", "historical", "historical"),
            ("Template example: We use Jenkins.", "Jenkins", "reported_in_use", "mention_only"),
            ("Seller recommends Jenkins.", "Jenkins", "reported_in_use", "mention_only"),
            ("### Current SCM\n\nGitLab", "GitLab", "reported_in_use", "reported_in_use"),
            ("### Current SCM\n\n[ ] GitLab", "GitLab", "reported_in_use", "mention_only"),
            ("We use Jenkins in these examples.", "Jenkins", "reported_in_use", "mention_only"),
            ("We use Jenkins?", "Jenkins", "reported_in_use", "mention_only"),
            ("### GitHub products in scope\n\nJenkins", "Jenkins", "reported_in_use", "mention_only"),
            ("### Decision criteria, competitors and alternatives\n\nJenkins", "Jenkins", "reported_in_use", "mention_only"),
            ("```\nWe use Jenkins.\n```", "Jenkins", "reported_in_use", "mention_only"),
            ("- [ ] We use Jenkins.", "Jenkins", "reported_in_use", "mention_only"),
            ("We never said we use Jenkins.", "Jenkins", "reported_in_use", "mention_only"),
        ]
        for content, tech, status, expected in cases:
            source = stack.source_record(A, "se_issue", "test", ISSUE_URL, TODAY, content, TODAY)
            claim = {"account_id": A, "technology": tech, "category": "CI/CD", "usage_status": status,
                     "source_id": "test", "content_hash": source["content_hash"], "evidence_quote": content}
            with self.subTest(content=content, requested=status):
                self.assertEqual(stack.verify(claim, {A: {"sources": {"test": source}}}, TODAY)["usage_status"], expected)
        source = stack.source_record(A, "gong", "test", ISSUE_URL, TODAY, "We do not use Jenkins.", TODAY)
        claim.update(technology="Jenkins", source_id="test", content_hash=source["content_hash"], evidence_quote="use Jenkins.")
        self.assertEqual(stack.verify(claim, {A: {"sources": {"test": source}}}, TODAY)["verification_state"], "ambiguous")
        for content in ("```\nWe use Jenkins.\n```", "### Third party integrations\n\nWe use Jenkins."):
            source = stack.source_record(A, "se_issue", "test", ISSUE_URL, TODAY, content, TODAY)
            claim.update(content_hash=source["content_hash"], evidence_quote="We use Jenkins.", usage_status="reported_in_use")
            self.assertEqual(stack.verify(claim, {A: {"sources": {"test": source}}}, TODAY)["verification_state"], "ambiguous")

    def test_failures_preserve_previous_dates_and_deleted_content_is_superseded(self):
        self.hydrate()
        stack.ingest([self.claim(), self.claim("synthetic/se#7:comment:3", "CircleCI", "evaluating")], TODAY)
        old = copy.deepcopy(seller.load()["accounts"][A]["tech_stack"]["rows"])
        stack.plan(self.model, self.cfg, TODAY)
        def failed_sf(cfg, query):
            return None, "Salesforce CLI unavailable"
        def failed_gh(endpoint):
            raise ValueError("gh unavailable")
        stack.hydrate(self.cfg, reader=failed_gh, sf_reader=failed_sf)
        retained = seller.load()["accounts"][A]["tech_stack"]
        self.assertEqual([r["collected_at"] for r in retained["rows"]], [r["collected_at"] for r in old])
        self.assertEqual(retained["coverage"]["issues"]["state"], "failed")
        stack.plan(self.model, self.cfg, TODAY)
        self.body, self.comment_body = "", None
        self.hydrate()
        self.assertTrue(all(not r["source_current"] for r in seller.load()["accounts"][A]["tech_stack"]["rows"]))

    def test_wrong_account_private_call_or_non_se_link_never_hydrates(self):
        self.call["Gong__Is_Private__c"] = True
        self.issue["GitHub_Issue_URL__c"] = "https://github.com/synthetic/account-plans/issues/7"
        result = self.hydrate()
        self.assertEqual(result["coverage"][A]["gong"]["state"], "failed")
        self.assertEqual(stack.load_sources()["accounts"][A]["sources"], {})

    def test_keyset_cap_is_partial_and_resumes_without_clearing(self):
        calls = []
        for i in range(101):
            calls.append({**self.call, "Id": f"a0B{i:012d}AAA"})
        def pages(cfg, query):
            if "GitHub_Issue__c" in query:
                rows = []
            else:
                rows = calls[100:] if "AND Id >" in query else calls[:100]
            return {"records": rows, "done": True, "totalSize": len(rows)}, None
        first = stack.hydrate(self.cfg, reader=self.gh, sf_reader=pages)
        self.assertFalse(first["coverage"][A]["gong"]["discovery_complete"])
        second = stack.hydrate(self.cfg, reader=self.gh, sf_reader=pages)
        self.assertTrue(second["coverage"][A]["gong"]["discovery_complete"])
        self.assertEqual(len(stack.load_sources()["accounts"][A]["sources"]), 101)

    def test_comment_pages_are_bounded_resumable_and_not_complete_at_cap(self):
        comments = [{"id": i, "html_url": ISSUE_URL + f"#issuecomment-{i}",
                     "issue_url": "https://api.github.com/repos/synthetic/se/issues/7",
                     "body": "We use Jenkins.", "updated_at": TODAY} for i in range(101)]
        def pages(endpoint):
            if "/comments?" in endpoint:
                return comments[100:] if "page=2" in endpoint else comments[:100]
            return {**self.gh(endpoint), "comments": 101}
        first = stack.hydrate(self.cfg, reader=pages, sf_reader=self.sf)
        self.assertEqual(first["coverage"][A]["issues"]["deferred"], 1)
        second = stack.hydrate(self.cfg, reader=pages, sf_reader=self.sf)
        self.assertEqual(second["coverage"][A]["issues"]["deferred"], 0)
        self.assertEqual(len(stack.load_sources()["accounts"][A]["sources"]), 103)

    def test_explicit_cohort_guard_and_no_legacy_stack_or_new_poc_reads(self):
        activity = seller.plan(self.model, self.cfg, TODAY, "activity", A)
        self.assertNotIn("get_proofs_of_concept", {t["tool"] for t in activity["tasks"]})
        with self.assertRaises(ValueError):
            stack.plan({"accounts": []}, self.cfg, TODAY)

    def test_resume_and_ingest_do_not_refresh_cached_collection_dates(self):
        first = TODAY + "T01:00:00Z"
        with patch.object(stack, "iso_now", return_value=first):
            self.hydrate()
        claim = self.claim(CALL)
        with patch.object(stack, "iso_now", return_value=TODAY + "T02:00:00Z"):
            self.hydrate()
            stack.ingest([claim], TODAY)
        self.assertEqual(stack.load_sources()["accounts"][A]["sources"][CALL]["collected_at"], first)
        self.assertEqual(seller.load()["accounts"][A]["tech_stack"]["collected_at"], first)

    def test_successful_discovery_removes_unlinked_and_newly_private_sources(self):
        self.hydrate()
        stack.ingest([self.claim(), self.claim(CALL)], TODAY)
        stack.plan(self.model, self.cfg, TODAY)
        stack.hydrate(self.cfg, reader=self.gh, sf_reader=lambda cfg, query: ({"records": [], "done": True, "totalSize": 0}, None))
        saved = seller.load()["accounts"][A]["tech_stack"]
        self.assertTrue(all(not r["source_current"] for r in saved["rows"]))
        self.assertEqual(saved["coverage"]["issues"]["hydrated"], 0)

    def test_invalid_call_date_is_partial_not_a_successful_empty_read(self):
        self.hydrate()
        stack.ingest([self.claim(CALL)], TODAY)
        stack.plan(self.model, self.cfg, TODAY)
        self.call["Gong__Call_Start__c"] = "invalid"
        result = self.hydrate()
        self.assertEqual(result["coverage"][A]["gong"]["state"], "partial")
        row = seller.load()["accounts"][A]["tech_stack"]["rows"][0]
        self.assertEqual(row["source_state"], "retained")
        self.assertEqual(row["observed_at"], TODAY)


if __name__ == "__main__":
    unittest.main()
