---
name: fy27-h1-focus-deck
description: "Build the FY27 H1 focus-accounts leadership presentation from a completed territory plan run. Selects 30-50 focus accounts ranked on potential ARR, live open pipeline, active communication and dated live triggers; sizes the opportunity in AIU, Copilot seats, and GHE+GHAS seats/ACR/ARR; scores quota coverage per bucket against the teammate's targets; grounds the execution plan in GitHub's Product Adoption Framework; and renders a 16-slide leadership deck, a 21-slide evidence deck and an evidence workbook. Use for 'H1 focus accounts', 'build my territory presentation', 'which 40 accounts for the half', 'focus account deck', 'leadership deck', 'presentation for my sales leader', 'how do I make my number', or 'H1 plan for FY27'."
---

# FY27 H1 Focus Accounts — Leadership Presentation

> **Starting from a raw SuperDash export?** Use **fy27-h1-run** instead — it produces the completed
> territory-plan run this skill needs, then drives this pipeline in order. Use this skill directly
> when a run already exists and only the decks need building or rebuilding.

## Mission

Turn a completed territory-plan run into the presentation a sales leader actually asks for:
**30–50 focus accounts for H1**, sized in dollars, with a named execution plan, Microsoft and
partner leverage, an honest read on what is and is not working, and specific asks.

The deck answers seven questions, in this order:

1. What are the key accounts?
2. What plays run across them?
3. What is the potential — AIU, Copilot seats, GHE + GHAS (seats, ACR, ARR)?
4. **How will I achieve it?** — weighted heaviest, one slide per play plus the operating cadence
5. Where is the Microsoft overlap, and how do I leverage Microsoft and partners?
6. What is working and what is not?
7. What is the ask of leadership and supporting functions?

## Prerequisite

A completed **fy27-territory-plan** run. This skill consumes that run directory; it does not
re-classify the book. If the teammate has not run the territory plan yet, run it first — the
play assignment lives there and must not be recomputed here.

`python-pptx` and `xlsxwriter` are required:

```bash
python3 -m pip install --user python-pptx xlsxwriter
```

## The rule that keeps this honest

**Deterministic Python owns all arithmetic and rendering. The agent only gathers evidence.**

Never compose slide content, dollar figures, account counts, or rankings in chat. Every number on
a slide is computed by these scripts from the run's own JSON. This is what stops the deck from
drifting away from the workbook it is supposed to summarise.

## Pipeline

`SCRIPTS` is this skill's `scripts/` directory. `RUN` is the territory-plan run directory.

### 0. Re-base sizing on live licensing (recommended)

The SuperDash upload reports signals org-wide, so it systematically overstates the population
an account is actually billed for. GHAS is the worst case, because GHAS bills per active
committer and the upload counts every committer in the tenant. Live licensing is what GitHub
itself bills, so it is the better basis.

Gather, per account: `revenue-mcp-server/get_salesforce_account` returns a `github_accounts`
array of `{slug, namespace}`; call `revenue-mcp-server/get_licensing_summary` for each. Store
the responses **verbatim** — the parser depends on the exact keys — in
`<RUN>/licensing/raw.json`:

```json
{"gatheredAt": "...", "accounts": {
  "<salesforceId>": {"name": "...", "githubAccounts": [...],
                     "summaries": [{"slug": "...", "namespace": "...", "summary": { ... }}],
                     "status": "ok"}}}
```

Then normalise:

```bash
python3 SCRIPTS/licensing.py "<RUN>"
```

Writes `<RUN>/licensing.json`, keyed by Salesforce Account ID and summed across an account's
tenants. `potential.py` and `plays.py` pick it up automatically if it is present.

**Who to gather.** Every account the enrichment pass resolved to a Salesforce ID — not just the
ranked focus set. The play sheets carry the whole book, so gathering only the top 40 leaves the
widest sheets sized on the upload alone. Work the queue in batches:

```bash
python3 SCRIPTS/licensing_worklist.py "<RUN>" --batch 25   # what is still outstanding
# ... gather those accounts, write the batch to a JSON file ...
python3 SCRIPTS/licensing_merge.py "<RUN>" "<RUN>/licensing/batch-01.json"
```

`licensing_worklist.py` reads what is already cached and returns only what is left, ranked
accounts first. Repeat until `remaining` is 0. The merge only ever adds, so an interrupted gather
resumes rather than restarting, and a failed retry never erases a good reading.

Record every miss with a status — `no-salesforce-match`, `no-github-account` or `error`. They lead
to three different actions (fix the name, link the tenant, retry), and collapsing them into one
bucket tells a seller their book is uncovered when what is actually wrong is three unlinked
Salesforce records.

**This has to run after enrichment, not before.** Licensing is keyed by GitHub slug, slugs come
off the Salesforce record, and SuperDash rows arrive keyed by name. The Salesforce resolution the
enrichment pass performs is the only thing that makes a row licensable at all. No enrichment means
**every licence field is blank for the whole book** — not a thin set of them.

