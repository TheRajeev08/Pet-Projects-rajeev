import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures as fx  # noqa: E402

from pulse import collect, digest, model, paths, privacy, queries, seats, sources  # noqa: E402
from pulse.util import LockBusy, RunLock, read_json  # noqa: E402

RUN = {"run_id": "abcd1234", "session": "s1", "started_at": "2026-10-26T01:00:00+00:00", "date": fx.DAY1}
LATER = "2026-10-26T02:00:00.123Z"
EARLIER = "2026-10-26T00:30:00Z"


def kusto_result(n=2):
    return {"columns": [{"name": "salesforce_account_id"}, {"name": "series"}, {"name": "pts"}],
            "rows": [[f"acc{i}", "ghas", fx.b64([])] for i in range(n)]}


class QueriesTest(fx.HomeCase):
    def test_render_all_queries(self):
        qs = queries.load(self.cfg(), "abcd1234")
        self.assertEqual([q["qid"] for q in qs], ["p01", "p02", "p03", "p04", "p05", "p06"])
        for q in qs:
            self.assertNotIn("{{", q["query"])
            self.assertLessEqual(len(q["query"]), 10000)
            if q["tool"] == "query_kusto":
                self.assertRegex(q["query"].splitlines()[0], rf"^// qid:{q['qid']} run:abcd1234 db:{q['db']} ")
        self.assertIn("ownerIdAAAAAAAA", qs[0]["query"])
        self.assertEqual(qs[0]["tool"], "query_salesforce")

    def test_config_validation(self):
        bad = dict(self.cfg(), owner_name='x"; drop')
        with self.assertRaises(SystemExit):
            queries.load(bad, "abcd1234")

    def test_run_reuse_window(self):
        t0 = dt.datetime(2026, 10, 26, 1, 0, tzinfo=dt.timezone.utc)
        r1, created = queries.current_run("s1", now=t0)
        self.assertTrue(created)
        r2, created = queries.current_run("s1", now=t0 + dt.timedelta(hours=11))
        self.assertEqual((r2["run_id"], created), (r1["run_id"], False))
        r3, created = queries.current_run("s1", now=t0 + dt.timedelta(hours=13))
        self.assertTrue(created)
        self.assertNotEqual(r3["run_id"], r1["run_id"])
        self.assertEqual(queries.current_run("s1", new=True, now=t0)[1], True)


