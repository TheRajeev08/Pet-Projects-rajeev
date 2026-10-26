# Customer Pulse daily refresh runbook (for the Copilot agent)

Follow these steps in order. The session needs the **revenue-mcp-server** and **workiq** MCP servers. Paths are relative to the repository root.

Config, data and output live in `~/CustomerPulse/`, outside the repo. **Never copy them into the repo or commit them.** They contain customer data and the repo is public. Don't modify repository files during a refresh.

1. **Start the run**
   ```bash
   python3 customer-pulse/pulse.py render --new-run
   ```
   This prints JSON with a `run_id` and a `queries` list giving each query's `qid`, `tool`, `database` and `file`. If it reports missing config, stop and tell the user to run `init-config` (see `customer-pulse/README.md`).

2. **Run the Kusto queries verbatim**
   - For every query whose `tool` is `query_kusto` (p02–p06), read its `file` and call `revenue-mcp-server-query_kusto`:
     - set `database` to the query's `database`
     - set `query` to the file's full text, **exactly as written**, including the first `// qid:pNN run:<id> db:<db> — …` line
   - Don't edit, reformat or summarise a query, and don't add `take` limits. The collector matches on exact text and the run id.
   - Parallel calls are fine. Retry a query once if it fails with a transient error or "Tool … does not exist".
   - Skip p01. Step 3 runs it through the Salesforce CLI.

3. **Collect the results**
   ```bash
   python3 customer-pulse/pulse.py collect
   ```
   This prints `collected`, `missing` and `errors`. Exit code 2 means something is missing:
   - **p01 missing** (the Salesforce CLI isn't logged in or failed): call `revenue-mcp-server-query_salesforce` with the exact text of the p01 file, then run `collect` again.
   - **"query text was changed"**: run that query again verbatim from its file, then run `collect` again.
   - **"row cap"**: don't retry. Report it, because the query needs splitting.
   - **Anything else missing**: run those queries verbatim once more, then run `collect` again.

   If a query is still missing, carry on. The build marks that feed as failed and the email says so.

4. **Build the dashboard**
   ```bash
   python3 customer-pulse/pulse.py build
   ```
   - Exit code 1 means the build failed. The previous dashboard is kept and the failure goes into the email, so continue to step 5.
   - Exit code 3 means another run holds the lock. Wait two minutes and retry once.

5. **Write the email**
   ```bash
   python3 customer-pulse/pulse.py digest
   ```
   This prints the email's `file`, `subject`, `to` and `send`. If `send` is `false`, today's email has already gone out and nothing new has appeared since. Skip steps 6 to 8 and go to step 9.

6. **Check the recipient.** Call `workiq-fetch` with `/me?$select=mail,userPrincipalName`. If `to` matches neither value (ignoring case), don't send. Report the mismatch instead.

7. **Send the email once.** Read the JSON object in `file` and call `workiq-do_action` with `actionUrl` `/me/sendMail` and `jsonBody` set to that object, unchanged. Don't resend if the result is unclear; step 8 checks it.

8. **Record the email as sent**
   ```bash
   python3 customer-pulse/pulse.py digest --mark-sent
   ```
   This finds the send in this session's log and records the items it covered, so tomorrow's email only shows what is new. If it reports that no send was found, report that. Don't use `--force` in an unattended run.

9. **Report back** in a few lines:
   - the output of `python3 customer-pulse/pulse.py status`
   - the email subject, or that no email was needed
   - the top three to five High actions or seat changes
   - anything missing, failed, stale or suspect
