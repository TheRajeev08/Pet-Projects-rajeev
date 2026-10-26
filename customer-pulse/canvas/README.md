# Customer Pulse canvas

The GitHub Copilot app canvas for [Customer Pulse](../README.md). It shows the dashboard built by `pulse.py build` and updates itself when a refresh writes a new snapshot.

## Install

In the GitHub Copilot app, ask Copilot to install the extension from this folder's GitHub URL, for example:

> Install the canvas extension from https://github.com/TheRajeev08/Pet-Projects-rajeev/tree/main/customer-pulse/canvas

Then open the **Customer Pulse** canvas. It reads `~/CustomerPulse/model.json` (or `$CP_HOME/model.json`), so run a refresh first if the canvas says there is no dashboard yet.

## Using it

- **Tabs:** Overview, Copilot, Impact, GHE, GHAS, Signals and Data. The address (for example `#/ghas/account/<id>?q=<search>`) records your tab, open account and search, so Back and Forward work.
- **Drill down:** click an account name anywhere (a table, an action or a seat change) to open the account panel. Product cards on the Overview open their tab, and table headers sort.
- **Actions:** mark them Done or Dismiss them; Undo and Reopen bring them back. Choices are saved in `~/CustomerPulse/state/actioned.json` and never sent anywhere.
- **Signals:** filter Gong, web and ZoomInfo evidence by priority, source, account search and Open/Done/Dismissed status. Each item shows evidence, competitor/theme chips, a play and reason, and its verification status. Done, Dismiss, Undo and Reopen use the same local action-state store. Account panels include their signals; Overview and badges highlight only new, verified, High-priority open signals. Unconnected signal sources show **Not connected**.
- **Refresh:** asks Copilot to run your daily automation now, using `workflow_id` from `~/CustomerPulse/config.json`. The canvas never runs queries itself and shows the new data as soon as the automation rebuilds it.
- **Data freshness:** a notice appears when a source wasn't refreshed today or a partial load was held back. The Data tab has the details.
- **Copilot:** `#/copilot` opens the native **Portfolio**: license and adoption KPIs, every Copilot customer in a searchable/sortable table, filtered totals, actions, seat changes, health, usage funnel and whitespace. Filter by attention, unassigned licenses, engagement under 50%, or renewal within 90 days. Rows support Enter/Space and names open the Pulse drawer. The one **Copilot views** control also opens `#/copilot/desk`, `/accounts`, `/trends`, `/changes` and `/notes`. `#/copilot/seats` redirects to Portfolio (preserving account/search). `#/copilot/account/<id>` opens the Pulse drawer; `#/copilot/desk/account/<id>` opens Adoption desk detail. **Open full screen** retains the standalone dashboard.
- **License definitions:** Licenses are contracted Copilot Business + Enterprise seats from the dated Pulse snapshot, not assigned seats or metered users. Pulse wins if the dashboard disagrees; **Sources differ** shows both dated readings. Assigned is the health feed's assigned users. Active/engaged devs have at least one active/engaged day in the last 28 days, respectively. Percentages use licenses, never assigned seats. Zero licenses has no defined utilization percentage. The installed IDE count remains partial and is not a funnel step. Missing values are dashes; each partial total is labeled with its own reporting coverage. The drawer retains at-risk users, GRR and billed LTM.
- **Reconciliation-only accounts:** If the dashboard still reports licenses for a Pulse account with no current seats, that account remains visible with a label and both readings instead of silently dropping the mismatch. It adds no licenses to the Pulse total; reporting denominators in the table/KPIs include these additional rows. The native health mix/funnel and `get_summary` still describe current Pulse Copilot customers.
- **Embedded deep dives:** `copilot/?embed=1` hides the inner header, tabs, snapshot/caveat banners and footer. Standalone chrome stays available without `embed=1`. Origin- and window-validated messages mirror Pulse's resolved theme tokens and resize the iframe to its document; embedded tables have no inner scroll. Embedded **Open Impact** controls navigate to Pulse's top-level Impact tab. Inner navigation updates the parent route without discarding current filters.
- **Impact:** `#/impact` embeds the same Copilot dashboard's Impact view (`copilot/?embed=1#/impact`) in its own frame, so it always opens on Impact whatever the Copilot tab last showed. It shows the enterprise Impact data in the Copilot snapshot, and says so when none was collected.
- **Embed failures:** if a frame gets anything other than the dashboard page (for example an older canvas server answering 404 after an update, or an unreachable server), the tab shows a short explanation with **Try again** instead of the raw error. Closing and reopening Customer Pulse, or starting a new Copilot session, starts the updated server. Unknown page navigations get a small HTML 404 page; API callers still get JSON.
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
| `app.js` | Browser state: routing, sorting, search, focus, drawer, toasts and live updates. |
| `index.html`, `style.css` | Page shell and styles, with light and dark themes that follow the app. |

The server listens on `127.0.0.1` on a random port. It serves a fixed list of files with a strict Content Security Policy, and every write request must come from the panel's own origin with the per-launch token injected into the page. It checks the model files every few seconds and pushes a server-sent event when they change, so the page reloads the data without losing your place.

## Develop

Preview the page in a normal browser with any `CP_HOME` that holds a `model.json`, such as one built from the synthetic fixtures in `../tests/fixtures.py`:

```bash
CP_HOME=/path/to/demo-home node --input-type=module -e '
import { createRefreshCoordinator, pulsePaths, startCanvasServer } from "./customer-pulse/canvas/server.mjs";
const paths = pulsePaths();
const refresh = createRefreshCoordinator({ modelPath: paths.model, launch: async () => {} });
console.log((await startCanvasServer({ paths, refresh })).url);
'
```

Run the tests from the repository root:

```bash
node --test customer-pulse/canvas/tests/
node --test customer-pulse/canvas/copilot/tests/
```

Use synthetic data only. This repository is public.

The suites cover null-preserving totals and coverage, mismatches (including Pulse zero), unassigned/over-assigned seats, trusted slug joins, escaping, filters, routes/deep links, embed messaging/chrome, per-account adoption sums, the compact endpoint and its degradation, and additive summary compatibility. For a live-data preview, copy only both model files into temporary directories and set a temporary `CP_HOME/config.json` with `copilot_home` pointing to the temporary Copilot directory. Never point development write routes at the live homes or commit customer data or screenshots.