class CollectTest(fx.HomeCase):
    def setUp(self):
        super().setUp()
        self.qs = {q["qid"]: q for q in queries.load(self.cfg(), RUN["run_id"])}

    def args(self, qid, text=None, db=None):
        q = self.qs[qid]
        return {"database": db or q["db"], "query": text if text is not None else q["query"]}

    def test_both_event_shapes(self):
        ev = fx.start_complete("c1", LATER, "query_kusto", self.args("p02"), kusto_result())
        ev.append(fx.hook(LATER, "revenue-mcp-server-query_kusto", self.args("p03"), kusto_result()))
        found, errors = collect.match_events(list(self.qs.values()), RUN, "s1", events=ev)
        self.assertEqual(sorted(found), ["p02", "p03"])
        self.assertEqual(errors, {})

    def test_rejects_other_runs_edits_old_and_capped(self):
        other = self.qs["p02"]["query"].replace("run:abcd1234", "run:ffff0000")
        edited = self.qs["p03"]["query"] + "\n| take 5"
        ev = (fx.start_complete("c1", LATER, "query_kusto", self.args("p02", other), kusto_result())
              + fx.start_complete("c2", LATER, "query_kusto", self.args("p03", edited), kusto_result())
              + fx.start_complete("c3", EARLIER, "query_kusto", self.args("p04"), kusto_result())
              + fx.start_complete("c4", LATER, "query_kusto", self.args("p05"), kusto_result(1000))
              + fx.start_complete("c5", LATER, "query_kusto", self.args("p06", db="rev_source"), kusto_result())
              + [fx.hook(LATER, "revenue-mcp-server-query_kusto", self.args("p06"), None, elided=True)])
        found, errors = collect.match_events(list(self.qs.values()), RUN, "s1", events=ev)
        self.assertEqual(found, {})
        self.assertIn("changed", errors["p03"])
        self.assertIn("row cap", errors["p05"])
        self.assertIn("changed", errors["p06"])
        self.assertNotIn("p02", errors)
        self.assertNotIn("p04", errors)

    def test_later_good_result_wins_and_soql_by_text(self):
        soql = {"done": True, "totalSize": 1, "records": [fx.sf_account("acc1", "Larkspur Freight")]}
        ev = (fx.start_complete("c1", LATER, "query_kusto", self.args("p02"), kusto_result(1000))
              + fx.start_complete("c2", LATER, "query_kusto", self.args("p02"), kusto_result())
              + fx.start_complete("c3", LATER, "query_salesforce", {"query": "  " + self.qs["p01"]["query"] + "\n"}, soql))
        found, errors = collect.match_events(list(self.qs.values()), RUN, "s1", events=ev)
        self.assertEqual(sorted(found), ["p01", "p02"])
        self.assertEqual(errors, {})

    def test_soql_incomplete_rejected(self):
        soql = {"done": True, "totalSize": 2, "records": [fx.sf_account("acc1", "Larkspur Freight")]}
        ev = fx.start_complete("c1", LATER, "query_salesforce", {"query": self.qs["p01"]["query"]}, soql)
        found, errors = collect.match_events(list(self.qs.values()), RUN, "s1", events=ev)
        self.assertEqual(found, {})
        self.assertIn("1 of 2", errors["p01"])

    def test_collect_writes_raw_and_reports_missing(self):
        ev = fx.start_complete("c1", LATER, "query_kusto", self.args("p02"), kusto_result())
        rep = collect.collect(self.cfg(), RUN, list(self.qs.values()), "s1", use_sf=False, events=ev)
        self.assertEqual(rep["collected"], ["p02"])
        self.assertEqual(rep["missing"], ["p01", "p03", "p04", "p05", "p06"])
        doc = read_json(collect.raw_path(self.cfg(), RUN, "p02"))
        self.assertEqual((doc["run_id"], doc["via"]), ("abcd1234", "mcp"))
        rep = collect.collect(self.cfg(), RUN, list(self.qs.values()), "s1", use_sf=False, events=[])
        self.assertIn("p02", rep["collected"])

    def test_ingest_file_validates(self):
        path = os.path.join(self.home, "r.json")
        with open(path, "w") as f:
            json.dump({"result": kusto_result()}, f)
        collect.ingest_file(self.cfg(), RUN, self.qs["p05"], path)
        self.assertTrue(collect.already_collected(self.cfg(), RUN, "p05"))
        with open(path, "w") as f:
            json.dump({"nope": 1}, f)
        with self.assertRaises(SystemExit):
            collect.ingest_file(self.cfg(), RUN, self.qs["p05"], path)


class LockTest(fx.HomeCase):
    def test_busy_and_stale(self):
        path = paths.lock_path()
        with RunLock(path):
            with self.assertRaises(LockBusy):
                RunLock(path).acquire()
        self.assertFalse(os.path.exists(path))
        RunLock(path).acquire()
        lock = RunLock(path, stale_seconds=60, now=lambda: time.time() + 3600)
        lock.acquire()
        lock.release()


