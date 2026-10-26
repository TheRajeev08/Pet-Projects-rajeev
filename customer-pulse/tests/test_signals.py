"""Synthetic evidence only; signals never fetch external data in tests."""
import copy
import datetime as dt
import json
import os

from fixtures import HomeCase, DAY1, hook, start_complete, write_day1
from pulse import digest, model, paths, signals
from pulse.util import atomic_write_json, read_json


def record(**kwargs):
    out = {"account_id": "acc1", "source": "gong", "date": DAY1, "title": "Engineering priorities",
           "evidence": "We use Cursor for coding.", "url": "https://example.com/call/one",
           "source_id": "call-one", "themes": [], "competitors": ["Cursor"]}
    out.update(kwargs)
    return out


def result(rec=None):
    rec = rec or record()
    return (DAY1 + "T01:00:00+00:00", "get_account_gong_calls", {"account_id": rec["account_id"]},
            {"calls": [{"id": rec["source_id"], "primary_account_id": rec["account_id"],
                        "call_start": rec["date"] + "T01:00:00Z", "call_brief": rec["evidence"], "url": rec["url"]}]})


class SignalsTest(HomeCase):
    def setUp(self):
        super().setUp()
        write_day1(self.home)

    def ingest(self, records=None, results=None, **kwargs):
        return signals.ingest(self.cfg(), records if records is not None else [record()], "test-session",
                              DAY1, results=results if results is not None else [result()], **kwargs)

    def item(self):
        return read_json(signals.store_path())["items"][0]

    def test_verified_competitor(self):
        self.ingest()
        self.assertTrue(self.item()["verified"])
        self.assertEqual(self.item()["priority"], "High")
        self.assertEqual(self.item()["competitors"], ["Cursor"])
        self.assertTrue(self.item()["id"].startswith("sig:"))

    def test_missing_evidence_is_not_verified(self):
        self.ingest(results=[])
        self.assertFalse(self.item()["verified"])
        self.assertEqual(self.item()["verification"], "needs verification")

    def test_null_calls_do_not_break_verification(self):
        self.ingest(results=[(DAY1, "get_account_gong_calls", {}, {"calls": None})])
        self.assertFalse(self.item()["verified"])

    def test_corrupt_store_does_not_block_build_or_digest(self):
        os.makedirs(os.path.dirname(signals.store_path()), exist_ok=True)
        with open(signals.store_path(), "w") as f:
            f.write("{broken")
        m, st = model.build(self.cfg(), DAY1)
        self.assertEqual(st["overall"], "failed")
        self.assertEqual(m["signals"], [])
        self.assertEqual(m["sources"][-1]["feed"], "signals_store")
        self.assertIn("JSONDecodeError", m["sources"][-1]["messages"][0])
        _, meta = digest.build_digest(m, self.cfg())
        self.assertIn("data issues", meta["subject"])

    def test_wrong_account_call_date_or_id_unverified(self):
        for key, value in (("primary_account_id", "acc2"), ("id", "other"), ("call_start", "2026-10-25")):
            res = copy.deepcopy(result())
            res[3]["calls"][0][key] = value
            self.ingest(results=[res])
            self.assertFalse(self.item()["verified"], key)

    def test_html_whitespace_normalization(self):
        rec = record(evidence="We use Cursor & agents for coding.")
        res = result(rec)
        res[3]["calls"][0]["call_brief"] = "We use Cursor &amp;<br> agents for coding."
        self.ingest([rec], [res])
        self.assertTrue(self.item()["verified"])

    def test_unknown_fields_rejected_atomically(self):
        with self.assertRaises(ValueError):
            self.ingest([record(), record(priority="High")])
        self.assertIsNone(read_json(signals.store_path()))

    def test_outside_book(self):
        with self.assertRaises(ValueError):
            self.ingest([record(account_id="outside")])

    def test_any_owned_id(self):
        self.ingest([record(account_id="acc5")], results=[])
        self.assertEqual(self.item()["account_id"], "acc5")

    def test_limits_and_enums(self):
        for rec in (record(evidence="x" * 301), record(title="x" * 201), record(source="linkedin"),
                    record(themes=["injected"]), record(competitors=["invented"]), record(date="tomorrow"),
                    record(evidence=""), record(url="http://example.com")):
            with self.assertRaises(ValueError):
                self.ingest([rec])
        with self.assertRaises(ValueError):
            self.ingest([record()] * 6)
        with self.assertRaises(ValueError):
            self.ingest({"records": []})

    def test_retention_and_future_date(self):
        for day in ("2025-09-09", "2026-10-27"):
            with self.assertRaises(ValueError):
                self.ingest([record(date=day)])
        oldest = (dt.date.fromisoformat(DAY1) - dt.timedelta(days=180)).isoformat()
        self.ingest([record(date=oldest)])
        signals.ingest(self.cfg(), [], "test", "2026-10-27", results=[])
        self.assertEqual(read_json(signals.store_path())["items"], [])

    def test_build_prunes_store_even_when_collection_failed(self):
        oldest = (dt.date.fromisoformat(DAY1) - dt.timedelta(days=180)).isoformat()
        self.ingest([record(date=oldest)], source_status={"gong": "failed"})
        model.build(self.cfg(), "2026-10-27")
        self.assertEqual(read_json(signals.store_path())["items"], [])

    def test_dedupe_and_first_seen(self):
        self.ingest()
        sid = self.item()["id"]
        signals.ingest(self.cfg(), [record(title="Updated title")], "test", "2026-10-27", results=[result()])
        self.assertEqual(len(read_json(signals.store_path())["items"]), 1)
        self.assertEqual(self.item()["id"], sid)
        self.assertEqual(self.item()["first_seen"], DAY1)
        self.assertEqual(self.item()["title"], "Updated title")

    def test_url_normalization(self):
        self.assertEqual(signals.normalize_url("https://EXAMPLE.com:443/a/?utm_source=x&b=2&a=1#frag"),
                         "https://example.com/a?a=1&b=2")
        for url in ("http://example.com", "https://linkedin.com/a", "https://www.linkedin.com/a",
                    "https://user:pass@example.com", "https://example.com:8000/a",
                    "https://example.com/\nx", "javascript:alert(1)", "https://example.com\\evil"):
            with self.assertRaises(ValueError):
                signals.normalize_url(url)

    def test_word_boundaries_and_unsupported_proposals(self):
        rec = record(evidence="Cursors and Snyky are unrelated.", competitors=["Cursor", "Snyk"])
        self.assertEqual(signals.classify(rec, self.cfg())["competitors"], [])
        self.ingest([record(evidence="Ordinary business news.", themes=["agentic-sdlc"])])
        self.assertEqual(read_json(signals.store_path())["items"], [])

    def test_claude_model_not_competitor(self):
        for evidence in ("Claude in GitHub Copilot is useful.", "We use Claude inside Copilot for code generation.",
                         "Claude is an excellent model."):
            self.assertNotIn("Claude Code", signals.classify(record(evidence=evidence), self.cfg())["competitors"])
        for evidence in ("We use Claude and Cursor for code generation.", "We use Claude for coding.", "We use Claude Code."):
            self.assertIn("Claude Code", signals.classify(record(evidence=evidence), self.cfg())["competitors"])

    def test_themes_and_plays(self):
        cases = {"agentic-sdlc": "Infusing AI into the SDLC",
                 "org-wide-skills": "Turning individual AI knowledge into org-wide skills",
                 "productivity-measurement": "Measuring developer productivity and AI ROI",
                 "appsec": "AppSec and software supply chain", "platform-engineering": "Platform engineering",
                 "modernization": "Modernization and migration", "cicd-speed": "CI/CD speed",
                 "code-quality": "Code quality and technical debt"}
        for theme, evidence in cases.items():
            classified = signals.classify(record(evidence=evidence), self.cfg())
            self.assertIn(theme, classified["themes"])
            self.assertEqual(classified["priority"], "High")
            self.assertIn(signals.THEMES[theme][1], classified["play"])

    def test_notify_and_fyi_not_generic_gong(self):
        for source in ("web", "zoominfo"):
            self.assertEqual(signals.classify(record(source=source, evidence="GitHub announced news."), self.cfg())["priority"], "Notify")
        self.ingest([record(evidence="GitHub check-in.")])
        self.assertEqual(read_json(signals.store_path())["items"], [])
        self.assertEqual(signals.classify(record(source="web", evidence="New office opened."), self.cfg())["priority"], "Info")

    def test_web_citations_not_answer_urls(self):
        rec = record(source="web", evidence="An agentic engineering platform launched.", date="2026-06-09")
        web = (DAY1, "web_search", {"query": '"Quillfeather Analytics Pvt Ltd" launch'},
               {"text": {"value": rec["evidence"] + " June 9, 2026 " + rec["url"],
                         "annotations": [{"url_citation": {"url": rec["url"]}}]}})
        self.ingest([rec], [web])
        self.assertTrue(self.item()["verified"])
        web[3]["text"]["annotations"] = []
        self.ingest([rec], [web])
        self.assertFalse(self.item()["verified"])

    def test_web_wrong_account_query(self):
        rec = record(source="web")
        web = (DAY1, "web_search", {"query": "some other company"},
               {"text": rec["evidence"], "citations": [{"url": rec["url"]}]})
        self.ingest([rec], [web])
        self.assertFalse(self.item()["verified"])

    def test_plain_text_log_and_elision(self):
        folder = os.path.join(self.home, "logs")
        os.makedirs(folder)
        rec = record(source="web")
        events = start_complete("one", DAY1 + "T01:00:00Z", "web_search", {"query": "Quillfeather Analytics Pvt Ltd"}, {})
        events[-1]["data"]["result"] = {"content": rec["date"] + " " + rec["evidence"] + " [source](" + rec["url"] + ")"}
        events += [hook(DAY1 + "T02:00:00Z", "get_account_gong_calls", {}, {}, elided=True)]
        path = os.path.join(folder, "events.jsonl")
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(e) for e in events))
        from unittest.mock import patch
        with patch("pulse.collect.events_path", return_value=path):
            results = list(signals.log_results("test"))
        self.assertEqual(len(results), 1)
        self.assertTrue(signals.verify(rec, results, "Quillfeather Analytics Pvt Ltd"))

    def test_statuses_and_model_merge(self):
        self.ingest(source_status={"gong": "ok", "web": "failed", "zoominfo": "not_connected"})
        m, st = model.build(self.cfg(), DAY1)
        self.assertEqual(len(m["signals"]), 1)
        self.assertTrue(m["signals"][0]["new"])
        self.assertEqual(st["counts"]["signals"], 1)
        self.assertEqual(m["sources"][-1]["state"], "not_connected")
        self.assertEqual(m["sources"][-2]["state"], "failed")
        self.assertEqual(m["status"], "failed")

    def test_bad_status_does_not_write(self):
        with self.assertRaises(ValueError):
            self.ingest(source_status={"gong": "pretend-ok"})
        self.assertIsNone(read_json(signals.store_path()))

    def test_actioned_and_notified_once(self):
        self.ingest()
        sid = self.item()["id"]
        for state in ("done", "dismissed"):
            items, _ = signals.merge(self.cfg(), DAY1, {"acc1"}, {}, {sid: {"state": state}})
            self.assertFalse(items[0]["new"])
            self.assertEqual(items[0]["status"], state)
        items, _ = signals.merge(self.cfg(), "2026-11-26", {"acc1"}, {sid: {"last_seen": DAY1}}, {})
        self.assertFalse(items[0]["new"])
        items, _ = signals.merge(self.cfg(), DAY1, {"acc2"}, {}, {})
        self.assertEqual(items, [])

    def test_digest_verified_and_escaping(self):
        self.ingest([record(evidence="We use Cursor <script>alert(1)</script>.")], results=[])
        m, _ = model.build(self.cfg(), DAY1)
        body, meta = digest.build_digest(m, self.cfg())
        self.assertNotIn(self.item()["id"], meta["ids"])
        self.assertEqual(meta["counts"]["signals"], 0)
        self.ingest()
        m, _ = model.build(self.cfg(), DAY1)
        body, meta = digest.build_digest(m, self.cfg())
        self.assertIn("Signals", body["message"]["body"]["content"])
        self.assertIn(self.item()["id"], meta["ids"])
        self.assertEqual(meta["counts"]["signals"], 1)
        self.assertLessEqual(len(json.dumps(body, indent=2).encode()), digest.MAX_BODY_BYTES)
        digest.mark_sent(meta, "test", force=True)
        m, _ = model.build(self.cfg(), DAY1)
        _, meta = digest.build_digest(m, self.cfg())
        self.assertEqual(meta["counts"]["signals"], 0)
        self.assertFalse(digest.needs_send(meta))

    def test_plan_bounded_and_fixed(self):
        cfg = self.cfg()
        cfg["gong_email"] = "test@example.com"
        m, st = model.build(cfg, DAY1)
        model.write(m, st)
        p = signals.plan(cfg, DAY1)
        self.assertLessEqual(len(p["tasks"]), 25)
        self.assertEqual(p["source_status"]["zoominfo"], "not_connected")
        self.assertEqual(p["tasks"][0]["args"]["lookback_days"], 3)
        self.assertTrue(all("-site:linkedin.com" in t["args"]["query"] for t in p["tasks"] if t["source"] == "web"))
        self.assertEqual(read_json(os.path.join(self.home, "signals", "plan.json"))["since"], p["since"])

    def test_weekly_sweep_and_daily_web_rotation(self):
        cfg = self.cfg()
        swept, searched = set(), set()
        for offset in range(7):
            day = (dt.date.fromisoformat(DAY1) + dt.timedelta(days=offset)).isoformat()
            p = signals.plan(cfg, day)
            swept.update(t["account_id"] for t in p["tasks"] if t["tool"] == "get_account_gong_calls")
            searched.update(t["account_id"] for t in p["tasks"] if t["source"] == "web")
        self.assertEqual(swept, set(signals.owned_accounts(cfg, DAY1)))
        self.assertEqual(searched, swept)

    def test_zoominfo_cache_and_batches(self):
        res = (DAY1, "search_companies", {"companyName": "Quillfeather Analytics Pvt Ltd"},
               {"companies": [{"companyName": "Quillfeather Analytics Pvt Ltd", "companyId": "company-one"}]})
        self.ingest([], [res])
        cfg = self.cfg()
        cfg["signals"]["zoominfo_enabled"] = True
        p = signals.plan(cfg, DAY1)
        enrich = [t for t in p["tasks"] if t["tool"] == "enrich_company_signals"]
        self.assertEqual(enrich[0]["args"]["zoominfoCompanyIds"], ["company-one"])
        rec = record(source="zoominfo")
        zoom = (DAY1, "enrich_company_signals", {"zoominfoCompanyIds": ["company-one"]},
                {"signals": [{"companyId": "company-one", "signalId": "call-one", "date": DAY1,
                              "text": rec["evidence"]}]})
        self.ingest([rec], [zoom])
        self.assertTrue(self.item()["verified"])

    def test_config_deep_merge(self):
        atomic_write_json(os.path.join(self.home, "config.json"), {"owner_name": "Test", "owner_sfdc_id": "owner",
                          "signals": {"daily_tool_budget": 12}})
        cfg = self.cfg()
        self.assertEqual(cfg["signals"]["top_n"], 50)
        self.assertEqual(cfg["signals"]["daily_tool_budget"], 12)

    def test_privacy_caps_cannot_be_raised(self):
        cfg = self.cfg()
        cfg["signals"].update({"evidence_max": 1000, "retention_days": 1000})
        for rec in (record(evidence="x" * 301), record(date="2025-09-09")):
            with self.assertRaises(ValueError):
                signals.ingest(cfg, [rec], "test", DAY1, results=[])

    def test_bounded_email_with_many_signals(self):
        self.ingest()
        m, _ = model.build(self.cfg(), DAY1)
        base = m["signals"][0]
        m["signals"] = [dict(base, id="sig:test-" + str(i), title="<b>" * 60, evidence="&" * 300) for i in range(50)]
        body, meta = digest.build_digest(m, self.cfg())
        text = body["message"]["body"]["content"]
        self.assertLessEqual(len(json.dumps(body, indent=2).encode()), digest.MAX_BODY_BYTES)
        self.assertIn("&lt;b&gt;", text)
        self.assertNotIn("<b><b><b>", text)
        self.assertGreater(meta["counts"]["high"], 49)
        self.assertLessEqual(sum(i.startswith("sig:") for i in meta["ids"]), 10)

    def test_title_cannot_invent_high_priority(self):
        classified = signals.classify(record(title="Cursor agentic SDLC", evidence="An ordinary check-in."), self.cfg())
        self.assertEqual(classified["priority"], "Info")

    def test_gong_link_must_match_source(self):
        rec = record(url="https://unrelated.example.com")
        self.ingest([rec], [result()])
        self.assertFalse(self.item()["verified"])

    def test_web_date_requires_corroboration(self):
        rec = record(source="web")
        res = (DAY1, "web_search", {"query": "Quillfeather Analytics Pvt Ltd"},
               {"text": rec["evidence"], "citations": [{"url": rec["url"]}]})
        self.ingest([rec], [res])
        self.assertFalse(self.item()["verified"])

    def test_fyi_count_is_notified_once(self):
        rec = record(source="web", evidence="A new office opened.")
        res = (DAY1, "web_search", {"query": "Quillfeather Analytics Pvt Ltd"},
               {"text": rec["evidence"] + " " + DAY1, "citations": [{"url": rec["url"]}]})
        self.ingest([rec], [res])
        m, _ = model.build(self.cfg(), DAY1)
        _, meta = digest.build_digest(m, self.cfg())
        self.assertEqual(meta["counts"]["fyi"], 1)
        self.assertIn(self.item()["id"], meta["ids"])
        digest.mark_sent(meta, "test", force=True)
        m, _ = model.build(self.cfg(), DAY1)
        _, meta = digest.build_digest(m, self.cfg())
        self.assertEqual(meta["counts"]["fyi"], 0)