**The cache stays in the run directory.** `<RUN>/licensing/raw.json` holds customer entitlement
data. It is under the same never-leaves rule as `overrides.json` and `conversations.json`, and it
must never be written to a shared path where one teammate's book could surface in another's.

**An absent reading is not a zero.** An account with no GHEC tenant, or a live count of zero,
keeps its upload figure and is labelled — it is never silently sized to nothing.

**Widening licensing moves accounts between plays.** Where live licensing shows a Team plan,
`potential.py` drops the GHAS line and `plays.py` moves the account to Scale. That correction now
reaches the whole book, so play counts shift on the first run after this change.
`plays.py` writes `<RUN>/reclassification.json` naming every account that moved and why — report
the count in the hand-off, or a shifted mix reads as the tool drifting.

### 1. Size the potential

```bash
python3 SCRIPTS/potential.py "<RUN>/fy27-territory-plan.json" "<RUN>"
```

Sizes each account from its observed product signals using the rates in `pricing.json`. Every
sized line carries a **basis**, in precedence order:

| Basis | Meaning |
|---|---|
| `seller-asserted` | a quantity the customer has agreed to (`pipeline` override) |
| `seller-corrected` | a telemetry input the seller has verified (`signals` override) |
| `live` | read from GitHub licensing rather than the upload |
| `observed` | this account's own effective price |
| `list` | published GitHub pricing |
| `derived` | a median where no list price exists — GHE only |

Where `licensing.json` is present, Copilot is sized as *(licensed seats − seats already on
Copilot)* and GHAS as *(billable committers − committers already licensed)*. **GHE is not
re-based** — it keeps its existing derivation. A seller assertion or correction always wins over
live data: the seller knows something the telemetry does not.

Accounts with no product signal are not sized; they are reported as needing discovery. Nothing is
ever sized without a signal.

**Team-plan accounts.** GHAS is not sold on GitHub Team, so any GHAS line against a Team account
prices something the customer could never be invoiced for. Where live licensing shows a Team
plan, the GHAS line is dropped, a GHE consolidation line is sized off the Team seats already in
use, and `plays.py` moves the account to **Scale** — unless an explicit seller `play` override
says otherwise. This is invisible in the upload; only live licensing exposes it.

Writes `<RUN>/potential.json`.

### 2. Add Kusto actuals

```bash
python3 SCRIPTS/actuals.py --print-queries
```

Run the printed KQL through `revenue-mcp-server/query_kusto` (database `rev_source`), save the
combined result as `<RUN>/kusto/raw-query.json` in the shape `{"arr": {...}, "consumption": {...}}`,
then:

```bash
python3 SCRIPTS/actuals.py ingest "<RUN>/kusto/raw-query.json" "<RUN>"
python3 SCRIPTS/potential.py "<RUN>/fy27-territory-plan.json" "<RUN>"
```

This attaches current ARR, seats by product, and annualised consumption including `copilot aiu`.
If the teammate has no Kusto access, skip it — the deck will lead on potential and say plainly
that installed-base figures were unavailable.

> **Two traps here, both silent.**
>
> `ingest` **writes** `<RUN>/raw-actuals.json`. Feeding it that file as *input* makes it read
> its own normalised output, find nothing to normalise, and overwrite it with an empty
> record. Always pass the raw Kusto capture (`kusto/raw-query.json`), never `raw-actuals.json`.
>
> `potential.py` reads `raw-actuals.json`, so it must run **after** `ingest`. Run it before and
> every account is sized as greenfield.
>
> Neither failure raises. Both surface only as `currentArr: 0` on the focus totals and a
> `$0` installed-base card on slide 1. **Check `currentArr` is non-zero before rendering.**
> The `--arr` / `--consumption` flags shown in older revisions of this file do not exist.

### 3. Stage 1 — pick the trigger candidates

```bash
python3 SCRIPTS/rank.py stage1 "<RUN>/fy27-territory-plan.json" "<RUN>/potential.json" "<RUN>" --count 60
```

Ranks on potential and communication only, and writes `<RUN>/trigger-candidates.json`. Triggers are
researched **after** this, not before — otherwise the ranking would depend on triggers that were
only fetched for accounts the ranking already favoured.

### 4. Research live triggers

For the candidates, use `web_search` (batch them across parallel research sub-agents; ~10 accounts
per agent keeps each one reliable). Ask for a single raw JSON object:

```json
{"accounts": {"<salesforceId>": [
  {"type": "funding", "date": "YYYY-MM-DD", "headline": "...",
   "url": "https://...", "soWhat": "one line on why GitHub matters now"}]}}
```

`type` must be one of the types `rank.py` can score: `funding`, `acquisition`, `security_incident`,
`leadership_change`, `ai_launch`, `product_launch`, `expansion`, `partnership`, `earnings`,
`recognition`, `other`. Anything else is dropped, so do not invent types.

**Drop any trigger without both a real date and a real source URL.** An undated claim is a rumour,
and a leader will find it. Only the last 18 months count.

