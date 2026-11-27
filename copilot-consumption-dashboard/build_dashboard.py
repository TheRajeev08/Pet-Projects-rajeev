#!/usr/bin/env python3
"""Normalize collected query results, compute week-over-week changes, render dashboard.html.

  python3 build_dashboard.py                 # latest data/raw/<date>/
  python3 build_dashboard.py --date 2026-09-25
  python3 build_dashboard.py --summary       # also print the headline changes (for the automation run log)

Standard library only (Python 3.9+).
"""
import argparse
import base64
import datetime as dt
import glob
import json
import math
import os
import re
import sys

from paths import ROOT, dashboard_path, data_dir
import recommendations

DATA = data_dir()
WEEKS = 13
ACTIVE_LOOKBACK_WEEKS = 13  # ~90 days

SURFACES = {
    "vscode-chat": "IDE chat", "visualstudio-chat": "IDE chat", "jetbrains-chat": "IDE chat",
    "xcode-chat": "IDE chat", "eclipse-chat": "IDE chat", "zed": "IDE chat", "copilot-language-server": "IDE chat",
    "copilot-chat": "GitHub.com chat", "copilot-mobile-ios": "Mobile", "copilot-mobile-android": "Mobile",
    "copilot-pr-reviews": "Code review",
    "copilot-developer-cli": "Copilot CLI", "copilot-4-cli": "Copilot CLI",
    "copilot-developer": "Coding agent", "copilot-developer-app": "Copilot app", "copilot-desktop": "Copilot app",
    "copilot-sdk": "SDK / integrations", "copilot-slack-integration": "SDK / integrations",
    "ghas-code-scanning-agentic": "Security agents",
    "claude-code": "Third-party agents", "claude-code-cloud": "Third-party agents",
    "codex-cloud": "Third-party agents", "opencode": "Third-party agents",
}


def surface(integration):
    return SURFACES.get(integration or "", "Other")


# ---------------------------------------------------------------- loading
def dec(v):
    """Kusto dynamic columns arrive base64-encoded JSON through the MCP server."""
    if isinstance(v, str) and v and v[0] not in "{[" and len(v) % 4 == 0:
        try:
            return json.loads(base64.b64decode(v, validate=True))
        except (ValueError, json.JSONDecodeError):
            return v
    return v


def num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def feature_metrics_known(row):
    return {key: num(row.get(key)) is not None
            for key in ("l7", "p7", "l28", "aiu28", "u7", "up7", "u28")}


def chunk(flat, cols):
    names = cols.split(",")
    flat = dec(flat) or []
    return [dict(zip(names, flat[i:i + len(names)])) for i in range(0, len(flat), len(names))]


def weekly(points, key):
    """wk-keyed dicts -> list indexed by wk (0 = latest week), None where missing."""
    out = [None] * WEEKS
    for p in points:
        wk = p.get("wk")
        if isinstance(wk, (int, float)) and 0 <= int(wk) < WEEKS:
            out[int(wk)] = p.get(key)
    return out


def prior_days(day):
    """data/raw/<date>/ folders at or before `day`, newest first."""
    base = os.path.join(DATA, "raw")
    if not os.path.isdir(base):
        return []
    days = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    return [d for d in reversed(days) if d <= day]


def load_enterprise_payloads(day):
    """Per-enterprise MCP payloads written by `refresh.py collect`, keyed by slug.

    Collection is incremental and spans many days, and these payloads describe month- and
    week-grained windows that do not move daily. A day whose per-enterprise calls did not run
    therefore carries the most recent earlier payload forward instead of dropping the enterprise
    entirely. Each payload keeps its own collected_from, so the UI still reports when it was read.
    """
    out = {}
    for kind in ("impact", "settings"):
        bucket = {}
        for d in prior_days(day):
            for path in sorted(glob.glob(os.path.join(DATA, "raw", d, kind, "*.json"))):
                try:
                    with open(path) as f:
                        payload = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                slug = payload.get("slug")
                if slug and slug not in bucket:
                    bucket[slug] = payload
        out[kind] = bucket
    return out


def suggest_account(slug, accounts):
    """Fallback slug -> Salesforce account match by name, used only where the warehouse supplies no
    mapping. Never asserted as confirmed: roughly half of all slugs are ambiguous or unmatched."""
    key = name_key(slug)
    hits = [a for a in accounts if names_match(key, name_key(a["name"]))]
    return {"confidence": "none" if not hits else "single" if len(hits) == 1 else "ambiguous",
            "candidates": [{"id": a["id"], "name": a["name"], "consuming": a["consuming"]} for a in hits[:6]]}


# Copilot feature policies that gate a usage surface, mapped onto the SURFACES taxonomy.
# A surface is only described as "unused" when a policy confirms it is switched on.
SURFACE_POLICIES = {
    "Copilot CLI": ["cli"],
    "Code review": ["code_review", "automatic_code_review"],
    "Coding agent": ["swe_agent", "copilot_cloud_agent"],
    "Copilot app": ["copilot_app", "desktop"],
    "GitHub.com chat": ["dotcom_chat", "github_enterprise_feature_group"],
    "IDE chat": ["chat_enabled", "agent_mode"],
    "Mobile": ["mobile_chat"],
}

# Policies worth surfacing even though they carry no matching UBB integration.
EXTRA_POLICIES = ("mcp", "spaces", "pr_summarizations", "cca_agentic_github_apps",
                  "model_native_search", "client_byok", "memory", "insights")


def policy_state(values):
    """Strongest signal wins: an explicit enable or disable beats an unset policy."""
    if "enabled" in values or "allowed" in values:
        return "enabled"
    if "disabled" in values or "blocked" in values:
        return "disabled"
    if "unconfigured" in values:
        return "unconfigured"
    return "no_policy" if values else "unknown"


def build_settings(payload):
    rows = (payload.get("result") or {}).get("settings") or []
    values = {r.get("identifier"): r.get("value") for r in rows if r.get("identifier")}
    labels = {r.get("identifier"): r.get("displayName") for r in rows if r.get("identifier")}
    return {
        "surfaces": {name: policy_state([values[p] for p in ids if p in values])
                     for name, ids in SURFACE_POLICIES.items()},
        "policies": [{"id": p, "label": labels.get(p, p), "value": values[p]}
                     for p in EXTRA_POLICIES if p in values],
        "count": len(rows),
        "collected_from": payload.get("collected_from"),
    }


MODEL_METRICS = ("l7", "p7", "l28", "aiu7", "aiup7", "aiu28")


def build_model_usage(rows, owned, source):
    """Validate packed enterprise/stamp/key slices before publishing any model totals."""
    out = {}
    identities = {}
    for row in owned:
        eid = str(row.get("enterprise_id") or "")
        slug = row.get("slug")
        if eid in identities and identities[eid] != slug:
            raise ValueError("q18: owned enterprise ID maps to multiple slugs")
        identities[eid] = slug
    if len(rows) >= 1000:
        raise ValueError("q18: enterprise result reached the 1000-row cap")
    for row in rows:
        eid, slug = str(row.get("enterprise_id") or ""), row.get("slug")
        if not eid or not slug or row.get("identity_count") != 1:
            raise ValueError("q18: ambiguous or missing enterprise identity")
        if row.get("dimension_duplicate_keys") != 0:
            raise ValueError("q18: duplicate or unverified integration-model dimension keys")
        if eid in out:
            raise ValueError("q18: duplicate enterprise result")
        # A carried source may include an enterprise no longer owned, but never a guessed join.
        if eid not in identities:
            continue
        if identities[eid] != slug:
            raise ValueError("q18: enterprise ID/slug does not match the owned source")
        slices = dec(row.get("models"))
        if isinstance(slices, str):
            try:
                slices = json.loads(slices)
            except json.JSONDecodeError as exc:
                raise ValueError("q18: invalid model array") from exc
        if not isinstance(slices, list) or row.get("slice_count") != len(slices):
            raise ValueError("q18: incomplete model array")
        cutoff = row.get("asof")
        if cutoff:
            try:
                dt.date.fromisoformat(cutoff[:10])
            except (TypeError, ValueError) as exc:
                raise ValueError("q18: invalid source cutoff") from exc
        elif slices:
            raise ValueError("q18: usage without a source cutoff")
        seen, normalized = set(), []
        for item in slices:
            if not isinstance(item, dict):
                raise ValueError("q18: invalid model slice")
            stamp, key = item.get("stamp"), item.get("key")
            if not isinstance(stamp, str) or not isinstance(key, str):
                raise ValueError("q18: missing stamp/key identity")
            identity = (stamp, key)
            if identity in seen:
                raise ValueError("q18: duplicate enterprise/stamp/model-key slice")
            seen.add(identity)
            values = {k: num(item.get(k)) for k in MODEL_METRICS}
            if any(v is None for v in values.values()):
                raise ValueError("q18: missing or non-finite additive metric")
            model, integration = item.get("model"), item.get("integration")
            if any(v is not None and not isinstance(v, str) for v in (model, integration)):
                raise ValueError("q18: invalid dimension labels")
            normalized.append({
                "stamp": stamp, "key": key, "model": (model or "").strip() or None,
                "integration": (integration or "").strip() or None,
                "last_date": item.get("last_date"), **values,
            })
        for metric, total_key in (("l28", "gross28_total"), ("aiu28", "aiu28_total")):
            expected = num(row.get(total_key))
            if expected is None or not math.isclose(sum(s[metric] for s in normalized), expected,
                                                     rel_tol=1e-9, abs_tol=1e-6):
                raise ValueError(f"q18: {metric} does not reconcile to source total")
        out[eid] = {
            "asof": cutoff, "collected_from": source.get("collected_from"),
            "carried_from": source.get("carried_from"),
            "status": "observed" if normalized else "no_usage" if cutoff else "unavailable",
            "slices": sorted(normalized, key=lambda s: (s["stamp"], s["key"])),
        }
    return out


