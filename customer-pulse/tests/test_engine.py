import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures as fx  # noqa: E402

from pulse import digest, model, rules, seats, sources, status  # noqa: E402

TH = fx.TH


class DecodeTest(unittest.TestCase):
    def test_split_points_base64_json_and_empty_fields(self):
        self.assertEqual(sources.split_points(fx.b64(["20261025|5||7"])), [["20261025", "5", None, "7"]])
        self.assertEqual(sources.split_points('["20261025|1"]'), [["20261025", "1"]])
        self.assertEqual(sources.split_points("20261025|2"), [["20261025", "2"]])
        self.assertEqual(sources.split_points(None), [])

    def test_kusto_rows_accepts_object_and_positional_rows(self):
        mcp = {"result": {"columns": ["a", "b"], "rows": [{"a": 1, "b": "x"}]}}
        positional = {"result": {"columns": [{"name": "a"}, {"name": "b"}], "rows": [[1, "x"]]}}
        self.assertEqual(sources.kusto_rows(mcp), [{"a": 1, "b": "x"}])
        self.assertEqual(sources.kusto_rows(positional), [{"a": 1, "b": "x"}])
        self.assertEqual(sources.kusto_rows({"result": {"columns": ["a"], "rows": ["junk", None]}}), [])

    def test_iso_and_sfid18(self):
        self.assertEqual(sources.iso("20261024"), "2026-10-24")
        self.assertEqual(sources.iso("2026-10-24T00:00:00Z"), "2026-10-24")
        self.assertIsNone(sources.iso("junk"))
        self.assertEqual(sources.sfid18("a" * 15), "a" * 15 + "AAA")
        self.assertEqual(sources.sfid18("A" + "a" * 13 + "A"), "A" + "a" * 13 + "A" + "BAQ")
        self.assertEqual(sources.sfid18("acc1"), "acc1")

    def test_parse_contracted_runs_and_meta(self):
        doc = {"result": fx.p02({"acc1": [("GHSP", "2026-10-01", "2026-10-03", 5, 900, "A; B")]},
                                [("2026-10-01", 10, 50)])}
        runs, meta = sources.parse_contracted(doc)
        self.assertEqual(runs, {"acc1": {"GHSP": [("2026-10-01", "2026-10-03", 5.0, 900.0, ["A", "B"])]}})
        self.assertEqual(meta, [("2026-10-01", 10.0, 50.0)])

    def test_parse_copilot_latest_record(self):
        doc = {"result": fx.series([("acc4", "health_latest", [("2026-10-25", 50, 20, 5, 4, 3, 2, 0.9, 60, 0.8, 40, 50, 45, "Red")])])}
        rec = sources.parse_copilot(doc)["health_latest"]["acc4"]
        self.assertEqual(rec["date"], "2026-10-25")
        self.assertEqual(rec["category"], "Red")
        self.assertEqual(rec["grr_30d"], 0.9)


class HealthyDaysTest(unittest.TestCase):
    def test_bad_first_day_rejected(self):
        meta = [("d0", 100, 1000.0)] + [(f"d{i}", 1000, 100000.0) for i in range(1, 6)]
        healthy, why = sources.healthy_days(meta)
        self.assertNotIn("d0", healthy)
        self.assertIn("rows", why["d0"])
        self.assertEqual(healthy, {f"d{i}" for i in range(1, 6)})

    def test_zero_sum_snapshot_rejected(self):
        meta = [("m1", 1000, 5000.0), ("m2", 1000, 5100.0), ("m3", 1000, 0.0)]
        healthy, why = sources.healthy_days(meta)
        self.assertEqual(healthy, {"m1", "m2"})
        self.assertIn("total", why["m3"])

    def test_level_shift_accepted_after_a_few_days(self):
        meta = [(f"d{i:02d}", 1000, 1000.0 if i < 10 else 2000.0) for i in range(20)]
        healthy, _ = sources.healthy_days(meta)
        self.assertEqual([f"d{i:02d}" for i in range(10, 14) if f"d{i:02d}" in healthy], [])
        self.assertTrue(all(f"d{i:02d}" in healthy for i in range(14, 20)))

    def test_trailing_unhealthy(self):
        meta = [("a", 1000, 1.0), ("b", 1000, 1.0), ("c", 10, 1.0), ("d", 10, 1.0)]
        self.assertEqual(sources.trailing_unhealthy(meta, {"a", "b"}), 2)
        self.assertEqual(sources.trailing_unhealthy(meta, {"a", "b", "c", "d"}), 0)