Write each agent's raw reply to its own file in `<RUN>/triggers/`, then merge:

```bash
python3 scripts/merge_triggers.py <RUN>/focus-candidates.json <RUN>/triggers <RUN>/triggers.json
```

`merge_triggers.py` strips markdown fences, resolves whichever identifier the agent used
(Salesforce id, account name, or candidate key) back onto the candidate key, enforces the
type/date/URL bar, and reports `dropped` plus `unmatchedKeys`. A non-zero `unmatchedKeys`
usually means the agent researched an account that is not in the candidate set — check it
rather than ignoring it.

### 5. Enrich partners and Microsoft overlap

Partner relationships hang off **opportunities**, not accounts, so query them that way:

```sql
SELECT Opportunity__r.AccountId, Partner_Name_Text__c, Partner_Involvement_PL__c,
       Source__c, CSP_Partner__c, Channel_Account_Manger_Name__c
FROM Partner__c WHERE Opportunity__r.AccountId IN (<focus account ids>)
```

`Channel_Account_Manger_Name__c` is the GitHub Partner Development Manager — surface it, it is the
name the teammate needs. Shape the result as
`{"accounts": {<accountKey>: {"partners": [...], "pdm": [...], "microsoft": {"csp": bool}}}}`
and save as `<RUN>/partners.json`.

Report coverage honestly. `get_account_partners` on an account ID returns nothing for most
accounts; that is a data gap, not evidence that no partner exists.

### 6. Targets, Microsoft overlap and open pipeline

Set the teammate's quota in `SCRIPTS/targets.json`. Bucket 1 is GHE + GHAS, sold as
deals. Bucket 2 is consumption (Copilot, Actions, GHAzDO) and is marked
`"recurring": true`, which changes how it is measured — see the run-rate model below.
Leave a target `null` if it is not yet set; the deck renders `TBD` and suppresses the
percentage rather than inventing a denominator. Q1 and H1 are tracked independently, so
a firm current-quarter number with next quarter still `null` renders correctly. Targets
are **net-new**; renewals are reported separately.

**Bucket 2 is a run rate, not a booking.** Consumption revenue repeats every month unless
something churns, so `targets.json` carries a `runRate` block with the last full month's
revenue per product and how many months the quarter carries it for:

```json
"runRate": { "month": 1, "monthsInQuarter": 3, "growthPerQuarter": 0,
             "products": { "Copilot": 0, "Actions": 0, "GHAzDO": 0 } }
```

`targets.py` projects that across the half and reports it as **H1 coverage**, with Q1 and Q2
computed separately:

- **Q1 is held flat at the measured base.** The base is measured now, inside the quarter
  already under way, so claiming growth within it would invent revenue that has not happened.
- **Q2 carries `base × (1 + growthPerQuarter) × monthsInQuarter`.** `growthPerQuarter` ships
  as `0` — a flat carry, the conservative floor. Set your own rate and the deck states it as
  an assumption beside the measured base, so leadership can challenge the rate rather than
  the total. `targets.py` reports `runRate.growthContribution` — exactly how much of the
  cover comes from the assumption rather than from measured revenue.

Two consequences that are easy to get wrong and are enforced in the script:

- **The elapsed month's attainment and month one of the carry are the same money.** They
  are counted once. Bucket 2 `h1Covered` is the carry alone; `attainedH1` is reported as a
  separate fact and never added on top.
- **Seat landings are never added on top of the run rate.** New seats show up *as* run-rate
  growth. A committed Copilot deal is therefore counted in the growth rate, not as a separate
  booking — adding both double-counts the same money.
- **Open Bucket 2 pipeline is metered consumption already inside the carry.** It is shown
  for context and excluded from coverage. Adding it would count the same revenue a third
  time.

Modelling Bucket 2 as a booking gap overstates the ask roughly four-fold: a book whose run
rate already covers most of the half has a growth gap, not a hunting problem.

**Microsoft overlap is three tiers, not a boolean.** A TPID alone is close to the default
state of a book — in the reference run, 142 of 251 accounts carry one — so reporting "has
TPID" as co-sell materially overstates it. `crm_context.py` reads `MsftOwnerName__c` and
tiers on the presence of a **named owner**:

| Tier | Rule | Means |
|---|---|---|
| 1 — Co-sell led | TPID **and** a named Microsoft AM/specialist | there is a person to sell with |
| 2 — Partner led | TPID only | a route in, but nobody assigned |
| 3 — Direct | neither | no Microsoft route today |

`MsftOwnerRole__c` is free text and dirty (`AE`, `ACCOUNT EXECUTIVE`, `Account Exective`, and
at least one record with an email address in the role field), so it is displayed for context
and never parsed. An `msftCoSell` override forces tier 1 with `msftTierSource: "seller"` and
records an `msftDataGap` naming exactly what Salesforce is missing — so the deck never implies
CRM data that is not there, and the gap becomes a data-quality ask instead of disappearing.

Pull Microsoft TPIDs and open opportunities:

