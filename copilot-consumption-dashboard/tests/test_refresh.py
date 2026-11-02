"""Synthetic regression coverage for session-scoped result collection."""
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
import refresh

QUERY = {"qid": "17", "name": "17_test.kql", "tool": "query_kusto",
         "db": "rev_source", "query": "// qid:17 db:rev_source\nsynthetic_table | count"}
RESULT = {"columns": ["Count"], "rows": [{"Count": 1}]}
STAMP = "2030-01-02T03:04:05Z"


def hook(query=QUERY["query"], database="rev_source", result=None, success=True):
    return {"type": "hook.start", "timestamp": STAMP, "data": {
        "hookType": "postToolUse", "input": {
            "toolName": "revenue-mcp-server-query_kusto",
            "toolArgs": {"query": query, "database": database},
            "toolResult": {"resultType": "success" if success else "failure",
                           "structuredContent": RESULT if result is None else result}}}}


class RefreshTests(unittest.TestCase):
    def collect(self, events):
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(session="query-session", date="2030-01-02")
            with patch.object(refresh, "DATA", folder), \
                    patch.object(refresh, "queries", return_value=[QUERY]), \
                    patch.object(refresh, "iter_events", return_value=events) as reader, \
                    patch.object(refresh, "owned_slugs", return_value=[]), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    refresh.cmd_collect(args)
                except SystemExit as exc:
                    self.assertEqual(exc.code, 2)
                reader.assert_called_once_with("query-session")
            path = os.path.join(folder, "raw", args.date, "17.json")
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
            return None

    def test_hook_preserves_real_payload_and_timestamp(self):
        doc = self.collect([hook()])
        self.assertEqual(doc["result"], RESULT)
        self.assertEqual(doc["collected_from"], STAMP)

    def test_execution_pairs_remain_supported(self):
        events = [
            {"type": "tool.execution_start", "data": {
                "toolCallId": "c1", "mcpToolName": "query_kusto",
                "arguments": json.dumps({"query": QUERY["query"], "database": QUERY["db"]})}},
            {"type": "tool.execution_complete", "timestamp": STAMP, "data": {
                "toolCallId": "c1", "success": True,
                "result": {"content": json.dumps(RESULT)}}},
        ]
        self.assertEqual(self.collect(events)["result"], RESULT)

    def test_rejects_edited_queries_wrong_database_failed_and_capped_results(self):
        cases = [
            hook(query=QUERY["query"] + "\n| take 1"),
            hook(database="c360"), hook(success=False),
            hook(result={"columns": [], "rows": [{}] * 1000}),
            hook(result={"error": "failed"}),
            hook(result={"isError": True, **RESULT}),
        ]
        for event in cases:
            with self.subTest(event=event["data"]["input"]["toolArgs"]):
                self.assertIsNone(self.collect([event]))

    def test_hook_text_fallback_and_elision(self):
        event = hook()
        result = event["data"]["input"]["toolResult"]
        result.pop("structuredContent")
        result["textResultForLlm"] = json.dumps(RESULT)
        self.assertEqual(self.collect([event])["result"], RESULT)
        result["textResultForLlm"] = "[copilot:elided]"
        self.assertIsNone(self.collect([event]))


if __name__ == "__main__":
    unittest.main()
