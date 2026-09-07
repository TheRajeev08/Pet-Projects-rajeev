#!/usr/bin/env python3
"""FY27 H1 leadership deck - the 30-minute executive cut.

Eleven slides: the seven questions leadership asked, plus a carry-forward slide and a
live-deals slide, and nothing else. The long deck (deck.py) remains the evidence pack;
this is what gets presented. Theme and layout
primitives are imported from deck.py so both decks stay visually identical and only one
of them owns rendering behaviour.

    exec_deck.py <runDir> <out.pptx>

Reads focus-accounts.json, potential.json, coverage.json, crm-context.json,
learnings.json, partners.json and fy27-territory-plan.json from the run directory.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deck import (  # noqa: E402
    Deck, money, num, truncate, truncate_fit,
    INK, PANEL, PANEL_2, LINE, TEXT, MUTED, WHITE, ACCENT, WARN, GOOD, PLAY_COLOR,
    MARGIN, BODY_TOP, W, H,
)
from pptx.util import Inches, Emu, Pt  # noqa: E402
from pptx.enum.text import PP_ALIGN  # noqa: E402

PLAYS = ("Innovate", "Trust", "Scale")

# Slide 2 renders one row per learning. Six wrapped rows is what fits between BODY_TOP
# and the footnote; adding a seventh needs the row height revisited, not this bumped.
LEARNING_ROWS = 6

# Same constraint on the key-deals table: seven wrapped rows fit above the footnote.
KEY_DEAL_ROWS = 7

PLAY_THESIS = {
    "Innovate": "Copilot seat expansion - a GHE base with attach under 25%, "
                "or a Copilot-led account",
    "Trust": "On GHE with Copilot embedded, or regulated - govern and secure at scale",
    "Scale": "Not yet on GHE - consolidate onto the platform, Copilot or Teams as the way in",
}

# Who runs the account. Derived from the Microsoft tier set in crm_context.py: a named
# Microsoft owner means co-sell, a TPID alone means the deal needs a delivery partner,
# neither means it is mine to run direct.
LED_LABEL = {1: "Microsoft led", 2: "Partner led", 3: "Seller led"}
LED_COLOR = {1: WHITE, 2: WARN, 3: MUTED}

# One account per play is worked through end to end; the rest are listed. The default is
# the highest-ranked account in the play, because that is the one leadership will ask
# about. A seller who wants a different illustration drops a `paf-accounts.json` in the
# run directory - {"Innovate": "<account name>", ...} - and it is honoured where the name
# matches an account in that play. No account name is hard-coded into the skill.
PAF_ACCOUNTS_FILE = "paf-accounts.json"

# Gathered conversation history for the worked-example accounts, and the PAF key actions
# selected to answer what that account actually said. Both are seller/run data rather than
# skill data: they name customers and quote their objections, so they live in the run
# directory and are never published with the plugin. Absent the file, the panel falls back
# to the generic key-action sequence for the play, which is what it always did.
CONVERSATIONS_FILE = "conversations.json"

# Seller-authored strategic asks that are not derivable from the data. Same reasoning:
# they are one seller's asks, so they are read from the run directory rather than
# hard-coded. Absent the file, slide 11 renders exactly the computed asks it always did.
ASKS_FILE = "asks.json"

# The product each play sells, and the token that reports it in the account's
# consumption string ("GHE 2 + VS bundle 0; CfB 76; ... GHAS 0").
PLAY_PRODUCT = {"Innovate": "Copilot", "Trust": "GHAS", "Scale": "GHE"}
CONSUMPTION_TOKEN = {"Innovate": "CfB", "Trust": "GHAS", "Scale": "GHE"}

# Four key actions fit the panel. Every play has at least four in both phases, so this
# never silently drops to a shorter sequence.
PAF_STEPS = 4

# The two motions every play runs, and the PAF phase each one draws its key actions from.
#
# The split is not a new judgement and carries no tuned threshold: an account that is not
# yet consuming the play's product is being *landed*, and one that already consumes it is
# being *expanded*. That is the same `land`/`expand` test `slide_play` already applies to
# its worked example, read from the account's own consumption string. Because it keys off
# the product rather than off any territory's shape, a book with no landed accounts simply
# renders one motion as empty and says so.
PLAY_MOTION = {
    "Innovate": {
        "land": ("SEAT EXPANSION",
                 "Turn a GHE base with no paid Copilot into landed, active seats"),
        "expand": ("USAGE EXPANSION",
                   "Seats are live - convert them into sustained AI usage"),
    },
    "Trust": {
        "land": ("SECURITY ACTIVATION",
                 "Active committers are unprotected - get scanning switched on"),
        "expand": ("COVERAGE EXPANSION",
                   "GHAS is live - widen coverage and remediate the debt behind it"),
    },
    "Scale": {
        "land": ("PLATFORM LANDING",
                 "Not yet on GHE - consolidate the estate onto the platform"),
        "expand": ("PLATFORM EXPANSION",
                   "GHE is in - migrate CI/CD onto Actions and govern at scale"),
    },
}

# Six key actions fit the execution slide's taller motion column.
EXEC_STEPS = 6

# Two columns of seven. Scale is the largest play at 15 accounts, so 14 listed beside
# the worked example is the binding case.
LIST_ROWS = 7


def load(run_dir, name, default=None):
    try:
        with open(os.path.join(run_dir, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def ratio_color(value):
    if value is None:
        return MUTED
    if value < 1.0:
        return WARN
    if value > 5.0:
        return ACCENT
    return GOOD


def bucket_of(coverage, name):
    for bucket in coverage.get("buckets", []) or []:
        if bucket.get("bucket") == name:
            return bucket
    return {}


def led_by(account):
    """Microsoft tier -> who runs the account."""
    return LED_LABEL.get(int(account.get("msftTier") or 3), "Seller led")


def partner_names(partner_map, account):
    named = [p.get("name") for p in
             (partner_map.get(account.get("salesforceId"), {}) or {}).get("partners", [])
             if p.get("name") and p.get("name") != "Invalid"]
    # Order is not meaningful in the source, so de-duplicate while keeping first-seen.
    seen, out = set(), []
    for name in named:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def consumption_token(text, token):
    """Read one figure out of 'GHE 2 + VS bundle 0; CfB 76; ... GHAS 0'.

    Splitting on both ';' and '+' isolates each claim, so 'GHAS' cannot match inside
    'GHAzDO' and 'GHE' cannot match inside a bundle count that follows it.
    """
    for part in re.split(r"[;+]", str(text or "")):
        match = re.match(r"^\s*%s\s+([0-9,]+)" % re.escape(token), part)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def paf_steps(paf, play, phase, limit=PAF_STEPS):
    actions = ((paf or {}).get("plays", {}).get(play, {}) or {}).get(phase, []) or []
    return actions[:limit]


def paf_by_id(paf, play):
    """Every key action for a play, keyed by id, across both phases.

    Selecting actions by id lets the gathered evidence pick the action that answers the
    account's stated blocker, which can sit in either phase. An account mid-rollout may
    still need a land-phase action it skipped.
    """
    found = {}
    for phase in ("land", "expand"):
        for action in ((paf or {}).get("plays", {}).get(play, {}) or {}).get(phase, []) or []:
            if action.get("id"):
                found[action["id"]] = action
    return found


def selected_paf(paf, play, ids, phase):
    """Resolve gathered action ids against paf.json, falling back to the generic sequence.

    An id that no longer exists in paf.json is skipped rather than rendered as a blank
    row: paf.json is rebuilt from the knowledge base and ids can legitimately disappear.
    """
    catalog = paf_by_id(paf, play)
    chosen = [catalog[i] for i in (ids or []) if i in catalog]
    return chosen or paf_steps(paf, play, phase, limit=3)


# The consumption string is internal telemetry shorthand ("GHE 2 + VS bundle 0; CfB 0;
# ... ADO TAM 276"). Leadership should not have to decode it, so translate the tokens
# that carry meaning and drop the zeroes.
FOOTPRINT_LABELS = (
    ("GHE", "GHE seats"),
    ("VS bundle", "Visual Studio bundle seats"),
    ("CfB", "Copilot Business seats"),
    ("Teams", "Team plan seats"),
    ("UBB", "on usage-based billing"),
    ("committers L90d", "active committers (90d)"),
    ("GHAS", "GHAS committers"),
    ("ADO TAM", "Azure DevOps users"),
)


def footprint(text):
    parts = []
    for token, label in FOOTPRINT_LABELS:
        value = consumption_token(text, token)
        if not value:
            continue
        if value == 1:
            for plural, singular in (("seats", "seat"), ("committers", "committer"),
                                     ("users", "user")):
                label = label.replace(plural, singular)
        parts.append("%s %s" % (num(value), label))
    return ", ".join(parts) or "no product footprint recorded"


def account_phase(account, by_id, play):
    """'land' or 'expand' for one account, read from its own consumption string.

    Identical to the test `slide_play` applies to its worked example. Factored out so the
    execution slide splits the whole play on exactly the same rule the play slide already
    uses on one account - two different answers to "has this account got the product yet"
    on adjacent slides would be worse than either answer alone.
    """
    record = by_id.get(account.get("salesforceId"), {}) or {}
    owned = consumption_token(record.get("consumption", ""), CONSUMPTION_TOKEN[play])
    return "expand" if (owned or 0) > 0 else "land"


def motion_split(play, accounts, report):
    """Split a play's accounts into its land motion and its expand motion."""
    by_id = {a.get("salesforceId"): a for a in (report or {}).get("accounts", []) or []}
    split = {"land": [], "expand": []}
    for account in accounts:
        split[account_phase(account, by_id, play)].append(account)
    return split