# ---------------------------------------------------------------- CCR / CCA / AIU by feature (queries 19-22)
CCR_COLS = ("month,prs,reviewed,ccr_prs,ccr_users,coverage,penetration,rate,benchmark,vs_industry,variants,orgs,"
            "ccr_avg,ccr_p50,ccr_p90,gen_avg,gen_p50,gen_p90,ccr_avg_label,ccr_p50_label,ccr_p90_label,"
            "gen_avg_label,gen_p50_label,gen_p90_label,lead_labels,lead_conflict")
CCA_COLS = "month,cca_prs,prs,cca_repos,repos,cca_days,days,share,penetration,frequency,rate,stage,dup"
CCA_TIER_COLS = ("tier,cca_prs,non_cca_prs,cca_epi_avg,non_cca_epi_avg,cca_epi_p50,non_cca_epi_p50,cca_epi_p90,"
                 "non_cca_epi_p90,lift_avg,lift_p50,lift_p90,comparison,has_cca,has_non_cca,summary,interpretation,action")
FEATURE_COLS = "wk,integration,gross,aiu"
LEAD_STATS = ("avg", "p50", "p90")
TIER_ORDER = {"low": 0, "medium": 1, "high": 2}
CCA_BANDS = ("S", "A", "B", "C", "D")
MONTHS = 13


def text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def truthy(v):
    return v is True or v == 1 or (isinstance(v, str) and v.strip().lower() in ("true", "1"))


def month_of(v):
    v = text(v)
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        return None


def count_ratio(part, whole):
    part, whole = num(part), num(whole)
    return part / whole if part is not None and whole else None


def build_ccr(rows19, run_day):
    """Per enterprise ID: 13 months of CCR adoption and lead time (q19).

    Enterprise totals repeat on every organization row upstream; the query keeps one value per
    enterprise-month and nothing here sums across organizations. The current month is month to
    date. Percentiles, penetration and the adoption score are published as reported, never averaged."""
    out = {}
    for r in rows19:
        eid = str(r.get("enterprise_id") or "").strip()
        if not eid:
            continue
        if eid in out:
            out[eid] = {"status": "withheld", "reason": "The CCR source returned this enterprise twice"}
            continue
        run = month_of(r.get("asof")) or run_day
        series, flags = {}, set()
        for p in chunk(r.get("s"), r.get("cols") or CCR_COLS):
            month = month_of(p.get("month"))
            if not month:
                continue
            if month in series:
                flags.add("duplicate_month")
                continue
            if (num(p.get("variants")) or 0) > 1:
                flags.add("org_rows_disagree")
            if truthy(p.get("lead_conflict")):
                flags.add("lead_time_rows_disagree")
            series[month] = {
                "month": month, "mtd": month[:7] >= run[:7],
                **{k: num(p.get(k)) for k in ("prs", "reviewed", "ccr_prs", "ccr_users", "coverage",
                                              "penetration", "rate", "benchmark", "orgs")},
                "vs_industry": text(p.get("vs_industry")),
                "lead_days": {side: {s: num(p.get(f"{key}_{s}")) for s in LEAD_STATS}
                              for side, key in (("ccr", "ccr"), ("general", "gen"))},
                "lead_labels": {side: {s: text(p.get(f"{key}_{s}_label")) for s in LEAD_STATS}
                                for side, key in (("ccr", "ccr"), ("general", "gen"))},
                "label_basis": [x for x in (text(p.get("lead_labels")) or "").split("|") if x.strip()],
                "variants": num(p.get("variants")),
                "lead_conflict": truthy(p.get("lead_conflict")),
            }
        points = sorted(series.values(), key=lambda p: p["month"])[-MONTHS:]
        complete = [p for p in points if not p["mtd"]]
        out[eid] = {
            "status": "observed" if points else "no_rows",
            "enterprise_id": eid,
            "sf_ids": sorted({str(x).strip() for x in (dec(r.get("sf_ids")) or []) if str(x).strip()}),
            "industry": text(r.get("industry")),
            "run_date": run,
            "latest_month": complete[-1]["month"] if complete else None,
            "series": points,
            "flags": sorted(flags),
        }
    return out


def rollup_ccr(payloads):
    """Monthly CCR totals across enterprises. Only count-based ratios are recomputed."""
    months = {}
    for c in payloads:
        for p in c.get("series") or []:
            m = months.setdefault(p["month"], {"month": p["month"], "mtd": False, "enterprises": 0,
                                               "prs": None, "reviewed": None, "ccr_prs": None,
                                               "_cov": [0.0, 0.0]})
            m["mtd"] = m["mtd"] or p["mtd"]
            m["enterprises"] += 1
            for k in ("prs", "reviewed", "ccr_prs"):
                if p[k] is not None:
                    m[k] = (m[k] or 0) + p[k]
            if p["ccr_prs"] is not None and p["reviewed"] is not None:
                m["_cov"][0] += p["ccr_prs"]
                m["_cov"][1] += p["reviewed"]
    out = []
    for m in sorted(months.values(), key=lambda m: m["month"])[-MONTHS:]:
        part, whole = m.pop("_cov")
        m["coverage"] = part / whole if whole else None
        out.append(m)
    return out


def ccr_summary(payloads):
    """Latest complete month per enterprise: benchmark labels and CCR vs general median lead time, as counts."""
    vs, lead = {}, {"faster": 0, "slower": 0, "same": 0, "compared": 0}
    latest = []
    for c in payloads:
        p = next((x for x in reversed(c.get("series") or []) if not x["mtd"]), None)
        if not p:
            continue
        latest.append(p["month"])
        if p["vs_industry"]:
            vs[p["vs_industry"]] = vs.get(p["vs_industry"], 0) + 1
        ccr, gen = p["lead_days"]["ccr"]["p50"], p["lead_days"]["general"]["p50"]
        if ccr is not None and gen is not None:
            lead["compared"] += 1
            lead["faster" if ccr < gen else "slower" if ccr > gen else "same"] += 1
    return {"enterprises": len(latest), "latest_month": max(latest) if latest else None,
            "vs_industry": vs, "median_lead": lead}


def build_cca(rows20):
    """Per billable owner (enterprise or organization): CCA adoption months and the latest EPI tiers (q20).

    Monthly snapshots cover completed months. EPI and lift are percent-ranked within a peer pool and
    observational; they are published per owner and never averaged."""
    owners = {}
    for r in rows20:
        oid, otype = text(str(r.get("owner_id") or "")), text(r.get("owner_type"))
        if not oid or not otype:
            continue
        key = (otype.lower(), oid)
        flags = set()
        series = {}
        for p in chunk(r.get("s"), r.get("cols") or CCA_COLS):
            month = month_of(p.get("month"))
            if not month:
                continue
            if month in series:
                flags.add("duplicate_month")
                continue
            if (num(p.get("dup")) or 0) > 1:
                flags.add("duplicate_owner_rows")
            series[month] = {"month": month,
                             **{k: num(p.get(k)) for k in ("cca_prs", "prs", "cca_repos", "repos", "cca_days",
                                                           "days", "share", "penetration", "frequency", "rate")},
                             "stage": text(p.get("stage"))}
        tiers = []
        for t in chunk(r.get("tiers"), r.get("tier_cols") or CCA_TIER_COLS):
            tier = text(t.get("tier"))
            if not tier:
                continue
            comparison = truthy(t.get("comparison"))
            tiers.append({
                "tier": tier, "cca_prs": num(t.get("cca_prs")), "non_cca_prs": num(t.get("non_cca_prs")),
                "epi": {side: {s: num(t.get(f"{side}_epi_{s}")) for s in LEAD_STATS} for side in ("cca", "non_cca")},
                "lift": {s: num(t.get(f"lift_{s}")) if comparison else None for s in LEAD_STATS},
                "comparison": comparison, "has_cca": truthy(t.get("has_cca")),
                "has_non_cca": truthy(t.get("has_non_cca")),
                "guidance": {k: text(t.get(k)) for k in ("summary", "interpretation", "action")},
            })
        tiers.sort(key=lambda t: (TIER_ORDER.get(t["tier"].lower(), 9), t["tier"]))
        points = sorted(series.values(), key=lambda p: p["month"])[-MONTHS:]
        payload = {
            "owner_id": oid, "owner_type": otype,
            "enterprise": "enterprise" in otype.lower(),
            "sf_id": text(str(r.get("sf_id") or "")),
            "status": "observed" if points or tiers else "no_rows",
            "latest_month": points[-1]["month"] if points else None,
            "series": points,
            "interpretation": text(r.get("interpretation")),
            "epi": {"month": month_of(r.get("epi_month")), "tiers": tiers} if tiers else None,
            "flags": sorted(flags),
        }
        if key in owners:
            payload = {**owners[key], "flags": sorted(set(owners[key]["flags"]) | {"duplicate_owner_rows"})}
        owners[key] = payload
    return sorted(owners.values(), key=lambda o: (o["sf_id"] or "", o["owner_type"], o["owner_id"]))


