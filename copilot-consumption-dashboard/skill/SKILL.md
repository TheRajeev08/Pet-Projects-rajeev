---
name: copilot-dashboard-ask
description: Answer questions about the user's Copilot consumption dashboard, meaning their owned Salesforce accounts' Copilot usage, seats, health, spend, surfaces, models and week-over-week changes. Examples are "which accounts are not active this week?", "top 5 by spend", "who is renewing soon with declining usage?" and "which accounts use Copilot CLI?". Use whenever the user asks about "my accounts", "the dashboard" or Copilot consumption across their book.
---

# Answering questions from the Copilot consumption dashboard

Answer **only from the dashboard's data** so answers always match what the dashboard page shows.

- **Never** query the Revenue MCP, Kusto, Salesforce or any other live source for these questions, even if it looks helpful.
- If the data can't answer a question, say so plainly and suggest adding it to the dashboard's daily refresh.

## How

1. Run `python3 {{ASK_PY}} schema` once per conversation. It lists the tables, columns, the shared definitions and the data date.
2. Translate the question into SQLite and run:
   `python3 {{ASK_PY}} sql "SELECT ..."`
   Use only SELECT or WITH. Run several small queries rather than one clever one if that's clearer.
3. Answer in a short sentence or two, followed by a compact table when the answer lists accounts.
4. Always mention the data date, e.g. "(data as of 2026-09-23)", from the `meta` table.

## Use the shared definitions

The dashboard's "Ask" box uses these same definitions. Don't invent your own.

| Question wording | Use |
|---|---|
| active / not active **this week** | `accounts.active_this_week` / `accounts.inactive_this_week` |
| active last week | `accounts.active_last_week` |
| stopped this week | `active_last_week = 1 AND active_this_week = 0` |
| using Copilot / consuming | `accounts.consuming = 1` (90-day window) |
| not using Copilot / whitespace | `consuming = 0` |
| whitespace seats / potential seats / SOM | `ws_whitespace_seats` / `ws_potential_seats` / `ws_som_annual` across all owned accounts, including non-consuming; label **Model estimate · parent-level** |
| projected to exceed pool | `entitlement_status = 'Projected to Exceed Included Pool'`; `forecast_above_pool` is projected above included pool, not invoiced overage |
| UBB forecast and range | `forecast_bill`, `forecast_bill_low`, `forecast_bill_high`; show range only for positive forecast |
| UBB adoption | `ubb_adoption_band`, `ubb_adoption_rate`, `ubb_week_users` (users with UBB activity), `ubb_license_users` (licensed users) |
| adoption funnel | `assigned` → `active_28d` → `engaged_1d28`; `installed_1d28` is partial IDE telemetry, not a funnel step |
| declining / growing | `declining` / `growing` (4-week trend of weekly actives, ±10%) |
| low utilisation | `low_utilization`; utilisation = 28d active ÷ assigned |
| renewing soon | `renewing_in_days BETWEEN 0 AND N` (default 90) |
| health changed | `health_moved = 1` (`health_prev` → `health_cat`) |
| week-over-week change | `wow_*` columns (latest 7d minus prior 7d) |
| uses a surface / product area | `integrations.surface` (e.g. 'Copilot CLI', 'Code review', 'Coding agent') or `integrations.integration` |
| uses a model | `models.model` |
| trend over weeks | `weekly` (`week_ago` 0 = latest 7 days) |
| what changed | `changes` table (same feed as the dashboard) |
| history across days | `snapshots` (grows by one row per account per daily refresh) |
| usage not linked / on the wrong or unowned Salesforce account / needs merging | `accounts.usage_unlinked = 1` and the `linked_usage` table (usage on unowned look-alike accounts, e.g. auto-created from a GitHub enterprise slug; **not** in account totals) |
| billed on one account but usage on another owned account | `accounts.usage_on_sibling` (JSON list of owned look-alike accounts carrying the usage; such accounts are not counted as inactive) |

Unless the user asks otherwise, scope questions about "accounts" to consuming accounts (`consuming = 1`). Say that you did.

## Tips

- Spend (`gross_*`, `ubb_*`) is **usage-side list value**, not invoiced revenue. Say so if the user treats it as revenue.
- Missing q16/q17 account values are unknown, never zero. Check `missing_sources` and source dates; weekly/monthly sources can be carried forward.
- Exclude `ws_estimate_source = 'rejected_as_implausible'` from seat/SOM estimates. Whitespace uses potential minus assigned (missing assigned means zero), clamped at zero. Never infer GHE/GHAS opportunity from these Copilot views.
- Show money as `$1,234`, percentages as `45%`, and sort results meaningfully.
- Offer the dashboard link at the end: `~/CopilotConsumptionDashboard/dashboard.html`.
- If `ask.py` says there's no data, tell the user to run the daily refresh (`copilot-consumption-dashboard/REFRESH.md`).