def play_whitespace(licensing, account, play):
    """The unconverted population for one account, in the unit *this play* sells.

    Each play has a different prize, so a single figure would misstate two of them:
    Innovate sells Copilot onto GHE seats, Trust sells GHAS onto active committers, and
    Scale sells the platform to committers not yet on a GitHub seat at all. Reporting
    Copilot whitespace under a Trust slide would name the wrong prize on a slide a leader
    will push on.

    Returns None - not zero - when the account was never resolved to a GitHub tenant. A
    zero means "no headroom", which is a claim; None means "not looked up", which is not.
    Collapsing the two is how an unlicensed account gets read as a fully-converted one.
    """
    record = ((licensing or {}).get("accounts", {}) or {}).get(account.get("salesforceId"))
    if not record:
        return None
    seats = int(record.get("enterpriseSeatsConsumed") or 0)
    team = int(record.get("teamSeatsConsumed") or 0)
    committers = int(record.get("maxCommitters") or 0)
    if play == "Innovate":
        return max(0, seats - int(record.get("copilotSeats") or 0))
    if play == "Trust":
        return max(0, committers - int(record.get("ghasMeteredCommitters") or 0))
    return max(0, committers - seats - team)


# What the whitespace figure counts, per play. Kept beside play_whitespace so the label
# and the arithmetic can never drift apart.
WHITESPACE_LABEL = {
    "Innovate": "COPILOT WHITESPACE",
    "Trust": "GHAS WHITESPACE",
    "Scale": "PLATFORM WHITESPACE",
}
WHITESPACE_BASIS = {
    "Innovate": "GHE seats carrying no Copilot",
    "Trust": "active committers not licensed for GHAS",
    "Scale": "active committers not yet on a GitHub seat",
}


# ---------------------------------------------------------------- slides

def slide_1_scorecard(deck, coverage, focus):
    """Where I am today. Leadership's first question is always 'what is the number'."""
    slide = deck.slide()
    totals = coverage.get("totals", {})
    b1 = bucket_of(coverage, "Bucket 1")
    b2 = bucket_of(coverage, "Bucket 2")

    eyebrow = " \u00b7 ".join(
        p for p in (
            "{} {}".format(coverage.get("fiscalYear") or "FY27",
                           coverage.get("half") or "H1").strip(),
            "TERRITORY PLAN",
            (coverage.get("territory") or "").upper(),
        ) if p
    )
    deck.text(slide, MARGIN, Inches(0.52), W - 2 * MARGIN, Inches(0.28),
              eyebrow, size=11, color=ACCENT,
              bold=True, space=True)
    deck.text(slide, MARGIN, Inches(0.86), W - 2 * MARGIN, Inches(0.62),
              "Where I am, and how I make the number", size=32, color=WHITE, bold=True)
    deck.fill(slide, MARGIN, Inches(1.62), Inches(1.6), Inches(0.04), ACCENT)

    def bucket_card(x, bucket, color):
        w = Inches(6.0)
        h = Inches(2.2)
        deck.fill(slide, x, Inches(1.95), w, h, PANEL, line=LINE, radius=True)
        deck.text(slide, Emu(int(x + Inches(0.3))), Inches(2.16), Emu(int(w - Inches(0.6))),
                  Inches(0.26), bucket.get("label", "").upper(), size=11.5, color=color,
                  bold=True, space=True)
        target = bucket.get("h1Target")
        known = bucket.get("targetKnown")
        recurring = bucket.get("recurring")
        covered = bucket.get("h1Covered") or 0
        # A recurring bucket's headline is its carry, not its booked month. Showing one
        # month of consumption against a half-year target read as 14% attained when the
        # same run rate actually covers 85% of the half on its own.
        headline = covered if recurring else (bucket.get("attainedH1") or 0)
        deck.text(slide, Emu(int(x + Inches(0.3))), Inches(2.5), Inches(2.4), Inches(0.6),
                  money(headline), size=34, color=WHITE, bold=True)
        if known:
            right = "of %s H1 target" % money(target)
            gap = bucket.get("h1Gap")
            pct = bucket.get("h1CoveredPct") or 0
            if recurring:
                sub = ("%s growth needed \u00b7 %.0f%% covered by run rate"
                       % (money(max(0.0, float(gap or 0))), pct))
            else:
                sub = ("%s covered incl. H1 pipeline (%.0f%%)"
                       % (money(covered), pct))
        else:
            right = "H1 target: TBD"
            # Kept short: with no target the pipeline clause is appended below, and two
            # rendered lines would run into the Q1/Q2 split beneath it.
            sub = "no target set"
        deck.text(slide, Emu(int(x + Inches(2.85))), Inches(2.72), Emu(int(w - Inches(3.15))),
                  Inches(0.3), right, size=12.5, color=MUTED)
        # The Q1/Q2 split sits under the H1 headline: the half is the number, but the
        # quarters are how it is actually landed, and they are not evenly loaded.
        q1t = bucket.get("q1Target")
        q2t = (round(target - q1t, 2) if (known and q1t is not None) else None)
        if q1t is not None and q2t is not None:
            split = "Q1 %s \u00b7 Q2 %s" % (money(q1t), money(q2t))
            deck.text(slide, Emu(int(x + Inches(0.3))), Inches(3.44),
                      Emu(int(w - Inches(0.6))), Inches(0.26), split, size=12,
                      color=MUTED)
        # Pipeline belongs inside its own bucket panel. Shown as one blended figure it
        # reads as cover for whichever gap it happens to sit next to.
        live = bucket.get("livePipeline") or 0
        if live and not recurring:
            sub = "%s \u00b7 %s live H1 pipeline" % (sub, money(live))
        deck.text(slide, Emu(int(x + Inches(0.3))), Inches(3.12), Emu(int(w - Inches(0.6))),
                  Inches(0.28), sub, size=12, color=MUTED)

        # Progress bar. A number without a denominator is a claim; a bar is a position.
        bar_w = w - Inches(0.6)
        deck.fill(slide, Emu(int(x + Inches(0.3))), Inches(3.76), bar_w, Inches(0.16), PANEL_2)
        if known and target:
            filled = max(0.02, min(1.0, float(headline) / float(target)))
            deck.fill(slide, Emu(int(x + Inches(0.3))), Inches(3.76),
                      Emu(int(bar_w * filled)), Inches(0.16), color)
        return h

    bucket_card(MARGIN, b1, ACCENT)
    bucket_card(Inches(6.95), b2, PLAY_COLOR["Innovate"])

    ftotals = focus.get("totals", {})
    b1_pipe = (coverage.get("pipeline", {}) or {}).get("byBucket", {}).get("Bucket 1")
    run = coverage.get("runRate", {}) or {}
    month_total = round(sum(float(v or 0) for v in (run.get("products") or {}).values()), 2)
    growth_pct = round(float(run.get("growthPerQuarter") or 0) * 100)
    cards = [
        ("Focus accounts", num(focus.get("selectedCount")), "of %s in book" % num(focus.get("bookSize"))),
        ("Bucket 2 run rate", money(month_total) if month_total else "TBD",
         "per month \u00b7 Q1 flat, Q2 +%d%%" % growth_pct if month_total
         else "set runRate in targets.json"),
        ("Current ARR", money(ftotals.get("currentArr")), "installed base in focus set"),
        ("Bucket 1 H1 pipeline", money(b1_pipe), "GHE + GHAS, close-dated in H1"),
    ]
    x = MARGIN
    for label, value, sub in cards:
        deck.card(slide, x, Inches(4.42), Inches(2.95), Inches(1.32), label, value, sub)
        x += Inches(3.11)

    deck.text(slide, MARGIN, Inches(6.06), W - 2 * MARGIN, Inches(0.5),
              "Bucket 1 is GHE + GHAS, sold as deals. Bucket 2 is recurring consumption "
              "\u2014 Copilot, Actions, GHAzDO \u2014 so it is measured as run rate carried "
              "across the half, not as bookings. Targets, coverage and focus accounts are "
              "all H1; the Q1/Q2 split is shown because the half is not evenly loaded.",
              size=12, color=MUTED)
    deck.footnote(slide, "Bucket 2 attainment to date and month-1 run rate are the same money, "
                         "counted once. All figures computed from SuperDash, Kusto billing facts "
                         "and Salesforce - none entered by hand.")
    return slide


def slide_2_learnings(deck, learnings):
    slide = deck.slide("What H2 FY26 taught me, and what changes",
                       "Carry-forward",
                       note="Each learning is derived from a count in the run data, not from "
                            "recollection. The right-hand column is the behaviour change, which "
                            "is the part that matters.")
    rows = []
    colors = {}
    items = learnings.get("learnings", [])[:LEARNING_ROWS]
    for index, item in enumerate(items):
        rows.append([
            item.get("headline", ""),
            item.get("detail", ""),
            item.get("carryForward", ""),
        ])
        colors[index] = WARN if index < 3 else ACCENT

    deck.table(slide, MARGIN, BODY_TOP, W - 2 * MARGIN,
               ["Learning", "What the record shows", "What I do differently in H1"],
               [3.0, 6.2, 4.1], rows, row_h=0.62, size=12, colors=colors, wrap=True)

    deck.footnote(slide, "Derived from focus-accounts.json, coverage.json and Salesforce open "
                         "opportunity records - every figure above is reproducible.")
    return slide