class BuildTest(fx.HomeCase):
    def setUp(self):
        super().setUp()
        fx.write_day1(self.home)
        self.m, self.st = model.build(self.cfg(), fx.DAY1, "run1")

    def acc(self, acct):
        return next(a for a in self.m["accounts"] if a["id"] == acct)

    def ev(self, acct, kind=None):
        return [e for e in self.m["events"] if e["account_id"] == acct and (kind is None or e["kind"] == kind)]

    def rules(self, acct):
        return {a["rule"]: a for a in self.m["actions"] if a["account_id"] == acct}

    def test_book_and_status(self):
        self.assertEqual(self.m["book_source"], "salesforce")
        self.assertEqual([a["id"] for a in self.m["accounts"]], ["acc1", "acc2", "acc3", "acc4", "acc5"])
        self.assertEqual(self.m["status"], "ok")
        feeds = {f["feed"]: f for f in self.m["sources"]}
        self.assertEqual(feeds["contracts"]["as_of"], "2026-10-25")
        self.assertEqual(feeds["metered"]["as_of"], "2026-10-24")
        self.assertEqual(feeds["metered"]["held"], ["2026-10-25"])
        self.assertEqual(feeds["copilot_seats"]["as_of"], "2026-09-30")
        self.assertTrue(self.m["baseline"])
        self.assertFalse(self.acc("acc5")["has_data"])
        self.assertTrue(self.acc("acc5")["in_book"])

    def test_account_summaries(self):
        a1 = self.acc("acc1")["products"]
        self.assertEqual(a1["ghe"]["contracted"], 100)
        self.assertEqual(a1["ghe"]["billing"], "volume")
        self.assertEqual((a1["ghe"]["ubu"], a1["ghe"]["ubu_as_of"]), (120, "2026-10-22"))
        self.assertAlmostEqual(a1["ghe"]["adoption"], 1.2)
        self.assertEqual(a1["ghas"]["contracted"], 50)
        self.assertEqual(sorted(a1["ghas"]["by_type"]), ["GHCS", "GHSP"])
        self.assertEqual(a1["ghas"]["committers"], 20)
        a3 = self.acc("acc3")["products"]["ghe"]
        self.assertEqual((a3["billing"], a3["metered"], a3["metered_as_of"]), ("metered", 95, "2026-10-24"))
        self.assertEqual(a3["metered_detail"], {"cloud": 60, "server": 35, "users": 95})
        a4 = self.acc("acc4")["products"]["ghcp"]
        self.assertEqual((a4["contracted"], a4["assigned"], a4["at_risk"]), (50, 50, 20))
        self.assertEqual(a4["health"]["category"], "Red")
        self.assertEqual(a4["funnel"]["engaged_1d28"], 22)
        self.assertEqual(self.acc("acc2")["renewing_in_days"], 60)

    def test_events_and_baseline_new(self):
        drop = self.ev("acc2", "dropped")
        self.assertEqual([(e["priority"], e["from"], e["to"], e["date"], e["new"]) for e in drop],
                         [("High", 200, 150, "2026-10-25", True)])
        mig = self.ev("acc1", "migration")
        self.assertEqual([(e["family"], e["new"], e["baseline"]) for e in mig], [("ghas", False, True)])
        ubu = self.ev("acc1", "added")
        self.assertEqual([(e["measure"], e["new"]) for e in ubu], [("ubu", True)])
        met = self.ev("acc3")
        self.assertEqual([(e["kind"], e["date"], e["new"]) for e in met], [("added", "2026-10-21", False)])
        self.assertEqual(self.ev("acc9"), [])
        self.assertTrue(all(e["recent"] for e in self.m["events"]))
        self.assertTrue(all(e["text"] for e in self.m["events"]))

    def test_actions(self):
        self.assertEqual(sorted(self.rules("acc1")), ["code_quality_usage", "copilot_penetration", "ghas_low_usage", "ghe_overage"])
        self.assertEqual(sorted(self.rules("acc2")), ["copilot_penetration", "ghas_whitespace", "renewal_risk"])
        self.assertEqual(sorted(self.rules("acc4")), ["copilot_at_risk", "copilot_penetration", "ghas_whitespace"])
        self.assertEqual(self.rules("acc3"), {})
        self.assertEqual(self.m["actions"][0]["priority"], "High")
        self.assertTrue(all(a["new"] for a in self.m["actions"]))

    def test_kpis(self):
        k = self.m["kpis"]
        self.assertEqual(k["overall"]["accounts"], 5)
        self.assertEqual(k["overall"]["new_high"], 3)
        self.assertEqual(k["ghe"]["contracted"]["value"], 650)
        self.assertEqual(k["ghe"]["contracted"]["d1"], -50)
        self.assertEqual(k["ghas"]["contracted"]["value"], 50)
        self.assertEqual(k["ghcp"]["contracted"]["value"], 50)
        self.assertEqual(k["ghcp"]["categories"], {"Red": 1})
        self.assertEqual(k["ghe"]["metered"]["value"], 95)

    def test_write_and_json_safe(self):
        model.write(self.m, self.st)
        self.assertEqual(read_json(paths.model_path())["date"], fx.DAY1)
        self.assertEqual(read_json(paths.status_path())["overall"], "ok")

    def test_kusto_fallback_book(self):
        os.remove(os.path.join(self.home, "data", "raw", fx.DAY1, "p01.json"))
        m, _ = model.build(self.cfg(), fx.DAY1)
        self.assertEqual(m["book_source"], "kusto")
        self.assertEqual([a["id"] for a in m["accounts"]], ["acc1", "acc2", "acc3", "acc4"])
        self.assertEqual({f["feed"]: f["state"] for f in m["sources"]}["book"], "missing")


