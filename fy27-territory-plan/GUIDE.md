# FY27 Territory Plan — Teammate Guide

## What this does

You give it your SuperDash export. It gives you back the territory plan you present to your
leader: every account sorted into **Innovate / Trust / Scale**, sized on what they actually own,
with who to call this sprint and how each play gets won.

Four things come out of it:

| You get | What it's for |
|---|---|
| **A 16-slide leadership deck** | The meeting itself — including a slide per play on *how you get there* |
| **An Excel workbook (8 tabs)** | Your working document. Every account carries its play, your activity with them, and their licences |
| **A Sprint Focus list** | Who to book **this** sprint, ranked, with the reason attached |
| **An evidence pack** | The backup, for when someone challenges a number |

You do not need to understand any of the machinery. There is one Terminal command, once, and
after that everything happens in the Copilot chat window.

---

## One-time setup (about 2 minutes)

**1. Open Terminal** — press `Cmd + Space`, type `Terminal`, press Enter.

**2. Paste this one line and press Enter:**

```bash
curl -fsSL https://raw.githubusercontent.com/TheRajeev08/fy27-territory-plan/main/install.sh | bash
```

Wait for it to say **"Done."** If it asks for your Mac password, type it — the screen won't
show characters as you type, which is normal.

**3. Quit Copilot completely** (`Cmd + Q`) and reopen it. Closing the window is not enough.

You never need to do this again.

**If the plan doesn't run**, check the install before anything else. Paste this into
Terminal — it prints exactly what is missing, and it is much faster than describing the
symptom:

```bash
bash ~/.copilot/installed-plugins/fy27-territory-plan/doctor.sh
```

Do **not** upload any of the plugin's files into the Copilot chat window. They are
documentation, not an installer — Copilot will try to work from them and quietly build a
stripped-down plan with none of the live data.

---

## Every time you want a plan

**1. Export from SuperDash** and save the `.xlsx` or `.csv` (Downloads is fine).

A SuperDash export is the only thing that works. A plain list of account names has no product
signal in it, so there would be nothing to sort the accounts *on* — anything it produced would be
invented. If you only have names, get the export first.

**2. In Copilot, type this one sentence:**

> Build my FY27 H1 territory plan and leadership deck

That is the whole instruction. It runs everything in the right order — this matters more than it
sounds, because doing it in two separate asks is the one reliable way to end up with a Sprint
Focus tab that is quietly missing half its content.

**3. It will ask for your file** — drag the file into the chat window, or paste its location.

**4. The first time only, it asks for your territory and quota.** It remembers them, so later runs
skip straight past. Anything you don't have, say so — it prints **TBD** rather than guessing.

**5. Wait 20–40 minutes.** It is doing a lot: cleaning your list, sorting every account into a
play, pulling your live Salesforce and Gong activity under *your* login, looking up what each
account actually has licensed, researching dated news triggers, ranking who to call, and building
the deck and workbooks. It checks its own work at the end and tells you plainly what came out
thin.

Leave it running. It will tell you when it is done, and give you the file paths.

---

## What you get back

### The deck (16 slides)

| Slides | What they answer |
|---|---|
| 1–3 | Where you stand: attainment, what you learned last half, deals in play |
| 4–5 | Which accounts, and which play runs on each |
| **6–11** | **The plays — and how each one actually gets run** |
| 12–13 | The prize in dollars, and how much of your target is already covered |
| 14–16 | Microsoft and partner leverage, an honest read, and your asks of leadership |

**Slides 6–11 come in pairs — this is the part leaders push on.** For each play you get:

- a **play slide** — every focus account in that play, who leads it, and one account worked
  through end to end using a real conversation you have already had
- an **execution slide** — the same play split into two motions: **landing** it at accounts that
  don't have the product yet, and **expanding** it at accounts that do. Each motion carries its
  step-by-step sequence from GitHub's Product Adoption Framework, under the licence whitespace
  that play is going after.

If a motion has no accounts this half, the slide says so rather than inventing work for it.

### The workbook (8 tabs)

| Tab | What's in it |
|---|---|
| Executive Summary | The one page for your leader |
| All Accounts | Everything — play, engagement **and** licences |
| Innovate / Trust / Scale | One tab per play, same columns |
| Unclassified | Discovery queue — no product signal yet |
| **Sprint Focus** | Who to book **this** sprint, ranked, with GHCP segments |
| Methodology | How it decided, for when you're challenged |

Alongside it you get an **evidence workbook**. When someone challenges a number on a slide, its
`Sizing Detail` tab shows exactly where it came from — the quantity, the rate, and whether that
rate was observed on the account, taken from published pricing, or derived.

---

## Reading the account tabs

Every account row now answers two different questions side by side: **are we talking to them**,
and **what do they actually own**.