```bash
python3 SCRIPTS/crm_context.py query "<RUN>/focus-accounts.json"
```

Run each emitted SOQL query with `query_salesforce`, save the combined result as
`{"accounts": [...], "opportunities": [...]}`, then:

```bash
python3 SCRIPTS/crm_context.py ingest "<RUN>/crm/raw.json" "<RUN>"
python3 SCRIPTS/plays.py "<RUN>"
python3 SCRIPTS/targets.py "<RUN>/potential.json" "<RUN>/focus-accounts.json" "<RUN>"
```

**`plays.py` must run after CRM ingest and before stage 2.** Play is assigned in
`workbook.py` from the account's own product footprint, using a deterministic ladder:

| Condition | Play | Priority within the play |
|---|---|---|
| Not on GHE | **Scale** — migration and displacement | Copilot or Teams seats already present |
| On GHE, Copilot on ≥ 25% of GHE licences | **Trust** — govern and secure at scale | Higher attach ratio |
| On GHE, Copilot < 25%, **regulated** industry | **Trust** — fallback tier | Higher attach ratio |
| On GHE, Copilot < 25%, not regulated | **Innovate** — seat-expansion headroom | Larger GHE base |

The Trust bar is `COPILOT_TRUST_RATIO` in `workbook.py`. Set it at the natural break in the
book's attach distribution rather than at a round number — accounts below it are headroom
stories, not govern-at-scale stories. **GHAS is deliberately not a Trust signal:** a GHAS
footprint with no Copilot is an expansion opportunity, and treating it as Trust hid those
accounts from the Innovate motion.

Membership and priority are different mechanisms in different files. Membership is
`classify_play()` in `workbook.py`. Priority is a **bounded tie-break** in `rank.py`
(`PLAY_PRIORITY_WEIGHT`), normalised *within each play cohort* and added after the main
composite score. It must stay bounded: the deck claims accounts are ranked on
potential/pipeline/communication/triggers, and a priority term large enough to override
those would make that claim false.

**"On GHE" means true GitHub Enterprise, not the blended GHE/VS column.** SuperDash
carries `Total GHE/VS Seats (Vol and Metered)`, which sums GitHub Enterprise seats *and*
Visual Studio bundle seats. A VS bundle **entitles** GHE but does not mean the customer
is on GitHub — a pure-VS account is a migration target, not an established customer.
`workbook.py` therefore classifies on
`ghe_true = Current GHE License Seats + Current GHE Metered Users` and reports VS bundle
seats separately as `vsBundleSeats`. VS seats still count towards *potential sizing*,because they are a legitimate migration TAM signal. Passing the blended figure into
`classify_play()` routes migration targets into Trust/Innovate and is the single most
damaging mistake available in this file.

`revenueSignals` on each account carries `gheSeats`, `vsBundleSeats`, `copilotSeats`,
`teamsSeats`, `ghazdoSeats`, `ghasSeats`, `activeCommitters` and `meteredConsumption`.
`teamsSeats` is the strongest Scale priority signal after Copilot — a Teams account has
already chosen GitHub and needs an upgrade conversation, not a displacement one.
`ghazdoSeats` is GitHub Advanced Security for Azure DevOps: immaterial as a sell line, but
a precise ADO-migration TAM signal feeding GHE and GHAS.

The regulated rung needs `Account.Industry`, which only arrives with CRM ingest. Accounts
waiting on it are marked `playPendingIndustry` and default to Innovate; `plays.py` resolves
them once industry is known. It can only move an account **between Trust and Innovate**, never
in or out of the play set, so the stage-1 candidate list cannot change depending on whether
enrichment ran — the sole exception being an override that carries `sellerAsserted: true`,
documented in step 8. Where industry is blank or miscoded, correct it in `overrides.json` with
`industry` / `regulated` / `industryReason`, or assert the play directly with `play` /
`playReason`.

TPIDs live on `MSFT_TPID__c`, `MSFT_All_TPIDs__c` and `MS_Sales_TPID_Best_Match__c`. There is
no `TPID__c`. An account with a TPID is run as co-sell with the Microsoft account team and a
delivery partner.

Opportunities whose close date has already passed are flagged **stale** and scored as zero.
They are reported as a hygiene finding, never counted as coverage — a past-dated deal is not
forecastable whatever its stage says.

### 7. Stage 2 — final ranking

```bash
python3 SCRIPTS/rank.py stage2 "<RUN>/fy27-territory-plan.json" "<RUN>/potential.json" "<RUN>" \
  --triggers "<RUN>/triggers.json" --crm "<RUN>/crm-context.json" --count 40 \
  --overrides "<RUN>/overrides.json"
```

Re-ranks on the full composite — **potential ARR 40, live H1 pipeline 20, active communication
20, trigger recency and type 20** — and cuts tiers at the top 25% (Tier 1 – Must win), next 35%
(Tier 2 – Build), remainder (Tier 3 – Develop). Use `--count` between 30 and 50. Pipeline value
is discounted by how far the best live opportunity has advanced, so a large deal parked early
cannot outrank a smaller one near close.

