# Customer Pulse canvas

The GitHub Copilot app canvas for [Customer Pulse](../README.md). It shows the dashboard built by `pulse.py build` and updates itself when a refresh writes a new snapshot.

## Install

In the GitHub Copilot app, ask Copilot to install the extension from this folder's GitHub URL, for example:

> Install the canvas extension from https://github.com/TheRajeev08/Pet-Projects-rajeev/tree/main/customer-pulse/canvas

Then open the **Customer Pulse** canvas. It reads `~/CustomerPulse/model.json` (or `$CP_HOME/model.json`), so run a refresh first if the canvas says there is no dashboard yet.

## Using it

- **Navigation:** Portfolio, Products, Work queue and Data groups replace the horizontal tab strip. At narrow widths, Menu exposes the same destinations with keyboard focus trapping and Escape dismissal. Old hashes remain valid, including `#/ghas` and `#/account360/activity/account/<id>`. Account sections share the navigation rather than adding another sidebar.
- **Overview:** GHE, Security and Copilot lead the page, with a Copilot AIU trend beside Copilot. The chart shows AI units consumed in 13 seven-day windows ending at the Copilot usage source cutoff (q07), summed only for reporting accounts in the owned book. Expand it for exact weekly readings and account coverage; missing readings are not zero. Book KPIs, product contribution, closed-month billing, renewal buckets and existing actions/signals follow. AI units are a usage quantity, not spend, invoices or ARR.
- **Consumption and product pages:** select a product for its 13-month **metered consumption billed** trend and its customer billing mix. The trend uses consumption invoices only. Volume license billing is not in that feed, so the trend is not total product billing and should not be read against ARR. GHAS keeps Code Security, Secret Protection, Code Quality and the legacy bundle distinct, and a bundle is never allocated to its components. Counts are distinct owned accounts with positive billing-model evidence, each shown with its own as-of date:
  - **Metered:** a positive consumption invoice for that SKU in the last closed month, whatever the billing channel (`billing_source` is a channel such as Zuora or azure, not the model). Positive `<sku>_metered_mrr` or a GHE metered/mixed contract model with metered users also count.
  - **Volume:** positive `<sku>_license_mrr` (GHE, the GHAS bundle, Code Security, Secret Protection), the GHE volume/mixed contract model, or a GHAS `Volume` licensing model with contracted seats or ARR. MRR is dated by its own month (the collector reads the latest MRR month, usually the current month), not the last closed month. Contract and licensing models are dated by their snapshots. GHE volume rests on family-level GHE licensing that may include GHE Server, so the GHE Cloud volume count and table cell carry a visible **GHE licensing (family-level)** qualifier.
  - **Both** counts once. Missing, zero or credit-only evidence never makes a paying customer, and there is no Unclassified bucket.
  - Copilot, AI credits, Actions, storage, Packages, Git LFS, Codespaces and other products with no volume source show Volume and Both as **Not available**, not 0. The Copilot page separately counts accounts with **Copilot MRR but no Copilot invoice in 13 months**. These are not treated as volume. The invoice feed must have been collected (`ok` or `no_rows`); stale, partial, failed or missing invoice feeds do not qualify.