class SnapshotMergeTest(fx.HomeCase):
    """Copilot seats publish month-end rows plus a rolling row that is re-dated every day."""

    def p04(self, raw_day, rolling, accounts):
        """accounts: {acct: (month_end_seats, rolling_seats)}. Month-end rows carry fewer rows than the rolling one."""
        items = [(a, "seats", [("2026-08-31", me, 0, 0), ("2026-09-30", me, 0, 0), (rolling, cur, 0, 0)])
                 for a, (me, cur) in accounts.items()]
        items.append(("__meta__", "seat_totals", [("2026-08-31", 600, 50000), ("2026-09-30", 600, 50000),
                                                  (rolling, 1000, 50000)]))
        fx.write_raw(self.home, raw_day, "p04", fx.series(items))

    def seat_events(self, today, acct):
        src = sources.load_all(self.cfg(), today)
        return src, [e for e in seats.all_events(src, [acct], self.cfg()["thresholds"]) if e["family"] == "ghcp"]

    def test_rolling_change_reported_once(self):
        self.p04("2026-10-23", "2026-10-22", {"acc1": (50, 40)})
        _, first = self.seat_events("2026-10-23", "acc1")
        self.assertEqual([(e["kind"], e["date"], e["from"], e["to"]) for e in first], [("dropped", "2026-10-22", 50, 40)])
        self.p04("2026-10-24", "2026-10-23", {"acc1": (50, 40)})
        src, second = self.seat_events("2026-10-24", "acc1")
        self.assertEqual([e["id"] for e in second], [first[0]["id"]])
        self.assertEqual(sorted(src["healthy"]["seats"]), ["2026-08-31", "2026-09-30", "2026-10-22", "2026-10-23"])
        self.p04("2026-10-25", "2026-10-24", {"acc1": (50, 37)})
        _, third = self.seat_events("2026-10-25", "acc1")
        self.assertEqual([(e["date"], e["from"], e["to"]) for e in third],
                         [("2026-10-24", 40, 37), ("2026-10-22", 50, 40)])
        self.assertEqual(third[1]["id"], first[0]["id"])

    def test_account_new_to_the_feed_has_no_false_change(self):
        self.p04("2026-10-23", "2026-10-22", {"acc1": (50, 50)})
        self.p04("2026-10-24", "2026-10-23", {"acc1": (50, 50), "acc2": (30, 30)})
        src, ev = self.seat_events("2026-10-24", "acc2")
        self.assertEqual(ev, [])
        self.assertNotIn("2026-10-22", sources.account_days(src, "seats", "acc2", sorted(src["healthy"]["seats"])))
        m, _ = model.build(self.cfg(), "2026-10-24")
        acc2 = next(a for a in m["accounts"] if a["id"] == "acc2")["products"]["ghcp"]
        self.assertEqual([p[0] for p in acc2["series"]["contracted"]["points"]], ["2026-08-31", "2026-09-30", "2026-10-23"])
        self.assertEqual(acc2["contracted"], 30)


