# Daily refresh runbook (for the Copilot agent)

Follow these steps exactly. The session running them must have the **revenue-mcp-server** MCP connected. All paths are relative to `copilot-consumption-dashboard/`.

Config, data and output live in `~/CopilotConsumptionDashboard/`, outside the repo. **Never copy them into the repo or commit them.** They contain customer data and the repo is public. Don't modify any repository files during a refresh.

1. **Render the queries**
   ```bash
   python3 refresh.py render
   ```
   This prints 13 blocks like `=== 07 · query_kusto · database=rev_source ===` followed by the query text. If it reports missing config, stop and tell the user to create `~/CopilotConsumptionDashboard/config.json` from `config.example.json`.

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

4. **Fetch Copilot Impact per enterprise**
   ```bash
   python3 refresh.py slugs
   ```
   This lists the GitHub enterprise slugs still needing Copilot Impact data today (query 13 supplies the list, so run step 3 first). For each one call `revenue-mcp-server-get_copilot_impact` with that `slug` and `namespace: "enterprise"`. Parallel calls are fine; batches of roughly eight work well. Some enterprises legitimately return `noDataReason` of `feature_disabled` or `low_or_no_usage` — collect those results as they are, do not retry them. Then run `python3 refresh.py collect` again to save the payloads, and `python3 refresh.py slugs` to confirm none are outstanding.

5. **Build the dashboard**
   ```bash
   python3 build_dashboard.py --summary
   ```
   Any `WARNING … 1000 rows` line means a query hit the row cap. Report it, because the query needs splitting.

6. **Refresh the chat skill** (keeps the `copilot-dashboard-ask` skill in sync with this code)
   ```bash
   python3 ask.py install-skill
   ```

7. **Report back** in a few lines:
   - the headline line printed by the builder;
   - the top 5–8 changes, prioritising `bad`/`warn`;
   - how many enterprises returned Copilot Impact data;
   - the path `~/CopilotConsumptionDashboard/dashboard.html`;
   - any missing sources or warnings.