def slide_3_key_deals(deck, focus, crm=None, report=None):
    """The deals themselves, not the accounts that hold them.

    Selection is deterministic: every dated, non-renewal H1 opportunity in the book,
    ranked by value, top KEY_DEAL_ROWS. Nothing here is hand-picked, so the slide
    cannot quietly become a curated list that flatters the pipeline.

    Deliberately sourced from the whole book rather than the focus 40. A live deal is
    a live deal regardless of where its account ranks, and the ranking model scores
    modelled whitespace - so an account that has already committed seats can rank low
    while carrying one of the largest deals on the desk. Scoping this slide to the
    focus set would hide exactly those deals.
    """
    slide = deck.slide("Key deals in play", "Live deals",
                       note="Every open, dated, non-renewal H1 opportunity in the book, "
                            "largest first. Renewals are excluded - they are not new "
                            "revenue against these targets.")
    plays = {}
    for row in (report or {}).get("accounts", []):
        sid = row.get("salesforceId")
        if sid:
            plays[sid] = row.get("primaryPlay", "")
    accounts = {}
    for account in focus.get("accounts", []):
        sid = account.get("salesforceId")
        if sid:
            accounts[sid] = account
    deals = []
    seen = set()
    sources = [(a.get("salesforceId"), a, a.get("openPipeline") or [])
               for a in focus.get("accounts", [])]
    for sid, row in ((crm or {}).get("accounts", {}) or {}).items():
        if sid in accounts:
            continue
        sources.append((sid, {"name": row.get("name", ""),
                              "play": plays.get(sid, ""),
                              "salesforceId": sid}, row.get("openPipeline") or []))
    for sid, account, pipeline in sources:
        for opp in pipeline:
            if not opp.get("inH1") or opp.get("isRenewal"):
                continue
            amount = float(opp.get("amount") or 0)
            if amount <= 0:
                continue
            token = (sid, opp.get("name"), amount)
            if token in seen:
                continue
            seen.add(token)
            deals.append((amount, account, opp))
    deals.sort(key=lambda d: -d[0])
    top = deals[:KEY_DEAL_ROWS]

    focus_ids = set(accounts)
    rows, colors = [], {}
    outside_value = 0.0
    outside_names = []
    for index, (amount, account, opp) in enumerate(top):
        scenario = opp.get("note") or ""
        if not scenario:
            close = str(opp.get("closeDate") or "")
            scenario = "%s%s" % (opp.get("stage") or "In stage",
                                 (" \u00b7 close %s" % close) if close else "")
        sid = account.get("salesforceId")
        outside = sid not in focus_ids
        if outside:
            outside_value += amount
            outside_names.append(account.get("name", ""))
        rows.append([
            account.get("name", "") + (" *" if outside else ""),
            account.get("play", ""),
            opp.get("product") or "\u2014",
            money(amount),
            opp.get("stage") or "\u2014",
            scenario,
        ])
        colors[index] = PLAY_COLOR.get(account.get("play"), ACCENT)

    deck.table(slide, MARGIN, BODY_TOP, W - 2 * MARGIN,
               ["Account", "Play", "Product", "Deal size", "Stage", "Where it stands"],
               [2.5, 1.2, 1.3, 1.3, 1.6, 5.4], rows, row_h=0.44, size=12,
               colors=colors, wrap=True)

    total = sum(d[0] for d in deals)
    shown = sum(d[0] for d in top)
    seller = sum(d[0] for d in deals if (d[2].get("source") == "seller"))
    note = ("%d open H1 deals worth %s in total; the %d largest are shown (%s). "
            "%s is seller-sourced and not yet raised in Salesforce. Renewals excluded."
            % (len(deals), money(total), len(top), money(shown), money(seller)))
    if outside_names:
        note += (" * %s sits outside the focus 40, so its %s is excluded from the "
                 "coverage figures on the target slides - those count focus accounts "
                 "only." % (" and ".join(outside_names), money(outside_value)))
    deck.footnote(slide, note)
    return slide


def slide_4_key_accounts(deck, focus):
    slide = deck.slide("Key accounts: the must-wins", "Q1 \u00b7 Key accounts",
                       note="Ranked on potential 40 / live pipeline 20 / two-way communication 20 "
                            "/ dated trigger 20. Every 'why now' below is a cited, dated public "
                            "event or an open Salesforce opportunity.")
    accounts = focus.get("accounts", [])
    tier1 = [a for a in accounts if (a.get("tier") or "").startswith("Tier 1")][:10]

    rows, colors = [], {}
    for index, account in enumerate(tier1):
        triggers = account.get("triggers") or []
        why = triggers[0].get("headline") if triggers else ""
        if not why and float(account.get("h1PipelineValue") or 0) > 0:
            why = "Open H1 opportunity \u00b7 %s" % (account.get("bestStage") or "in stage")
        pipeline = float(account.get("h1PipelineValue") or 0)
        rows.append([
            truncate(account.get("name", ""), 26),
            account.get("play", ""),
            money(pipeline) if pipeline else "\u2014",
            "Yes" if account.get("msftOverlap") else "\u2014",
            truncate(why, 62),
        ])
        colors[index] = PLAY_COLOR.get(account.get("play"), ACCENT)

    deck.table(slide, MARGIN, BODY_TOP, W - 2 * MARGIN,
               ["Account", "Play", "Live H1 pipe", "MSFT", "Why now"],
               [2.9, 1.4, 1.5, 0.9, 6.3], rows, row_h=0.44, size=12, colors=colors)

    bottom = float(BODY_TOP) + Inches(0.3) + Inches(0.44) * len(rows) + Inches(0.24)
    two_way = sum(1 for a in tier1 if a.get("twoWay"))
    with_pipe = sum(1 for a in tier1 if float(a.get("h1PipelineValue") or 0) > 0)
    with_tpid = sum(1 for a in tier1 if a.get("msftOverlap"))
    deck.text(slide, MARGIN, Emu(int(bottom)), W - 2 * MARGIN, Inches(0.34),
              "%d must-wins \u00b7 %d in two-way comms \u00b7 %d with a dated H1 deal \u00b7 "
              "%d co-sellable through a Microsoft TPID."
              % (len(tier1), two_way, with_pipe, with_tpid), size=11.5, color=TEXT)
    deck.footnote(slide, "Tier 2 and Tier 3 accounts are worked to the same plays on a lighter "
                         "cadence; the full 40 are in the evidence workbook.")
    return slide


def slide_5_portfolio(deck, focus, coverage):
    slide = deck.slide("The book, split three ways", "Q2 \u00b7 Key plays",
                       note="Play assignment comes from the account's own product footprint and "
                            "whitespace, not from preference. TPID accounts are run with "
                            "Microsoft and a partner by default.")
    accounts = focus.get("accounts", [])

    by_play = {p: [] for p in PLAYS}
    for account in accounts:
        if account.get("play") in by_play:
            by_play[account["play"]].append(account)

    x = MARGIN
    col_w = Inches(4.02)
    for play in PLAYS:
        rows = by_play[play]
        pipe = sum(float(r.get("h1PipelineValue") or 0) for r in rows)
        engaged = sum(1 for r in rows if r.get("twoWay"))
        overlap = sum(1 for r in rows if r.get("msftOverlap"))
        color = PLAY_COLOR[play]

        deck.fill(slide, x, BODY_TOP, col_w, Inches(4.5), PANEL, line=LINE, radius=True)
        deck.fill(slide, x, BODY_TOP, col_w, Inches(0.06), color)
        deck.text(slide, Emu(int(x + Inches(0.26))), Emu(int(BODY_TOP + Inches(0.26))),
                  Emu(int(col_w - Inches(0.52))), Inches(0.32), play.upper(),
                  size=13, color=color, bold=True, space=True)
        deck.text(slide, Emu(int(x + Inches(0.26))), Emu(int(BODY_TOP + Inches(0.62))),
                  Emu(int(col_w - Inches(0.52))), Inches(0.5),
                  "%s accounts \u00b7 %s live" % (len(rows), money(pipe) if pipe else "no"),
                  size=19, color=WHITE, bold=True)
        deck.text(slide, Emu(int(x + Inches(0.26))), Emu(int(BODY_TOP + Inches(1.06))),
                  Emu(int(col_w - Inches(0.52))), Inches(0.64),
                  PLAY_THESIS[play], size=12, color=MUTED)
        deck.text(slide, Emu(int(x + Inches(0.26))), Emu(int(BODY_TOP + Inches(1.76))),
                  Emu(int(col_w - Inches(0.52))), Inches(0.26),
                  "%d IN TWO-WAY COMMS \u00b7 %d WITH MICROSOFT TPID"
                  % (engaged, overlap),
                  size=9.5, color=color, bold=True, space=True)

        top = sorted(rows, key=lambda r: int(r.get("rank") or 999))[:6]
        cursor = float(BODY_TOP + Inches(2.14))
        for account in top:
            flag = " \u25c6" if account.get("msftOverlap") else ""
            deck.text(slide, Emu(int(x + Inches(0.26))), Emu(int(cursor)),
                      Emu(int(col_w - Inches(1.5))), Inches(0.28),
                      truncate(account.get("name", ""), 28) + flag, size=12, color=TEXT)
            deck.text(slide, Emu(int(x + col_w - Inches(1.24))), Emu(int(cursor)),
                      Inches(0.98), Inches(0.28), "#%d" % int(account.get("rank") or 0),
                      size=12, color=color, bold=True, align=PP_ALIGN.RIGHT)
            cursor += Inches(0.33)
        if len(rows) > 6:
            deck.text(slide, Emu(int(x + Inches(0.26))), Emu(int(cursor + Inches(0.04))),
                      Emu(int(col_w - Inches(0.52))), Inches(0.26),
                      "+%d more" % (len(rows) - 6), size=11, color=MUTED)
        x += Inches(4.18)

    deck.text(slide, MARGIN, Inches(6.32), W - 2 * MARGIN, Inches(0.3),
              "\u25c6 = Microsoft TPID present \u2192 run as co-sell with the Microsoft account "
              "team and a delivery partner. %d of %d focus accounts qualify."
              % (focus.get("totals", {}).get("withMsftOverlap", 0), len(accounts)),
              size=12, color=MUTED)
    deck.footnote(slide, "Top six by composite rank shown per play; full list in the evidence "
                         "workbook.")
    return slide


