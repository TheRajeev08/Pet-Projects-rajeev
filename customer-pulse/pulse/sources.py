"""Load the newest collected result for each query and decode it into plain Python structures.

Every Kusto series point is a "|"-delimited string (`strcat_delim`), so empty fields keep their
position and decode to None. Dates are returned as ISO strings ("2026-10-24").
"""
import glob
import os
import statistics

from . import paths
from .util import decode_dynamic, num, read_json, truthy

SOURCES = {
    "p01": {"label": "Salesforce book", "lag": 0},
    "p02": {"label": "Contracts", "lag": 1},
    "p03": {"label": "GHE usage", "lag": 2},
    "p04": {"label": "Copilot", "lag": 3},
    "p05": {"label": "GHAS committers", "lag": 2},
    "p06": {"label": "Account attributes", "lag": 2},
}

GHE_TYPES = ("GitHub Enterprise", "GitHub Enterprise Cloud", "GitHub Enterprise Server", "Visual Studio")
GHAS_TYPES = ("Advanced Security", "GHSP", "GHCS")
OTHER_TYPES = ("GitHub Team", "Legacy")
TYPE_LABELS = {"GHSP": "Secret Protection", "GHCS": "Code Security", "Visual Studio": "Visual Studio bundle"}


def family_of(product_type):
    if product_type in GHE_TYPES:
        return "ghe"
    if product_type in GHAS_TYPES:
        return "ghas"
    if product_type in OTHER_TYPES:
        return "other"
    return None


def iso(v):
    """'20261024' or '2026-10-24T00:00:00Z' -> '2026-10-24'."""
    if not v:
        return None
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10] if len(s) >= 10 and s[4] == "-" else None


_SF_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def sfid18(v):
    """Normalize a Salesforce id to its 18-character form."""
    if not isinstance(v, str):
        return v
    v = v.strip()
    if len(v) != 15:
        return v
    suffix = ""
    for i in range(3):
        bits = sum(1 << j for j, ch in enumerate(v[i * 5:i * 5 + 5]) if "A" <= ch <= "Z")
        suffix += _SF_CHARS[bits]
    return v + suffix


def raw_path(cfg, day, qid):
    return os.path.join(paths.raw_dir(cfg), day, f"{qid}.json")


def latest_raw(cfg, qid, upto=None):
    """Return (raw_date, doc) for the newest collected result of `qid` on or before `upto`."""
    best = None
    for path in glob.glob(raw_path(cfg, "*", qid)):
        day = os.path.basename(os.path.dirname(path))
        if upto and day > upto:
            continue
        if best is None or day > best[0]:
            best = (day, path)
    if not best:
        return None, None
    return best[0], read_json(best[1])


def raw_dates(cfg, qid):
    return sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(raw_path(cfg, "*", qid)))


def kusto_rows(doc):
    """Rows as dicts. The Revenue MCP returns each row as an object keyed by column; plain Kusto
    results use positional lists, so both shapes are accepted."""
    res = (doc or {}).get("result") or {}
    cols = [c if isinstance(c, str) else (c or {}).get("name") for c in res.get("columns") or []]
    out = []
    for r in res.get("rows") or []:
        if isinstance(r, dict):
            out.append(r)
        elif isinstance(r, list):
            out.append(dict(zip(cols, r)))
    return out


def split_points(v):
    """Decode a dynamic list of '|' strings into lists of fields (empty -> None)."""
    v = decode_dynamic(v)
    if isinstance(v, str):
        v = [v]
    out = []
    for p in v or []:
        if isinstance(p, str):
            out.append([x if x != "" else None for x in p.split("|")])
    return out


def _series_rows(doc, id_col="salesforce_account_id"):
    for r in kusto_rows(doc):
        yield sfid18(r.get(id_col)), r.get("series"), split_points(r.get("pts"))


def _meta(points):
    out = []
    for p in points:
        d = iso(p[0])
        if d:
            out.append((d, num(p[1]) if len(p) > 1 else None, num(p[2]) if len(p) > 2 else None))
    return sorted(out)


def _rows(points, width):
    out = []
    for p in points:
        d = iso(p[0])
        if d:
            out.append(tuple([d] + [num(p[i]) if i < len(p) else None for i in range(1, width)]))
    return sorted(out)


def parse_contracted(doc):
    """p02 -> ({acct: {product_type: [(start, end, seats, arr, skus)]}}, meta[(date, n, s)])."""
    runs, meta = {}, []
    for r in kusto_rows(doc):
        acct = sfid18(r.get("account_id"))
        pts = split_points(r.get("runs"))
        if acct == "__meta__":
            meta = sorted((iso(p[1]), num(p[3]), num(p[4]) if len(p) > 4 else None) for p in pts if len(p) > 3 and iso(p[1]))
            continue
        for p in pts:
            if len(p) < 5 or not iso(p[1]):
                continue
            skus = [s for s in (p[5] or "").split("; ") if s] if len(p) > 5 else []
            runs.setdefault(acct, {}).setdefault(p[0], []).append((iso(p[1]), iso(p[2]), num(p[3]) or 0.0, num(p[4]), skus))
    for by_type in runs.values():
        for lst in by_type.values():
            lst.sort()
    return runs, meta