def rollup_cca(owners):
    """Monthly CCA totals across owners: PR share and repo penetration from summed counts only."""
    months = {}
    for o in owners:
        for p in o["series"]:
            m = months.setdefault(p["month"], {"month": p["month"], "owners": 0, "cca_prs": 0.0, "prs": 0.0,
                                               "cca_repos": 0.0, "repos": 0.0, "_pr": 0, "_repo": 0})
            m["owners"] += 1
            if p["cca_prs"] is not None and p["prs"] is not None:
                m["cca_prs"] += p["cca_prs"]
                m["prs"] += p["prs"]
                m["_pr"] += 1
            if p["cca_repos"] is not None and p["repos"] is not None:
                m["cca_repos"] += p["cca_repos"]
                m["repos"] += p["repos"]
                m["_repo"] += 1
    out = []
    for m in sorted(months.values(), key=lambda m: m["month"])[-MONTHS:]:
        has_pr, has_repo = m.pop("_pr"), m.pop("_repo")
        if not has_pr:
            m["cca_prs"] = m["prs"] = None
        if not has_repo:
            m["cca_repos"] = m["repos"] = None
        m["share"] = count_ratio(m["cca_prs"], m["prs"])
        m["penetration"] = count_ratio(m["cca_repos"], m["repos"])
        out.append(m)
    return out


def cca_summary(owners):
    """Latest month: stage mix and, per complexity tier, how many owners show positive or negative EPI lift."""
    latest = max((o["latest_month"] for o in owners if o["latest_month"]), default=None)
    stages, tiers = {}, {}
    for o in owners:
        p = o["series"][-1] if o["series"] and o["series"][-1]["month"] == latest else None
        if p and p["stage"]:
            stages[p["stage"]] = stages.get(p["stage"], 0) + 1
        for t in (o["epi"] or {}).get("tiers", []):
            c = tiers.setdefault(t["tier"], {"tier": t["tier"], "positive": 0, "negative": 0, "flat": 0,
                                             "no_comparison": 0})
            lift = t["lift"]["avg"]
            if lift is None:
                c["no_comparison"] += 1
            else:
                c["positive" if lift > 0 else "negative" if lift < 0 else "flat"] += 1
    return {"owners": sum(1 for o in owners if o["status"] == "observed"), "latest_month": latest,
            "stages": stages,
            "epi_tiers": sorted(tiers.values(), key=lambda t: (TIER_ORDER.get(t["tier"].lower(), 9), t["tier"]))}


def build_cca_opportunities(rows21, accounts):
    """Latest expansion and new-logo readiness rows (q21). Recommended actions are source guidance."""
    if not rows21:
        return None
    names = {a["id"]: a["name"] for a in accounts}
    expansion, new_logo = [], []
    for row in rows21:
        for item in dec(row.get("rows")) or []:
            if not isinstance(item, dict):
                continue
            d = dec(item.get("d")) or {}
            if not isinstance(d, dict):
                continue
            sf_id = text(str(item.get("sf_id") or ""))
            base = {"owner_id": text(str(item.get("owner_id") or "")), "owner_type": text(item.get("owner_type")),
                    "sf_id": sf_id, "account": names.get(sf_id), "month": month_of(item.get("month")),
                    "score": num(d.get("score"))}
            if item.get("kind") == "expansion":
                band = text(d.get("band"))
                letter = band[0].upper() if band and band[0].upper() in CCA_BANDS else None
                expansion.append({**base, "band": band, "band_letter": letter, "stage": text(d.get("stage")),
                                  **{k: num(d.get(k)) for k in ("share", "penetration", "frequency", "cca_repos",
                                                                "repos", "untapped_repos", "lift",
                                                                "cca_prs_low_medium", "speed_multiplier")},
                                  "guidance": {k: text(d.get(k)) for k in ("lift_summary", "lead_time_summary",
                                                                           "untapped_summary", "action")}})
            elif item.get("kind") == "new_logo":
                new_logo.append({**base, "repos": num(d.get("repos")), "non_cca_prs": num(d.get("non_cca_prs")),
                                 "components": {k: num(d.get(f"{k}_score")) for k in ("pr_volume", "lead_time", "repo")},
                                 "guidance": {k: text(d.get(k)) for k in ("lead_time_summary", "opportunity_summary",
                                                                          "action")}})
    order = lambda e: (-(e["score"] if e["score"] is not None else -1), e["account"] or "", e["owner_id"] or "")
    first = rows21[0]
    return {"expansion_month": month_of(first.get("expansion_month")),
            "readiness_month": month_of(first.get("readiness_month")),
            "expansion": sorted(expansion, key=order), "new_logo": sorted(new_logo, key=order)}


def build_feature_weekly(rows22):
    """Per Salesforce account: 13 weekly points of gross $ and AI units by Copilot feature (q22).

    The query keeps every non-zero week, so a missing week within an account's row is an observed zero."""
    out = {}
    for r in rows22:
        aid = r.get("id")
        if not aid:
            continue
        feats = out.setdefault(aid, {})
        for p in chunk(r.get("s"), r.get("cols") or FEATURE_COLS):
            wk = p.get("wk")
            if not isinstance(wk, (int, float)) or not 0 <= int(wk) < WEEKS:
                continue
            f = feats.setdefault(surface(p.get("integration")), {"gross": [0.0] * WEEKS, "aiu": [0.0] * WEEKS})
            f["gross"][int(wk)] += num(p.get("gross")) or 0
            f["aiu"][int(wk)] += num(p.get("aiu")) or 0
    return {aid: dict(sorted(f.items(), key=lambda kv: (-sum(kv[1]["aiu"]), kv[0]))) for aid, f in out.items()}


def build_enterprises(rows13, rows14, rows15, payloads, accounts, rows18=(), source18=None,
                      ccr=None, cca=None):
    model_usage = build_model_usage(rows18, rows13, source18 or {})
    cca_by_enterprise = {o["owner_id"]: o for o in (cca or []) if o["enterprise"]}
    impact, settings = payloads.get("impact", {}), payloads.get("settings", {})
    owned_by_id = {a["id"]: a for a in accounts}
    usage = {}
    for r in rows14:
        slug = r.get("slug")
        if not slug:
            continue
        cols = r.get("cols") or "wk,gross"
        points = chunk(r.get("s"), cols)
        usage.setdefault(slug, []).append({
            "integration": r.get("integration"), "surface": surface(r.get("integration")),
            "l7": num(r.get("l7")) or 0, "p7": num(r.get("p7")) or 0, "l28": num(r.get("l28")) or 0,
            "aiu28": num(r.get("aiu28")) or 0, "u7": r.get("u7") or 0, "up7": r.get("up7") or 0,
            "u28": r.get("u28") or 0,
            "feature_metrics_known": feature_metrics_known(r),
            "weekly": weekly(points, "gross"),
            # Collections before the AIU column was added carry no weekly AIU: unknown, not zero.
            "weekly_aiu": weekly(points, "aiu") if "aiu" in cols.split(",") else None,
        })
    adoption = {r["slug"]: r for r in rows15 if r.get("slug")}

    out = []
    for row in rows13:
        slug = row.get("slug")
        if not slug:
            continue
        payload = impact.get(slug) or {}
        data = payload.get("result") or {}
        extra = adoption.get(slug) or {}
        settings_payload = settings.get(slug)
        # The warehouse mapping is authoritative; name matching is only a labelled fallback.
        sf_id = (extra.get("sf_id") or "").strip()
        linked = owned_by_id.get(sf_id)
        if linked:
            match = {"confidence": "confirmed", "source": "warehouse",
                     "candidates": [{"id": linked["id"], "name": linked["name"],
                                     "consuming": linked["consuming"]}]}
        else:
            match = {**suggest_account(slug, accounts), "source": "name",
                     "unowned_sf_id": sf_id or None}
        eid = str(row.get("enterprise_id") or "").strip()
        ccr_row = (ccr or {}).get(eid)
        if ccr_row and ccr_row.get("sf_ids") and sf_id and sf_id not in ccr_row["sf_ids"]:
            ccr_row = {"status": "withheld", "enterprise_id": eid,
                       "reason": "The CCR source links this enterprise to a different Salesforce account"}
        out.append({
            "slug": slug, "enterprise_id": row.get("enterprise_id"),
            "plan": row.get("plan") or None, "segment": row.get("segment") or None,
            "region": row.get("region") or None, "industry": row.get("industry") or None,
            "deployment": row.get("deployment") or None,
            "match": match,
            "collected_from": payload.get("collected_from"),
            "impact": data or None,
            "has_impact": bool(data.get("dataAvailable")) and bool(data.get("impact")),
            "integrations": sorted(usage.get(slug, []), key=lambda r: -r["l28"]),
            "model_usage": model_usage.get(str(row.get("enterprise_id") or "")),
            "ccr": ccr_row if ccr is not None else None,
            "cca": cca_by_enterprise.get(eid) if cca is not None else None,
            "settings": build_settings(settings_payload) if settings_payload else None,
            "adoption": {k: extra.get(k) for k in (
                "licensed", "ubb_users", "adoption_rate", "adoption_band", "wow_ubb_users",
                "promo_util", "standard_util", "overage_risk", "utilization_band",
                "projected_entitlement_status", "insight_category", "feature_categories",
                "feature_category_count", "integration_count", "model_count", "adoption_maturity",
                "spend_intensity", "product_breadth", "conversation_theme", "week_asof", "month_asof",
            )} if extra else None,
        })
    return sorted(out, key=lambda e: e["slug"].lower())