### 8. Seller corrections — `overrides.json`

Salesforce is never fully current. The seller knows things the CRM has not been told: a deal
agreed but not yet raised, a conversation that happened off-system, a partner flag that is
wrong. `overrides.json` in the run directory carries those facts into the arithmetic so they
survive a re-run.

**The rule that makes this safe: overrides supply missing facts, never forced rankings.**
The deck states its accounts are ranked on potential, pipeline, communication and triggers.
Pinning an account to a rank would make that sentence false. Correct the underlying fact and
let the existing weights move the account — or report that they did not.

```jsonc
{
  "accounts": {
    "001XXXXXXXXXXXXXXX": {              // Salesforce ID, with name fallback
      "name": "Example Account Ltd",
      "pipeline": [                       // sized through pricing.json, never typed as dollars
        {"product": "GHE",  "seats": 335,      "quarter": "FY27 Q1", "reason": "agreed, not yet raised"},
        {"product": "GHAS", "committers": 285, "quarter": "FY27 Q1", "reason": "agreed, not yet raised"}
      ],
      "engagement": {"twoWay": true, "meetings": 1, "lastActivity": "2026-07-01",
                     "reason": "live GHAS discussion"},
      "industry": "Financial Services",   // Salesforce Industry blank or miscoded
      "regulated": true,                  // feeds the Trust fallback tier
      "industryReason": "what the account actually does — printed as the basis",
      "msftOverlap": false,               // e.g. GitHub-direct account wrongly carrying TPIDs
      "msftCoSell": true,                 // forces Microsoft tier 1, sourced "seller"
      "msftCoSellReason": "worked jointly with the Microsoft account team",
      "play": "Scale",                    // correct a play the ladder got wrong
      "playReason": "why the ladder is wrong here — printed as the play basis",
      "sellerAsserted": true,             // REQUIRED to give a play to an Unclassified account
      "suppressOpportunities": ["Acme India"]  // drop a misfiled opp by name fragment
    }
  }
}
```

A pipeline line may carry `rateMonth` where the SKU has no rate in `pricing.json`
(Secret Protection, for example), so the quantity stays visible instead of collapsing
to a bare dollar amount.

**`potential.py` reads the overrides too**, via an optional fifth positional argument
(defaults to `<RUN>/overrides.json`). Seller-asserted `pipeline` lines become sizing lines
with `basis: "seller-asserted"`, so a committed deal reaches `potentialArr` and the account
can rank on its own merit.

> A seller-asserted line **replaces** the modelled line for the same product rather than
> adding to it. The model estimates whitespace; a known contract is better information than
> an estimate, and summing the two would count the same seats twice. This matters most for
> accounts the model scores at zero — an account that already owns a product has no modelled
> whitespace, so without this it would never enter the candidate pool no matter how large its
> live deal.

`potential.py` reports `sellerAssertedAccounts` and `overridesUnmatched`. **Check
`overridesUnmatched` is empty** — a typo'd name fails silently otherwise.

### Correcting a wrong telemetry input: `signals`

Some SuperDash figures are reported org-wide and are the wrong *basis* for a quote even
when they are internally consistent. The clearest case is **active committers**: the upload
counts every cloud user who pushed in 90 days, which can be far larger than the population
actually in scope for GHAS — and because GHAS sizes per committer, a wrong committer count
is the single largest distortion available to this model.

A seller who has verified the real number states it:

```json
"<account name>": {
  "signals": { "activeCommitters": 374 },
  "signalsReason": "SuperDash reports 989 cloud committers org-wide; 374 verified with the customer as in scope for GHAS."
}
```

Any key already present in the account's `revenueSignals` can be corrected —
`activeCommitters`, `copilotWhitespace`, `adoWhitespace`, `ghasSeats`, `gheSeats`. An
**unknown key is ignored**, because a typo would otherwise size off a field nothing reads.

> **This is not the `pipeline` override, and must not be used as one.** `pipeline` asserts a
> deal the customer has agreed to and flows into H1 coverage. `signals` only corrects an
> input to the whitespace model — it changes `potentialArr` and therefore ranking, and it
> never touches pipeline or attainment. Using `pipeline` to fix a bad telemetry reading
> would silently inflate coverage.

The corrected line renders with `basis: "seller-corrected"` and its note records what the
upload said, the corrected value, and the stated reason, so the number stays traceable to
whoever asserted it. `potential.py` also reports every correction under `signalCorrections`.

**Corrections apply at sizing, not at ingest.** The whole-book workbook stays a faithful
record of what the upload said; the focus pack carries the corrected figure. That is
deliberate — the two are answering different questions, and overwriting the upload would
destroy the evidence that a correction was needed.

### The flag that tells you a correction is needed

A correction only helps if someone notices the number is wrong. `potential.py` therefore
flags any account whose active committers exceed its licensed GHE/VS seats by more than
**1.5×**, and reports them under `dataQualityFlags`. The flag text lands in the
**`Check before quoting`** column of the focus workbook's `Sizing Detail` sheet, on the
GHAS row it affects — so the assumption is legible next to the dollars it produced.

