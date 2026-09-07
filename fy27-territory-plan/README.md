# FY27 Territory Plan

> **A mirror of [`TheRajeev08/fy27-territory-plan`](https://github.com/TheRajeev08/fy27-territory-plan)**,
> which is the canonical repo and the one the installer points at. This copy is the published,
> scrubbed bundle: no territory, quota, run rate or account data. That is enforced rather than
> remembered — the publisher runs an allowlist gate that fails the build on any unrecognised file
> shape, any non-null figure in `targets.json`, or any file quoting a real account name from the
> author's local run.

Turn a SuperDash export into an FY27 territory plan you can hand to a sales leader — every account
bucketed into **Innovate**, **Trust**, or **Scale**, enriched live from **your own** Salesforce and
Gong access, with a meeting-booking shortlist and an Excel workbook.

Ask Copilot:

> Build my FY27 H1 territory plan and leadership deck from ~/Downloads/Super Summary.xlsx

## Why a plugin instead of the web app

The [browser app](https://therajeev08.github.io/fy27-territory-plan-team/) does classification and
export, but a static page can never hold CRM credentials, so engagement is always Unknown there.

This plugin runs inside your authenticated Copilot session, so the Salesforce and Gong calls happen
on **your** credentials, scoped to **your** uploaded accounts. No manifest export, no JSON re-import,
no shared logins.

## Install

Paste this into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/TheRajeev08/fy27-territory-plan/main/install.sh | bash
```

It downloads the plugin, installs the one Python dependency, and verifies the result. It is
safe to re-run — an existing copy is updated, and a damaged one is repaired.

Then **quit the Copilot app completely and reopen it** (closing the window is not enough), and ask:

> Build my FY27 H1 territory plan and leadership deck

That one sentence builds the plan, the sprint queue and both decks in the order they depend on
each other. Asking for the plan and the deck separately is the one reliable way to end up with an
incomplete Sprint Focus tab and no error to tell you.

Optionally install the canvas so results render in-app — ask Copilot:

> Install the extension from https://github.com/TheRajeev08/fy27-territory-plan/tree/main/fy27-territory-plan/extensions/fy27-territory-plan

<details>
<summary>Prefer to install by hand?</summary>

```bash
git clone https://github.com/TheRajeev08/fy27-territory-plan.git \
  ~/.copilot/installed-plugins/fy27-territory-plan
python3 -m pip install --user xlsxwriter
```

The clone target is the *bundle* directory: the loader looks for plugins at
`~/.copilot/installed-plugins/<bundle>/<plugin>/plugin.json`, which is why this repo nests the
plugin one level down rather than putting `plugin.json` at its root. The repeated folder name
after cloning is expected.
</details>

### Requirements

| Requirement | Why |
|---|---|
| `github-revenue` plugin, Salesforce-authenticated | Live activity, contacts, pipeline, Gong |
| Python 3.9+ | Classification and scoring |
| `xlsxwriter` | The Excel workbook export |
| `python-pptx` | The H1 focus presentation |

Install the two third-party dependencies:

```bash
python3 -m pip install --user xlsxwriter python-pptx
```

Without Revenue MCP the plugin still works — plays, Sprint Focus, and the workbook all build
locally. Engagement simply stays **Unknown**, which is reported honestly rather than shown as cold.

## What you get

1. **Normalization stats** — source rows, exact duplicates, parent/child groups collated, and the
   de-duplicated account count, so the numbers are defensible before anyone reads the plan.
2. **Play distribution** — Innovate / Trust / Scale, plus an Unclassified discovery queue for
   accounts with no qualifying product signal. Accounts are never assigned a play without evidence.
3. **Live engagement** — activity volume and direction, verified two-way evidence, named personas,
   open pipeline, and Gong calls for the shortlist, with coverage and an as-of date.
4. **Sprint Focus** — a ranked shortlist of who to book this sprint and why.
5. **Excel workbook** — eight sheets, executive dashboard first.
6. **Licence and consumption data** — GHE seats, Copilot seats, attach rate, active committers,
   GHAS coverage and plan type on every account, read live from GitHub's own licensing rather than
   inferred from the upload. Where a lookup did not resolve, the cell is **blank, never zero** — a
   zero would assert the account has no seats, which is a far stronger claim than "we did not
   manage to check".
7. **H1 leadership presentation** — built by the same single instruction, not a separate ask.

   A 16-slide leadership deck plus an evidence pack naming 30–50 focus accounts for the half, sized
   in AIU, Copilot seats and GHE + GHAS. Each play gets a **pair** of slides: the accounts, then an
   execution slide splitting the play into landing the product where it is absent and expanding it
   where it is already in, with the step sequence for each motion taken from GitHub's Product
   Adoption Framework. Plus Microsoft and partner leverage, an honest working / not-working read,
   and the asks of leadership. It ships
   with a companion evidence workbook whose `Sizing Detail` sheet gives one row per sized line with
   its rate and basis, so any figure on a slide can be traced in a single lookup.

## How enrichment stays trustworthy

- **Scoped.** Only Salesforce IDs present in your upload are ever queried; anything else is rejected.
- **Deterministic.** The agent fetches rows; `enrich_activity.py` does every calculation, so the
  same CRM state always yields the same scores.
- **Honest about gaps.** No match means *Unknown*, never cold. A failed source keeps its own status
  and error instead of being rendered as a zero.
- **Isolated.** Each run writes to `~/.copilot/fy27-territory-plan/runs/<timestamp>/`. Nothing is
  written back into this package and nothing is shared between teammates.

Engagement tiers are evidence-first, not score-first:

| Tier | Meaning |
|---|---|
| Priority | Verified two-way contact **and** a meeting |
| High | Two-way contact **or** a meeting |
| Medium | One-way outreach only, but recent |
| Low | One-way outreach only, and stale |
| Unranked | No matched activity — Unknown, not cold |

## Layout

```
plugin.json
skills/fy27-territory-plan/     orchestrator + scripts (workbook, enrichment, sprint, run setup)
skills/fy27-crm-enrichment/     pinned SOQL and guardrails
skills/fy27-h1-focus-deck/      H1 focus presentation: sizing, ranking, PPTX, evidence workbook
extensions/fy27-territory-plan/ canvas that renders the plan in-app
enrich-test.py                  regression tests for the enrichment transform
```

`workbook.py` is the single source of truth for play classification. The browser app's
`engine-core.js` is parity-tested against it, so the two surfaces cannot drift.

## Caveat

Decision support, not forecast. Potential, engagement, and readiness are hypotheses that need
seller validation — they are not ARR, pipeline, or propensity.
