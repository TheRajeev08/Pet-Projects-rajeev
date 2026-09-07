---
name: fy27-crm-enrichment
description: "Pull Salesforce activity, contacts, open pipeline, and Gong calls for the accounts in an FY27 territory plan, using the invoking teammate's own Revenue MCP credentials. Produces the raw JSON that enrich_activity.py turns into governed engagement scores. Use when enriching a territory plan, refreshing engagement data, or when asked why an account shows as Unknown."
---

# FY27 CRM Enrichment

> This skill is a phase, not an entry point. It is called by **fy27-territory-plan**, which is in
> turn driven by **fy27-h1-run** for a full build.

## Mission

Fetch CRM evidence for one seller's book and hand it to `enrich_activity.py` as raw rows. This skill
does **no arithmetic**. Every score, tier, and coverage number is computed by the script, which is
what makes two runs over the same CRM state produce identical output.

Called by the **fy27-territory-plan** skill after classification.

## Prerequisite

The `github-revenue` plugin's `revenue-mcp-server`, authenticated as the invoking teammate. If
Salesforce auth fails, do not retry in a loop: report that Revenue Copilot Salesforce
authentication is missing and continue with whatever other sources work.

## Step 1 — scope

Read `<runDir>/fy27-territory-plan.json` and collect every non-empty `accounts[].salesforceId`.
That list is the **only** permitted query scope. Never widen it, never substitute a name search,
never query an account the teammate did not upload.

Batch the IDs in chunks of **100** for the `IN` clauses below.

## Step 2 — pinned queries

Run these with `revenue-mcp-server/query_salesforce`. Do not improvise alternatives; if one fails,
record the failure and move on.

**Activity (direction and volume)** — `windowDays` defaults to 90.

`ActivityDate` is groupable but **not aggregatable**, so recency needs its own query. Run both
and put the rows from both into `taskGroups`; the transform merges them.

```sql
SELECT AccountId, TaskSubtype, CallType, Type, COUNT(Id)
FROM Task
WHERE AccountId IN (...) AND ActivityDate = LAST_N_DAYS:90
GROUP BY AccountId, TaskSubtype, CallType, Type
```

```sql
SELECT AccountId, MAX(CreatedDate)
FROM Task
WHERE AccountId IN (...) AND ActivityDate = LAST_N_DAYS:90
GROUP BY AccountId
```

`lastActivity` is therefore the last *logged* touch (`CreatedDate`), not the business
`ActivityDate`. Say so if a user asks; do not silently relabel it.

**Meetings held** — `COUNT(Id)` lands in `expr0`, `MAX(CreatedDate)` in `expr1`:

```sql
SELECT AccountId, COUNT(Id), MAX(CreatedDate)
FROM Event
WHERE AccountId IN (...) AND ActivityDate = LAST_N_DAYS:90
GROUP BY AccountId
```

**Named personas:**

```sql
SELECT AccountId, Name, Title, Email
FROM Contact
WHERE AccountId IN (...) AND Title != null
```

**Open pipeline:**

```sql
SELECT AccountId, Name, StageName, Amount, CloseDate, NextStep
FROM Opportunity
WHERE AccountId IN (...) AND IsClosed = false
```

**Gong** — `revenue-mcp-server/get_account_gong_calls` is per-account, so run it **only for the
top ~25 Sprint Focus candidates**, not the whole book. Enriching 250 accounts one call at a time is
not worth the time or the context.

## Step 3 — write the raw file

Write `<runDir>/raw-crm.json`:

```json
{
  "windowDays": 90,
  "asOf": "YYYY-MM-DD",
  "accountIds": ["001...", "..."],
  "sources": {
    "salesforce": {"status": "ok"},
    "gong": {"status": "unavailable", "error": "not authorized"}
  },
  "taskGroups":    [{"AccountId": "001...", "TaskSubtype": "Email", "CallType": null, "Type": "Email", "count": 12, "lastActivity": "2026-07-30"}],
  "eventGroups":   [{"AccountId": "001...", "count": 2, "lastActivity": "2026-07-28"}],
  "contacts":      [{"AccountId": "001...", "Name": "...", "Title": "...", "Email": "..."}],
  "opportunities": [{"AccountId": "001...", "Name": "...", "StageName": "...", "Amount": 50000, "CloseDate": "2026-12-01", "NextStep": "..."}],
  "gongCalls":     {"001...": [{"title": "...", "date": "2026-07-15", "url": "..."}]}
}
```

Aggregate SOQL returns unaliased columns as `expr0` / `expr1`. Those are accepted verbatim as
`count` / `lastActivity`, so tool output can be pasted through without renaming.

Then:

```bash
python3 ../fy27-territory-plan/scripts/enrich_activity.py "<runDir>/raw-crm.json" "<runDir>"
```

## How the script reads the data

Worth knowing so you can explain a number, but do not recompute any of it yourself:

- `CallType = 'Internal'` is dropped entirely — internal chatter is not customer engagement.
- **Inbound** means recorded customer response: `CallType = 'Inbound'`, or a `Type` beginning
  `Connected` or `Answered`. Everything else logged is outbound.
- **Meetings** are Events plus Tasks typed `Connected - Meeting Set/Confirmed/Rescheduled`.
- **Two-way** requires inbound evidence. It is never inferred from outbound volume.
- Tier is evidence-first: `Priority` = two-way *and* a meeting, `High` = either, then `Medium`/`Low`
  by score, and `Unranked` when nothing matched.

## Guardrails

- Only IDs from the uploaded file. The script rejects anything else outright.
- A source that fails is reported with its own `status` and `error` and stays visibly unavailable.
  Never emit a success-shaped zero for data you could not fetch.
- No match means **Unknown**, not cold. This distinction is the whole point of the coverage metric.
- Every artifact carries `generatedAt`, `accountCount`, `matchedCount`, and per-source status.
- Enrichment output stays in the teammate's run directory. It is never committed or shared.