def parse_ghe_usage(doc):
    out = {"metered": {}, "volume": {}, "engagement": {}, "metered_meta": [], "volume_meta": []}
    for acct, series, pts in _series_rows(doc):
        if series == "metered":
            out["metered"][acct] = _rows(pts, 5)
        elif series == "volume":
            out["volume"][acct] = _rows(pts, 2)
        elif series == "engagement":
            out["engagement"][acct] = _rows(pts, 3)
        elif series == "metered_rows":
            out["metered_meta"] = _meta(pts)
        elif series == "volume_rows":
            out["volume_meta"] = _meta(pts)
    return out


HEALTH_LATEST = ("date", "assigned", "at_risk", "mau_risk", "intracohort_risk", "no_activity_180d_risk",
                 "pru_mau_retention_risk", "grr_30d", "hwm_assigned_180d", "retention_180d_hwm",
                 "product_health", "commercial_health", "copilot_health", "category")
CODE_QUALITY = ("date", "committers", "repos_active", "repos_waiting", "total_repos", "autofixes_l30d",
                "prs_fixed_autofix_l30d", "prs_fixed_l30d", "prs_not_fixed_l30d")


def _record(fields, p):
    rec = {}
    for i, k in enumerate(fields):
        v = p[i] if i < len(p) else None
        rec[k] = iso(v) if k == "date" else (v if k == "category" else num(v))
    return rec


def parse_copilot(doc):
    out = {"health": {}, "health_latest": {}, "seats": {}, "health_meta": [], "seats_meta": []}
    for acct, series, pts in _series_rows(doc):
        if series == "health":
            out["health"][acct] = _rows(pts, 3)
        elif series == "health_latest" and pts:
            out["health_latest"][acct] = _record(HEALTH_LATEST, pts[0])
        elif series == "seats":
            out["seats"][acct] = _rows(pts, 4)
        elif series == "health_rows":
            out["health_meta"] = _meta(pts)
        elif series == "seat_totals":
            out["seats_meta"] = _meta(pts)
    return out


def parse_ghas(doc):
    out = {"ghas": {}, "code_quality": {}, "ghas_meta": []}
    for acct, series, pts in _series_rows(doc):
        if series == "ghas":
            out["ghas"][acct] = _rows(pts, 4)
        elif series == "code_quality" and pts:
            out["code_quality"][acct] = _record(CODE_QUALITY, pts[0])
        elif series == "ghas_rows":
            out["ghas_meta"] = _meta(pts)
    return out


DATE_FIELDS = ("next_renewal_date", "measurement_date")
TEXT_FIELDS = ("salesforce_account_id", "salesforce_account_name", "account_type", "salesforce_parent_account_id",
               "salesforce_parent_account_name", "billing_country", "industry", "business_segment", "business_line",
               "managed_account_type", "sales_segment", "ghas_licensing_model", "final_ghas_plan")
BOOL_FIELDS = ("g2k", "is_test_account", "ghas_enabled", "has_ghe_license", "has_ghas_license", "has_copilot",
               "has_actions", "has_codespaces", "has_ghec_metered", "has_cloud_orgs", "has_server_orgs")


def parse_attributes(doc):
    out = {}
    for r in kusto_rows(doc):
        acct = sfid18(r.get("salesforce_account_id"))
        if not acct:
            continue
        rec = {}
        for k, v in r.items():
            if k in DATE_FIELDS:
                rec[k] = iso(v)
            elif k in TEXT_FIELDS:
                rec[k] = v if v not in ("", None) else None
            elif k in BOOL_FIELDS:
                rec[k] = None if v in ("", None) else truthy(v)
            else:
                rec[k] = num(v)
        rec["salesforce_account_id"] = acct
        out[acct] = rec
    return out


def parse_universe(doc):
    res = (doc or {}).get("result") or {}
    out = {}
    for r in res.get("records") or []:
        acct = sfid18(r.get("Id"))
        if not acct:
            continue
        parent = r.get("Parent") or {}
        out[acct] = {
            "name": r.get("Name"),
            "type": r.get("Type"),
            "industry": r.get("Industry"),
            "country": r.get("BillingCountry"),
            "parent": parent.get("Name") if isinstance(parent, dict) else None,
            "created": iso(r.get("CreatedDate")),
            "last_activity": iso(r.get("LastActivityDate")),
        }
    return out