def load_raw(day):
    base = os.path.join(DATA, "raw")
    if day is None:
        days = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
        if not days:
            sys.exit("No data/raw/<date>/ folder — run the refresh first (see REFRESH.md).")
        day = days[-1]
    folder = os.path.join(base, day)
    raw, meta = {}, {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        qid = os.path.basename(path)[:-5]
        if not re.fullmatch(r"\d{2}", qid):
            continue
        with open(path) as f:
            payload = json.load(f)
        res = payload["result"]
        rows = res.get("records") if "records" in res else res.get("rows", [])
        raw[qid] = [{k: dec(v) for k, v in r.items()} for r in rows]
        meta[qid] = {"rows": len(rows), "collected_from": payload.get("collected_from")}
        if len(rows) >= 1000:
            print(f"WARNING: query {qid} returned {len(rows)} rows — Kusto 1000-row cap may have truncated results.", file=sys.stderr)
    return day, raw, meta


# Enterprise-grained queries. They feed the Impact view, are collected incrementally, and describe
# windows that do not move daily, so a run that skipped them reuses the last result rather than
# publishing a model with no enterprises at all.
ENTERPRISE_QUERIES = ("13", "14", "15", "18")
# CCR (19), CCA (20, 21) and feature AIU (22) describe completed months or 13-week windows; a run that
# skipped them reuses the last result and reports it under stale_sources.
SLOW_QUERIES = ENTERPRISE_QUERIES + ("16", "17", "19", "20", "21", "22")


def carry_forward(day, raw, meta):
    """Fill absent enterprise, weekly and monthly sources from the most recent earlier day."""
    carried = {}
    for qid in SLOW_QUERIES:
        if qid in raw:
            continue
        for d in prior_days(day):
            path = os.path.join(DATA, "raw", d, f"{qid}.json")
            if not os.path.isfile(path):
                continue
            try:
                with open(path) as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            res = payload["result"]
            rows = res.get("records") if "records" in res else res.get("rows", [])
            raw[qid] = [{k: dec(v) for k, v in r.items()} for r in rows]
            meta[qid] = {"rows": len(rows), "collected_from": payload.get("collected_from"),
                         "carried_from": d}
            carried[qid] = d
            break
    return carried


# ---------------------------------------------------------------- model

def period(d):
    """ARR facts land on month-ends plus a 'latest' day that differs by product; bucket non-month-end dates as current."""
    day = dt.date.fromisoformat(d)
    return d if (day + dt.timedelta(days=1)).day == 1 else "9999-current"


def arr_series(row, dates):
    pts, cur_dates = {}, []
    for p in (chunk(row["s"], row["cols"]) if row else []):
        k = period(p["date"])
        if k.startswith("9999"):
            cur_dates.append(p["date"])
        agg = pts.setdefault(k, {"copilot_arr": 0.0, "total_arr": 0.0, "cb": 0.0, "ce": 0.0, "cs": 0.0})
        agg["copilot_arr"] += num(p["copilot_arr"]) or 0
        agg["total_arr"] += num(p["total_arr"]) or 0
        agg["cb"] += num(p["cb_seats"]) or 0
        agg["ce"] += num(p["ce_seats"]) or 0
        agg["cs"] += num(p["standalone_seats"]) or 0
    # Each product's latest snapshot lands on its own day, and right after a month closes it moves onto the new
    # month-end. When that month-end is newer than the account's 'current' days, fill any field that is empty in
    # 'current' from it instead of reading it as zero.
    month_ends = [d for d in dates if not d.startswith("9999")]
    if dates and dates[-1].startswith("9999") and month_ends and month_ends[-1] in pts and max(cur_dates, default="") < month_ends[-1]:
        cur = pts.setdefault(dates[-1], {"copilot_arr": 0.0, "total_arr": 0.0, "cb": 0.0, "ce": 0.0, "cs": 0.0})
        for k, v in pts[month_ends[-1]].items():
            if not cur[k]:
                cur[k] = v
    out = []
    for d in dates:  # otherwise a missing period means no contracted products then -> zero
        g = pts.get(d, {"copilot_arr": 0.0, "total_arr": 0.0, "cb": 0.0, "ce": 0.0, "cs": 0.0})
        out.append({"date": "current" if d.startswith("9999") else d, "copilot_arr": g["copilot_arr"], "total_arr": g["total_arr"],
                    "seats": g["cb"] + g["ce"] + g["cs"], "cb": g["cb"], "ce": g["ce"]})
    return out

def build(day, raw, meta):
    sf = {r["Id"]: r for r in raw.get("01", [])}
    snap = {r["id"]: r for r in raw.get("02", [])}
    by = {q: {r["id"]: r for r in raw.get(q, []) if r.get("id")} for q in ("03", "04", "05", "06", "07", "09", "16", "17")}
    integ, models = {}, {}
    for r in raw.get("08", []):
        integ.setdefault(r["id"], []).append(r)
    for r in raw.get("11", []):
        models.setdefault(r["id"], []).append(r)
    linked = {}
    for r in raw.get("12", []):
        linked.setdefault(r["id"], []).append({"id": r["linked_id"], "name": r["linked_name"], "gross_7d": num(r["gross_7d"]) or 0,
                                               "gross_p7": num(r["gross_p7"]) or 0, "gross_28d": num(r["gross_28d"]) or 0,
                                               "users_7d": r.get("users_7d") or 0})

    asof = {}
    arr_dates = sorted({period(p["date"]) for r in raw.get("09", []) for p in chunk(r["s"], r["cols"])})
    for q in ("03", "04", "06", "07", "08", "10", "12", "14", "16", "17", "18", "20", "21", "22"):
        rows = raw.get(q) or []
        if rows and rows[0].get("asof"):
            asof[q] = rows[0]["asof"][:10]
    if raw.get("02"):
        asof["02"] = (raw["02"][0].get("measurement_date") or "")[:10]

    accounts = []
    for aid, s in sf.items():
        c = snap.get(aid, {})
        a = {
            "id": aid, "name": s.get("Name"), "type": s.get("Type"), "industry": s.get("Industry") or c.get("industry"),
            "country": s.get("BillingCountry"), "parent": (s.get("Parent") or {}).get("Name"),
            "segment": c.get("segment"), "region": c.get("region"), "territory": c.get("territory"),
            "csm": c.get("csm") or None, "csa": c.get("csa") or None,
            "renewal": (c.get("next_renewal_date") or "")[:10] or None,
            "deployment": c.get("deployment") or None, "emu": c.get("is_emu"),
            "total_arr": num(c.get("total_arr")), "ghe_seats": num(c.get("license_seats")), "ghe_meu": num(c.get("ghe_meu")),
            "copilot_est_arr": num(c.get("copilot_est_arr")),
            "copilot_billed_ltm": num(c.get("copilot_billed_ltm")), "copilot_aiu_billed_ltm": num(c.get("copilot_aiu_billed_ltm")),
            "copilot_billed_lcm": num(c.get("copilot_billed_lcm")), "copilot_aiu_billed_lcm": num(c.get("copilot_aiu_billed_lcm")),
            "c360_assigned": num(c.get("assigned")), "installed_1d28": num(c.get("installed_1d28")),
            "engaged_1d28": num(c.get("engaged_1d28")), "c360_active_1d28": num(c.get("active_1d28")),
            "hwm_assigned_180d": num(c.get("hwm_assigned_180d")), "pct_users_at_risk": num(c.get("pct_users_at_risk")),
            "open_renewal_arr": num(c.get("open_renewal_license_arr")), "open_pipeline_net": num(c.get("open_pipeline_license_net")),
            "mover": c.get("mover") or None,
            "sf_url_id": aid,
        }
        # active users
        add_forecast_whitespace(a, by["16"].get(aid, {}), by["17"].get(aid, {}))
        u = chunk(by["03"].get(aid, {}).get("s"), by["03"].get(aid, {}).get("cols", "wk,a7,a28"))
        a["a7"], a["a28"] = weekly(u, "a7"), weekly(u, "a28")
        # health
        h = by["06"].get(aid)
        hp = chunk(h["s"], h["cols"]) if h else []
        for k in ("assigned", "at_risk", "health", "health_cat", "grr_30d"):
            a["h_" + k] = weekly(hp, k)
        latest_h = next((p for p in sorted(hp, key=lambda p: p["wk"])), None)
        a["risk"] = {k: latest_h.get(k) for k in ("risk_mau", "risk_intracohort_90d", "risk_no_activity_180d", "risk_pru_mau_retention")} if latest_h else None
        a["product_health"] = latest_h.get("product_health") if latest_h else None
        a["commercial_health"] = latest_h.get("commercial_health") if latest_h else None
        # UBB
        b = by["07"].get(aid)
        bp = chunk(b["s"], b["cols"]) if b else []
        a["gross"], a["billable"] = weekly(bp, "gross"), weekly(bp, "billable")
        a["aiu"], a["ubb_users"] = weekly(bp, "ai_units"), weekly(bp, "max_daily_users")
        cur_month = b and b.get("last_day") and b.get("month_start") and b["last_day"] >= b["month_start"]
        a["mtd_gross"] = num(b.get("mtd_gross")) if cur_month else None
        a["mtd_billable"] = num(b.get("mtd_billable")) if cur_month else None
        a["projected_month_spend"] = num(b.get("projected_month_end_spend")) if cur_month else None
        a["promo_util"] = num(b.get("promo_util")) if cur_month else None
        a["standard_util"] = num(b.get("standard_util")) if cur_month else None
        a["promo_pool"] = num(b.get("promo_pool")) if cur_month else None
        a["gross_run_rate_month"] = None
        if cur_month and a["mtd_gross"] is not None:
            ld = dt.date.fromisoformat(str(b["last_day"])[:10])
            dim = ((ld.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day
            a["gross_run_rate_month"] = round(a["mtd_gross"] / ld.day * dim, 2)
        # telemetry
        t = by["04"].get(aid)
        tp = chunk(t["s"], t["cols"]) if t else []
        a["accepted"], a["shown"] = weekly(tp, "accepted"), weekly(tp, "shown")
        a["loc_accepted"], a["loc_shown"] = weekly(tp, "loc_accepted"), weekly(tp, "loc_shown")
        a["editors"] = {k: {"accepted": v[0], "shown": v[1]} for k, v in (dec(t.get("editors")) or {}).items()} if t else {}
        # engagement / retention
        e = by["05"].get(aid, {})
        eng = e.get("eng") or {}
        def ratio(name):
            v = eng.get(name)
            return (v[0] / v[1]) if v and v[1] else None
        a["eng_active"], a["eng_active_prev"] = ratio("Copilot Engaged / Active"), ratio("Copilot Engaged / Active (prev)")
        a["eng_assigned"], a["eng_assigned_prev"] = ratio("Copilot Engaged / Assigned"), ratio("Copilot Engaged / Assigned (prev)")
        ret = (e.get("retention") or {}).get("30_day")
        a["mau_grr"], a["mau_nrr"] = (num(ret[0]), num(ret[1])) if ret else (None, None)
        a["mau_retained"], a["mau_churned"], a["mau_new"] = (ret[2], ret[3], ret[4]) if ret else (None, None, None)
        a["adoption_phase"] = e.get("adoption_phase") or None
        # contracted seats / ARR
        a["arr_series"] = arr_series(by["09"].get(aid), arr_dates)
        last9 = a["arr_series"][-1] if a["arr_series"] else {}
        a["copilot_arr"] = last9.get("copilot_arr")
        a["contracted_seats"] = last9.get("seats")
        a["cb_seats"], a["ce_seats"] = last9.get("cb"), last9.get("ce")
        # surfaces & models (last 28d)
        a["integrations"] = sorted([{"integration": r["integration"], "surface": surface(r["integration"]),
                                     "l7": num(r["l7"]) or 0, "p7": num(r["p7"]) or 0, "l28": num(r["l28"]) or 0,
                                     "aiu28": num(r.get("aiu28")) or 0, "u7": r.get("u7") or 0, "up7": r.get("up7") or 0,
                                     "u28": r.get("u28") or 0, "feature_metrics_known": feature_metrics_known(r)}
                                    for r in integ.get(aid, [])], key=lambda r: -r["l28"])
        a["models"] = sorted([{"model": r["model"], "l7": num(r["l7"]) or 0, "p7": num(r["p7"]) or 0, "l28": num(r["l28"]) or 0}
                              for r in models.get(aid, [])], key=lambda r: -r["l28"])
        a["linked_usage"] = sorted(linked.get(aid, []), key=lambda r: -r["gross_28d"])
        derive(a)
        accounts.append(a)

    link_billed_to_siblings(accounts)

    orphans = []
    kusto_ids = set(snap) | set(by["03"]) | set(by["06"]) | set(by["07"])
    for aid in sorted(kusto_ids - set(sf)):
        orphans.append({"id": aid, "name": (snap.get(aid) or {}).get("name") or aid})

    portfolio = build_portfolio(accounts, raw.get("10", []))
    # Sources 19-22 are optional: absent means "not collected in this model", never zero.
    ccr = build_ccr(raw["19"], day) if "19" in raw else None
    cca = build_cca(raw["20"]) if "20" in raw else None
    enterprises = build_enterprises(raw.get("13", []), raw.get("14", []), raw.get("15", []),
                                    load_enterprise_payloads(day), accounts, raw.get("18", []), meta.get("18"),
                                    ccr=ccr, cca=cca)
    attach_copilot_impact(accounts, enterprises, ccr, cca, build_feature_weekly(raw["22"]) if "22" in raw else None)
    if ccr is not None:
        observed = [e["ccr"] for e in enterprises if (e.get("ccr") or {}).get("status") == "observed"]
        portfolio["ccr"] = {"series": rollup_ccr(observed), "summary": ccr_summary(observed),
                            "withheld": sum(1 for e in enterprises if (e.get("ccr") or {}).get("status") == "withheld")}
        latest = portfolio["ccr"]["summary"]["latest_month"]
        if latest:
            asof["19"] = latest
    if cca is not None:
        portfolio["cca"] = {"series": rollup_cca(cca), "summary": cca_summary(cca)}
    opportunities = build_cca_opportunities(raw["21"], accounts) if "21" in raw else None
    return {"generated_at": dt.datetime.now().isoformat(timespec="minutes"), "data_date": day, "asof": asof,
            "sources": meta, "accounts": accounts, "portfolio": portfolio, "orphans": orphans,
            "enterprises": enterprises, "cca_opportunities": opportunities,
            "recommendations": recommendations.load(DATA, [day] + [d for d in prior_days(day) if d != day], accounts)}


def attach_copilot_impact(accounts, enterprises, ccr, cca, features):
    """Account-level CCR, CCA and AIU-by-feature. Only confirmed warehouse links contribute CCR;
    only CCA owners carrying the account's Salesforce ID contribute CCA. None = source not collected."""
    linked = {}
    for e in enterprises:
        cands = (e.get("match") or {}).get("candidates") or []
        if (e.get("match") or {}).get("confidence") == "confirmed" and cands:
            linked.setdefault(cands[0]["id"], []).append(e)
    owners = {}
    for o in cca or []:
        if o["sf_id"]:
            owners.setdefault(o["sf_id"], []).append(o)
    for a in accounts:
        a["aiu_by_feature"] = None if features is None else features.get(a["id"], {})
        if ccr is None:
            a["ccr"] = None
        else:
            ents = linked.get(a["id"], [])
            seen = [e for e in ents if e.get("ccr")]
            obs = [e["ccr"] for e in seen if e["ccr"].get("status") == "observed"]
            a["ccr"] = {
                "status": "observed" if obs else "withheld" if any(e["ccr"].get("status") == "withheld" for e in seen)
                          else "no_rows" if ents else "no_enterprise",
                "enterprises": [{"slug": e["slug"], "enterprise_id": e["ccr"].get("enterprise_id"),
                                 "status": e["ccr"].get("status"), "reason": e["ccr"].get("reason")} for e in seen],
                "series": rollup_ccr(obs),
            }
        if cca is None:
            a["cca"] = None
        else:
            mine = owners.get(a["id"], [])
            a["cca"] = {"status": "observed" if any(o["status"] == "observed" for o in mine) else "no_rows",
                        "owners": mine, "series": rollup_cca(mine)}

def add_forecast_whitespace(account, forecast, whitespace):
    numbers = {
        "ubb_license_users": "license_users", "ubb_week_users": "ubb_users",
        "ubb_adoption_rate": "ubb_adoption_rate", "forecast_bill": "forecast_bill",
        "forecast_bill_low": "forecast_bill_low", "forecast_bill_high": "forecast_bill_high",
        "included_pool": "pool", "forecast_pool_ratio": "forecast_pool_ratio",
        "wow_ubb_users": "wow_ubb_users",
    }
    account.update({key: num(forecast.get(source)) for key, source in numbers.items()})
    account.update({key: forecast.get(source) or None for key, source in {
        "ubb_adoption_band": "ubb_adoption_band", "entitlement_status": "entitlement_status",
        "utilization_band": "utilization_band", "ubb_insight": "insight",
    }.items()})
    bill, pool = account["forecast_bill"], account["included_pool"]
    account["forecast_above_pool"] = max(0, bill - pool) if bill is not None and pool is not None else None
    account.update({key: num(whitespace.get(source)) for key, source in {
        "ws_headcount": "headcount", "ws_potential_seats": "potential_seats",
        "ws_assigned_seats": "assigned_seats", "ws_tam_annual": "tam_annual", "ws_som_annual": "som_annual",
        "ghe_potential_seats": "ghe_potential_seats", "ghe_som_annual": "ghe_som_annual",
        "ghas_potential_seats": "ghas_potential_seats", "ghas_som_annual": "ghas_som_annual",
    }.items()})
    account.update({key: whitespace.get(source) or None for key, source in {
        "ws_play": "play", "ws_play_reason": "play_reason", "ws_play_context": "play_context",
        "ws_estimate_source": "seat_estimate_source",
    }.items()})
    potential = account["ws_potential_seats"]
    account["ws_whitespace_seats"] = max(0, potential - (account["ws_assigned_seats"] or 0)) if potential is not None else None
    if account["ws_estimate_source"] == "rejected_as_implausible":
        for key in ("ws_potential_seats", "ws_whitespace_seats", "ws_tam_annual", "ws_som_annual"):
            account[key] = None


NAME_SUFFIXES = (
    "privatelimited",
    "technologies",
    "corporation",
    "consulting",
    "technology",
    "solutions",
    "software",
    "services",
    "systems",
    "pvtltd",
    "limited",
    "india",
    "group",
    "gmbh",
    "labs",
    "corp",
    "pvt",
    "ltd",
    "inc",
    "llc",
    "llp",
    "co",
)


def name_key(n):
    """Same normalisation as query 12: lowercase alphanumerics with trailing corporate suffixes removed."""
    k = re.sub(r"[^a-z0-9]", "", (n or "").lower())
    while True:
        for suffix in NAME_SUFFIXES:
            if k.endswith(suffix):
                k = k[:-len(suffix)]
                break
        else:
            return k


def names_match(k1, k2):
    return len(k1) >= 5 and len(k2) >= 5 and (k1 == k2 or (len(k1) >= 8 and k2.startswith(k1)) or (len(k2) >= 8 and k1.startswith(k2)))


def link_billed_to_siblings(accounts):
    """A billed-only account whose usage is recorded on another owned look-alike account (e.g. after Sales Ops
    reassigns the slug-created account) is covered, not unlinked."""
    direct = [b for b in accounts if set(b["signals"]) & {"active users", "seats assigned", "UBB spend"}]
    for a in accounts:
        a["usage_on_sibling"] = []
        if not a["usage_unlinked"] or a.get("linked_usage"):
            continue
        k = name_key(a["name"])
        sib = [{"id": b["id"], "name": b["name"], "gross_28d": b.get("gross_28d")} for b in direct if b["id"] != a["id"] and names_match(k, name_key(b["name"]))]
        if sib:
            a["usage_on_sibling"] = sib
            a["usage_unlinked"] = False
            a["signals"].append("usage on sibling account")


def v0(series):
    return series[0] if series else None


def any_pos(series, weeks=ACTIVE_LOOKBACK_WEEKS):
    return any((num(x) or 0) > 0 for x in series[:weeks])


def derive(a):
    a["active_7d"] = v0(a["a7"])
    a["active_28d"] = v0(a["a28"]) if v0(a["a28"]) is not None else a["c360_active_1d28"]
    a["assigned"] = v0(a["h_assigned"]) if v0(a["h_assigned"]) is not None else a["c360_assigned"]
    a["health_cat"] = v0(a["h_health_cat"]) or None
    a["health"] = v0(a["h_health"])
    a["at_risk"] = v0(a["h_at_risk"])
    a["gross_7d"] = v0(a["gross"]) or 0
    a["gross_28d"] = sum(num(x) or 0 for x in a["gross"][:4])
    a["gross_90d"] = sum(num(x) or 0 for x in a["gross"])
    acc4 = sum(num(x) or 0 for x in a["accepted"][:4]); sh4 = sum(num(x) or 0 for x in a["shown"][:4])
    a["acceptance_28d"] = acc4 / sh4 if sh4 else None
    a["utilization"] = (a["active_28d"] / a["assigned"]) if a["assigned"] and a["active_28d"] is not None else None
    a["top_surface"] = None
    if a["integrations"]:
        agg = {}
        for r in a["integrations"]:
            agg[r["surface"]] = agg.get(r["surface"], 0) + r["l28"]
        a["surface_mix"] = dict(sorted(agg.items(), key=lambda kv: -kv[1]))
        a["top_surface"] = next(iter(a["surface_mix"]))
    else:
        a["surface_mix"] = {}
    a["top_model"] = a["models"][0]["model"] if a["models"] else None
    signals = []
    if any_pos(a["a7"]) or any_pos(a["a28"]):
        signals.append("active users")
    if any_pos(a["h_assigned"]) or (a["c360_assigned"] or 0) > 0 or any((p["seats"] or 0) > 0 for p in a["arr_series"]):
        signals.append("seats assigned")
    if any_pos(a["gross"]):
        signals.append("UBB spend")
    if (a.get("copilot_billed_lcm") or 0) > 0:
        signals.append("Copilot billed")
    if a.get("linked_usage"):
        signals.append("UBB on unowned account")
    a["signals"] = signals
    # No usage telemetry under this Salesforce ID even though the customer is billed or has usage on an
    # unowned look-alike account (typically auto-created from a GitHub enterprise slug).
    a["usage_unlinked"] = bool(signals) and set(signals) <= {"Copilot billed", "UBB on unowned account"}
    a["linked_gross_7d"] = round(sum(r["gross_7d"] for r in a.get("linked_usage") or []), 2) if a.get("linked_usage") else None
    a["linked_gross_28d"] = round(sum(r["gross_28d"] for r in a.get("linked_usage") or []), 2) if a.get("linked_usage") else None
    a["consuming"] = bool(signals)
    # 13-week trend of active users: last 4 weeks avg vs weeks 4-7 avg
    def avg(xs):
        xs = [num(x) for x in xs if num(x) is not None]
        return sum(xs) / len(xs) if xs else None
    r, p = avg(a["a7"][:4]), avg(a["a7"][4:8])
    a["trend_4w"] = ((r - p) / p) if r is not None and p else None
    for k, s in (("a7", "a7"), ("a28", "a28"), ("assigned", "h_assigned"), ("gross", "gross"), ("aiu", "aiu"), ("ubb_users", "ubb_users"), ("at_risk", "h_at_risk"), ("health", "h_health")):
        cur, prev = num(a[s][0]), num(a[s][1])
        a["wow_" + k] = (cur - prev) if cur is not None and prev is not None else None
    acc = [(num(x), num(y)) for x, y in zip(a["accepted"][:2], a["shown"][:2])]
    rates = [x / y if x is not None and y else None for x, y in acc]
    a["wow_acceptance"] = (rates[0] - rates[1]) if None not in rates else None


def build_portfolio(accounts, rows10):
    cons = [a for a in accounts if a["consuming"]]
    def series(key):
        return [sum(num(a[key][w]) or 0 for a in cons) for w in range(WEEKS)]
    p = {"owned": len(accounts), "consuming": len(cons),
         "a7": series("a7"), "a28": series("a28"), "assigned": series("h_assigned"), "gross": series("gross"),
         "billable": series("billable"), "aiu": series("aiu"), "at_risk": series("h_at_risk"),
         "accepted": series("accepted"), "shown": series("shown"),
         "loc_accepted": series("loc_accepted"), "loc_shown": series("loc_shown"),
         "consuming_by_week": [sum(1 for a in cons if (num(a["a7"][w]) or 0) > 0 or (num(a["gross"][w]) or 0) > 0) for w in range(WEEKS)],
         "copilot_arr": sum(a["copilot_arr"] or 0 for a in cons), "contracted_seats": sum(a["contracted_seats"] or 0 for a in cons),
         "mtd_gross": sum(a["mtd_gross"] or 0 for a in cons), "projected_month_spend": sum(a["projected_month_spend"] or 0 for a in cons),
         "gross_run_rate_month": sum(a["gross_run_rate_month"] or 0 for a in cons),
         "copilot_billed_ltm": sum((a["copilot_billed_ltm"] or 0) + (a["copilot_aiu_billed_ltm"] or 0) for a in cons)}
    hc = {}
    for a in cons:
        hc[a["health_cat"] or "n/a"] = hc.get(a["health_cat"] or "n/a", 0) + 1
    p["health_mix"] = hc
    hc_prev = {}
    for a in cons:
        k = (a["h_health_cat"][1] if a["h_health_cat"][1] else None) or "n/a"
        hc_prev[k] = hc_prev.get(k, 0) + 1
    p["health_mix_prev"] = hc_prev
    surf = {}
    for r in rows10:
        s = surface(r.get("integration"))
        surf.setdefault(s, [0.0] * WEEKS)
        wk = r.get("wk")
        if isinstance(wk, (int, float)) and 0 <= wk < WEEKS:
            surf[s][int(wk)] += num(r.get("gross")) or 0
    p["surface_weekly"] = dict(sorted(surf.items(), key=lambda kv: -sum(kv[1])))
    # AI units by surface; collections from before q10 carried AIU have no "aiu" field -> unknown.
    if any("aiu" not in r for r in rows10):
        p["surface_weekly_aiu"] = None
    else:
        surf_aiu = {}
        for r in rows10:
            wk = r.get("wk")
            series = surf_aiu.setdefault(surface(r.get("integration")), [0.0] * WEEKS)
            if isinstance(wk, (int, float)) and 0 <= wk < WEEKS:
                series[int(wk)] += num(r.get("aiu")) or 0
        p["surface_weekly_aiu"] = dict(sorted(surf_aiu.items(), key=lambda kv: -sum(kv[1])))
    integ = {}
    for r in rows10:
        integ.setdefault(r["integration"], [0.0] * WEEKS)
        if 0 <= r["wk"] < WEEKS:
            integ[r["integration"]][int(r["wk"])] += num(r["gross"]) or 0
    p["integration_weekly"] = dict(sorted(integ.items(), key=lambda kv: -sum(kv[1])))
    mdl = {}
    for a in cons:
        for m in a["models"]:
            d = mdl.setdefault(m["model"], {"l7": 0, "p7": 0, "l28": 0, "accounts": 0})
            d["l7"] += m["l7"]; d["p7"] += m["p7"]; d["l28"] += m["l28"]; d["accounts"] += 1
    p["models"] = dict(sorted(mdl.items(), key=lambda kv: -kv[1]["l28"]))
    for key in ("entitlement_status", "ubb_insight"):
        counts = {}
        for account in accounts:
            value = account.get(key)
            if value:
                counts[value] = counts.get(value, 0) + 1
        p[key + "_counts"] = counts
    for key in ("ws_potential_seats", "ws_whitespace_seats", "ws_som_annual",
                "assigned", "active_28d", "engaged_1d28"):
        values = [a.get(key) for a in accounts if a.get(key) is not None]
        p["total_" + key] = sum(values) if values else None
    return p


# ---------------------------------------------------------------- changes
def pct(cur, prev):
    return (cur - prev) / prev if prev else None


def changes(model, prev_snapshot):
    ev = []
    today = dt.date.fromisoformat(model["data_date"])
    def add(kind, sev, a, text, value=None):
        ev.append({"kind": kind, "severity": sev, "id": a["id"] if a else None, "account": a["name"] if a else None, "text": text, "value": value})

    for a in model["accounts"]:
        a7, g = a["a7"], a["gross"]
        act = lambda w: (num(a7[w]) or 0) > 0 or (num(g[w]) or 0) > 0
        if a["consuming"]:
            if act(0) and not any(act(w) for w in range(1, 5)):
                add("new", "good", a, "Started consuming Copilot this week", a["active_7d"])
            if not act(0) and any(act(w) for w in range(1, 5)):
                add("stopped", "bad", a, "No Copilot activity this week (active last month)")
        c, p = num(a7[0]), num(a7[1])
        if c is not None and p is not None and abs(c - p) >= 3 and (not p or abs(c - p) / p >= 0.2):
            add("active_users", "good" if c > p else "bad", a, f"Weekly active users {int(p)} → {int(c)} ({fmt_pct(pct(c, p))})", c - p)
        c, p = num(a["h_assigned"][0]), num(a["h_assigned"][1])
        if c is not None and p is not None and c != p:
            add("assigned", "good" if c > p else "bad", a, f"Assigned Copilot seats {int(p)} → {int(c)}", c - p)
        ser = a["arr_series"]
        if len(ser) >= 2 and ser[-1]["seats"] != ser[-2]["seats"]:
            add("contract", "good" if ser[-1]["seats"] > ser[-2]["seats"] else "bad", a,
                f"Contracted Copilot seats {int(ser[-2]['seats'])} → {int(ser[-1]['seats'])} (since {ser[-2]['date']})", ser[-1]["seats"] - ser[-2]["seats"])
        hc, hp = a["h_health_cat"][0], a["h_health_cat"][1]
        if hc and hp and hc != hp:
            order = {"Red": 0, "Yellow": 1, "Green": 2}
            add("health", "good" if order.get(hc, 1) > order.get(hp, 1) else "bad", a, f"Copilot health {hp} → {hc}")
        c, p = num(a["h_at_risk"][0]), num(a["h_at_risk"][1])
        if c is not None and p is not None and c - p >= 2:
            add("risk", "bad", a, f"Users at risk up {int(p)} → {int(c)}", c - p)
        c, p = num(g[0]) or 0, num(g[1]) or 0
        if abs(c - p) >= 50 and (not p or abs(c - p) / p >= 0.3):
            add("spend", "good" if c > p else "warn", a, f"UBB gross spend ${p:,.0f} → ${c:,.0f} WoW ({fmt_pct(pct(c, p))})", c - p)
        for r in a["integrations"]:
            if r["l7"] > 0 and r["p7"] == 0 and r["l28"] == r["l7"] and r["surface"] not in ("IDE chat",):
                add("surface", "good", a, f"New surface adopted: {r['integration']} ({r['surface']}), ${r['l7']:,.0f} this week", r["l7"])
        if a["promo_util"] and a["promo_util"] > 1:
            add("overage", "warn", a, f"Projected to use {a['promo_util']*100:.0f}% of included pool this month (${a['projected_month_spend'] or 0:,.0f})", a["promo_util"])
        if a["renewal"] and a["consuming"]:
            days = (dt.date.fromisoformat(a["renewal"]) - today).days
            if 0 <= days <= 90 and a["trend_4w"] is not None and a["trend_4w"] <= -0.1:
                add("renewal", "bad", a, f"Renews in {days} days with active users down {fmt_pct(a['trend_4w'])} (4-wk avg)", days)
            elif 0 <= days <= 90:
                add("renewal", "info", a, f"Renews in {days} days ({a['renewal']})", days)
        for r in a.get("linked_usage") or []:
            add("linked", "warn", a, f"Copilot usage on unowned Salesforce account '{r['name']}': ${r['gross_7d']:,.0f} this week, ${r['gross_28d']:,.0f} over 28 days "
                f"({r['users_7d']} users). Ask Sales Ops to merge it into this account", r["gross_28d"])
        if a["utilization"] is not None and a["assigned"] and a["assigned"] >= 5 and a["utilization"] < 0.5:
            add("utilization", "warn", a, f"Low utilization: {a['utilization']*100:.0f}% of {int(a['assigned'])} assigned seats active (28d)", a["utilization"])

    if prev_snapshot:
        prev_ids = {x["id"]: x for x in prev_snapshot.get("accounts", [])}
        cur_ids = {x["id"]: x for x in model["accounts"]}
        for aid in set(cur_ids) - set(prev_ids):
            add("ownership", "info", cur_ids[aid], f"Account newly owned in Salesforce (since {prev_snapshot['data_date']})")
        for aid in set(prev_ids) - set(cur_ids):
            x = prev_ids[aid]
            ev.append({"kind": "ownership", "severity": "info", "id": aid, "account": x["name"], "text": f"No longer owned in Salesforce (was owned on {prev_snapshot['data_date']})", "value": None})
    rank = {"bad": 0, "warn": 1, "good": 2, "info": 3}
    ev.sort(key=lambda e: (rank.get(e["severity"], 9), -abs(e["value"]) if isinstance(e["value"], (int, float)) else 0))
    return ev


def fmt_pct(x):
    return "n/a" if x is None else f"{x*100:+.0f}%"


# ---------------------------------------------------------------- snapshots / output
def previous_snapshot(day):
    snaps = sorted(glob.glob(os.path.join(DATA, "snapshots", "*.json")))
    target = dt.date.fromisoformat(day) - dt.timedelta(days=7)
    older = [s for s in snaps if os.path.basename(s)[:-5] < day]
    if not older:
        return None
    # prefer the snapshot closest to 7 days ago, else the latest earlier one
    best = min(older, key=lambda s: abs((dt.date.fromisoformat(os.path.basename(s)[:-5]) - target).days))
    with open(best) as f:
        return json.load(f)


DEFINITIONS = {
    "forecast_above_pool": "Projected above included pool: max(0, forecast bill minus included pool); not invoiced overage",
    "forecast_bill": "UBB month-end forecast; show its low-high range only for positive forecasts",
    "ubb_week_users": "Users with UBB activity; ubb_license_users means licensed users",
    "ws_whitespace_seats": "Model estimate · parent-level: max(0, potential seats minus assigned seats, with missing assigned treated as zero). Direct owned-account matches only",
    "ws_som_annual": "Model estimate · parent-level annual Copilot serviceable obtainable market. Rejected implausible estimates are withheld",
    "installed_1d28": "Installed IDE telemetry over 28 days; partial coverage, not a step in the adoption funnel",
    "engaged_1d28": "Users engaged at least one day over the last 28 days",
    "consuming": "Any Copilot active users, seats assigned, or UBB spend in the last 90 days, Copilot billed last month, or UBB on an unowned look-alike account",
    "linked_usage": "UBB spend (28d) on Salesforce accounts with no real owner (e.g. Data Syncer, auto-created from a GitHub enterprise slug) whose name matches this account. Shown for context; not added to this account's totals",
    "active_this_week": "Active users > 0 or UBB gross spend > 0 in the latest 7 days",
    "usage_unlinked": "Billed for Copilot or has usage on an unowned look-alike account, but no usage telemetry under this Salesforce account; excluded from inactive_this_week",
    "usage_on_sibling": "Billed for Copilot with no telemetry of its own, but another owned account with a matching name carries the usage (e.g. a reassigned slug-created account); excluded from inactive_this_week",
    "active_last_week": "Same as active_this_week, for the 7 days before that",
    "inactive_this_week": "Consuming (90d) but not active this week",
    "trend_4w": "Avg weekly active users, last 4 weeks vs the 4 weeks before",
    "declining": "trend_4w <= -10%",
    "growing": "trend_4w >= +10%",
    "low_utilization": "Consuming, >= 10 assigned seats and 28-day active / assigned < 30%",
    "renewing_in_days": "Days from the data date to next renewal (negative = past)",
    "health_moved": "Copilot health category differs from one week earlier",
    "utilization": "28-day active users / assigned seats",
    "wow_*": "Latest 7 days minus the prior 7 days",
    "ccr": "Copilot code review (source 19), per enterprise and month. coverage = PRs reviewed by Copilot / reviewed PRs. penetration and the adoption score are published as reported; the current month is month to date. Account and portfolio rollups recompute coverage from summed counts and never average percentiles, penetration or scores",
    "ccr_lead_days": "Median and percentile PR lead time in days for Copilot-reviewed vs all other PRs; labels are passed through from the source and are observational, not causal",
    "cca": "Copilot coding agent decision intelligence (source 20), per enterprise or organization owner and completed month. share = CCA PRs / PRs, penetration = CCA repos / repos, frequency = CCA active days / active days, rate = cube root of their product; stage is the source's band",
    "cca_epi_lift": "Engineering-productivity index lift by PR complexity tier: CCA EPI / non-CCA EPI - 1, ranked within a peer pool. Observational, not causal; withheld where the owner has no non-CCA comparison",
    "cca_opportunities": "Source guidance (source 21): expansion signal score and band (S >= 60, A >= 40, B >= 20, C >= 5, D below) for CCA owners, and new-logo readiness for owners without CCA. Not a recommendation from this dashboard",
    "aiu_by_feature": "AI units and gross $ by Copilot feature per account and week (source 22). AI units are consumption units, not billable dollars",
}


def add_flags(model):
    """Shared, question-friendly flags used by both the dashboard Ask box and ask.py."""
    today = dt.date.fromisoformat(model["data_date"])
    for a in model["accounts"]:
        def pos(key, i):
            v = num((a.get(key) or [None, None])[i])
            return v is not None and v > 0
        a["active_this_week"] = pos("a7", 0) or pos("gross", 0)
        a["active_last_week"] = pos("a7", 1) or pos("gross", 1)
        a["inactive_this_week"] = a["consuming"] and not a["active_this_week"] and not a.get("usage_unlinked") and not a.get("usage_on_sibling")
        t = a.get("trend_4w")
        a["declining"] = t is not None and t <= -0.1
        a["growing"] = t is not None and t >= 0.1
        u = a.get("utilization")
        a["low_utilization"] = bool(a["consuming"] and u is not None and (a.get("assigned") or 0) >= 10 and u < 0.3)
        a["renewing_in_days"] = (dt.date.fromisoformat(a["renewal"]) - today).days if a.get("renewal") else None
        hc = a.get("h_health_cat") or [None, None]
        a["health_prev"] = hc[1] if len(hc) > 1 else None
        a["health_moved"] = bool(a.get("health_cat") and a["health_prev"] and a["health_cat"] != a["health_prev"])
    model["definitions"] = DEFINITIONS
    model["new_metrics"] = [{"key": key, "since": "2026-10-26"} for key in (
        "forecast_bill", "forecast_bill_low", "forecast_bill_high", "included_pool", "forecast_pool_ratio",
        "forecast_above_pool", "entitlement_status", "utilization_band", "ubb_insight",
        "ubb_license_users", "ubb_week_users", "ubb_adoption_rate", "ubb_adoption_band",
        "ws_potential_seats", "ws_whitespace_seats", "ws_som_annual", "ws_play",
        "engaged_1d28", "installed_1d28", "mover")]


SNAP_FIELDS = ("id", "name", "consuming", "active_this_week", "active_7d", "active_28d", "assigned", "contracted_seats", "utilization",
               "at_risk", "health", "health_cat", "gross_7d", "gross_28d", "mtd_gross", "top_surface", "top_model")


def atomic_write(path, text):
    """Write through a temp file and rename it, so the canvas never reads a half-written file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def slim(model):
    return {"data_date": model["data_date"], "accounts": [{k: a.get(k) for k in SNAP_FIELDS} for a in model["accounts"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--out", default=dashboard_path())
    args = ap.parse_args()

    day, raw, meta = load_raw(args.date)
    carried = carry_forward(day, raw, meta)
    missing = [f"{q:02d}" for q in range(1, 23) if f"{q:02d}" not in raw]
    if "01" in missing:
        sys.exit("Query 01 (owned accounts) is required.")
    model = build(day, raw, meta)
    add_flags(model)
    model["missing_sources"] = missing
    model["stale_sources"] = carried
    prev = previous_snapshot(day)
    model["compared_to"] = prev["data_date"] if prev else None
    model["changes"] = changes(model, prev)

    os.makedirs(os.path.join(DATA, "snapshots"), exist_ok=True)
    atomic_write(os.path.join(DATA, "snapshots", f"{day}.json"), json.dumps(slim(model)))

    with open(os.path.join(ROOT, "template.html")) as f:
        tpl = f.read()
    compact = json.dumps(model, separators=(",", ":"))
    atomic_write(os.path.join(os.path.dirname(args.out), "model.json"), compact)
    blob = compact.replace("</", "<\\/")
    with open(os.path.join(ROOT, "ask_engine.js")) as f:
        engine = f.read().replace("</", "<\\/")
    atomic_write(args.out, tpl.replace("/*__ASK_ENGINE__*/", engine).replace("/*__DATA__*/null", blob))

    p = model["portfolio"]
    print(f"Dashboard written: {args.out}")
    print(f"Data date {day} · {p['consuming']} consuming of {p['owned']} owned accounts · "
          f"weekly active users {p['a7'][0]:.0f} (WoW {p['a7'][0]-p['a7'][1]:+.0f}) · UBB gross last 7d ${p['gross'][0]:,.0f} (WoW {fmt_pct(pct(p['gross'][0], p['gross'][1]))})")
    if missing:
        print("Missing sources: " + ", ".join(missing))
    if args.summary:
        print("\nTop changes this week:")
        for e in model["changes"][:15]:
            print(f"  [{e['severity']}] {e['account'] or ''}: {e['text']}")


if __name__ == "__main__":
    main()
