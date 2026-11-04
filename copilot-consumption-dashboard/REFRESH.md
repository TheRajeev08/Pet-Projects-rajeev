# Daily refresh runbook (for the Copilot agent)

Follow these steps exactly. The session running them must have the **revenue-mcp-server** MCP connected. All paths are relative to `copilot-consumption-dashboard/`.

Config, data and output live in `~/CopilotConsumptionDashboard/`, outside the repo. **Never copy them into the repo or commit them.** They contain customer data and the repo is public. Don't modify any repository files during a refresh.

## Extraction failures and data-only repairs

- Query 17 uses `rev_source.salesforce_parent_account_monthly_tam_som_fact`.
  An unresolved-table semantic error needs catalog/schema verification and a code
  repair, not repeated unchanged queries. A later access-denied retry does not
  establish that authentication caused the original failure.
- If `query_kusto` is unavailable, report the affected extraction as blocked.
  Do not substitute another dataset or relabel historical results as newly read.
- For an authorized data-only repair, render and execute the affected queries
  verbatim, collect from the same session through step 3, then build through
  step 5. Existing collected results remain available; do not fetch unrelated
  signals, reinstall the skill, or send email.
- Report the repaired query's collection timestamp and actual source date
  separately from the dashboard build time, and identify any carried-forward
  results. A new HTML timestamp alone does not demonstrate fresh extraction.
- An isolated worktree fix does not update the recurring source checkout.
  Report that integration boundary without changing automation.

## Daily refresh

1. **Render the queries**
   ```bash
   python3 refresh.py render
   ```
   This prints one block per file in `queries/` (18 today), like `=== 07 · query_kusto · database=rev_source ===`, followed by the query text. If it reports missing config, stop and tell the user to create `~/CopilotConsumptionDashboard/config.json` from `config.example.json`.

2. **Run every query verbatim**
   - `query_salesforce` blocks → `revenue-mcp-server-query_salesforce` with the SOQL exactly as printed.
   - `query_kusto` blocks → `revenue-mcp-server-query_kusto` with the `database` from the header and the query text **exactly as printed**, including the leading `// qid:NN …` comment line.
   - Do not edit, reformat, summarise or add `take` limits. The collector matches on exact text.
   - Parallel calls are fine. If a call returns "Tool … does not exist" or a transient error, retry that query once.

3. **Collect the results from this session's log**
   ```bash
   python3 refresh.py collect
   ```
   Exit code 2 means some queries weren't found (it lists the IDs). Re-run just those queries verbatim, then run `collect` again.

   `collect` reads only this session's log (from `COPILOT_AGENT_SESSION_ID`) and never falls back to other sessions. When running it outside the session that ran the queries, pass `--session <id>`.
   It accepts successful execution-pair and post-tool-hook events, preserving the
   tool timestamp and exact query match. Malformed or capped Kusto results are
   reported and not collected.

4. **Fetch Copilot Impact and feature settings per enterprise**
   ```bash
   python3 refresh.py slugs
   ```
   This lists the GitHub enterprise slugs still needing data today and, for each, whether `impact`, `settings` or both are outstanding (query 13 supplies the list, so run step 3 first). For each slug call `revenue-mcp-server-get_copilot_impact` **and** `revenue-mcp-server-get_copilot_feature_settings`, both with `namespace: "enterprise"`. Parallel calls are fine; batches of roughly eight work well. Some enterprises legitimately return `noDataReason` of `feature_disabled` or `low_or_no_usage` — collect those as they are, do not retry them. Then run `python3 refresh.py collect` again to save the payloads, and `python3 refresh.py slugs` to confirm none are outstanding.

   Feature settings are what let the dashboard tell *"CLI is switched off"* apart from *"CLI is on and nobody uses it"*, so skipping them leaves the surface gaps blank rather than wrong.

5. **Build the dashboard**
   ```bash
   python3 build_dashboard.py --summary
   ```
   Any `WARNING … 1000 rows` line means a query hit the row cap. Report it, because the query needs splitting.

   Query **18** supplies enterprise model usage for Impact (separate from account-grain query 11).
   It packs stamp/integration-model slices into one row per owned enterprise and reports a shared
   source cutoff for latest 7d, prior 7d and latest 28d. The builder rejects duplicate dimension
   keys, ambiguous enterprise identity, incomplete arrays and non-reconciling totals. A rejection
   is an extraction/data-integrity failure, not zero usage; do not remove the guards or shorten
   the model list to make a build succeed. Blank model names remain unattributed allocation.
   Missing q18 stays unknown; carried q18 retains its original collection time and source cutoff.
   The panels label source cutoffs older than three days as stale, independently of build time.

6. **Refresh the chat skill** (keeps the `copilot-dashboard-ask` skill in sync with this code)
   ```bash
   python3 ask.py install-skill
   ```

7. **Report back** in a few lines:
   - the headline line printed by the builder;
   - the top 5–8 changes, prioritising `bad`/`warn`;
   - how many enterprises returned Copilot Impact data and feature settings;
   - the path `~/CopilotConsumptionDashboard/dashboard.html`;
   - any missing sources or warnings.