def healthy_days(meta, ratio=0.80, value_tol=0.20, window=7):
    """Decide which source days are complete enough to diff.

    A day is healthy when its global row count is at least `ratio` of the reference and its global
    value sum is within `value_tol` of the reference. The reference is the median of the previous
    `window` reported days (or of the whole series when fewer than three precede it), so a single bad
    day is rejected while a lasting level shift is accepted after a few days.
    """
    present = [m for m in meta if m[1]]
    if not present:
        return set(), {}
    all_n = statistics.median(m[1] for m in present)
    sums = [m[2] for m in present if m[2] is not None]
    all_s = statistics.median(sums) if sums else None
    healthy, why = set(), {}
    for i, (d, n, s) in enumerate(meta):
        prev = [m for m in meta[max(0, i - window):i] if m[1]]
        if len(prev) >= 3:
            ref_n = statistics.median(m[1] for m in prev)
            psums = [m[2] for m in prev if m[2] is not None]
            ref_s = statistics.median(psums) if psums else None
        else:
            ref_n, ref_s = all_n, all_s
        if not n:
            why[d] = "no rows"
        elif n < ratio * ref_n:
            why[d] = f"{int(n)} rows vs ~{int(ref_n)} expected"
        elif s is not None and ref_s and (s <= 0 or abs(s - ref_s) > value_tol * ref_s):
            why[d] = f"total {s:,.0f} vs ~{ref_s:,.0f} expected"
        else:
            healthy.add(d)
    return healthy, why


def trailing_unhealthy(meta, healthy):
    """Number of most recent reported days that are not healthy (breaker still tripped)."""
    n = 0
    for d, _, _ in reversed(meta):
        if d in healthy:
            break
        n += 1
    return n


# Snapshot facts publish month-end rows plus one rolling row whose date moves forward every day
# (Copilot seats on 08-31, 09-30 and 10-22, then 10-23 the next day). Older raw files keep each rolling
# row, so the series turns daily from the first run and a change is reported once instead of being
# re-dated, and re-sent, every morning. Their rolling rows carry more rows than month-ends, so only
# the totals decide whether a day is healthy.
SNAPSHOTS = {"p03": ("volume", parse_ghe_usage), "p04": ("seats", parse_copilot)}
SNAPSHOT_KEYS = tuple(key for key, _ in SNAPSHOTS.values())


def _merge_snapshots(cfg, src, today):
    """Add the dates older raw files have and newer ones lack; newer files win on shared dates.

    An account missing from an older file has an unknown value on that file's dates, recorded in
    src["unknown"][key][account] so it is neither diffed nor summed there."""
    for qid, (key, parse) in SNAPSHOTS.items():
        newest = (src["files"].get(qid) or {}).get("raw_date")
        if not newest:
            continue
        unknown = src["unknown"].setdefault(key, {})
        for day in reversed([d for d in raw_dates(cfg, qid) if d < newest and d <= today]):
            older = parse(read_json(raw_path(cfg, day, qid)))
            have = {m[0] for m in src[f"{key}_meta"]}
            extra = [m for m in older[f"{key}_meta"] if m[0] not in have]
            if not extra:
                continue
            dates = {m[0] for m in extra}
            src[f"{key}_meta"] = sorted(src[f"{key}_meta"] + extra)
            for acct in src[key]:
                if acct not in older[key]:
                    unknown.setdefault(acct, set()).update(dates)
            for acct, rows in older[key].items():
                add = [r for r in rows if r[0] in dates]
                if add:
                    src[key][acct] = sorted(src[key].get(acct, []) + add)


def account_days(src, key, acct, days):
    """`days` without the dates this account's value is unknown for (see _merge_snapshots)."""
    skip = ((src.get("unknown") or {}).get(key) or {}).get(acct)
    return [d for d in days if d not in skip] if skip else days


def load_all(cfg, today):
    """Return a dict with every decoded source plus file metadata, using the newest raw file per query
    (snapshot series also keep the dates of older raw files)."""
    th = cfg["thresholds"]
    files, docs = {}, {}
    for qid in SOURCES:
        day, doc = latest_raw(cfg, qid, upto=today)
        docs[qid] = doc
        if doc:
            res = doc.get("result") or {}
            rows = len(res.get("rows") or res.get("records") or [])
            files[qid] = {"raw_date": day, "run_id": doc.get("run_id"), "via": doc.get("via"),
                          "collected_from": doc.get("collected_from"), "rows": rows}
    src = {"files": files}
    src["universe"] = parse_universe(docs["p01"]) if docs["p01"] else None
    src["attrs"] = parse_attributes(docs["p06"]) if docs["p06"] else {}
    src["contracted"], src["contracted_meta"] = parse_contracted(docs["p02"]) if docs["p02"] else ({}, [])
    src.update(parse_ghe_usage(docs["p03"]) if docs["p03"] else parse_ghe_usage(None))
    src.update(parse_copilot(docs["p04"]) if docs["p04"] else parse_copilot(None))
    src.update(parse_ghas(docs["p05"]) if docs["p05"] else parse_ghas(None))
    src["unknown"] = {}
    _merge_snapshots(cfg, src, today)
    ratio, tol = th["healthy_rows_ratio"], th["breaker_drop_pct"]
    src["healthy"], src["unhealthy_why"], src["meta"] = {}, {}, {}
    for key in ("contracted", "metered", "volume", "health", "seats", "ghas"):
        meta = src.get(f"{key}_meta") or []
        h, why = healthy_days(meta, 0.0 if key in SNAPSHOT_KEYS else ratio, tol)
        src["healthy"][key], src["unhealthy_why"][key], src["meta"][key] = h, why, meta
    return src