- **Seller screens:** Top 50 separates rank membership from column sorting and keeps product detail out of ordinary rows. Optional columns expose open verified-SE counts, stack availability and product footprint. Accounts searches the entire owned book. Returning from an account restores the originating list's search, rank, sort, scroll and focus.
- **Account 360 › Summary** shows Copilot seats under contract, assigned seats, active and engaged developers (28 days), weekly active developers with their change against the previous week, and health. Pulse readings take precedence; dated dashboard readings fill gaps for the same Salesforce ID. **View Copilot metrics** adds 13 weeks of AI units and gross usage value, 28-day metered surface and model mix, and code review and coding agent outcomes/opportunities. Enterprise Impact (PRs merged) and feature policies are listed per confirmed warehouse-linked slug, not summed or inferred from similar account names. Each section labels its own cutoff or collection date, missing coverage and carried-forward dashboard sources. **Open in Adoption desk** is available only when the dashboard contains the selected ID. No additional collection is required.
- **Contacts (POCs):** every stable-ID Salesforce Contact is included, even without email or engagement. Shared email addresses do not merge contact identities. CRM activity and exact-email-matched email/Gong interaction are separate attributes. Retained contacts remain inspectable, with their source state visible. Proofs-of-concept displays and their stack fallback are removed; historical raw records are untouched.
- **Tech stack:** only versioned SE issue/comment and Gong evidence is shown. Explicit reported use, evaluation, plans, historical use, mentions and conflicts are distinct. An exact quote is not independent verification of today's deployment. Quotes, source links/dates, retained versions and incomplete collection remain inspectable. Activity can include non-SE account-linked issues; these are not automatically counted as SE requests or treated as stack evidence.
- **Drill down:** click an account name anywhere (a table, an action or a seat change) to open the account panel. Product cards on the Overview open their tab, and table headers sort.
- **Seat changes:** Overview, product tabs, account panels and `get_summary` use explicit **increased by** or **decreased by** wording with from/to values. Contract changes say **seats under contract**. Saved snapshots gain this wording without a data refresh; reporting interruptions/resumptions, migrations, trends, reverted labels and reporting-gap dates retain their distinct meanings.
- **Actions:** mark them Done or Dismiss them; Undo and Reopen bring them back. Choices are saved in `~/CustomerPulse/state/actioned.json` and never sent anywhere.
- **Signals:** filter Gong, web and ZoomInfo evidence by priority, source, account search and Open/Done/Dismissed status. Each item shows evidence, competitor/theme chips, a play and reason, and its verification status. Done, Dismiss, Undo and Reopen use the same local action-state store. Account panels include their signals; Overview and badges highlight only new, verified, High-priority open signals. Unconnected signal sources show **Not connected**.
- **Refresh:** asks Copilot to run your daily automation now, using `workflow_id` from `~/CustomerPulse/config.json`. The canvas never runs queries itself and shows the new data as soon as the automation rebuilds it.
- **Data freshness:** a notice appears when a source wasn't refreshed today or a partial load was held back. The Data tab has the details.
- **Copilot:** `#/copilot` opens the native **Portfolio**: license and adoption KPIs, every Copilot customer in a searchable/sortable table, filtered totals, actions, seat changes, health, usage funnel and whitespace. Filter by attention, unassigned licenses, engagement under 50%, or renewal within 90 days. Rows support Enter/Space and names open the Pulse drawer. The one **Copilot views** control also opens `#/copilot/desk`, `/accounts`, `/trends`, `/changes` and `/notes`. `#/copilot/seats` redirects to Portfolio (preserving account/search). `#/copilot/account/<id>` opens the Pulse drawer; `#/copilot/desk/account/<id>` opens Adoption desk detail. **Open full screen** retains the standalone dashboard.
- **License definitions:** Licenses are contracted Copilot Business + Enterprise seats from the dated Pulse snapshot, not assigned seats or metered users. Pulse wins if the dashboard disagrees; **Sources differ** shows both dated readings. Assigned is the health feed's assigned users. Active/engaged devs have at least one active/engaged day in the last 28 days, respectively. Percentages use licenses, never assigned seats. Zero licenses has no defined utilization percentage. The installed IDE count remains partial and is not a funnel step. Missing native values show **No data**; each partial total is labeled with its own reporting coverage. The drawer retains at-risk users, GRR and billed LTM.
- **Reconciliation-only accounts:** If the dashboard still reports licenses for a Pulse account with no current seats, that account remains visible with a label and both readings instead of silently dropping the mismatch. It adds no licenses to the Pulse total; reporting denominators in the table/KPIs include these additional rows. The native health mix/funnel and `get_summary` still describe current Pulse Copilot customers.
- **Narrow Portfolio layouts:** Attention priority, action count and renewal details fold into the Customer cell when their standalone columns no longer fit, including stacked rows. Priority and action count stay together; renewal dates and countdowns wrap only between complete units. Business and Enterprise plan counts stack on separate lines. The hidden copy is excluded from both the visual layout and accessibility tree.
- **Adoption desk sources:** The funnel strip labels its readings as dashboard-source figures; Portfolio continues to use Pulse's dated license/assigned readings. Source cutoffs can differ.
- **Embedded fact strips:** Three-metric strips fill three equal columns, switching directly to a single column at 540px within the embedded page. Narrow five-metric strips use two columns with a full-width final cell. Adjacent strips retain a small gap.
- **Embedded deep dives:** `copilot/?embed=1` hides the inner header, tabs, snapshot/caveat banners and footer. Standalone chrome stays available without `embed=1`. Origin- and window-validated messages mirror Pulse's resolved theme tokens and resize the iframe to its document; embedded tables have no inner scroll. Embedded **Open Impact** controls navigate to Pulse's top-level Impact tab. Inner navigation updates the parent route without discarding current filters.
- **Impact:** `#/impact` embeds the same Copilot dashboard's Impact view (`copilot/?embed=1#/impact`) in its own frame, so it always opens on Impact whatever the Copilot tab last showed. It shows the enterprise Impact data in the Copilot snapshot, and says so when none was collected.
- **Models used:** Impact overview and enterprise detail show enterprise-grain q18 model usage, including enterprises without ROI data. Latest/prior 7-day and latest 28-day gross usage value (USD before pool deduction), AI units and 28-day value shares are metered/UBB readings, not invoice totals or every interaction. Blank/unresolved models remain unattributed and are included in totals; no unique-user counts are inferred. Coverage, source cutoff and enterprise collection time distinguish missing, observed zero, carried and stale data (cutoff older than three days). Portfolio totals only combine a common cutoff. Monthly adoption model counts and account-level model data are unchanged. Model tables stack labeled values at narrow widths in both embedded and standalone views.
- **Code review & cloud agent usage:** Impact overview and enterprise detail use the exact q14 `copilot-pr-reviews` and `copilot-developer` integrations; account detail uses q08. CLI, app and third-party agents are separate. Usage is metered UBB, not outcomes or all interactions. Enterprise user signals are peak daily model-slice counts; account signals are peak daily sums across model slices and can double-count people. Neither is a deduplicated period-unique count, and signals are not summed across entities. An enabled policy and an explicitly observed zero 28-day usage signal are required for an idle signal. Missing rows, fields or policies remain unknown. New builds retain q08/q14 metric completeness and q14's cutoff; legacy normalized zeros cannot be verified and are shown as unknown. Seven-day AI units and feature-specific PR/review outcomes are unavailable in these feeds; overall Impact throughput and ROI are not attributed to either feature.
- **Code review, coding agent & AI units by feature:** These come from the warehouse tables behind the Copilot AIU, CCR and CCA reports (q19–q22). Shared rendering lives in `copilot/code-impact.mjs` and `.css`. The sections appear in:
  - Pulse **Copilot** (`#/copilot`): book-level view;
  - the embedded and standalone **Impact** overview and enterprise detail;
  - Copilot account detail;
  - **Account 360 › Copilot** and **Products & consumption**;
  - the account drawer, as a compact summary that links to Account 360.

  They cover:
  - **CCR:** coverage (CCR-reviewed ÷ reviewed PRs), user penetration, adoption score against the industry benchmark, and CCR vs general PR lead time.
  - **CCA:** PR share, repo penetration, active-day frequency, adoption stage, and EPI lift by complexity.
  - **AI units by feature:** a 13-week trend per feature.
  - **CCA opportunities:** expansion and new-logo readiness lists.

  Account sections appear for Copilot customers. They also appear for any account the data applies to: observed or withheld CCR, observed CCA, AI units by feature, or an opportunity row carrying its Salesforce ID. Opportunity rows are used only if q21 was collected or carried.

  Rules for the figures:
  - Rates are recomputed from summed counts, never averaged across owners. The current CCR month is month to date and drawn as a hollow point.
  - Withheld, carried, missing and no-rows sources are labelled. Missing data is never shown as zero.
  - AI units are a usage quantity, not usage value or a billable amount.
  - Lead time and EPI are observational, not caused by Copilot.
  - There are no Power BI deep links.
  - Charts keep text and point sizes fixed at any width. Paired charts and tables stack in narrow cards. if a frame gets anything other than the dashboard page (for example an older canvas server answering 404 after an update, or an unreachable server), the tab shows a short explanation with **Try again** instead of the raw error. Closing and reopening Customer Pulse, or starting a new Copilot session, starts the updated server. Unknown page navigations get a small HTML 404 page; API callers still get JSON.
