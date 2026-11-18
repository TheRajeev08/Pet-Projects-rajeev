"""Synthetic recommendation collection and normalization tests."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recommendations as rec
import refresh

ARGS = {"owner": rec.OWNER, "status": list(rec.STATUSES), "limit": rec.LIMIT}
TOOL = "revenue-mcp-server-get_account_recommendations"
STAMP = "2030-01-02T03:04:05Z"
ITEM = {"id": "test", "account_id": "owned", "status": "New",
        "signals": [{"recommended_product": "Copilot", "ml_driven_insights": "<test>"}]}


class RecommendationsTests(unittest.TestCase):
    def test_exact_arguments_and_failures(self):
        result = {"recommendations": [ITEM]}
        self.assertTrue(rec.matches(TOOL, ARGS, result, STAMP))
        for tool, args, data, stamp in [
            ("other-get_account_recommendations", ARGS, result, STAMP),
            (TOOL, {**ARGS, "owner": "other"}, result, STAMP),
            (TOOL, {**ARGS, "limit": 99}, result, STAMP),
            (TOOL, {**ARGS, "status": ["New"]}, result, STAMP),
            (TOOL, ARGS, {"recommendations": [{**ITEM, "owner_github_username": "other"}]}, STAMP),
            (TOOL, ARGS, {"recommendations": [{**ITEM, "signals": "bad"}]}, STAMP),
            (TOOL, ARGS, {"recommendations": [{**ITEM, "signals": [None]}]}, STAMP),
            (TOOL, ARGS, {"recommendations": [{**ITEM, "signals": [{"recommended_product": []}]}]}, STAMP),
            (TOOL, ARGS, {"error": "failed", **result}, STAMP),
            (TOOL, ARGS, result, None),
        ]:
            self.assertFalse(rec.matches(tool, args, data, stamp))

    def test_event_success_and_failed_result(self):
        start = {"type": "tool.execution_start", "data": {
            "toolCallId": "c", "toolName": TOOL, "arguments": ARGS}}
        end = {"type": "tool.execution_complete", "timestamp": STAMP, "data": {
            "toolCallId": "c", "success": True, "result": {"structuredContent": {"recommendations": [ITEM]}}}}
        rows = list(refresh.tool_results([start, end]))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rec.matches(rows[0][1], rows[0][2], rows[0][3], rows[0][0]))
        end["data"]["success"] = False
        self.assertEqual(list(refresh.tool_results([start, end])), [])

    def test_collector_writes_only_exact_successful_feed(self):
        event = {"type": "hook.start", "timestamp": STAMP, "data": {"hookType": "postToolUse",
            "input": {"toolName": TOOL, "toolArgs": ARGS, "toolResult": {
                "resultType": "success", "structuredContent": {"recommendations": [ITEM]}}}}}
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(session="synthetic", date="2030-01-02")
            path = os.path.join(root, "raw", args.date, "recommendations.json")
            with patch.object(refresh, "DATA", root), patch.object(refresh, "queries", return_value=[]), \
                    patch.object(refresh, "owned_slugs", return_value=[]), \
                    patch.object(refresh, "iter_events", return_value=[event]), contextlib.redirect_stdout(io.StringIO()):
                refresh.cmd_collect(args)
            with open(path) as f:
                doc = json.load(f)
            self.assertEqual(doc["collected_from"], STAMP)
            self.assertEqual(doc["result"]["recommendations"], [ITEM])
            os.remove(path)
            event["data"]["input"]["toolArgs"] = {**ARGS, "owner": "other"}
            with patch.object(refresh, "DATA", root), patch.object(refresh, "queries", return_value=[]), \
                    patch.object(refresh, "owned_slugs", return_value=[]), \
                    patch.object(refresh, "iter_events", return_value=[event]), contextlib.redirect_stdout(io.StringIO()):
                refresh.cmd_collect(args)
            self.assertFalse(os.path.exists(path))

    def test_unknown_empty_owned_and_truncation(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(rec.load(root, ["2030-01-02"], [])["items"])
            folder = os.path.join(root, "raw", "2030-01-02")
            os.makedirs(folder)
            path = os.path.join(folder, "recommendations.json")
            snapshot = {"tool": TOOL, "arguments": ARGS, "collected_from": STAMP,
                        "result": {"recommendations": []}}
            def save():
                with open(path, "w") as f:
                    json.dump(snapshot, f)
            save()
            self.assertEqual(rec.load(root, ["2030-01-02"], [])["items"], [])
            snapshot["result"]["recommendations"] = [ITEM, {**ITEM, "account_id": "unowned"}] + [ITEM] * 98
            save()
            feed = rec.load(root, ["2030-01-02"], [{"id": "owned", "name": "Synthetic"}])
            self.assertTrue(feed["possibly_truncated"])
            self.assertEqual(feed["collected_from"], STAMP)
            self.assertTrue(feed["items"][0]["in_book"])
            self.assertFalse(feed["items"][1]["in_book"])
            carried = rec.load(root, ["2030-01-03", "2030-01-02"], [{"id": "owned", "name": "Synthetic"}])
            self.assertTrue(carried["carried"])
            self.assertEqual(carried["age_days"], 1)
            self.assertEqual(carried["source_date"], "2030-01-02")


if __name__ == "__main__":
    unittest.main()