class DigestTest(fx.HomeCase):
    def setUp(self):
        super().setUp()
        fx.write_day1(self.home)
        self.m, self.st = model.build(self.cfg(), fx.DAY1, "run1")

    def send_event(self, meta, body, status=202, ts=None, subject=None, as_text=False):
        msg = json.loads(json.dumps(body))
        if subject:
            msg["message"]["subject"] = subject
        ts = ts or (dt.datetime.fromisoformat(meta["generated_at"]) + dt.timedelta(minutes=1)).isoformat()
        args = {"actionUrl": "/me/sendMail", "jsonBody": json.dumps(msg) if as_text else msg}
        return fx.hook(ts, "workiq-do_action", args, {"statusCode": status, "data": None})

    def test_body_and_meta(self):
        body, meta = digest.build_digest(self.m, self.cfg(), self.st, today=fx.DAY1)
        self.assertEqual(meta["subject"], "Customer Pulse · 2026-10-26 · 3 high")
        html = body["message"]["body"]["content"]
        self.assertIn("Nimbus &amp; &lt;Orbit&gt; Ltd", html)
        self.assertNotIn("<Orbit>", html)
        self.assertIn('href="https://github.lightning.force.com/lightning/r/Account/acc2/view"', html)
        self.assertIn("First run", html)
        self.assertNotIn("moved between products", html)
        self.assertEqual(body["message"]["toRecipients"], [{"emailAddress": {"address": "me@example.com"}}])
        self.assertTrue(body["saveToSentItems"])
        self.assertEqual(set(meta["ids"]), {e["id"] for e in self.m["events"]} | {a["id"] for a in self.m["actions"]})

    def test_stale_model_and_build_error_flagged(self):
        body, meta = digest.build_digest(self.m, self.cfg(), {"build_error": "KeyError: 'x'"}, today="2026-10-27")
        self.assertTrue(meta["subject"].endswith("· data issues"))
        html = body["message"]["body"]["content"]
        self.assertIn("Build failed", html)
        self.assertIn("last built for 2026-10-26", html)

    def test_requires_recipient(self):
        with self.assertRaises(SystemExit):
            digest.build_digest(self.m, dict(self.cfg(), digest_to=None))

    def test_busy_day_fits_size_budget(self):
        m = json.loads(json.dumps(self.m))
        long = "Synthetic reason text that is deliberately long to mimic a verbose rule explanation. " * 6
        for i in range(60):
            pri = "High" if i % 2 else "Medium"
            m["actions"].append({"id": f"x{i}", "rule": "synthetic", "kind": "risk", "priority": pri, "new": True,
                                 "account_id": "acc2", "account_name": f"Synthetic Account Number {i}",
                                 "title": "Synthetic title", "why": long})
            m["events"].append({"id": f"y{i}", "priority": pri, "new": True, "date": fx.DAY1, "account_id": "acc2",
                                "account_name": f"Synthetic Account Number {i}", "text": "Synthetic seat change " * 4})
        body, meta = digest.build_digest(m, self.cfg(), self.st, today=fx.DAY1)
        self.assertLessEqual(len(json.dumps(body, indent=2)), digest.MAX_BODY_BYTES)
        content = body["message"]["body"]["content"]
        self.assertIn("more in the dashboard", content)
        self.assertLess(content.count("<li>"), 3 * digest.MAX_ITEMS)
        self.assertTrue({f"x{i}" for i in range(60)} <= set(meta["ids"]))

    def test_find_send(self):
        body, meta = digest.build_digest(self.m, self.cfg(), self.st, today=fx.DAY1)
        self.assertIsNotNone(digest.find_send(meta, "s1", events=[self.send_event(meta, body)]))
        self.assertIsNotNone(digest.find_send(meta, "s1", events=[self.send_event(meta, body, as_text=True)]))
        self.assertIsNone(digest.find_send(meta, "s1", events=[self.send_event(meta, body, status=400)]))
        self.assertIsNone(digest.find_send(meta, "s1", events=[self.send_event(meta, body, subject="Other")]))
        self.assertIsNone(digest.find_send(meta, "s1", events=[self.send_event(meta, body, ts="2026-01-01T00:00:00Z")]))
        other = json.loads(json.dumps(body))
        other["message"]["toRecipients"].append({"emailAddress": {"address": "someone@example.com"}})
        self.assertIsNone(digest.find_send(meta, "s1", events=[self.send_event(meta, other)]))

    def test_mark_sent_dedupes_and_next_day_is_new(self):
        body, meta = digest.build_digest(self.m, self.cfg(), self.st, today=fx.DAY1)
        self.assertTrue(digest.needs_send(meta))
        with self.assertRaises(SystemExit):
            digest.mark_sent(meta, "s1", events=[])
        os.makedirs(paths.state_dir(), exist_ok=True)
        with open(model.notified_path(), "w") as f:
            json.dump({"items": {"old": {"first": "2026-05-01", "last_seen": "2026-06-01"}}}, f)
        digest.mark_sent(meta, "s1", events=[self.send_event(meta, body)])
        doc = read_json(model.notified_path())
        self.assertNotIn("old", doc["items"])
        self.assertEqual(len(doc["items"]), len(meta["ids"]))
        self.assertTrue(doc["sent"][-1]["verified"])

        again, _ = model.build(self.cfg(), fx.DAY1)
        self.assertFalse(again["baseline"])
        self.assertFalse(any(e["new"] for e in again["events"]))
        self.assertFalse(any(a["new"] for a in again["actions"]))
        body2, meta2 = digest.build_digest(again, self.cfg(), today=fx.DAY1)
        self.assertEqual(meta2["counts"]["high"], 0)
        self.assertIn("No new changes", body2["message"]["body"]["content"])
        self.assertFalse(digest.needs_send(meta2))

        fx.write_day2(self.home)
        m2, _ = model.build(self.cfg(), fx.DAY2)
        new = sorted((e["account_id"], e["kind"]) for e in m2["events"] if e["new"])
        self.assertEqual(new, [("acc4", "added"), ("acc5", "left_book"), ("acc6", "entered_book")])
        left = next(e for e in m2["events"] if e["kind"] == "left_book")
        self.assertEqual(left["account_name"], "Driftwood Ceramics")
        feeds = {f["feed"]: f["state"] for f in m2["sources"]}
        self.assertEqual((feeds["book"], feeds["contracts"], feeds["ghas"]), ("ok", "ok", "failed"))
        body3, meta3 = digest.build_digest(m2, self.cfg(), today=fx.DAY2)
        self.assertEqual(meta3["subject"], "Customer Pulse · 2026-10-27 · 0 high · data issues")
        self.assertIn("+20 GHE contracted seats", body3["message"]["body"]["content"])
        self.assertTrue(digest.needs_send(meta3))

    def test_actioned_state_suppresses_new(self):
        first = self.m["actions"][0]
        os.makedirs(paths.state_dir(), exist_ok=True)
        with open(model.actioned_path(), "w") as f:
            json.dump({"items": {first["id"]: {"state": "dismissed", "at": "2026-10-26T08:00:00Z"}}}, f)
        m, _ = model.build(self.cfg(), fx.DAY1)
        a = next(x for x in m["actions"] if x["id"] == first["id"])
        self.assertEqual((a["state"], a["new"]), ("dismissed", False))

    def test_prune_raw(self):
        old = os.path.join(self.home, "data", "raw", "2026-09-01")
        os.makedirs(old)
        model.prune_raw(self.cfg(), fx.DAY1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(os.path.join(self.home, "data", "raw", fx.DAY1)))


