---
name: fy27-territory-plan
description: "Build an FY27 GitHub territory plan from a SuperDash export, enriched live from Salesforce and Gong. Classifies every account into the Innovate, Trust, and Scale revenue plays, reports duplicate/parent-child normalization, scores engagement and a meeting-booking Sprint Focus shortlist, renders an interactive app, and exports an Excel workbook. Use for 'build my territory plan', 'FY27 book of business', 'SuperDash plan', 'which accounts go in which play', or 'territory plan for my sales leader'."
---

# FY27 Territory Plan

> **Building the full H1 plan *and* the leadership deck?** Use **fy27-h1-run** instead — it drives
> this skill, the enrichment and the deck in the right order, and it enforces the licensing-before-
> workbook-rebuild rule the GHCP sprint queue depends on. Use this skill on its own when the
> teammate wants only the plan and the workbook.

## Mission

Turn a seller's SuperDash export into a territory plan they can hand to a sales leader:
every account bucketed into **Innovate**, **Trust**, **Scale**, or **Unclassified**, with live
Salesforce and Gong engagement, a meeting-booking shortlist, and an Excel workbook.

The enrichment runs on **the invoking teammate's own MCP credentials**. Nothing is shared, nothing
is cached across users, and no credential ever reaches a browser.

## When to invoke

- "Build my FY27 territory plan from <file>"
- "Which of my accounts are Innovate / Trust / Scale?"
- "Prep my book of business for the QBR"
- "Refresh my territory plan with the latest Salesforce activity"

## Inputs

A SuperDash export (`.xlsx` or `.csv`). Accept an absolute path or an attachment. If the teammate
has not given one, ask for the file path — do not guess.

## Pipeline

Run these in order. `SCRIPTS` is this skill's `scripts/` directory.

### 1. Create an isolated run

```bash
python3 SCRIPTS/new_run.py "<path to SuperDash export>"
```

Returns `runDir`, `inputPath`, `sourceName`. Use `runDir` for everything that follows. Never write
results into the plugin directory or into another run.

### 2. Classify the book

```bash
python3 SCRIPTS/workbook.py "<inputPath>" "<runDir>" "" "<sourceName>"
```

Returns `reportPath`, `workbookPath`, `accountCount`. Read `reportPath` and report to the teammate:

- source rows, excluded rows, exact-duplicate groups, parent/child groups collated
- de-duplicated account count
- accounts per play, including the Unclassified discovery queue

Do **not** restate or reinvent the classification rules. `workbook.py` owns them; describing them
differently in chat is how the plan and the workbook drift apart.

### 3. Enrich from Salesforce and Gong

Follow the **fy27-crm-enrichment** skill. It holds the pinned queries, the batching rules, and the
guardrails. It produces one raw JSON file; then:

```bash
python3 SCRIPTS/enrich_activity.py "<runDir>/raw-crm.json" "<runDir>"
```

If the teammate has no Revenue MCP access, or Salesforce/Gong errors, skip this step and say
plainly which source was unavailable. The plan is still valid — engagement simply stays **Unknown**.
Never present an unenriched account as cold.

### 4. Rebuild with engagement, then score the sprint

```bash
python3 SCRIPTS/workbook.py "<inputPath>" "<runDir>" "<runDir>/salesforce-activity.json" "<sourceName>"
python3 SCRIPTS/sprint_score.py "<runDir>/fy27-territory-plan.json"
python3 SCRIPTS/workbook.py --from-report "<runDir>"
```

The final call is required: it is what folds the Sprint Focus sheet into the workbook.

**Use `--from-report` for every rebuild after the first.** The normal invocation re-derives
`fy27-territory-plan.json` from the raw SuperDash export, which silently discards any play
overrides applied since. `--from-report` rebuilds the workbook from the report already in the
run directory and writes no JSON, so overrides survive.

#### What lands in the Sprint Focus sheet

The sheet is the meeting-booking queue, and it takes the best source available in the run
directory:

1. **`focus-accounts.json`** — if an H1 focus run has produced a ranked focus list, that *is*
   the sprint plan. It already carries the seller's overrides, the agreed play, the tier, the
   Microsoft/partner motion, the next action and the pipeline. It wins, because re-scoring the
   raw book here would contradict the deck built from the same run.