- **Copilot data:** `copilot_home` in Pulse config selects its model directory (default `~/CopilotConsumptionDashboard`; the model's `copilot.model_path` is a fallback). GET `/api/copilot-licenses` returns compact Pulse-authoritative rows, totals, per-metric coverage and reconciliation flags; it joins enterprise slugs only through unique confirmed/single matches. A missing or invalid Copilot model leaves Pulse figures available with an explicit reconciliation note. Portfolio does not download the full Copilot model. `/copilot/api/model`, `/copilot/api/status` and `/copilot/events` power deep dives; `/copilot/api/refresh` shares Pulse's coordinator and write guards. `/copilot/classic` retains offline Ask and a relative Back link. Only this generated classic route allows inline scripts in its CSP.

Copilot can also drive the canvas through three actions:

| Action | Does |
| --- | --- |
| `get_summary` | Returns today's KPIs, open actions, recent seat changes, source freshness, signal totals/open priority counts and top open signals with evidence and verification. Adds `products.copilot.licenses_total`, `active_devs_28d`, and `engaged_devs_28d`; all existing fields, including the numeric `signals` count, are unchanged. |
| `request_refresh` | Same as the Refresh button. |
| `set_action_state` | Marks an action or signal (`sig:` id) `done` or `dismissed`, or reopens it with `null`. |

Opening the canvas with `{ "view": "copilot", "account": "<name or id>" }` jumps straight to a tab and account.

## How it is built

| File | Role |
| --- | --- |
| `extension.mjs` | Registers the canvas and its actions with the Copilot SDK. Never write to stdout here; it carries the SDK's JSON-RPC. |
| `server.mjs` | Local HTTP server for the panel: serves the page, the model, action states, refresh requests and live updates. |
| `model.mjs` | Pure helpers shared by the server and the page: routing, summaries, account lookup. |
| `views.mjs` | Renders every view and the account panel as escaped HTML strings. |
| `seller-data.mjs`, `seller-views.mjs` | Null-preserving seller analytics, ranking, typed money, feed coverage and Account 360 views. |
| `app.js` | Browser state: routing, sorting, search, focus, drawer, toasts and live updates. |
| `index.html`, `style.css` | Page shell and styles, with light and dark themes that follow the app. |

The server listens on `127.0.0.1` on a random port. It serves a fixed list of files with a strict Content Security Policy, and every write request must come from the panel's own origin with the per-launch token injected into the page. It checks the model files every few seconds and pushes a server-sent event when they change, so the page reloads the data without losing your place.

## Develop

Use the checked-in synthetic fixture generator with an explicit new or empty
temporary directory. Nonempty and symbolic-link destinations are refused before
the first write; existing dashboard files are never overwritten:

```bash
node customer-pulse/canvas/tests/workspace-preview.mjs /absolute/path/to/synthetic-preview
```

Run the tests from the repository root:

```bash
node --test customer-pulse/canvas/tests/
node --test customer-pulse/canvas/copilot/tests/
```

The printed localhost URL serves 64 synthetic accounts, including missing attributes,
shared-email contacts and source-linked stack examples. Live refresh is disabled.
Both Pulse and Copilot model roots and classic HTML are pinned beneath the explicit
directory. Never omit the Copilot pin: otherwise the server can fall back to the real
`~/CopilotConsumptionDashboard` even when Pulse itself is synthetic.

Use synthetic data only. This repository is public. Do not commit generated models
or screenshots with customer information. A live release, extension reload, source
backfill or automation change requires a separate decision.

The suites cover null-preserving totals and coverage, mismatches (including Pulse zero), unassigned/over-assigned seats, trusted slug joins, escaping, filters, routes/deep links, embed messaging/chrome, per-account adoption sums, the compact endpoint and its degradation, and additive summary compatibility. Seller regressions cover contact completeness, source-linked synthetic citations, full-book selection, ranking, renewal buckets, charts and conflicting/superseded stack evidence.

### Visual system

This is an operational seller workspace: host-provided light/dark tokens, system
fonts, blue selection, restrained labeled health colors and tabular numbers. The
196px sidebar collapses at 900px; content columns and tables also respond to their
container width. KPI cards use a muted surface; work/evidence panels remain quiet.
Ordinary Top 50 rows target 66px. At 1280x800, check at least six complete rows and
no document-level horizontal scrolling. Verify 390/768/1024/1280/1440px layouts,
keyboard account switching/return, reduced motion, light/dark and zoom reflow.

# Salesforce recommendations

The Recommendations view reads the separately collected Copilot model's Salesforce
Account Recommender feed. It is display-only, distinct from Pulse signals, and shows
the source timestamp and any possible truncation. Impact enterprise detail only
attributes Copilot recommendations using a confirmed warehouse Salesforce mapping.