def slide_6_the_number(deck, potential, focus, coverage):
    slide = deck.slide("What the half is worth", "Q3 \u00b7 The number",
                       note="Copilot is sized on seats without Copilot today. GHAS is sized per "
                            "active committer because that is how it bills. GHE is Azure DevOps "
                            "seats available to migrate. These are volumes, not revenue \u2014 "
                            "deliberately, since price per account is not yet negotiated.")
    accounts = focus.get("accounts", [])
    aiu = potential.get("aiuTotals", {})

    quantities = {"Copilot": 0, "GHAS": 0, "GHE": 0}
    values = {"Copilot": 0.0, "GHAS": 0.0, "GHE": 0.0}
    for account in accounts:
        for line in account.get("lines", []):
            product = line.get("product")
            if product in values:
                values[product] += float(line.get("value") or 0)
                quantities[product] += int(line.get("quantity") or 0)

    cards = [
        ("Copilot seats", num(quantities["Copilot"]), "seats with no Copilot today",
         PLAY_COLOR["Innovate"]),
        ("GHAS committers", num(quantities["GHAS"]), "active committers not covered",
         PLAY_COLOR["Trust"]),
        ("GHE seats", num(quantities["GHE"]), "Azure DevOps seats to migrate", PLAY_COLOR["Scale"]),
        ("AI credits / yr", num(aiu.get("currentAnnualisedCredits")),
         "%s invoiced run-rate" % money(aiu.get("currentAnnualisedSpend")), ACCENT),
    ]
    x = MARGIN
    for label, value, sub, color in cards:
        deck.card(slide, x, BODY_TOP, Inches(2.95), Inches(1.42), label, value, sub, color)
        x += Inches(3.11)

    rows = [
        ["Copilot", "Bucket 2", "Seats with no Copilot today", num(quantities["Copilot"]),
         "seats"],
        ["GHAS", "Bucket 1", "Active committers L90d not covered", num(quantities["GHAS"]),
         "committers"],
        ["GHE", "Bucket 1", "Azure DevOps seats available to migrate", num(quantities["GHE"]),
         "seats"],
        ["AI credits", "Bucket 2", "Accounts consuming credits today",
         num(aiu.get("accountsConsumingAiu")),
         money(aiu.get("currentAnnualisedSpend")) + " run-rate"],
    ]
    colors = {0: PLAY_COLOR["Innovate"], 1: PLAY_COLOR["Trust"],
              2: PLAY_COLOR["Scale"], 3: ACCENT}
    deck.table(slide, MARGIN, Inches(3.42), W - 2 * MARGIN,
               ["Product", "Bucket", "Sizing basis", "Quantity", "Unit"],
               [1.6, 1.3, 6.2, 1.6, 1.8], rows, row_h=0.46, size=12, colors=colors)

    deck.text(slide, MARGIN, Inches(5.66), W - 2 * MARGIN, Inches(0.62),
              "This is the addressable volume in the focus set, counted in units we can verify "
              "\u2014 seats, committers and invoiced credits. What the half needs against it is "
              "on the next slide, in dated pipeline.", size=12.5, color=TEXT)
    deck.footnote(slide, "Quantities are measured, not modelled: seats and committers come from "
                         "product telemetry, credits from invoiced run-rate. No ARR is imputed.")
    return slide


def slide_7_coverage(deck, coverage, focus):
    products = {p.get("product"): p for p in coverage.get("products", []) or []}
    pipeline = coverage.get("pipeline", {})
    buckets = {b.get("bucket"): b for b in coverage.get("buckets", []) or []}
    ghe = products.get("GHE") or {}
    ghas = products.get("GHAS") or {}
    copilot = products.get("Copilot") or {}
    b1 = buckets.get("Bucket 1") or {}
    b2 = buckets.get("Bucket 2") or {}
    ghe_gap = ghe.get("h1Gap")
    ghas_cover = ghas.get("h1CoveredPct")
    b2_gap = b2.get("h1Gap")
    b1_covered_pct = b1.get("h1CoveredPct")
    run_cfg = coverage.get("runRate") or {}
    month_total = round(sum(float(v or 0) for v in (run_cfg.get("products") or {}).values()), 2)
    growth_pct = round(float(run_cfg.get("growthPerQuarter") or 0) * 100)
    growth_contrib = float(run_cfg.get("growthContribution") or 0)

    # Every column below is a dated, invoiceable number. Nothing modelled.
    if not b1.get("targetKnown") or not b2.get("targetKnown"):
        narrative = ("Targets are not yet set for every product, so coverage reads TBD. Fill in "
                     "targets.json and re-run to see the H1 gap per product.")
        note = ("Coverage reads TBD wherever targets.json is not yet filled in.")
    else:
        narrative = (
            "Bucket 1 is a deal problem: %s of dated H1 pipeline against a %s target, but it "
            "leans on GHAS at %.0f%% while GHE is still %s short. Bucket 2 is not a deal "
            "problem at all \u2014 the existing run rate already covers %.0f%% of the half, "
            "and the whole ask is %s of growth on top, %s of it Copilot."
            % (money(b1.get("livePipeline")), money(b1.get("h1Target")),
               ghas_cover or 0, money(max(0.0, float(ghe_gap or 0))),
               b2.get("h1CoveredPct") or 0, money(max(0.0, float(b2_gap or 0))),
               money(max(0.0, float(copilot.get("h1Gap") or 0)))))
        note = (
            "The two buckets fail differently and must be managed differently. Bucket 1 is "
            "carried by GHAS closes - concentration risk, not comfort - with GHE %s short on "
            "its own line, and its pipeline is loaded into Q1 rather than spread across the "
            "half. Bucket 2 needs no new logos: hold the %s monthly run rate and add %s of "
            "net-new consumption, overwhelmingly Copilot seats. The Q2 carry assumes %d%% "
            "quarter-on-quarter growth, worth %s of the cover shown - challenge that rate, "
            "not the total."
            % (money(max(0.0, float(ghe_gap or 0))), money(month_total),
               money(max(0.0, float(b2_gap or 0))), growth_pct, money(growth_contrib)))

    slide = deck.slide("Coverage: H1 target vs what already covers it",
                       "H1 \u00b7 Coverage math", note=note)
    rows, colors = [], {}

    for index, product in enumerate(coverage.get("products", []) or []):
        known = product.get("targetKnown")
        pct = product.get("h1CoveredPct")
        recurring = (buckets.get(product.get("bucket")) or {}).get("recurring")
        gap = product.get("h1Gap")
        rows.append([
            product.get("product", ""),
            "Run rate" if recurring else "Dated pipeline",
            money(product.get("h1Target")) if known else "TBD",
            money(product.get("h1Covered")),
            ("%.0f%%" % pct) if pct is not None else "\u2014",
            (money(gap) if gap is not None and gap > 0 else "Covered") if known else "\u2014",
        ])
        # Colour on coverage: that is the number that carries risk.
        colors[index] = ratio_color((pct / 100.0) if pct is not None else None)

    deck.table(slide, MARGIN, BODY_TOP, W - 2 * MARGIN,
               ["Product", "Covered by", "H1 target", "H1 covered", "Cover", "Gap to target"],
               [1.7, 2.0, 1.9, 1.9, 1.5, 3.1], rows, row_h=0.46, size=12,
               colors=colors)

    top = float(BODY_TOP) + Inches(0.3) + Inches(0.46) * len(rows) + Inches(0.32)

    seller = pipeline.get("seller") or 0
    b1_gap = b1.get("h1Gap")
    b1_over = b1_gap is not None and b1_gap <= 0
    b1_known = b1.get("targetKnown")
    b2_known = b2.get("targetKnown")
    # With no target there is no denominator, so a ratio card would be dividing a real
    # number by zero and printing it as fact. Say TBD instead.
    cards = [
        ("Bucket 1 H1 covered",
         ("%s / %s" % (money(b1.get("h1Covered")), money(b1.get("h1Target"))))
         if b1_known else money(b1.get("h1Covered")),
         ("attained + dated H1 pipeline (%.0f%%)" % (b1_covered_pct or 0)) if b1_known
         else "dated H1 pipeline \u00b7 target TBD",
         PLAY_COLOR["Scale"] if (b1_known and b1_over) else WARN),
        ("Bucket 2 H1 covered",
         ("%s / %s" % (money(b2.get("h1Covered")), money(b2.get("h1Target"))))
         if b2_known else money(b2.get("h1Covered")),
         ("run rate: Q1 flat, Q2 +%d%% (%.0f%%)" % (growth_pct, b2.get("h1CoveredPct") or 0))
         if b2_known else "run rate \u00b7 target TBD", ACCENT),
        ("Bucket 2 growth gap",
         money(max(0.0, float(b2_gap or 0))) if b2_known else "TBD",
         ("%s Copilot \u00b7 the real H1 ask" % money(max(0.0, float(copilot.get("h1Gap") or 0))))
         if b2_known else "set the Bucket 2 target to size this",
         WARN),
        ("H1 renewal pipeline", money(pipeline.get("renewal")),
         "excluded from attainment", MUTED),
    ]
    x = MARGIN
    for label, value, sub, color in cards:
        deck.card(slide, x, Emu(int(top)), Inches(2.94), Inches(1.3), label, value, sub, color)
        x += Inches(3.105)

    deck.text(slide, MARGIN, Emu(int(top + Inches(1.52))), W - 2 * MARGIN, Inches(0.68),
              narrative, size=12, color=TEXT)

    # Provenance. Leadership will look these numbers up in Salesforce; anything that
    # will not be found there has to be declared on the slide, not in a backup pack.
    marks = ["Targets are net-new; renewals are context only.",
             "Bucket 2 is recurring: the elapsed month and month one of the carry are the "
             "same money, counted once."]
    if seller:
        # Name the accounts from the data. Hardcoding them here meant every seller who
        # ran the skill printed one particular customer's name on their own deck.
        named = sorted({a.get("name", "") for a in focus.get("accounts", [])
                        for o in (a.get("openPipeline") or [])
                        if o.get("source") == "seller" and o.get("inH1")
                        and not o.get("isRenewal") and a.get("name")})
        marks.append("Includes %s of seller-confirmed pipeline not yet raised in Salesforce%s."
                     % (money(seller),
                        (" (%s)" % ", ".join(named[:3])) if named else ""))
    inferred = pipeline.get("inferredProduct") or 0
    if inferred:
        marks.append("%s of pipeline has its product inferred from seat-based opportunity "
                     "naming rather than an explicit product field." % money(inferred))
    deck.footnote(slide, " ".join(marks))
    return slide