A high ratio is not automatically an error: contractors and monorepo automation inflate the
cloud-wide count legitimately. The flag asserts nothing; it just makes the largest available
distortion arguable before the number is quoted. Once a seller states the real figure with
a `signals` override, the flag clears, because the question has been answered.

Then apply and re-run the downstream steps:

```bash
python3 SCRIPTS/overrides.py check "<RUN>/overrides.json" \
    "<RUN>/fy27-territory-plan.json" "<RUN>/focus-accounts.json"
```

Keys are validated against the **full report**, not the focus set — overrides
legitimately target accounts outside the 40 (suppressing a misfiled opportunity,
asserting a play on a prospect). The optional third argument reports which overrides
fall outside the focus set as information, not as an error.

**`sellerAsserted` breaks an invariant on purpose.** `plays.py` normally only moves
accounts *between* plays, never in or out of the play set, so the candidate list never
depends on whether enrichment ran. A prospect with no product row in SuperDash is
"Unclassified" and would be skipped entirely. Setting `sellerAsserted: true` alongside
`play` lets the seller put such an account into a play. It is gated behind that explicit
flag so it can never happen by accident. Note that an account entering this way still
has to *earn* a rank: with no seats, committers or logged activity it will score zero on
potential and communication and will not reach the focus 40. That is correct behaviour —
it appears on the Microsoft co-sell slide with a blank rank rather than being padded
into the ranking.

Four guarantees, each deliberate:

* **Unmatched keys are fatal.** A typo'd Salesforce ID silently doing nothing is worse than a
  crash, because the correction looks applied and is not.
* **Manual pipeline is sized, not typed.** Seats and committers go through `pricing.json` at
  the same rates as every other line, so a seller-sourced number cannot drift off list.
* **Engagement overrides ride the real curve.** `apply_engagement()` imports `score_for()` from
  the `fy27-territory-plan` skill rather than reimplementing it, so an overridden account sits
  on the identical scoring curve as an un-overridden one.
* **Seller-sourced pipeline is visually distinct on the deck.** Leadership will look for these
  numbers in Salesforce. If they cannot find them and the deck did not say so, the deck loses
  credibility. Slide 9 footnotes the amount and the account.

Record any genuine assumption under an `assumption` key alongside `reason`, so the deck can be
defended line by line and the assumption can be reverted to see what moves.

#### Seller learnings