def runs_to_days(runs, day_list, fam):
    return seats.contracted_daily(runs, day_list, fam)


class ContractedEventsTest(unittest.TestCase):
    D = fx.days("2026-10-01", "2026-10-06")

    def events(self, runs, fam="ghe", day_list=None):
        day_list = day_list or self.D
        return seats.contracted_events("acc1", fam, day_list, runs_to_days(runs, day_list, fam), TH)

    def test_missing_row_means_zero(self):
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 40, 0, [])]})
        self.assertEqual([(e["kind"], e["priority"], e["date"]) for e in ev], [("product_to_zero", "High", "2026-10-04")])

    def test_new_product(self):
        ev = self.events({"GitHub Enterprise": [("2026-10-04", "2026-10-06", 40, 0, [])]})
        self.assertEqual([(e["kind"], e["priority"]) for e in ev], [("new_product", "Notify")])

    def test_migration_between_types_is_info(self):
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 100, 0, [])],
                          "GitHub Enterprise Cloud": [("2026-10-04", "2026-10-06", 100, 0, [])]})
        self.assertEqual([(e["kind"], e["priority"]) for e in ev], [("migration", "Info")])
        self.assertIn("moved between products", model.event_text(ev[0]))

    def test_ghas_same_direction_type_change_reported_per_type(self):
        ev = self.events({"GHCS": [("2026-10-01", "2026-10-06", 70, 0, [])],
                          "GHSP": [("2026-10-01", "2026-10-03", 50, 0, []), ("2026-10-04", "2026-10-06", 60, 0, [])]},
                         fam="ghas")
        self.assertEqual([(e["kind"], e.get("product_type"), e["from"], e["to"]) for e in ev], [("added", "GHSP", 50.0, 60.0)])
        self.assertTrue(model.event_text(ev[0]).startswith("+10 Secret Protection seats"))

    def test_sku_only_change_is_not_an_event(self):
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 100, 0, ["Old SKU"]),
                                                ("2026-10-04", "2026-10-06", 100, 0, ["New SKU"])]})
        self.assertEqual(ev, [])

    def test_drop_priorities(self):
        small = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 200, 0, []), ("2026-10-04", "2026-10-06", 195, 0, [])]})
        big = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 200, 0, []), ("2026-10-04", "2026-10-06", 150, 0, [])]})
        self.assertEqual((small[0]["kind"], small[0]["priority"]), ("dropped", "Medium"))
        self.assertEqual((big[0]["kind"], big[0]["priority"]), ("dropped", "High"))

    def test_percentage_drop_needs_min_seats_for_high(self):
        one = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 10, 0, []), ("2026-10-04", "2026-10-06", 9, 0, [])]})
        three = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 20, 0, []), ("2026-10-04", "2026-10-06", 17, 0, [])]})
        self.assertEqual((one[0]["kind"], one[0]["priority"]), ("dropped", "Medium"))
        self.assertEqual((three[0]["kind"], three[0]["priority"]), ("dropped", "High"))
        self.assertTrue(model.event_text(one[0]).startswith("−1 GitHub Enterprise seat (10 → 9)"))

    def test_tiny_product_to_zero_is_medium(self):
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-03", 1, 0, [])]})
        self.assertEqual([(e["kind"], e["priority"]) for e in ev], [("product_to_zero", "Medium")])

    def test_gap_reads_as_range_in_digest(self):
        day_list = ["2026-10-01", "2026-10-02", "2026-10-06"]
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-04", 100, 0, []), ("2026-10-05", "2026-10-06", 130, 0, [])]},
                         day_list=day_list)[0]
        ev["text"] = model.event_text(ev)
        self.assertEqual(digest._event_when(ev), ("+30 GHE contracted seats (100 → 130)", "between Oct 2 and Oct 6"))
        ev = dict(ev, since=None, gap_days=None, text="+30 GHE contracted seats (100 → 130)")
        self.assertEqual(digest._event_when(ev), ("+30 GHE contracted seats (100 → 130)", "on Oct 6"))

    def test_revert_marks_both(self):
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-02", 100, 0, []), ("2026-10-03", "2026-10-03", 90, 0, []),
                                                ("2026-10-04", "2026-10-06", 100, 0, [])]})
        self.assertEqual(len(ev), 2)
        self.assertTrue(all(e["reverted"] for e in ev))
        self.assertEqual(ev[1]["reverts"], ev[0]["id"])

    def test_gap_reads_since(self):
        day_list = ["2026-10-01", "2026-10-02", "2026-10-06"]
        ev = self.events({"GitHub Enterprise": [("2026-10-01", "2026-10-04", 100, 0, []), ("2026-10-05", "2026-10-06", 130, 0, [])]},
                         day_list=day_list)
        self.assertEqual((ev[0]["since"], ev[0]["gap_days"]), ("2026-10-02", 4))
        self.assertTrue(model.event_text(ev[0]).endswith("since 2026-10-02"))

    def test_event_ids_are_stable(self):
        runs = {"GitHub Enterprise": [("2026-10-01", "2026-10-03", 200, 0, []), ("2026-10-04", "2026-10-06", 150, 0, [])]}
        self.assertEqual(self.events(runs)[0]["id"], self.events(runs)[0]["id"])