def slide_play(deck, play, focus, report, paf, partner_map, eyebrow, hero_names=None,
               conversations=None):
    """One slide per play: who leads every account, and how one of them gets run.

    The worked example is rendered from paf.json key actions, so the framework claim in
    the footnote is sourced rather than asserted. If paf.json is absent the panel says so
    instead of substituting invented motions.

    Where gathered conversation history exists for the worked account, the panel leads
    with what has actually been said - agreed, open, committed - and the key actions are
    the ones selected to answer that account's stated blocker rather than the generic
    sequence. The play basis moves to the speaker notes, because the conversation says
    more about why the account sits here than the classifier's sentence does.
    """
    accounts = sorted([a for a in focus.get("accounts", []) if a.get("play") == play],
                      key=lambda r: int(r.get("rank") or 999))
    color = PLAY_COLOR[play]
    counts = {1: 0, 2: 0, 3: 0}
    for account in accounts:
        counts[int(account.get("msftTier") or 3)] = counts.get(
            int(account.get("msftTier") or 3), 0) + 1

    slide = deck.slide("%s \u00b7 %d accounts" % (play, len(accounts)), eyebrow,
                       note="Every account in the play is named, with who leads it. The worked "
                            "example uses GitHub Product Adoption Framework key actions for "
                            "this play; the rest follow the same sequence.")

    # --- led-by strip
    strip_h = Inches(0.66)
    deck.fill(slide, MARGIN, BODY_TOP, W - 2 * MARGIN, strip_h, PANEL, line=LINE, radius=True)
    deck.fill(slide, MARGIN, BODY_TOP, Inches(0.07), strip_h, color)
    deck.text(slide, Emu(int(MARGIN + Inches(0.28))), Emu(int(BODY_TOP + Inches(0.19))),
              Inches(7.5), Inches(0.32), PLAY_THESIS[play], size=13, color=TEXT)
    chip_x = float(MARGIN + Inches(8.05))
    chip_w = Inches(1.32)
    for tier in (1, 2, 3):
        deck.text(slide, Emu(int(chip_x)), Emu(int(BODY_TOP + Inches(0.09))),
                  Emu(int(chip_w)), Inches(0.34), str(counts.get(tier, 0)),
                  size=19, color=LED_COLOR[tier], bold=True, align=PP_ALIGN.RIGHT,
                  wrap=False)
        deck.text(slide, Emu(int(chip_x)), Emu(int(BODY_TOP + Inches(0.42))),
                  Emu(int(chip_w)), Inches(0.22), LED_LABEL[tier].upper(),
                  size=9.5, color=MUTED, bold=True, space=True, align=PP_ALIGN.RIGHT)
        chip_x += float(chip_w + Inches(0.2))

    panel_top = BODY_TOP + Inches(0.84)
    panel_h = Inches(4.42)
    left_w = Inches(5.15)
    right_x = MARGIN + left_w + Inches(0.2)
    right_w = W - MARGIN - right_x

    # --- worked example
    target_name = (hero_names or {}).get(play, "")
    hero = next((a for a in accounts
                 if a.get("name", "").lower() == str(target_name).lower()), None)
    if hero is None and accounts:
        hero = accounts[0]

    # Land or expand is read from the account's own footprint, not chosen: an account
    # already consuming the play's product needs the expand sequence.
    by_id = {a.get("salesforceId"): a for a in (report or {}).get("accounts", []) or []}
    consumption = ""
    basis = ""
    phase = "land"
    if hero is not None:
        record = by_id.get(hero.get("salesforceId"), {})
        consumption = record.get("consumption", "")
        # playBasis carries the classifier's own sentence, including the printed reason
        # when a play was seller-asserted over the ladder. Reading it here means the
        # slide states why this account sits in this play rather than implying it.
        basis = record.get("playBasis", "") or ""
        owned = consumption_token(consumption, CONSUMPTION_TOKEN[play])
        phase = "expand" if (owned or 0) > 0 else "land"

    # The classifier's play basis moves off the panel into the speaker notes. The
    # conversation section now says more about why the account sits here, but the basis
    # is still the auditable reason and should not be lost. The gathering note goes with
    # it, because where the history was found is itself a finding.
    hero_convo = ((conversations or {}).get("heroes", {}) or {}).get(
        (hero or {}).get("name", "")) or {}
    extra = []
    if basis:
        extra.append("Why %s sits in %s: %s" % (hero.get("name", ""), play, basis))
    if hero_convo.get("sourceNote"):
        extra.append("Conversation source: %s" % hero_convo["sourceNote"])
    if hero_convo.get("strategic"):
        extra.append(hero_convo["strategic"])
    if extra:
        deck.notes = [(s, "\n\n".join([n] + extra) if s is slide else n)
                      for s, n in deck.notes]

    deck.fill(slide, MARGIN, panel_top, left_w, panel_h, PANEL, line=LINE, radius=True)
    inner_x = Emu(int(MARGIN + Inches(0.26)))
    inner_w = Emu(int(left_w - Inches(0.52)))
    deck.text(slide, inner_x, Emu(int(panel_top + Inches(0.2))), inner_w, Inches(0.24),
              "HOW I RUN THIS PLAY", size=10, color=color, bold=True, space=True)

    if hero is not None:
        convo = ((conversations or {}).get("heroes", {}) or {}).get(hero.get("name", "")) or {}
        deck.text(slide, inner_x, Emu(int(panel_top + Inches(0.38))), inner_w, Inches(0.30),
                  truncate_fit(hero.get("name", ""), int(inner_w), 16), size=16,
                  color=WHITE, bold=True)
        meta = "#%d \u00b7 %s \u00b7 %s \u00b7 %s potential" % (
            int(hero.get("rank") or 0), (hero.get("tier") or "").split(" - ")[0],
            led_by(hero), money(float(hero.get("potentialArr") or 0)))
        deck.text(slide, inner_x, Emu(int(panel_top + Inches(0.70))), inner_w, Inches(0.22),
                  meta, size=11, color=color, bold=True, wrap=False)
        today = "Today: %s" % footprint(consumption)
        deck.text(slide, inner_x, Emu(int(panel_top + Inches(0.93))), inner_w, Inches(0.22),
                  truncate_fit(today, int(inner_w), 10), size=10, color=MUTED,
                  wrap=False)

        # --- where the conversation stands
        # Sourced from gathered call history. The header states the last touch and the
        # call count so a reader can tell a live conversation from a stale one, and says
        # "no call record" outright when there is none rather than implying contact.
        if convo:
            if convo.get("source") == "gong" and convo.get("lastTouch"):
                stamp = "LAST TOUCH %s \u00b7 %d CALL%s" % (
                    convo.get("lastTouch"), int(convo.get("callCount") or 0),
                    "" if int(convo.get("callCount") or 0) == 1 else "S")
            else:
                stamp = "NO CALL RECORD \u00b7 FROM THE OPPORTUNITY"
            head = "WHERE THE CONVERSATION STANDS \u00b7 %s" % stamp
        else:
            head = "WHERE THE CONVERSATION STANDS"
        deck.text(slide, inner_x, Emu(int(panel_top + Inches(1.19))), inner_w, Inches(0.20),
                  truncate_fit(head, int(inner_w), 9), size=9, color=color, bold=True,
                  space=True, wrap=False)

        cursor = float(panel_top + Inches(1.41))
        if convo:
            for label, key in (("Agreed", "agreed"), ("Open", "blocker"),
                               ("Committed", "committed")):
                value = convo.get(key)
                if not value:
                    continue
                deck.text(slide, inner_x, Emu(int(cursor)), Inches(0.70), Inches(0.20),
                          label, size=9.5, color=color, bold=True, wrap=False)
                deck.text(slide, Emu(int(MARGIN + Inches(0.98))), Emu(int(cursor)),
                          Emu(int(left_w - Inches(1.24))), Inches(0.49),
                          truncate(value, 168), size=9.5, color=TEXT)
                cursor += float(Inches(0.51))
        else:
            deck.text(slide, inner_x, Emu(int(cursor)), inner_w, Inches(0.49),
                      "No gathered conversation history for this account. Add it to "
                      "conversations.json in the run directory.", size=9.5, color=MUTED)
            cursor += float(Inches(0.51))

        # --- what gets them fully onboarded
        steps = selected_paf(paf, play, convo.get("pafActions"), phase)
        onboard_top = max(cursor + float(Inches(0.06)), float(panel_top + Inches(2.98)))
        deck.text(slide, inner_x, Emu(int(onboard_top)), inner_w, Inches(0.20),
                  "WHAT GETS THEM FULLY ONBOARDED \u00b7 PAF", size=9, color=color,
                  bold=True, space=True, wrap=False)
        cursor = onboard_top + float(Inches(0.22))
        if steps:
            for index, action in enumerate(steps, start=1):
                deck.text(slide, inner_x, Emu(int(cursor)), Inches(0.3), Inches(0.20),
                          "%d" % index, size=11, color=color, bold=True, wrap=False)
                title_w = int(left_w - Inches(0.84))
                deck.text(slide, Emu(int(MARGIN + Inches(0.58))), Emu(int(cursor)),
                          Emu(title_w), Inches(0.20),
                          truncate_fit(action.get("title", ""), title_w, 11),
                          size=11, color=WHITE, bold=True, wrap=False)
                deck.text(slide, Emu(int(MARGIN + Inches(0.58))),
                          Emu(int(cursor + Inches(0.20))), Emu(title_w), Inches(0.20),
                          truncate_fit(action.get("summary", ""), title_w, 9),
                          size=9, color=MUTED, wrap=False)
                cursor += float(Inches(0.40))
        else:
            deck.text(slide, inner_x, Emu(int(cursor)), inner_w, Inches(0.6),
                      "paf.json is missing from the skill, so the key actions for this "
                      "play cannot be shown. Rebuild it with build_paf.py.",
                      size=11, color=WARN)

    # --- the rest of the play
    deck.fill(slide, right_x, panel_top, right_w, panel_h, PANEL, line=LINE, radius=True)
    rest = [a for a in accounts if a is not hero]
    deck.text(slide, Emu(int(right_x + Inches(0.26))), Emu(int(panel_top + Inches(0.2))),
              Emu(int(right_w - Inches(0.52))), Inches(0.24),
              "THE REST OF THE PLAY \u00b7 %d ACCOUNTS" % len(rest),
              size=10, color=color, bold=True, space=True)

    col_w = float((right_w - Inches(0.52) - Inches(0.24)) / 2)
    row_h = float(Inches(0.52))
    # Derive the row capacity from the panel rather than assuming it. When the list
    # overflows, the "+N more" footer needs its own band at the bottom; a fixed row
    # count silently spends that band and the last row lands on top of the footer.
    list_top = float(panel_top + Inches(0.58))
    list_bottom = float(panel_top + panel_h - Inches(0.24))
    rest_count = len(rest)
    capacity = max(1, int((list_bottom - list_top) // row_h))
    if rest_count > capacity * 2:
        # Overflow: give the footer a row's worth of clearance.
        capacity = max(1, int((list_bottom - float(Inches(0.34)) - list_top) // row_h))
    list_rows = min(LIST_ROWS, capacity)
    shown = rest[:list_rows * 2]
    for index, account in enumerate(shown):
        col, row = divmod(index, list_rows)
        x = float(right_x + Inches(0.26)) + col * (col_w + float(Inches(0.24)))
        y = list_top + row * row_h
        tier = int(account.get("msftTier") or 3)
        name_w = int(col_w - Inches(0.78))
        # Truncate the composed string, not the name alone: the rank prefix takes width
        # too, and a second rendered line is what pushes the row into its neighbour.
        deck.text(slide, Emu(int(x)), Emu(int(y)), Emu(name_w), Inches(0.26),
                  truncate_fit("#%d  %s" % (int(account.get("rank") or 0),
                                            account.get("name", "")), name_w, 12),
                  size=12, color=TEXT, wrap=False)
        deck.text(slide, Emu(int(x + col_w - Inches(0.76))), Emu(int(y)),
                  Inches(0.76), Inches(0.26),
                  money(float(account.get("potentialArr") or 0)),
                  size=11.5, color=color, bold=True, align=PP_ALIGN.RIGHT, wrap=False)
        named = partner_names(partner_map, account)
        detail = named[0] if named else ("partner to source" if tier == 2 else "no partner")
        if tier == 1:
            detail = account.get("msftOwner") or "Microsoft owner named"
        detail_w = int(col_w - Inches(0.1))
        deck.text(slide, Emu(int(x)), Emu(int(y + Inches(0.25))),
                  Emu(detail_w), Inches(0.24),
                  truncate_fit("%s \u00b7 %s" % (LED_LABEL[tier], detail), detail_w, 10),
                  size=10, color=LED_COLOR[tier], wrap=False)
    if len(rest) > len(shown):
        deck.text(slide, Emu(int(right_x + Inches(0.26))),
                  Emu(int(panel_top + panel_h - Inches(0.34))),
                  Emu(int(right_w - Inches(0.52))), Inches(0.24),
                  "+%d more in the evidence workbook" % (len(rest) - len(shown)),
                  size=10, color=MUTED)

    deck.footnote(slide, "Led by: a named Microsoft owner is Microsoft led, a TPID alone is "
                         "partner led, neither is seller led. The worked example uses the "
                         "GitHub Product Adoption Framework %s key actions for %s."
                  % (phase, PLAY_PRODUCT[play]))
    return slide


def slide_execution(deck, play, focus, report, paf, licensing, eyebrow):
    """How the play actually gets run: the two motions, side by side, in PAF steps.

    The play slide answers *which* accounts and *why now*. A leader's next question is
    always "so what are you actually going to do", and until now that answer only existed
    in the seller's head. This slide is that answer, and every step in it is a GitHub
    Product Adoption Framework key action read from paf.json - so the method is sourced
    rather than asserted, exactly like the worked example on the play slide.

    Nothing here is composed for a particular territory. The motion labels come from
    PLAY_MOTION, the steps from paf.json, and the account counts from the same land/expand
    test the play slide uses. A book where every account is already landed renders the
    land column as "no accounts in this motion" rather than inventing work.
    """
    accounts = [a for a in focus.get("accounts", []) if a.get("play") == play]
    color = PLAY_COLOR[play]
    split = motion_split(play, accounts, report)

    # Whitespace is summed only over accounts that actually resolved to a tenant, and the
    # unresolved ones are counted separately, so the headline number is never a floor
    # dressed up as a total.
    measured = [play_whitespace(licensing, a, play) for a in accounts]
    resolved = [v for v in measured if v is not None]
    whitespace = sum(resolved)
    unresolved = len(measured) - len(resolved)

    slide = deck.slide(
        "How I run %s \u00b7 two motions" % play, eyebrow,
        note="The two motions are split on whether the account already consumes the "
             "play's product: not yet is a landing motion, already is an expansion "
             "motion. Every step is a GitHub Product Adoption Framework key action for "
             "this play, so the sequence is sourced rather than invented.")

    # --- headline strip
    strip_h = Inches(0.66)
    deck.fill(slide, MARGIN, BODY_TOP, W - 2 * MARGIN, strip_h, PANEL, line=LINE, radius=True)
    deck.fill(slide, MARGIN, BODY_TOP, Inches(0.07), strip_h, color)

    chips = [(num(whitespace) if resolved else "n/a", WHITESPACE_LABEL[play], color),
             (str(len(split["land"])), PLAY_MOTION[play]["land"][0], WHITE),
             (str(len(split["expand"])), PLAY_MOTION[play]["expand"][0], WHITE)]
    # Anchored to the right margin rather than a fixed left offset, so the last chip
    # cannot drift past the panel it sits on when a label or the chip count changes.
    chip_w = Inches(1.5)
    chip_gap = Inches(0.12)
    chip_x = float(W - MARGIN) - (len(chips) * float(chip_w)
                                  + (len(chips) - 1) * float(chip_gap))

    # The thesis takes whatever the chips leave, so the two can never overlap.
    thesis_x = float(MARGIN + Inches(0.28))
    deck.text(slide, Emu(int(thesis_x)), Emu(int(BODY_TOP + Inches(0.19))),
              Emu(int(chip_x - thesis_x - float(Inches(0.16)))), Inches(0.32),
              "Land the product where it is absent, grow it where it is already in",
              size=13, color=TEXT)

    for value, label, chip_color in chips:
        deck.text(slide, Emu(int(chip_x)), Emu(int(BODY_TOP + Inches(0.09))),
                  Emu(int(chip_w)), Inches(0.34), value, size=19, color=chip_color,
                  bold=True, align=PP_ALIGN.RIGHT, wrap=False)
        deck.text(slide, Emu(int(chip_x)), Emu(int(BODY_TOP + Inches(0.42))),
                  Emu(int(chip_w)), Inches(0.22), label, size=8.5, color=MUTED,
                  bold=True, space=True, align=PP_ALIGN.RIGHT, wrap=False)
        chip_x += float(chip_w + chip_gap)

    # --- one column per motion
    panel_top = BODY_TOP + Inches(0.84)
    panel_h = Inches(4.42)
    col_w = float((W - 2 * MARGIN - Inches(0.2)) / 2)

    for index, phase in enumerate(("land", "expand")):
        x = float(MARGIN) + index * (col_w + float(Inches(0.2)))
        label, thesis = PLAY_MOTION[play][phase]
        members = split[phase]
        deck.fill(slide, Emu(int(x)), panel_top, Emu(int(col_w)), panel_h,
                  PANEL, line=LINE, radius=True)
        inner_x = Emu(int(x + Inches(0.26)))
        inner_w = Emu(int(col_w - Inches(0.52)))

        deck.text(slide, inner_x, Emu(int(panel_top + Inches(0.2))), inner_w, Inches(0.24),
                  "%s \u00b7 %d ACCOUNT%s" % (label, len(members),
                                              "" if len(members) == 1 else "S"),
                  size=10, color=color, bold=True, space=True, wrap=False)
        deck.text(slide, inner_x, Emu(int(panel_top + Inches(0.46))), inner_w, Inches(0.24),
                  truncate_fit(thesis, int(inner_w), 10), size=10, color=MUTED, wrap=False)

        steps = paf_steps(paf, play, phase, limit=EXEC_STEPS)
        cursor = float(panel_top + Inches(0.82))
        if not steps:
            deck.text(slide, inner_x, Emu(int(cursor)), inner_w, Inches(0.6),
                      "paf.json is missing from the skill, so the key actions for this "
                      "motion cannot be shown. Rebuild it with build_paf.py.",
                      size=11, color=WARN)
        elif not members:
            # An empty motion is a finding about the book, not a rendering failure, so it
            # is stated plainly and the sequence is still shown as the method on file.
            deck.text(slide, inner_x, Emu(int(cursor)), inner_w, Inches(0.4),
                      "No focus account sits in this motion this half - every %s account "
                      "is in the other one. The sequence below is the method on file for "
                      "when one lands here." % play, size=10, color=WARN)
            cursor += float(Inches(0.46))

        for step_index, action in enumerate(steps, start=1):
            deck.text(slide, inner_x, Emu(int(cursor)), Inches(0.3), Inches(0.22),
                      "%d" % step_index, size=11, color=color, bold=True, wrap=False)
            title_w = int(col_w - Inches(0.84))
            deck.text(slide, Emu(int(x + Inches(0.58))), Emu(int(cursor)),
                      Emu(title_w), Inches(0.22),
                      truncate_fit(action.get("title", ""), title_w, 11),
                      size=11, color=WHITE, bold=True, wrap=False)
            deck.text(slide, Emu(int(x + Inches(0.58))), Emu(int(cursor + Inches(0.20))),
                      Emu(title_w), Inches(0.22),
                      truncate_fit(action.get("summary", ""), title_w, 9),
                      size=9, color=MUTED, wrap=False)
            cursor += float(Inches(0.42))

    note = ("Motions split on the account's own product footprint: no %s yet is a landing "
            "motion, %s already in place is an expansion motion. Whitespace counts %s. "
            "Steps are GitHub Product Adoption Framework key actions for %s."
            % (PLAY_PRODUCT[play], PLAY_PRODUCT[play], WHITESPACE_BASIS[play], play))
    if unresolved:
        note += (" Whitespace covers %d of %d accounts; %d had no GitHub tenant to read."
                 % (len(resolved), len(measured), unresolved))
    deck.footnote(slide, note)
    return slide


def slide_9_msft_partners(deck, focus, partners, cosell=None):
    slide = deck.slide("Microsoft overlap and partner leverage", "Q5 \u00b7 Co-sell",
                       note="A TPID alone is close to the default state of this book, so it is "
                            "not reported as co-sell. Only a TPID plus a named Microsoft "
                            "account manager or specialist gives a person to sell with; that is "
                            "tier 1. TPID without a named owner is partner-led.")
    accounts = focus.get("accounts", [])
    tier1 = [a for a in accounts if int(a.get("msftTier") or 3) == 1]
    tier2 = [a for a in accounts if int(a.get("msftTier") or 3) == 2]
    tier3 = [a for a in accounts if int(a.get("msftTier") or 3) == 3]
    partner_map = (partners or {}).get("accounts", {}) or {}

    with_partner = [a for a in accounts
                    if [p for p in (partner_map.get(a.get("salesforceId"), {}) or {}).get("partners", [])
                        if p.get("name") and p.get("name") != "Invalid"]]

    t1_pipe = sum(float(a.get("h1PipelineValue") or 0) for a in tier1)
    cards = [
        ("Tier 1 \u00b7 Co-sell led", num(len(tier1)),
         "TPID + named Microsoft seller", ACCENT),
        ("Tier 2 \u00b7 Partner led", num(len(tier2)),
         "TPID only \u2014 no named counterpart", PLAY_COLOR["Trust"]),
        ("Tier 3 \u00b7 GitHub direct", num(len(tier3)),
         "no Microsoft route today", MUTED),
        ("Tier 1 pipeline", money(t1_pipe),
         "dated H1 deals with a named seller" if t1_pipe > 0
         else "co-sell is unworked, not unavailable", WARN if t1_pipe <= 0 else WHITE),
    ]
    x = MARGIN
    for label, value, sub, color in cards:
        deck.card(slide, x, BODY_TOP, Inches(2.95), Inches(1.42), label, value, sub, color)
        x += Inches(3.11)

    # The table body holds 7 rows before it runs into the caption below, so the
    # co-sell watchlist takes its slots from the ranked list rather than extending
    # past the plate. Tier 1 leads: those are the accounts with a person to call.
    focus_ids = {a.get("salesforceId") for a in accounts}
    watchlist = [a for a in (cosell or []) if a.get("salesforceId") not in focus_ids][:2]
    ranked = sorted(tier1, key=lambda a: int(a.get("rank") or 999)) + \
        sorted(tier2, key=lambda a: int(a.get("rank") or 999))
    top_overlap = ranked[:7 - len(watchlist)]
    rows, colors = [], {}
    for index, account in enumerate(top_overlap):
        entry = partner_map.get(account.get("salesforceId"), {}) or {}
        names = [p.get("name") for p in (entry.get("partners") or [])
                 if p.get("name") and p.get("name") != "Invalid"]
        tier = int(account.get("msftTier") or 3)
        owner = account.get("msftOwner") or ""
        if tier == 1:
            route = owner if owner else "Seller-asserted \u2014 not named in Salesforce"
        else:
            route = "Partner led \u2014 no Microsoft owner named"
        rows.append([
            truncate(account.get("name", ""), 24),
            account.get("play", ""),
            "#%d" % int(account.get("rank") or 0),
            "Tier %d" % tier,
            truncate(route, 22),
            truncate(", ".join(names) if names else "No partner mapped \u2014 needs sourcing", 30),
        ])
        colors[index] = ACCENT if tier == 1 else PLAY_COLOR.get(account.get("play"), MUTED)

    # Accounts I am working with the Microsoft team that carry no product footprint
    # yet. They have no rank because there is nothing in the data to rank them on -
    # showing them with a blank rank is the honest rendering, not an omission.
    for account in watchlist:
        index = len(rows)
        rows.append([
            truncate(account.get("name", ""), 24),
            account.get("play", ""),
            "\u2014",
            "Tier 1",
            "Seller-asserted",
            "Prospect \u2014 Microsoft-led, no footprint yet",
        ])
        colors[index] = MUTED

    deck.table(slide, MARGIN, Inches(3.42), W - 2 * MARGIN,
               ["Account", "Play", "Rank", "Tier", "Microsoft route", "Partner"],
               [2.7, 1.35, 0.85, 1.0, 2.6, 3.6], rows, row_h=0.36, size=12, colors=colors)

    deck.text(slide, MARGIN, Inches(6.4), W - 2 * MARGIN, Inches(0.42),
              "How I use it: tier 1 gets a joint account-team introduction before any "
              "GitHub-only outreach. Tier 2 gets a partner attached to the migration before "
              "the technical win, so delivery is never the reason a deal slips. Tier 3 is run "
              "direct and is not counted as Microsoft coverage.",
              size=12, color=MUTED)
    deck.footnote(slide, "Tier from Salesforce MsftOwnerName__c plus MSFT_All_TPIDs__c / "
                         "MS_Sales_TPID_Best_Match__c. Partner relationships from Salesforce "
                         "Partner records. Seller-asserted tiers are labelled as such.")
    return slide


def slide_10_working(deck, learnings):
    slide = deck.slide("What's working, what's not", "Q6 \u00b7 Honest read",
                       note="Both columns are counts from the run data. The right-hand column is "
                            "where the asks on the next slide come from.")
    working = ["%s \u2014 %s" % (w.get("point"), w.get("proof"))
               for w in learnings.get("working", [])]
    not_working = ["%s \u2014 %s" % (w.get("point"), w.get("proof"))
                   for w in learnings.get("notWorking", [])]

    deck.panel(slide, MARGIN, BODY_TOP, Inches(6.0), "WHAT'S WORKING", working,
               GOOD, size=12, gap=0.2, min_h=Inches(4.4))
    deck.panel(slide, Inches(6.95), BODY_TOP, Inches(6.0), "WHAT'S NOT", not_working,
               WARN, size=12, gap=0.2, min_h=Inches(4.4))

    deck.text(slide, MARGIN, Inches(6.3), W - 2 * MARGIN, Inches(0.4),
              "The pattern: I can open accounts, and the installed base is real. What I cannot "
              "yet do at scale is convert opening into dated, forecastable pipeline \u2014 which "
              "is exactly what the asks address.", size=12, color=TEXT)
    deck.footnote(slide, "Every figure computed from the focus set; none estimated.")
    return slide


def slide_11_asks(deck, coverage, focus, learnings, cosell=None, asks=None):
    slide = deck.slide("Asks", "Q7 \u00b7 Leadership and cross-functional",
                       note="Seller-authored strategic asks lead each column; the asks "
                            "below them are each tied to a specific number on an earlier "
                            "slide, so they are answerable rather than aspirational.")
    facts = learnings.get("facts", {})
    products = {p.get("product"): p for p in coverage.get("products", []) or []}
    ghe = products.get("GHE", {})
    ghas = products.get("GHAS", {})

    leadership = []
    if ghe.get("targetKnown"):
        b1 = next((b for b in coverage.get("buckets", []) or []
                   if b.get("bucket") == "Bucket 1"), {})
        gap = b1.get("h1Gap")
        ghe_short = max(0.0, float(ghe.get("h1Gap") or 0))
        if gap is not None and gap <= 0:
            # Bucket 1 nets out only because GHAS over-covers. The ask is still real,
            # so it is framed on the GHE line rather than the netted bucket.
            leadership.append(
                "GHE supply: %s is close-dated against a %s H1 target (%.0f%%), %s short on "
                "the GHE line. Bucket 1 only nets to covered because GHAS is carrying it, so "
                "I need migration-led demand generation or account additions on GHE rather "
                "than relying on that concentration holding."
                % (money(ghe.get("livePipeline")), money(ghe.get("h1Target")),
                   ghe.get("h1CoveredPct") or 0, money(ghe_short)))
        else:
            leadership.append(
                "GHE supply: %s is close-dated against a %s H1 target (%.0f%%), leaving %s of "
                "Bucket 1 uncovered. I need migration-led demand generation or account "
                "additions to close the supply gap, not just conversion pressure."
                % (money(ghe.get("livePipeline")), money(ghe.get("h1Target")),
                   ghe.get("h1CoveredPct") or 0,
                   money(gap) if gap is not None else "the balance"))
    else:
        leadership.append(
            "GHE target: not yet set. I have %s of dated GHE pipeline in the focus set - I "
            "need the target so I can tell you whether that is coverage or a supply gap."
            % money(ghe.get("livePipeline")))

    # Bucket 2 is a run-rate business, so the ask is growth on top of the carry, not a
    # hunt for the whole target. Sizing it as a booking gap overstated it four-fold.
    b2 = bucket_of(coverage, "Bucket 2")
    copilot = products.get("Copilot") or {}
    if b2.get("targetKnown"):
        b2_gap = max(0.0, float(b2.get("h1Gap") or 0))
        if b2_gap > 0:
            leadership.append(
                "Copilot seat growth: the existing run rate already covers %.0f%% of the %s "
                "Bucket 2 half, so the ask is %s of net-new consumption \u2014 %s of it "
                "Copilot. That is seat expansion inside accounts already live, not new logos, "
                "and it needs adoption support rather than more pipeline."
                % (b2.get("h1CoveredPct") or 0, money(b2.get("h1Target")), money(b2_gap),
                   money(max(0.0, float(copilot.get("h1Gap") or 0)))))
    else:
        leadership.append(
            "Bucket 2 target: consumption target is still unset. I am carrying %s of dated "
            "consumption pipeline and %s attained - I need the number to plan against."
            % (money((products.get("Consumption") or {}).get("livePipeline")),
               money(b2.get("attainedH1"))))

    ghas_cover = ghas.get("h1CoveredPct")
    if ghas_cover is not None and ghas_cover >= 100:
        leadership.append(
            "GHAS delivery capacity: GHAS is already %.0f%% covered by dated H1 pipeline (%s). "
            "The risk is no longer sourcing it, it is landing it \u2014 I need "
            "security-specialist time to get these to production, not more pipeline."
            % (ghas_cover, money(ghas.get("livePipeline"))))
    else:
        leadership.append(
            "GHAS technical capacity: %d of %d focus accounts consume GHAS today, against %s "
            "of dated GHAS pipeline. Converting that needs security-specialist time, not more "
            "pipeline." % (facts.get("consumingAccounts", {}).get("ghas", 0),
                           facts.get("focusCount", 0), money(ghas.get("livePipeline"))))

    accounts = focus.get("accounts", []) or []
    tier1 = [a for a in accounts if int(a.get("msftTier") or 3) == 1]
    tier2 = [a for a in accounts if int(a.get("msftTier") or 3) == 2]
    # A TPID with no named Microsoft owner is not co-sell coverage - it is an
    # unworked route. Splitting the ask keeps that distinction on the slide.
    # Accounts worked with Microsoft whose Salesforce record does not say so. Read from
    # the data rather than named in code: the ask is real for whoever it applies to, and
    # hard-coding customer names would ship one seller's book inside the plugin.
    gap_named = sorted({a.get("name") for a in accounts
                        if a.get("msftDataGap") and a.get("name")})
    gap_named += sorted({a.get("name") for a in (cosell or [])
                         if a.get("msftDataGap") and a.get("name")})
    xfn = [
        "Partnerships: %d focus accounts are partner-led (TPID, no named Microsoft owner) "
        "and only %d have a named partner. I need partner sourcing on the Scale accounts."
        % (len(tier2), facts.get("withNamedPartner", 0)),
        "Microsoft co-sell: only %d focus accounts have a named Microsoft counterpart. Joint "
        "planning on those, plus a route into the %d partner-led accounts, before Q2."
        % (len(tier1), len(tier2)),
    ]
    if gap_named:
        xfn.append(
            "Sales ops \u00b7 data quality: %s %s worked with Microsoft but %s no named owner "
            "in Salesforce. Untagged means no co-sell credit."
            % (truncate(", ".join(gap_named[:3]), 60),
               "is" if len(gap_named) == 1 else "are",
               "carries" if len(gap_named) == 1 else "carry"))
    xfn += [
        "Marketing / SDR: %d of %d focus accounts have no two-way contact. Demand generation "
        "there is faster than cold outbound from me."
        % (facts.get("withoutTwoWay", 0), facts.get("focusCount", 0)),
        "Deal desk: %d stale opportunities worth %s need a hygiene pass."
        % (facts.get("staleCount", 0), money(facts.get("staleValue"))),
    ]

    # Seller-authored asks lead each column. They are not derivable from the data - a
    # competitor's packaging or a partner's motivation does not appear in a SuperDash
    # export - so they are stated from asks.json rather than computed. Absent the file,
    # both columns render exactly the computed asks they always did.
    seller = asks or {}
    lead_seller = list(seller.get("leadership") or [])
    xfn_seller = ([("Partnerships: %s" % a) for a in (seller.get("partnerships") or [])]
                  + [("Microsoft: %s" % a) for a in (seller.get("microsoft") or [])])

    # The panel has to clear the commitment bar at 6.4in, so the two lists cannot both
    # grow unbounded. Trim the computed asks - never the seller's - until the taller
    # column fits. A silently off-slide ask is worse than an ask that moves to the notes.
    panel_w = Inches(6.0)
    inner_w = panel_w - Inches(0.52)
    max_body = Inches(6.3) - BODY_TOP - Inches(1.06)
    size, gap = 11.5, 0.18

    def fit(fixed, extra):
        items = list(fixed) + list(extra)
        while len(items) > len(fixed) and deck.bullet_height(
                inner_w, items, size=size, gap=gap) > max_body:
            items = items[:-1]
        return items

    dropped_items = []
    kept_lead = fit(lead_seller, leadership)
    kept_xfn = fit(xfn_seller, xfn)
    dropped_items += leadership[len(kept_lead) - len(lead_seller):]
    dropped_items += xfn[len(kept_xfn) - len(xfn_seller):]
    leadership, xfn = kept_lead, kept_xfn

    deck.panel(slide, MARGIN, BODY_TOP, panel_w, "FROM LEADERSHIP", leadership,
               ACCENT, size=size, gap=gap, min_h=Inches(4.5))
    deck.panel(slide, Inches(6.95), BODY_TOP, panel_w,
               "FROM PARTNERSHIPS AND MICROSOFT",
               xfn, PLAY_COLOR["Scale"], size=size, gap=gap, min_h=Inches(4.5))
    if dropped_items:
        # Say where they went, and actually put them there. A slide that claims an ask
        # is recorded somewhere it is not is worse than one that drops it silently.
        deck.footnote(slide, "%d further data-derived ask%s did not fit this slide and "
                             "%s in the speaker notes."
                      % (len(dropped_items), "" if len(dropped_items) == 1 else "s",
                         "is" if len(dropped_items) == 1 else "are"))
        deck.notes = [(s, "\n\n".join([n, "Asks that did not fit the slide:"]
                                      + ["- %s" % d for d in dropped_items])
                       if s is slide else n) for s, n in deck.notes]

    b1_gap = bucket_of(coverage, "Bucket 1").get("h1Gap")
    b1_pipe = (coverage.get("pipeline", {}) or {}).get("byBucket", {}).get("Bucket 1")
    commitment = (
        "Commitment: %s of Bucket 1 gap closed in H1, worked through %d focus accounts "
        "with %s already in dated pipeline." % (money(b1_gap), focus.get("selectedCount", 0),
                                                money(b1_pipe))
        if b1_gap and b1_gap > 0 else
        "Commitment: %d focus accounts with %s in dated H1 pipeline, worked to the plan on "
        "the previous slides." % (focus.get("selectedCount", 0), money(b1_pipe))
    )
    deck.text(slide, MARGIN, Inches(6.4), W - 2 * MARGIN, Inches(0.36),
              commitment, size=12.5, color=WHITE, bold=True)
    return slide


# ---------------------------------------------------------------- entry

def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: exec_deck.py <runDir> <out.pptx>")
    run_dir, out_path = sys.argv[1], sys.argv[2]

    focus = load(run_dir, "focus-accounts.json")
    potential = load(run_dir, "potential.json", {})
    coverage = load(run_dir, "coverage.json", {})
    learnings = load(run_dir, "learnings.json", {})
    partners = load(run_dir, "partners.json", {})
    report = load(run_dir, "fy27-territory-plan.json", {})
    if not focus:
        raise SystemExit("Cannot read focus-accounts.json - run rank.py stage2 first")

    # Partner counts are needed by the asks slide but are computed on slide 8, so derive
    # the one figure the asks reference here rather than duplicating the join.
    partner_map = (partners or {}).get("accounts", {}) or {}
    named_partners = sum(
        1 for a in focus.get("accounts", [])
        if [p for p in (partner_map.get(a.get("salesforceId"), {}) or {}).get("partners", [])
            if p.get("name") and p.get("name") != "Invalid"]
    )
    learnings.setdefault("facts", {})["withNamedPartner"] = named_partners

    # Seller-asserted co-sell accounts: named on the Microsoft slide even though they
    # carry no product signal and therefore cannot earn a rank. The play comes from
    # the report, the co-sell flag from the CRM context, so join them here.
    crm = load(run_dir, "crm-context.json", {}) or {}
    plays_by_id = {a.get("salesforceId"): a.get("primaryPlay")
                   for a in (report or {}).get("accounts", []) or []}
    cosell = [{"salesforceId": sid, "name": rec.get("name", ""),
               "tpids": rec.get("tpids") or [],
               "msftTier": rec.get("msftTier") or 1,
               "msftDataGap": rec.get("msftDataGap", ""),
               "play": plays_by_id.get(sid, "")}
              for sid, rec in ((crm.get("accounts") or {}).items())
              if rec.get("msftCoSell")]
    cosell.sort(key=lambda a: a["name"].lower())

    deck = Deck()
    slide_1_scorecard(deck, coverage, focus)
    slide_2_learnings(deck, learnings)
    slide_3_key_deals(deck, focus, crm, report)
    slide_4_key_accounts(deck, focus)
    slide_5_portfolio(deck, focus, coverage)
    paf = load(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
               "paf.json", {})
    hero_names = load(run_dir, PAF_ACCOUNTS_FILE, {})
    conversations = load(run_dir, CONVERSATIONS_FILE, {})
    licensing = load(run_dir, "licensing.json", {})
    for play, eyebrow in zip(PLAYS, ("Q2 \u00b7 Innovate", "Q2 \u00b7 Trust", "Q2 \u00b7 Scale")):
        slide_play(deck, play, focus, report, paf, partner_map, eyebrow, hero_names,
                   conversations)
        # Immediately after each play slide, so "which accounts" and "how I run them" are
        # read as one pair rather than separated by two other plays.
        slide_execution(deck, play, focus, report, paf, licensing,
                        "%s \u00b7 execution" % eyebrow)
    slide_6_the_number(deck, potential, focus, coverage)
    slide_7_coverage(deck, coverage, focus)
    slide_9_msft_partners(deck, focus, partners, cosell)
    slide_10_working(deck, learnings)
    slide_11_asks(deck, coverage, focus, learnings, cosell, load(run_dir, ASKS_FILE, {}))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    deck.save(out_path)
    print(json.dumps({"deckPath": out_path, "slides": len(deck.prs.slides._sldIdLst)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