class PrivacyTest(fx.HomeCase):
    def test_record_ids(self):
        real = "00" + "1" + "Kq" * 6
        self.assertEqual([m.group(1) for m in privacy.SFDC_RECORD.finditer(f"id={real} x")], [real])
        self.assertEqual(privacy.scan_lines([("f", 1, f"id {real}AAA")], None)[0][3], real + "AAA")
        self.assertEqual(privacy.scan_lines([("f", 1, "00" + "5" + "X" * 15)], None), [])
        self.assertEqual(privacy.scan_lines([("f", 1, "00" + "2" + "Kq" * 6)], None), [])

    def test_name_terms(self):
        words = {"lantern", "freight"}
        terms = privacy.terms_from_names(["Quillfeather Analytics Pvt Ltd", "Lantern", "Lantern Freight", "GitHub Inc", "Abc"],
                                         dictionary=words)
        self.assertIn("Quillfeather Analytics", terms)
        self.assertIn("Quillfeather Analytics Pvt Ltd", terms)
        self.assertIn("Lantern Freight", terms)
        self.assertNotIn("Lantern", terms)
        self.assertNotIn("GitHub", terms)
        self.assertNotIn("Abc", terms)
        self.assertEqual(privacy.terms_from_names(["Quillfeather Labs"], allow=["quillfeather"], dictionary=set()),
                         ["Quillfeather Labs"])

    def test_patterns_from_local_data(self):
        fx.write_day1(self.home)
        rx, n = privacy.build_patterns(self.cfg())
        self.assertGreater(n, 0)
        hits = privacy.scan_lines([("a.py", 3, "# notes about tessellate robotics"), ("b.py", 1, "mail me@example.com"),
                                   ("c.py", 1, "nothing to see")], rx)
        self.assertEqual([(h[0], h[3].lower()) for h in hits], [("a.py", "tessellate robotics"), ("b.py", "me@example.com")])

    def test_staged_lines(self):
        repo = tempfile.mkdtemp(prefix="cp-git-")
        subprocess.run(["git", "init", "-q", repo], check=True)
        with open(os.path.join(repo, "f.txt"), "w") as f:
            f.write("one\ntwo\n")
        subprocess.run(["git", "-C", repo, "add", "f.txt"], check=True)
        self.assertEqual(list(privacy.staged_lines(repo)), [("f.txt", 1, "one"), ("f.txt", 2, "two")])


