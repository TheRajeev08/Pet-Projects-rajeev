# Copilot Consumption Dashboard

A daily-refreshed, **local-only** dashboard of the Salesforce accounts you own that are actively consuming GitHub Copilot. It shows every available consumption signal and what changed week over week.

> **Privacy:** this repo is public. Only code and query templates live here. Your config, account data and the rendered dashboard live **outside the repo** in `~/CopilotConsumptionDashboard/` (override with the `CCD_HOME` env var), so they can't be committed. That also means every daily run, whatever worktree it lands in, extends the same history.

## What's in it

| Section | Contents |
|---|---|
| **Overview KPIs** | Consuming accounts, accounts active this week, weekly/28-day active users, assigned and contracted seats, users at risk, acceptance rate, UBB gross/billable spend, AI units, month-to-date spend with run-rate and projected billable, Copilot billed over the last 12 months, GitHub ARR, health mix. All show WoW deltas and 13-week sparklines. |
| **What changed this week** | New or stopped consumers, active-user swings, seat assignment and contract changes, health category moves, rising users at risk, spend spikes and drops, newly adopted surfaces, pool overage risk, renewals within 90 days with declining usage, low utilisation, and ownership adds/removes. Filterable, and each item links to its account. |
| **Accounts table** | Sortable and searchable, with WoW deltas: health, actives, assigned, utilisation, contracted seats, at risk, 4-week trend, UBB 7d/28d/MTD/run-rate, acceptance, top surface and model, ARR, renewal. CSV export. |
| **Account drill-down** | 13-week charts (actives, seats, health, at risk, spend, AI units, acceptance), contracted seat history, a per-integration/surface table, model mix, risk-factor breakdown, editors, MAU retention (GRR/NRR), pools, billing, pipeline, CSM/CSA. |
| **Portfolio trends** | Active users, active accounts, spend, spend stacked by surface, AI units, acceptance, surface adoption table, model mix. |
| **Not yet consuming** | Owned accounts with no Copilot signal, ranked by GHE seats and ARR. These are whitespace. |
| **Data notes** | Definitions, per-source freshness and row counts, and caveats. |

**Consuming** means any Copilot active users, seats assigned, or usage-based-billing (UBB) spend in the last 90 days. **Scope** is accounts where you are the Salesforce Account Owner.

## How it works

```mermaid
flowchart LR
  A[Daily Copilot app automation] --> B[Agent runs queries/*.kql + .soql via Revenue MCP]
  B --> C[refresh.py collect<br/>reads results from the session log]
  C --> D[~/CopilotConsumptionDashboard/data/raw/DATE/*.json]
  D --> E[build_dashboard.py<br/>metrics · WoW · changes]
  E --> F[data/snapshots/DATE.json]
  E --> G[~/CopilotConsumptionDashboard/dashboard.html<br/>self-contained, offline]
```

- The queries in `queries/` are the versioned source of truth. `{{OWNER_ID}}` and `{{OWNER_NAME}}` come from your config.
- **WoW** compares the latest 7 days with the 7 before, using daily facts, so it works from the first run. Ownership changes compare with the saved snapshot closest to 7 days earlier.
- The dashboard is a single HTML file with inline JS/SVG, no network calls and no CDN.

## Setup

1. `mkdir -p ~/CopilotConsumptionDashboard && cp config.example.json ~/CopilotConsumptionDashboard/config.json`, then fill in your Salesforce User Id (`005…`) and your name exactly as it appears as account owner. `data_dir` is optional. A `config.local.json` next to the scripts also works.
2. Make sure the **revenue-mcp-server** MCP is connected in the Copilot app.
3. Ask Copilot to *"Follow copilot-consumption-dashboard/REFRESH.md"*, or create a daily automation with that prompt.
4. Open `~/CopilotConsumptionDashboard/dashboard.html`.

Requires Python 3.9+ (stdlib only).

## Data sources and caveats

- **rev_source:** daily Copilot health score, daily UBB burn rate, UBB revenue by integration/model, monthly product ARR and seats.
- **c360 (experimental):** account entity (ARR, billing, renewal, CSM), seat/engagement snapshot, IDE telemetry, MAU retention.
- **Salesforce:** owned accounts. This is the source of truth for ownership. Accounts attributed to you in the warehouse but not owned in Salesforce are listed separately.
- UBB dollars are **usage-side** list value, not invoiced revenue. "Billable" is usage beyond included pools.
- Copilot product lines carry seats but $0 ARR in the product ARR fact, so ARR shown is the account's total GitHub ARR.
