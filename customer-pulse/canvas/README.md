# Customer Pulse canvas

The GitHub Copilot app canvas for [Customer Pulse](../README.md). It shows the dashboard built by `pulse.py build` and updates itself when a refresh writes a new snapshot.

## Install

In the GitHub Copilot app, ask Copilot to install the extension from this folder's GitHub URL, for example:

> Install the canvas extension from https://github.com/TheRajeev08/Pet-Projects-rajeev/tree/main/customer-pulse/canvas

Then open the **Customer Pulse** canvas. It reads `~/CustomerPulse/model.json` (or `$CP_HOME/model.json`), so run a refresh first if the canvas says there is no dashboard yet.

## Using it

- **Tabs:** Overview, Copilot, GHE, GHAS, Signals and Data. The address (for example `#/ghas/account/<id>?q=<search>`) records your tab, open account and search, so Back and Forward work.
- **Drill down:** click an account name anywhere (a table, an action or a seat change) to open the account panel. Product cards on the Overview open their tab, and table headers sort.
- **Actions:** mark them Done or Dismiss them; Undo and Reopen bring them back. Choices are saved in `~/CustomerPulse/state/actioned.json` and never sent anywhere.
- **Signals:** filter Gong, web and ZoomInfo evidence by priority, source, account search and Open/Done/Dismissed status. Each item shows evidence, competitor/theme chips, a play and reason, and its verification status. Done, Dismiss, Undo and Reopen use the same local action-state store. Account panels include their signals; Overview and badges highlight only new, verified, High-priority open signals. Unconnected signal sources show **Not connected**.
- **Refresh:** asks Copilot to run your daily automation now, using `workflow_id` from `~/CustomerPulse/config.json`. The canvas never runs queries itself and shows the new data as soon as the automation rebuilds it.
- **Data freshness:** a notice appears when a source wasn't refreshed today or a partial load was held back. The Data tab has the details.
- **Copilot:** Adoption dashboard is the default sub-view, served in a same-origin iframe at `copilot/?embed=1`. Only that query parameter hides the inner brand header; navigation stays available. `#/copilot/seats` opens Pulse's Seats & actions view. A fallback link opens `/copilot/` in its own tab, and account drawer links select an account in the embedded dashboard.
- **Copilot data:** `copilot_home` in Pulse config selects its model directory (default `~/CopilotConsumptionDashboard`; the model's `copilot.model_path` is a fallback). `/copilot/api/model`, `/copilot/api/status` and `/copilot/events` read this model; `/copilot/api/refresh` shares Pulse's coordinator and write guards. `/copilot/classic` retains offline Ask and a relative Back link. Only this generated classic route allows inline scripts in its CSP.

Copilot can also drive the canvas through three actions:

| Action | Does |
| --- | --- |
| `get_summary` | Returns today's KPIs, open actions, recent seat changes, source freshness, signal totals/open priority counts and top open signals with evidence and verification. Includes the new verified High open signal count and local decisions. |
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