2. **`sprint-focus.json`** — otherwise the trigger-scored shortlist from `sprint_score.py`.
3. **Neither** — the sheet still renders its header and source line. It must never come out
   blank; a zero-row `add_table` writes nothing at all, which is how this sheet silently
   shipped empty.

`salesforce-contacts.json` fills the Key Contacts column when the focus stage has no contacts
of its own, ranked so the most senior name appears first. Where no contact exists the row says
so rather than leaving a blank a reader would mistake for a rendering fault.

#### GHCP segmentation

When `licensing.json` is also present, the queue is re-ordered around GitHub Copilot rather
than composite rank, because seat attach and token activation are different conversations with
different personas. `ghcp.py` splits the book into three segments:

- **Copilot seat expansion** — GHE licences exist without Copilot. Addressable headroom is
  `installed GHE seats − Copilot seats`. Agreed-but-unlanded GHE deliberately does *not* count:
  a seat that has not landed cannot have Copilot attached to it.
- **AIU activation** — Copilot seats are sold but users are under the 1,900 credits/user/month
  bundled with the seat. Measured per user, not per account.
- **Land GHE first** — no GHE landed, so there is nothing for Copilot to attach to.

An account appears in exactly one segment, chosen by *seats at stake* (`headroom` vs
`dormant seats`) rather than dollars. Overage revenue is legitimately zero until an account
exhausts its allowance, so a dollar comparison would always favour seat expansion and would
push a large activation case into the wrong segment for the sake of a handful of seats.

Seat prize uses the account's own observed Copilot rate where it has billing history, but only
if that rate falls inside a plausibility band around list. A blended realised rate is not the
price of the next seat — an account that added seats recently annualises far below what it
actually pays, and pricing new seats off that number understates the prize. Rejected rates fall
back to list and record the reason in the Prize Basis column.

Without `licensing.json` the sheet falls back to the 18-column focus layout and says so in its
source line.

### 5. Render in the app

```
open_canvas(canvasId: "fy27-territory-plan", instanceId: "fy27-territory-plan",
            input: { reportPath: "<runDir>/fy27-territory-plan.json",
                     inputPath:  "<runDir>/<uploaded file>" })
```

The canvas derives everything from `reportPath`, so it always shows that run's data.

### 6. Hand off

Give the teammate:

- the play distribution and what changed after enrichment
- coverage: how many accounts matched activity, how many are Unknown, as-of date, window
- the top Sprint Focus accounts and why each is ranked there
- the workbook path (`<runDir>/FY27 Territory Plan.xlsx`)

## How to talk about the plays

When the teammate asks how to actually win an account, use the **paf** skill for GitHub's Product
Adoption Framework key actions, and keep the motion tied to the account's observed product signals.

| Play | What qualifies an account | Where to take it |
|---|---|---|
| **Innovate** | Copilot whitespace, AI/developer-productivity headroom | Developer experience and velocity; land Copilot where the seats already exist |
| **Trust** | Security whitespace, GHAS gap against GHE footprint | Secure the SDLC; AppSec and platform-security owners |
| **Scale** | Actions/ADO migration TAM, metered consumption growth | Platform consolidation and CI/CD standardization |
| **Unclassified** | No qualifying product signal in this upload | Discovery queue — never assign a play without evidence |

## Guardrails

- **Decision support, not forecast.** Scores are hypotheses requiring seller validation. Never
  present potential, engagement, or readiness as ARR, pipeline, or propensity.
- **Unknown is not cold.** An account with no matched activity is Unknown. Say so.
- **Scope.** Only accounts present in the uploaded file are ever queried or reported.
- **Isolation.** All output lives in the teammate's run directory under
  `~/.copilot/fy27-territory-plan/runs/`. Never commit it, never copy it between teammates.
- **One source of truth.** `workbook.py` owns classification and scoring; `enrich_activity.py`
  owns engagement arithmetic. Do not compute either in chat.

## Fallback

Teammates without Revenue MCP access can use the browser app at
<https://therajeev08.github.io/fy27-territory-plan-team/>, which does classification, Sprint Focus,
and Excel export locally with no CRM. It also supports the older manifest export / enrichment import
handoff if someone else runs the enrichment for them.