class CliTest(fx.HomeCase):
    def run_cli(self, *args):
        env = dict(os.environ, CP_HOME=self.home, COPILOT_AGENT_SESSION_ID="cli-test")
        p = subprocess.run([sys.executable, os.path.join(fx.ROOT, "pulse.py"), *args], capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr

    def test_build_status_digest_mark_sent(self):
        fx.write_day1(self.home)
        code, out, err = self.run_cli("build", "--today", fx.DAY1, "--no-prune")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["accounts"], 5)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        code, out, err = self.run_cli("digest", "--today", fx.DAY1)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(json.loads(out)["file"]))
        self.assertTrue(json.loads(out)["send"])
        code, _, err = self.run_cli("digest", "--mark-sent")
        self.assertNotEqual(code, 0)
        code, out, err = self.run_cli("digest", "--mark-sent", "--force")
        self.assertEqual(code, 0, err)
        self.assertTrue(read_json(model.notified_path())["items"])
        self.assertEqual(self.run_cli("build", "--today", fx.DAY1, "--no-prune")[0], 0)
        code, out, err = self.run_cli("digest", "--today", fx.DAY1)
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertFalse(info["send"])
        self.assertNotIn("send_with", info)

    def test_build_error_keeps_previous_model_and_flags_digest(self):
        fx.write_day1(self.home)
        self.assertEqual(self.run_cli("build", "--today", fx.DAY1, "--no-prune")[0], 0)
        before = read_json(paths.model_path())["generated_at"]
        with open(os.path.join(self.home, "data", "raw", fx.DAY1, "p02.json"), "w") as f:
            f.write("{not json")
        code, _, err = self.run_cli("build", "--today", fx.DAY1, "--no-prune")
        self.assertEqual(code, 1)
        self.assertIn("JSONDecodeError", err)
        self.assertEqual(read_json(paths.model_path())["generated_at"], before)
        st = read_json(paths.status_path())
        self.assertEqual(st["overall"], "failed")
        self.assertTrue(st["build_error"].startswith("JSONDecodeError"))
        code, out, _ = self.run_cli("status")
        self.assertIn("FAILED", out)
        self.assertIn("build error", out)
        code, out, err = self.run_cli("digest", "--today", fx.DAY1)
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertTrue(info["subject"].endswith("· data issues"))
        with open(info["file"]) as f:
            self.assertIn("Build failed", json.load(f)["message"]["body"]["content"])


if __name__ == "__main__":
    unittest.main()