class ConsumedEventsTest(unittest.TestCase):
    D = fx.days("2026-10-01", "2026-10-12")

    def kinds(self, values, trend=True):
        return [(e["kind"], e["priority"], e["date"]) for e in
                seats.consumed_events("acc1", "ghe", "metered", self.D[:len(values)], values, TH, trend=trend)]

    def test_missing_is_not_zero(self):
        self.assertEqual(self.kinds([50, 50, None, 50, 50], trend=False),
                         [("stopped", "Medium", "2026-10-03"), ("resumed", "Info", "2026-10-04")])

    def test_started(self):
        self.assertEqual(self.kinds([None, None, 30, 30], trend=False), [("started", "Notify", "2026-10-03")])

    def test_reporting_zero_is_not_a_change(self):
        self.assertEqual(self.kinds([None, 0, 0, None, 0], trend=False), [])
        self.assertEqual(self.kinds([None, 0, 8], trend=False), [("added", "Notify", "2026-10-03")])

    def test_after_gap_change_is_flagged(self):
        ev = seats.consumed_events("acc1", "ghe", "metered", self.D[:4], [100, None, None, 60], TH, trend=False)
        self.assertEqual([(e["kind"], e["priority"]) for e in ev], [("stopped", "Medium"), ("dropped", "High")])
        self.assertTrue(ev[1]["after_gap"])
        self.assertEqual(ev[1]["since"], "2026-10-01")

    def test_small_noise_ignored(self):
        self.assertEqual(self.kinds([100, 102, 99, 101], trend=False), [])

    def test_slow_trend(self):
        vals = [100, 103, 106, 109, 112, 115, 118, 121, 124, 127]
        ev = self.kinds(vals)
        self.assertEqual(ev, [("trend_up", "Notify", "2026-10-10")])

    def test_trend_suppressed_by_same_direction_step(self):
        vals = [100] * 8 + [130, 130]
        self.assertEqual(self.kinds(vals), [("added", "Notify", "2026-10-09")])

    def test_flap_marked_reverted(self):
        ev = seats.consumed_events("acc1", "ghe", "metered", self.D[:4], [100, 60, 100, 100], TH, trend=False)
        self.assertTrue(all(e["reverted"] for e in ev))


class BookEventsTest(unittest.TestCase):
    def test_entered_and_left(self):
        ev = seats.book_events({"acc1": {"name": "A"}, "acc2": {"name": "B"}}, {"acc1": {"name": "A"}, "acc3": {"name": "C"}},
                               "2026-10-25", "2026-10-26")
        self.assertEqual(sorted((e["kind"], e["account_id"]) for e in ev), [("entered_book", "acc2"), ("left_book", "acc3")])
        self.assertEqual(seats.book_events({"acc1": {}}, None, None, "2026-10-26"), [])


def account(**over):
    base = {"id": "acc1", "name": "Synthetic", "renewal": None, "renewing_in_days": None, "arr": 1000,
            "products": {"ghe": {"contracted": 0, "ubu": None, "adoption": None, "billing": None},
                         "ghas": {"contracted": 0, "committers": None, "by_type": {}, "code_quality": None},
                         "ghcp": {"contracted": 0, "assigned": None, "at_risk": None, "health": None}}}
    for k, v in over.items():
        if k in ("ghe", "ghas", "ghcp"):
            base["products"][k].update(v)
        else:
            base[k] = v
    return base


