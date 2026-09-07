# Copilot Analytics Assistant

This folder contains a standalone HTML dashboard for a three-month GitHub
Copilot adoption review. It covers active and engaged users, suggestion
acceptance, model and surface usage, CCA and CLI adoption, Copilot Impact,
pull-request review impact, benchmark comparisons, and recommended Product
Adoption Framework (PAF) actions.

## Open locally

Open `copilot-analytics-dashboard.html` directly in a modern browser:

```bash
open copilot-analytics-assistant/copilot-analytics-dashboard.html
```

No server, package installation, or build step is required. The dashboard is
self-contained, and its existing export actions remain available in the page.

## Dataset refresh

The dataset currently embedded in the HTML is for Evalueserve. Treat it as a
reusable snapshot: when generating the dashboard for another enterprise slug,
overwrite or refresh the embedded dataset and account labels for that slug
rather than appending customer data to this file.