| Column | What it tells you |
|---|---|
| Engagement Tier, Two-way, Last Activity, Meetings | Your real activity, from Salesforce and Gong |
| **GHE Seats** | Enterprise + Team seats they are actually consuming |
| **Copilot Seats** | Copilot seats live today |
| **Copilot Attach %** | Copilot as a share of their GHE base — your headroom in one number |
| **Active Committers** | The population GHAS would actually bill on |
| **GHAS Committers** | How many of those are already covered |
| **Plan** | Enterprise or Team. This one changes the play — see below |

These are read live from GitHub's own licensing, not from your SuperDash upload. The upload
counts signals across the whole cloud tenant, which overstates what a customer is billed for —
badly for GHAS, which bills per **active committer**, not per seat.

### Blank, zero and Unknown mean three different things

They sit in adjacent columns, and mixing them up will make you drop a good account.

| You see | It means | Do |
|---|---|---|
| **Blank** licence cell | We couldn't look this account up — usually no GitHub tenant linked in Salesforce | Get the tenant linked, then re-run |
| **0** | We looked, and they genuinely have none | Treat as real whitespace |
| **Unknown** engagement | No logged activity matched — very often just CRM hygiene | Don't deprioritise on this alone |

If your Revenue MCP / Salesforce connection isn't set up, **every** licence column comes back
blank, not zero. That is deliberate. A zero would be telling you the account has no seats, which
is a much stronger claim than "we didn't manage to check".

---

## Reading the plays

| Play | Why an account landed here | Your angle |
|---|---|---|
| **Innovate** | Copilot whitespace, AI/productivity headroom | Developer velocity — land Copilot where seats already exist |
| **Trust** | Security gap against their GitHub footprint | Secure the SDLC — AppSec and platform-security owners |
| **Scale** | Actions / ADO migration TAM, metered growth | Platform consolidation, CI/CD standardization |
| **Unclassified** | No qualifying signal in this upload | Discovery — never assign a play without evidence |

**Sprint Focus tiers:** *Priority* = two-way conversation **and** meetings happening.
*High* = one or the other. *Medium/Low* = no recent contact, ranked by account potential.
*Unranked* = no activity data matched.

**Ask follow-ups in the same chat**, e.g. *"How do I win the Trust accounts?"* — it pulls GitHub's
Product Adoption Framework for concrete next actions tied to each account's own product signals.

### Why a play count can change between runs

If you ran this before and your Trust number just dropped, that is almost certainly a
**correction, not a bug**.

GHAS is not sold on GitHub Team. So an account on a Team plan can look like a security play in the
upload — it has committers, and the upload counts them — while being an account that could never
actually be invoiced for GHAS. Live licensing is the only place their plan type is visible. When
it shows Team, the GHAS line is dropped and the account moves to **Scale**, because the real
motion is consolidating them onto GHE first.

The run tells you exactly which accounts moved and why. If your leader asks why the mix shifted,
that list is your answer.

---

## Two things to be careful about

- **"Unknown" does not mean cold.** It means no logged activity matched — often just CRM hygiene.
  Don't deprioritize an account on that alone.
- **Scores are hypotheses, not forecasts.** They tell you where to *look*, not what will close.
  Validate the stakeholder and a dated next step in Salesforce before it goes into a plan.

---

## Re-running later

Don't start a fresh run to refresh an existing one. Any corrections you made live in that run, and
a new run doesn't have them. Instead:

> Refresh my existing run with the latest Salesforce activity, keep my overrides, and rebuild the
> workbook and both decks.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| "Done." never appeared | Re-run the same command — it is safe to repeat and repairs itself |
| Copilot doesn't recognize the request | The app wasn't fully quit. `Cmd + Q`, reopen, try again |
| Plan works but no Salesforce/Gong data | You need the `github-revenue` plugin connected and Salesforce-authenticated. Everything else still works |
| Licence columns are all blank | Same cause as above — no Salesforce connection means no GitHub tenant to look up. Blank is the correct output, not an error |
| Some licence columns blank, most filled | Those accounts have no GitHub tenant linked on their Salesforce record. Link it, then re-run |
| Deck has 13 slides, not 16 | An older run. Ask it to rebuild the decks from your existing run |
| Excel file won't generate | Run `python3 -m pip install --user xlsxwriter` |
| Presentation won't generate | Run `python3 -m pip install --user python-pptx` |
| Deck says "no partner relationships mapped" | That's a data gap, not an error — partner records exist per opportunity, and most accounts have none. The deck turns it into an ask |
| Deck shows 0 triggers for most accounts | Only news with a real date *and* a real source link is counted. Unverifiable claims are dropped on purpose |
| No Revenue MCP access at all | Use the browser version: <https://therajeev08.github.io/fy27-territory-plan-team/> — no CRM, but classification, Sprint Focus and Excel export all work |

Your CRM data stays on your machine, runs under your own credentials, and is never shared
between teammates. Your accounts, your licence readings and your corrections all stay inside your
own run folder — nothing about your book is written anywhere another teammate could reach.