class RulesTest(unittest.TestCase):
    import datetime as _dt
    TODAY = _dt.date(2026, 10, 26)

    def rules(self, acc, events=()):
        return {a["rule"]: a for a in rules.account_actions(acc, list(events), TH, self.TODAY)}

    def test_overage_volume_and_mixed_not_metered(self):
        for billing, expect in (("volume", True), ("mixed", True), ("metered", False)):
            got = self.rules(account(ghe={"contracted": 100, "ubu": 120, "billing": billing, "adoption": 1.2}))
            self.assertEqual("ghe_overage" in got, expect, billing)
        self.assertEqual(self.rules(account(ghe={"contracted": 100, "ubu": 120, "billing": "volume"}))["ghe_overage"]["priority"], "High")

    def test_renewal_folds_risks_into_one_high(self):
        acc = account(renewing_in_days=60, renewal="2026-12-25",
                      ghe={"contracted": 100, "ubu": 30, "adoption": 0.3, "billing": "volume"})
        got = self.rules(acc)
        self.assertIn("renewal_risk", got)
        self.assertNotIn("ghe_low_adoption", got)
        self.assertEqual(got["renewal_risk"]["priority"], "High")
        self.assertIn("Low GHE seat adoption", got["renewal_risk"]["reasons"][0])

    def test_zero_ghe_usage_is_worded_as_not_reported(self):
        got = self.rules(account(ghe={"contracted": 160, "ubu": 0, "adoption": 0.0, "billing": "volume"}))
        self.assertEqual(got["ghe_low_adoption"]["title"], "No GHE usage reported")
        self.assertIn("160 contracted", got["ghe_low_adoption"]["why"])
        self.assertNotIn("0%", got["ghe_low_adoption"]["why"])
        got = self.rules(account(ghe={"contracted": 160, "ubu": 40, "adoption": 0.25, "billing": "volume"}))
        self.assertEqual(got["ghe_low_adoption"]["title"], "Low GHE seat adoption")

    def test_ghas_low_usage_singular(self):
        why = self.rules(account(ghas={"contracted": 10, "committers": 1}))["ghas_low_usage"]["why"]
        self.assertTrue(why.startswith("1 active committer on 10 GHAS seats"), why)

    def test_renewal_with_recent_contracted_drop(self):
        drop = {"account_id": "acc1", "date": "2026-10-20", "measure": "contracted", "kind": "dropped", "family": "ghe",
                "from": 200.0, "to": 150.0, "reverted": False}
        got = self.rules(account(renewing_in_days=90, renewal="2027-01-24", ghe={"contracted": 150}), [drop])
        self.assertIn("renewal_risk", got)
        to_zero = dict(drop, kind="product_to_zero", to=0.0)
        why = self.rules(account(renewing_in_days=90, renewal="2027-01-24"), [to_zero])["renewal_risk"]["why"]
        self.assertTrue(why.startswith("Renews Jan 24, 2027. "), why)
        self.assertIn("Contracted GHE seats went to zero on Oct 20 (200 → 0).", why)
        reverted = dict(drop, reverted=True)
        self.assertNotIn("renewal_risk", self.rules(account(renewing_in_days=90, ghe={"contracted": 150}), [reverted]))

    def test_whitespace_and_penetration(self):
        got = self.rules(account(ghe={"contracted": 400}, ghcp={"contracted": 50, "assigned": 40}))
        self.assertEqual(got["ghas_whitespace"]["priority"], "Medium")
        self.assertEqual(got["copilot_penetration"]["title"], "Copilot expansion")
        got = self.rules(account(ghe={"contracted": 400}))
        self.assertEqual(got["copilot_penetration"]["title"], "Copilot whitespace")

    def test_copilot_risk_and_ghas_usage(self):
        got = self.rules(account(ghcp={"assigned": 50, "at_risk": 20, "health": {"category": "Green"}},
                                 ghas={"contracted": 50, "committers": 20}))
        self.assertIn("copilot_at_risk", got)
        self.assertIn("ghas_low_usage", got)
        got = self.rules(account(ghas={"contracted": 50, "committers": 60}))
        self.assertEqual(got["ghas_overage"]["priority"], "High")

    def test_all_actions_sorted_high_first(self):
        accs = [account(id="acc1", ghe={"contracted": 400}),
                account(id="acc2", arr=5, ghe={"contracted": 100, "ubu": 150, "billing": "volume"})]
        out = rules.all_actions(accs, [], TH, self.TODAY)
        self.assertEqual(out[0]["priority"], "High")
        self.assertEqual(out[0]["account_id"], "acc2")


