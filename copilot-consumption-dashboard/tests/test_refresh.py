"""Synthetic regression coverage for session-scoped result collection."""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import refresh
import build_dashboard as builder

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
    def test_kql_templates_avoid_reserved_bare_identifiers(self):
        for path in Path(refresh.QDIR).glob("*.kql"):
            with self.subTest(query=path.name):
                # Catch the offending identifier positions without rejecting join/union kind= syntax.
                code = re.sub(r"""//[^\n]*|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'""", " ", path.read_text())
                self.assertNotRegex(code, r"(?i)\blet\s+latest\b|\bjoin\s+kind\s*=\s*\w+\s+latest\b")
                self.assertNotRegex(code, r"(?i)(?:\bproject\s+|,\s*)kind\s*[,)=]")

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

    def test_terminal_gap_is_per_feed_and_day_not_collected_data(self):
        slug = "synthetic-tenant"
        args = argparse.Namespace(session="synthetic", date="2030-01-02")
        request = {"namespace": "enterprise", "slug": slug}
        start = {"type": "tool.execution_start", "data": {"toolCallId": "s", "arguments": request,
                 "mcpToolName": "get_copilot_feature_settings"}}
        miss = {"code": "failure", "message": f"MCP server 'revenue-mcp-server': not found: {slug} (github.com)"}
        end = {"type": "tool.execution_complete", "timestamp": STAMP, "data": {
            "toolCallId": "s", "success": False, "error": miss}}
        impact = hook()["data"]["input"]
        impact.update(toolName="revenue-mcp-server-get_copilot_impact", toolArgs=request)
        event = {"type": "hook.start", "timestamp": STAMP, "data": {"hookType": "postToolUse", "input": impact}}
        events = [start, end, event]
        with tempfile.TemporaryDirectory() as folder, patch.object(refresh, "DATA", folder), \
                patch.object(builder, "DATA", folder), patch.object(refresh, "queries", return_value=[]), \
                patch.object(refresh, "owned_slugs", return_value=[slug]), patch.object(refresh, "iter_events", return_value=events):
            def output(fn, arg=args):
                with contextlib.redirect_stdout(io.StringIO()) as buf:
                    fn(arg)
                return buf.getvalue()

            for error in ["Tool not found", "Account lookup timed out", "Unauthorized", "404 Not Found",
                          miss["message"].replace(slug, "other"), miss["message"].replace("github.com", "other.invalid")]:
                impact["toolResult"] = {"resultType": "failure", "textResultForLlm": error}
                output(refresh.cmd_collect)
                self.assertIn(f"{slug}\timpact\n", output(refresh.cmd_slugs))
            impact["toolResult"] = {"resultType": "failure", "textResultForLlm": miss["message"]}
            self.assertIn(f"Terminal gap: {slug} (impact not found)", output(refresh.cmd_collect))
            self.assertIn("Impact collected 0 · feature settings collected 0 · outstanding enterprises 0", output(refresh.cmd_slugs))
            doc = builder.build_enterprises([{"slug": slug}], [], [], builder.load_enterprise_payloads(args.date), [])[0]
            self.assertIsNone(doc["impact"])
            self.assertIsNone(doc["settings"])
            self.assertIsNone(doc["collected_from"])
            self.assertIn(f"{slug}\timpact+settings", output(refresh.cmd_slugs, argparse.Namespace(date="2030-01-03")))
            impact["toolResult"] = {"resultType": "success", "structuredContent": {"noDataReason": "feature_disabled"}}
            output(refresh.cmd_collect)
            self.assertIn("Impact collected 1 · feature settings collected 0 · outstanding enterprises 0", output(refresh.cmd_slugs))


if __name__ == "__main__":
    unittest.main()
