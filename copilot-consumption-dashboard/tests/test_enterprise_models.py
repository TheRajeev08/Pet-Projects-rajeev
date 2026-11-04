"""Synthetic enterprise-model identity, decoding, reconciliation and freshness coverage."""
import argparse
import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("model_builder", os.path.join(ROOT, "build_dashboard.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
import refresh

CUTOFF = "2030-01-07T00:00:00Z"
COLLECTED = "2030-01-08T03:04:05Z"
OWNED = [{"enterprise_id": "101", "slug": "synthetic-one"},
         {"enterprise_id": "102", "slug": "synthetic-two"}]
SOURCE = {"collected_from": COLLECTED}


def slice_row(**extra):
    return {"stamp": "stamp-a", "key": "key-a", "model": "Future Model <X>",
            "integration": "synthetic-chat", "last_date": CUTOFF,
            "l7": 2, "p7": 3, "l28": 10, "aiu7": 20, "aiup7": 30, "aiu28": 100, **extra}


def feed(slices=None, **extra):
    slices = [slice_row()] if slices is None else slices
    return {"enterprise_id": "101", "slug": "synthetic-one", "identity_count": 1,
            "dimension_duplicate_keys": 0, "asof": CUTOFF, "models": slices,
            "slice_count": len(slices), "gross28_total": sum(s["l28"] for s in slices),
            "aiu28_total": sum(s["aiu28"] for s in slices), **extra}


class EnterpriseModelTests(unittest.TestCase):
    def test_native_base64_and_json_arrays_decode_identically(self):
        row = feed()
        expected = builder.build_model_usage([row], OWNED, SOURCE)
        for value in (json.dumps(row["models"]),
                      base64.b64encode(json.dumps(row["models"]).encode()).decode()):
            actual = builder.build_model_usage([{**row, "models": value}], OWNED, SOURCE)
            self.assertEqual(actual, expected)
        self.assertEqual(expected["101"]["collected_from"], COLLECTED)
        self.assertEqual(expected["101"]["asof"], CUTOFF)

    def test_stamp_identity_new_labels_unattributed_and_deterministic_order(self):
        slices = [slice_row(stamp="stamp-b"), slice_row(),
                  slice_row(key="unknown", model=" ", integration=None, l28=5)]
        actual = builder.build_model_usage([feed(slices)], OWNED, SOURCE)["101"]["slices"]
        reversed_rows = builder.build_model_usage([feed(list(reversed(slices)))], OWNED, SOURCE)
        self.assertEqual(actual, reversed_rows["101"]["slices"])
        self.assertEqual(sum(s["l28"] for s in actual), 25)
        self.assertEqual(sum(s["aiu28"] for s in actual), 300)
        self.assertIn(None, [s["model"] for s in actual])
        self.assertIn("Future Model <X>", [s["model"] for s in actual])
        self.assertNotIn("users", actual[0])

    def test_no_account_fanout_or_monthly_count_change(self):
        raw = {
            "01": [{"Id": "syn-a", "Name": "Synthetic Account"}],
            "11": [{"id": "syn-a", "model": "Account-only Model", "l7": 99, "p7": 88, "l28": 9999}],
            "13": OWNED,
            "15": [{"slug": r["slug"], "sf_id": "syn-a", "model_count": 19} for r in OWNED],
            "18": [feed()],
        }
        with patch.object(builder, "load_enterprise_payloads", return_value={}):
            model = builder.build("2030-01-08", raw, {"18": SOURCE})
        one, two = model["enterprises"]
        self.assertEqual(one["model_usage"]["slices"][0]["l28"], 10)
        self.assertIsNone(two["model_usage"])
        self.assertIsNone(one["impact"])
        self.assertEqual(one["adoption"]["model_count"], 19)
        self.assertEqual(model["accounts"][0]["models"][0]["l28"], 9999)
        self.assertEqual(model["asof"]["18"], "2030-01-07")

    def test_missing_empty_zero_and_unavailable_are_distinct(self):
        self.assertEqual(builder.build_model_usage([], OWNED, {}), {})
        empty = builder.build_model_usage([feed([])], OWNED, SOURCE)["101"]
        self.assertEqual(empty["status"], "no_usage")
        zero = slice_row(**dict.fromkeys(builder.MODEL_METRICS, 0))
        observed = builder.build_model_usage([feed([zero])], OWNED, SOURCE)["101"]
        self.assertEqual(observed["status"], "observed")
        self.assertEqual(len(observed["slices"]), 1)
        unavailable = builder.build_model_usage([feed([], asof=None)], OWNED, SOURCE)["101"]
        self.assertEqual(unavailable["status"], "unavailable")

    def test_rejects_bad_identity_dimension_truncation_and_totals(self):
        cases = [
            feed(identity_count=2), feed(dimension_duplicate_keys=1),
            feed(slug="guessed-name"), feed(enterprise_id=""), feed(slice_count=2),
            feed(models="not an array"), feed(models=[["not", "an", "object"]]),
            feed(models=[slice_row(l7=None)]), feed(models=[slice_row(l7=float("nan"))]),
            feed(models=[slice_row(key=None)]), feed(models=[slice_row(stamp=None)]),
            feed(models=[slice_row(model=42)]), feed(asof=None), feed(asof="invalid"),
            feed(gross28_total=11), feed(aiu28_total=101),
            feed([slice_row(), slice_row()]),
        ]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                builder.build_model_usage([row], OWNED, SOURCE)
        for rows in ([feed(), feed()], [feed()] * 1000):
            with self.assertRaises(ValueError):
                builder.build_model_usage(rows, OWNED, SOURCE)
        with self.assertRaises(ValueError):
            builder.build_model_usage([feed()], OWNED + [{**OWNED[0], "slug": "other"}], SOURCE)

    def test_old_snapshots_and_carried_dates(self):
        self.assertIsNone(builder.build_enterprises(OWNED, [], [], {}, [])[0]["model_usage"])
        with tempfile.TemporaryDirectory() as data:
            folder = os.path.join(data, "raw", "2030-01-08")
            os.makedirs(folder)
            with open(os.path.join(folder, "18.json"), "w") as f:
                json.dump({"collected_from": COLLECTED, "result": {"records": [feed()]}}, f)
            raw, meta = {}, {}
            with patch.object(builder, "DATA", data):
                carried = builder.carry_forward("2030-01-10", raw, meta)
            self.assertEqual(carried, {"18": "2030-01-08"})
            normalized = builder.build_model_usage(raw["18"], OWNED, meta["18"])["101"]
            self.assertEqual(normalized["carried_from"], "2030-01-08")
            self.assertEqual(normalized["collected_from"], COLLECTED)
            self.assertEqual(normalized["asof"], CUTOFF)

    def test_query_is_bounded_packed_and_collected_with_existing_guards(self):
        with patch.object(refresh, "load_config", return_value={
            "owner_sfdc_id": "ownerIdAAAAAAAA", "owner_name": "Synthetic Owner",
        }):
            query = next(q for q in refresh.queries() if q["qid"] == "18")
        text = query["query"]
        self.assertEqual(query["db"], "rev_source")
        self.assertNotIn("{{", text)
        self.assertIn('sales_owner == "Synthetic Owner"', text)
        for fragment in ("measurement_date == snap", "measurement_date > asof - 28d",
                         "measurement_date <= asof", "stamp = tostring(stamp_converted)",
                         "make_list(bag_pack(", "slice_count = count()", "dimension_rows = count()"):
            self.assertIn(fragment, text)
        self.assertNotIn("nbr_of_ubb_users", text)
        self.assertNotIn("round(", text)
        for bad in (False, True):
            result = {"columns": [], "rows": [feed()] * (1000 if bad else 1)}
            event = {"type": "hook.start", "timestamp": COLLECTED, "data": {
                "hookType": "postToolUse", "input": {
                    "toolName": "revenue-mcp-server-query_kusto",
                    "toolArgs": {"query": text, "database": "rev_source"},
                    "toolResult": {"resultType": "success", "structuredContent": result}}}}
            with tempfile.TemporaryDirectory() as data, \
                    patch.object(refresh, "DATA", data), \
                    patch.object(refresh, "queries", return_value=[query]), \
                    patch.object(refresh, "iter_events", return_value=[event]), \
                    patch.object(refresh, "owned_slugs", return_value=[]), \
                    contextlib.redirect_stdout(io.StringIO()):
                args = argparse.Namespace(session="actual-query-session", date="2030-01-08")
                if bad:
                    with self.assertRaises(SystemExit):
                        refresh.cmd_collect(args)
                else:
                    refresh.cmd_collect(args)
                path = os.path.join(data, "raw", args.date, "18.json")
                self.assertEqual(os.path.exists(path), not bad)
                if not bad:
                    with open(path) as f:
                        self.assertEqual(json.load(f)["collected_from"], COLLECTED)


if __name__ == "__main__":
    unittest.main()