class StatusTest(unittest.TestCase):
    def src(self, raw_date="2026-10-26", meta=None, healthy=None):
        meta = meta if meta is not None else [(d, 1000, 1.0) for d in fx.days("2026-10-20", "2026-10-25")]
        healthy = healthy if healthy is not None else {m[0] for m in meta}
        files = {q: {"raw_date": raw_date, "via": "mcp", "rows": 1} for q in ("p01", "p02", "p03", "p04", "p05", "p06")}
        keys = ("contracted", "metered", "volume", "health", "seats", "ghas")
        return {"files": files, "attrs": {"acc1": {"measurement_date": "2026-10-24"}},
                "healthy": {k: set(healthy) for k in keys}, "meta": {k: list(meta) for k in keys},
                "unhealthy_why": {k: {m[0]: "200 rows vs ~1000 expected" for m in meta} for k in keys}}

    def by_feed(self, src, today="2026-10-26"):
        return {f["feed"]: f for f in status.feed_status(src, today, TH)}

    def test_ok(self):
        feeds = self.by_feed(self.src())
        self.assertEqual({f["state"] for f in feeds.values()}, {"ok"})
        self.assertEqual(status.overall(list(feeds.values())), "ok")

    def test_failed_and_missing(self):
        src = self.src(raw_date="2026-10-25")
        del src["files"]["p05"]
        feeds = self.by_feed(src)
        self.assertEqual(feeds["contracts"]["state"], "failed")
        self.assertEqual(feeds["ghas"]["state"], "missing")
        self.assertEqual(status.overall(list(feeds.values())), "missing")
        self.assertEqual(status.overall(list(feeds.values()), build_error="boom"), "failed")

    def test_suspect_after_three_held_days(self):
        meta = [(d, 1000, 1.0) for d in fx.days("2026-10-18", "2026-10-25")]
        two = self.by_feed(self.src(meta=meta, healthy={m[0] for m in meta[:-2]}))
        three = self.by_feed(self.src(meta=meta, healthy={m[0] for m in meta[:-3]}))
        self.assertEqual(two["contracts"]["state"], "ok")
        self.assertIn("held back", two["contracts"]["messages"][0])
        self.assertEqual(three["contracts"]["state"], "suspect")

    def test_stale(self):
        meta = [(d, 1000, 1.0) for d in fx.days("2026-10-10", "2026-10-15")]
        feeds = self.by_feed(self.src(meta=meta))
        self.assertEqual(feeds["contracts"]["state"], "stale")
        self.assertEqual(feeds["copilot_seats"]["state"], "ok")


class EventNewTest(unittest.TestCase):
    def ev(self, **over):
        e = {"id": "e1", "recent": True, "reverted": False, "family": "ghe", "measure": "contracted", "date": "2026-10-25"}
        e.update(over)
        return e

    def test_baseline_only_latest_day_is_new(self):
        latest = {"contracted": "2026-10-25"}
        self.assertTrue(model._event_new(self.ev(), {}, True, latest))
        self.assertFalse(model._event_new(self.ev(date="2026-10-24"), {}, True, latest))
        self.assertTrue(model._event_new(self.ev(date="2026-10-24"), {}, False, latest))

    def test_notified_and_old_are_not_new(self):
        self.assertFalse(model._event_new(self.ev(), {"e1": {}}, False, {}))
        self.assertFalse(model._event_new(self.ev(recent=False), {}, False, {}))

    def test_revert_is_new_only_if_original_was_emailed(self):
        e = self.ev(reverted=True, reverts="e0")
        self.assertFalse(model._event_new(e, {}, False, {}))
        self.assertTrue(model._event_new(e, {"e0": {}}, False, {}))

    def test_action_renotify(self):
        a = {"id": "a1"}
        self.assertTrue(model._action_new(a, {}, "2026-10-26", TH))
        self.assertFalse(model._action_new(a, {"a1": {"last_seen": "2026-10-25"}}, "2026-10-26", TH))
        self.assertTrue(model._action_new(a, {"a1": {"last_seen": "2026-10-19"}}, "2026-10-26", TH))


if __name__ == "__main__":
    unittest.main()