The computed learnings can be corrected or extended from the same file. Each entry may carry
`replaces` (a headline prefix; **fatal if it matches nothing**, so a reworded computed learning
can never leave the seller's version arguing beside it), plus `headline`, `detail`,
`carryForward` and `evidence`. Text may use the tokens `{focusTotal}`, `{ghasAccounts}`,
`{ghasPipeline}` and `{ghasCoverage}`, substituted from run data; an unresolved token is fatal.

```jsonc
"learnings": [
  {
    "replaces": "GHAS is a story we told",
    "headline": "GHAS is a strong product we under-narrated",
    "detail": "... {ghasPipeline} of GHAS is close-dated in H1, {ghasCoverage}x the target.",
    "carryForward": "Lead security conversations with the differentiators, not an attach ask."
  }
]
```

### 9. Derive the learnings

```bash
python3 SCRIPTS/learnings.py "<RUN>"
```

Computes the H2 carry-forward learnings and the working / not-working read from counts in the
run's own records. Nothing here is authored in chat.

### 10. Build the decks and the evidence workbook

```bash
# 16-slide executive cut - what gets presented in a 30-minute slot
python3 SCRIPTS/exec_deck.py "<RUN>" "<RUN>/fy27-h1-leadership.pptx"

# 21-slide evidence pack - the detail brought as backup
python3 SCRIPTS/deck.py "<RUN>/fy27-territory-plan.json" "<RUN>/potential.json" \
  "<RUN>/focus-accounts.json" "<RUN>" --partners "<RUN>/partners.json"

python3 SCRIPTS/focus_workbook.py "<RUN>/fy27-territory-plan.json" "<RUN>/potential.json" \
  "<RUN>/focus-accounts.json" "<RUN>" --partners "<RUN>/partners.json"
```

`exec_deck.py` imports its `Deck` class and theme from `deck.py`, so both decks stay visually
identical and only one file owns rendering behaviour. Its sixteen slides map to the seven
questions:

| # | Slide | Question |
|---|---|---|
| 1 | H1 scorecard — attainment against Bucket 1 and Bucket 2, with the Q1/Q2 split | opener |
| 2 | H2 learnings carried forward | opener |
| 3 | Key deals in play — deal, size, stage, where it stands | opener |
| 4 | Key accounts — Tier 1 must-wins | Q1 |
| 5 | Portfolio by play, with TPID flags | Q2 |
| 6 | Innovate — every account, who leads it, one worked in PAF detail | Q2 |
| 7 | Innovate · Execution — how the play actually gets run | Q2 |
| 8 | Trust — every account, who leads it, one worked in PAF detail | Q2 |
| 9 | Trust · Execution — how the play actually gets run | Q2 |
| 10 | Scale — every account, who leads it, one worked in PAF detail | Q2 |
| 11 | Scale · Execution — how the play actually gets run | Q2 |
| 12 | The number — AIU, Copilot seats, GHE + GHAS | Q3 |
| 13 | Coverage — H1 target vs what already covers it | Q4 |
| 14 | Microsoft overlap and partner leverage | Q5 |
| 15 | What's working, what's not | Q6 |
| 16 | Asks — leadership and cross-functional | Q7 |

**Slides 6–11 are the answer to "how".** Slide 5 stays the one-page overview; each play then
gets a pair. The first names *every* focus account in the play, so nothing is summarised away,
and works one account through end to end. The second is the execution slide: it splits that
play's accounts into a **landing** motion and an **expansion** motion — on whether the account
already owns the product, the same test the play slide uses — and lays the PAF sequence for each
motion side by side, under the licence whitespace the play is going after.

A motion with no accounts says so plainly rather than inventing work for it. The two slides
answer different questions and are both needed: the play slide is *who*, the execution slide is
*how*.

**Led by is derived, never typed.** A TPID plus a named Microsoft owner is *Microsoft led*; a
TPID alone is *Partner led*, because a TPID account needs a partner even where none is mapped
yet, and those render `partner to source` — which is exactly the partnerships ask on slide 13.
Neither is *Seller led*.

**The PAF panel reads `paf.json`, it does not paraphrase it.** Step titles and summaries come
from the framework's key actions for that play. Land or expand is chosen from the account's own
`consumption` string — an account already consuming the play's product gets the expand sequence
— so the phase is observed, not asserted. If `paf.json` is missing the panel prints a visible
warning instead of inventing motions.

**The worked account per play defaults to rank, and is overridable per run.** Slides 6-8 each
detail their highest-ranked account. To illustrate a play with a different account, drop a
`paf-accounts.json` in the run directory:

```json
{ "Innovate": "<account name>", "Trust": "<account name>", "Scale": "<account name>" }
```

A name that does not match an account in that play is ignored and the rank default is used, so
a stale entry degrades rather than blanking the panel. No account name is baked into the skill.

**Type floor for screen sharing.** Body text and account lists render at 12pt, PAF step titles
at 12.5pt, table bodies at 12pt. Larger type buys fewer characters per cell, so strings are
truncated to fit rather than allowed to wrap into a collision; single-line cells set
`wrap=False` so `verify_deck.py`'s width check actually fires on them.

**Slide 3 is scoped to the whole book, not the focus 40.** Every other slide counts focus
accounts only. A live deal is a live deal wherever its account ranks, and because ranking
scores *modelled whitespace*, an account that has already committed seats can rank low while
carrying one of the largest deals on the desk. Deals shown from outside the focus set are
marked `*` and the footnote states that their value is excluded from the coverage figures,
so the two slides cannot be read as contradicting each other.

**A negative uncovered gap is rendered as language, not a minus sign.** Once dated pipeline
exceeds the remaining gap, the gap goes negative; printing "$-16K uncovered" reads as
a hole when it is a surplus. Slides 10 and 13 resolve the sign into words and, when a bucket
only nets out because one product over-covers, say so explicitly rather than reporting
comfort.

**Focus accounts, targets and coverage are all H1-scoped.** `crm_context.py` still dates every
opportunity against both windows (`inH1`, `inQ1`), and `targets.json` still holds per-quarter
quota, so the Q1 view remains available — the deck reports the half and shows the Q1/Q2 split
underneath, because the half is not evenly loaded. Watch for a bucket whose Q1 coverage runs
far ahead of its H1 coverage: that is pipeline concentrated in one quarter, and it should be
presented as concentration risk rather than comfort.

**Coverage is stated per bucket on its own terms.** Bucket 1 is `attained + dated H1
pipeline`; Bucket 2 is the run-rate carry alone. Presenting both as one blended percentage
hides the fact that they fail in completely different ways — Bucket 1 by a deal slipping,
Bucket 2 by consumption churning.

**No slide carries modelled potential ARR.** Sizing is presented on slide 9 in units that can
be verified — seats, active committers, invoiced credits — and every dollar figure elsewhere is
dated pipeline, a set target, or invoiced attainment. Potential ARR still drives *ranking*
(`W_STAGE1` 0.65, `W_STAGE2` 0.40), because it is a reasonable relative ordering signal; it is
simply never shown as though it were money in hand.

Verify before handing off — the PowerPoint canvas is unreliable, so check the file itself:

```bash
python3 SCRIPTS/verify_deck.py "<RUN>/fy27-h1-leadership.pptx" --coverage "<RUN>/coverage.json"
python3 SCRIPTS/verify_deck.py "<RUN>/fy27-h1-focus-accounts.pptx"
```

Three defect classes, and a non-zero exit so it can gate a hand-off:

* **overflow** — estimated rendered text height against the box, plus any shape off the slide.
* **collision** — text shapes overlapping by more than 30% of the smaller one.
* **inconsistency** — headline figures on the slides against `coverage.json`, including a
  regression guard that the blended net-new figure never appears (it reads as Bucket 1
  coverage while containing Bucket 2 money).

Geometry alone has missed real text defects twice. **Also read the rendered text** of slides
1, 6, 9 and 10 after any narrative change — a stale count passes every geometry check.

The deck is the argument; the workbook is the evidence. `Sizing Detail` gives one row per product
line with its rate and basis, so any figure on a slide can be traced in a single lookup.

### 11. Hand off

Give the teammate:

- both deck paths — the 16-slide leadership cut and the 21-slide evidence pack
- the account/tier/play mix, and total potential ARR against current ARR
- **the coverage read per bucket**, because that is what leadership will push on
- how many accounts carry a dated trigger, and how many do not
- the workbook path, and the fact that `Sizing Detail` is where challenges get settled

Offer to preview the deck by opening the `powerpoint` canvas on the generated file.

## Execution guidance comes from PAF

The per-play slides (6-11) are grounded in real **Product Adoption Framework** key actions, baked into
`paf.json` at build time by `build_paf.py`. Each play gets a **land** sequence for greenfield
accounts and an **expand** sequence for accounts with a footprint, and the appendix carries the
real resource links. That land/expand split is exactly what the execution slides render as their
two motions, which is why they are sourced rather than composed.

Regenerate only when PAF itself changes:

```bash
python3 SCRIPTS/build_paf.py   # requires gh auth; writes paf.json
```

Do not invent adoption steps in chat. If a key action is not in `paf.json`, it is not in the deck.

### Grounding a play in the real conversation

The worked-example panel on the play slides leads with what has actually been said to that account,
not with the generic sequence. Gather it into `<RUN>/conversations.json` before rendering:

```
heroes: { "<account name>": {
  source: "gong" | "override",   sourceNote, lastTouch, callCount, participants,
  agreed, blocker, committed,    strategic,
  pafActions: ["<paf.json action id>", ...],
  evidence: [{callName, date, id}]
} }
```

Gathering rules, each learned the hard way:

- **Walk the hierarchy, and search by name.** Gong files calls against whichever account record
  the meeting was booked under. One account in testing had **three unlinked Salesforce records**
  plus a partner-filed migration call, and `get_account_hierarchy` returned only itself. Querying
  the focus account's ID alone returns a partial history.
- **A missing call record is not a missing conversation.** An account can have no Gong calls and no
  notes under its own record while its history sits under the account the opportunity was filed
  against. Where there is genuinely no call, set `source: "override"` and the panel says
  "no call record" rather than implying contact.
- **`pafActions` answers the blocker.** Ids resolve across both phases, because an account
  mid-rollout may still need a land-phase action it skipped. An unknown id is skipped, not
  rendered blank. Omit `pafActions` and the play's generic sequence is used.
- Extract close to source wording. `agreed`/`blocker`/`committed` are rendered verbatim; do not
  compose slide sentences in chat.

`strategic` and `sourceNote` go to the speaker notes along with the play basis, which moved off
the panel to make room.

### Seller-authored asks

Slide 11 computes asks from the numbers. Asks that are not derivable — a competitor's packaging,
a partner's motivation — go in `<RUN>/asks.json` under `leadership`, `partnerships` and
`microsoft`, and render ahead of the computed ones.

The columns must clear the commitment bar, so computed asks are **trimmed to fit and the seller's
never are**. Whatever is trimmed is written into the speaker notes and the footnote says so.

Both files stay in the run directory. They name customers and carry competitive intelligence, so
**neither is ever published with the plugin**.

## Guardrails

- **Potential is not pipeline.** It is an opportunity size derived from product signals, to be
  qualified in discovery. Never present it as forecast, commit, or propensity.
- **AIU stays out of potential ARR.** Consumption already invoiced is existing revenue, and
  included credits ship bundled with the seat. Counting either as upside double-counts the book.
  The deck shows AIU as measured run-rate and as capacity unlocked, separately.
- **Every trigger is dated and cited, or it is dropped.**
- **A focus account must belong to a play.** `rank.py` excludes unclassified accounts from both
  stages: the deck organises Q2 and Q4 by play, so an account with no play has nothing to be
  presented against no matter how large its potential is. It stays in the territory plan; it just
  cannot be a focus account until a product or usage signal classifies it.
- **State coverage, never imply completeness.** Where activity, partner mapping, or Microsoft AM
  data is thin, the deck says so on the slide. That is deliberate — it is what makes the asks
  credible.
- **GHE has no public per-seat price.** Its rate is a derived median and is labelled `derived`
  everywhere it appears. Say so if asked.
- **Isolation.** Everything is written into the teammate's own run directory. Never commit it,
  never copy it between teammates.
