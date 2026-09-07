"""Bake PAF key actions into a local file the deck generator can use offline.

The "how do I win" slides are the ones leadership pushes hardest on, so the play
guidance behind them has to be real GitHub adoption guidance rather than invented
motion language. That guidance lives in github/product-adoption-frameworks.

Fetching it at deck time would mean the deck could not be generated without network
and GitHub auth, and would let content drift between runs. So this runs at BUILD time,
writes `paf.json` next to the skill, and the deck generator only reads that file.

Run when PAF guidance should be refreshed:

    python3 build_paf.py [outputPath]

Requires `gh` to be authenticated with access to github/product-adoption-frameworks.
"""

import json
import os
import re
import subprocess
import sys

REPO = "github/product-adoption-frameworks"

# Plays map to products, and each play gets a "land" sequence for accounts with no
# footprint and an "expand" sequence for accounts already using the product. Ordering
# inside each list is the order a seller should actually run them.
PLAYS = {
    "Innovate": {
        "focus": "Agentic engineering with GitHub Copilot",
        "land": [
            "define-measurable-goals",
            "define-rollout-plan",
            "conduct-initial-setup",
            "kick-off-champions-program",
            "define-copilot-budget-plan",
        ],
        "expand": [
            "launch-day",
            "understand-prompting",
            "run-adoption-events",
            "build-and-monitor-success-metrics",
            "automate-simple-tasks-with-copilot-agents",
            "accelerate-pull-request-velocity-quality-with-copilot-code-review",
        ],
    },
    "Trust": {
        "focus": "Securing the software supply chain with GitHub Advanced Security",
        "land": [
            "run-the-code-security-risk-assessment",
            "ghas-orientation",
            "determine-an-adoption-strategy",
            "define-a-code-scanning-default-configuration",
            "rollout-default-scanning-on-identified-reposorganizations",
            "review-alerts",
        ],
        "expand": [
            "implement-pr-required-checks",
            "launch-security-campaigns-to-remediate-security-debt",
            "enable-codeql-advanced-setup",
            "create-a-secret-protection-configuration-and-apply-it-to-repositories",
        ],
    },
    "Scale": {
        "focus": "Platform consolidation onto GitHub Enterprise and Actions",
        "land": [
            "define-enterprise-success-metrics-and-goals",
            "enterprise-account-setup",
            "configure-identity-and-access-management",
            "design-organization-and-team-structure",
            "plan-and-execute-initial-migration",
        ],
        "expand": [
            "accelerate-scaled-migrations-to-github",
            "migrate-existing-cicd-systems-to-github-actions",
            "implement-scaled-governance",
            "define-github-actions-runner-strategy",
        ],
    },
}

RESOURCE_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*\[link\]\((.+?)\)\s*\|\s*$")


def fetch(path):
    result = subprocess.run(
        ["gh", "api", "repos/%s/contents/%s" % (REPO, path), "--jq", ".content"],
        capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        return None
    import base64
    return base64.b64decode(result.stdout).decode("utf-8", "replace")


def parse(text, action_id):
    """Pull the title, intent and supporting resources out of a key-action file."""
    lines = text.splitlines()
    title = action_id.replace("-", " ").title()
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    summary = ""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            summary = stripped
            break

    resources, in_resources = [], False
    for line in lines:
        if line.startswith("## Supporting resources"):
            in_resources = True
            continue
        if in_resources and line.startswith("## "):
            break
        if not in_resources:
            continue
        match = RESOURCE_ROW.match(line)
        if not match:
            continue
        name, kind, provider, url = match.groups()
        if name.lower() == "resource" or set(name) <= {"-", " ", ":"}:
            continue
        resources.append({"name": name, "type": kind, "provider": provider, "url": url})

    # Public docs first: they are the resources a seller can share without gating.
    resources.sort(key=lambda r: (r["type"] != "self-service", r["name"]))
    seen, unique = set(), []
    for resource in resources:
        if resource["url"] in seen:
            continue
        seen.add(resource["url"])
        unique.append(resource)

    return {
        "id": action_id,
        "title": title,
        "summary": (summary[:300] + "...") if len(summary) > 300 else summary,
        "resources": unique[:4],
    }


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "paf.json")

    plays, missing = {}, []
    for play, config in PLAYS.items():
        entry = {"focus": config["focus"], "land": [], "expand": []}
        for phase in ("land", "expand"):
            for action_id in config[phase]:
                text = fetch("knowledge/key-actions/%s.md" % action_id)
                if not text:
                    missing.append(action_id)
                    continue
                entry[phase].append(parse(text, action_id))
        plays[play] = entry

    out = {
        "source": "github/product-adoption-frameworks knowledge/key-actions",
        "note": "Baked at build time so deck generation needs no network or GitHub auth.",
        "plays": plays,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    print(json.dumps({
        "pafPath": os.path.abspath(out_path),
        "actions": {p: len(v["land"]) + len(v["expand"]) for p, v in plays.items()},
        "missing": missing,
    }))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
