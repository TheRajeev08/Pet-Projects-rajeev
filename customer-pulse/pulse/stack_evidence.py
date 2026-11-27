"""Versioned, read-only SE/Gong evidence. Never emits signals or prunes history.

Revenue limit-only adapters remain partial. Collector-owned Salesforce keyset
pages use the pinned source contract, retaining nonprivate Gong scope.
Only collector-owned CLI reads and account-scoped seller results are evidence.
"""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import subprocess
from urllib.parse import urlsplit

from . import collect, paths, seller, sources
from .signals import normalize_url
from .util import atomic_write_json, iso_now, read_json

SCHEMA = 1
CATEGORIES = {"SCM", "CI/CD", "Security", "Languages/frameworks", "Cloud/runtime", "Work tracking", "Other"}
STATUSES = {"reported_in_use", "evaluating", "planned", "historical", "mention_only"}
ALIASES = {"github actions": "GitHub Actions", "gitlab": "GitLab", "jenkins": "Jenkins",
           "azure devops": "Azure DevOps", "sonarqube": "SonarQube", "circleci": "CircleCI"}
FIELDS = {"account_id", "technology", "category", "usage_status", "source_id", "content_hash", "evidence_quote"}
ISSUE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$")
SFID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
MAX_READS = 40
MAX_CLAIMS = 500
SOURCE_PIN = "36f0fe3dd99b0b59373b9ad91199a0815a35ce6c"
SE_REPOSITORIES = ["github/solutions-engineering"]
ISSUE_FIELDS = "Id,Account__c,Name,GitHub_Issue_URL__c,GitHub_Repo__c,GitHub_State__c,Issue_Type__c,CreatedDate"
GONG_FIELDS = ("Id,Name,Gong__Primary_Account__c,Gong__Is_Private__c,Gong__Call_Start__c,"
               "Gong__Call_Brief__c,Gong__Call_Highlights_Next_Steps__c,Gong__Call_Key_Points__c")


def source_path():
    return os.path.join(paths.home(), "seller", "stack-sources.json")


def load_sources():
    doc = read_json(source_path()) or {"schema": SCHEMA, "accounts": {}}
    if doc.get("schema") != SCHEMA or not isinstance(doc.get("accounts"), dict):
        raise ValueError("Unsupported stack source store")
    return doc


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def source_record(aid, kind, sid, url, day, content, collected, **extra):
    dt.date.fromisoformat(day)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Source has no text")
    row = {"account_id": aid, "source_type": kind, "source_id": sid, "source_url": normalize_url(url),
           "observed_at": day, "collected_at": collected, "content": content, **extra}
    row["content_hash"] = digest({k: v for k, v in row.items() if k != "collected_at"})
    return row


def _call_url(row, cfg):
    if row.get("url"):
        url = normalize_url(row["url"])
        host = urlsplit(url).hostname
        if host == "gong.io" or host.endswith(".gong.io"):
            return url
    # A Salesforce record link is not a recording URL. Validate identity before using it.
    if SFID.fullmatch(row["id"]):
        base = normalize_url(cfg.get("sfdc_base_url") or paths.DEFAULTS["sfdc_base_url"])
        if urlsplit(base).hostname.endswith(".force.com"):
            return base.rstrip("/") + "/lightning/r/" + row["id"] + "/view"
    raise ValueError("Gong citation missing: no recording URL or validated Salesforce record ID")


def plan(model, cfg, today):
    activity = read_json(seller.plan_path("activity")) or {}
    if activity.get("date") != today or activity.get("group") != "activity":
        raise ValueError("Plan and collect today's activity before planning evidence")
    owned = {a["id"] for a in model["accounts"] if a.get("in_book") is not False}
    ids = activity.get("accounts", [])
    if not ids or not set(ids) <= owned:
        raise ValueError("Activity cohort is absent or outside the owned book")
    repositories = (cfg.get("stack_evidence") or {}).get("se_repositories", SE_REPOSITORIES)
    if not isinstance(repositories, list) or any(not isinstance(r, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) for r in repositories):
        raise ValueError("stack_evidence.se_repositories must contain verified owner/repository names")
    feeds = seller.load()["accounts"]
    tasks, coverage, calls = [], {}, []
    for aid in ids:
        coverage[aid] = {}
        for feed in ("issues", "gong"):
            entry = feeds.get(aid, {}).get(feed, {})
            valid = (entry.get("run_id") == activity["run_id"]
                     and entry.get("provenance", {}).get("account_id") == aid
                     and seller.coverage(feed, entry, today)["state"] in ("ok", "no_rows"))
            info = {"state": "partial" if valid else "failed", "discovered": len(entry.get("rows", [])),
                    "discovery_complete": False, "all_states_verified": True if feed == "issues" else None,
                    "reason": ("Provider exposes limit only; pagination and upstream scope are unverified."
                               if valid else "No current collector-verified activity result; previous evidence retained.")}
            coverage[aid][feed] = info
            if not valid:
                continue
            if feed == "issues" and not repositories:
                info.update(state="not_connected", reason="Verified SE repository scope is not configured; linked records may be non-SE.")
                continue
            for row in entry.get("rows", []):
                if feed == "issues":
                    match = ISSUE.fullmatch(str(row.get("url", "")))
                    if not match or match[1].casefold() not in {r.casefold() for r in repositories}:
                        info["unclassified"] = info.get("unclassified", 0) + 1
                        continue
                    repository, number = match.groups()
                    tasks.append({"account_id": aid, "source_id": f"{repository}#{number}", "url": row["url"],
                                  "endpoint": f"repos/{repository}/issues/{number}", "link_id": row["id"]})
                elif row.get("account_id") == aid:
                    try:
                        calls.append(source_record(aid, "gong", row["id"], _call_url(row, cfg), row["date"],
                                                   row.get("description"), entry["collected_at"]))
                    except (ValueError, TypeError) as exc:
                        info.setdefault("errors", []).append(f"{row['id']}: {exc}")
                else:
                    info.setdefault("errors", []).append("Call lacks a matching primary account ID")
    result = {"schema": SCHEMA, "group": "evidence", "date": today, "started_at": iso_now(), "run_id": secrets.token_hex(16),
              "activity_run_id": activity["run_id"], "accounts": ids, "tasks": tasks,
              "coverage": coverage, "calls": calls, "max_reads": MAX_READS, "repositories": repositories,
              "source_contract": SOURCE_PIN,
              "discovery": [{"account_id": aid, "feed": feed} for aid in ids for feed in ("issues", "gong")],
              "note": "After activity: collector-owned sf keyset pages and gh GET reads only. Resume until no deferred pages. No live scheduling."}
    atomic_write_json(seller.plan_path("evidence"), result, indent=2)
    return {**result, "calls": [{k: v for k, v in r.items() if k != "content"} for r in calls]}


def _gh(endpoint):
    try:
        result = subprocess.run(["gh", "api", "--method", "GET", endpoint],
                                capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("gh issue read unavailable or timed out") from exc
    if result.returncode:
        # Do not echo a potentially sensitive response body.
        raise ValueError("gh issue read failed; check repository access and CLI sign-in")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("gh returned non-JSON issue content") from exc


def _planned():
    planned = read_json(seller.plan_path("evidence")) or {}
    activity = read_json(seller.plan_path("activity")) or {}
    started = collect._parse_ts(planned.get("started_at"))
    if (not started or (dt.datetime.now(dt.timezone.utc) - started).total_seconds() > 86400
            or planned.get("activity_run_id") != activity.get("run_id")):
        raise ValueError("Evidence plan expired or activity changed; plan evidence again")
    return planned


def discovery_query(aid, feed, cursor=None):
    if not SFID.fullmatch(aid) or (cursor and not SFID.fullmatch(cursor)):
        raise ValueError("Invalid discovery account/cursor")
    if feed == "issues":
        fields, obj, scope = ISSUE_FIELDS, "GitHub_Issue__c", f"Account__c = '{aid}'"
    elif feed == "gong":
        fields, obj, scope = GONG_FIELDS, "Gong__Gong_Call__c", f"Gong__Primary_Account__c = '{aid}' AND Gong__Is_Private__c = false"
    else:
        raise ValueError("Unsupported discovery feed")
    return f"SELECT {fields} FROM {obj} WHERE {scope}" + (f" AND Id > '{cursor}'" if cursor else "") + " ORDER BY Id LIMIT 100"


def _discover(planned, doc, cfg, sf_reader):
    """One page per account/source per invocation, with durable cursor and union."""
    used = 0
    tasks = {}
    discovery = doc.setdefault("discovery", {})
    for spec in planned["discovery"]:
        aid, feed = spec["account_id"], spec["feed"]
        part = discovery.setdefault(f"{aid}:{feed}", {"cursor": None, "done": False, "rows": [], "fetched_at": {}})
        info = doc["accounts"][aid]["coverage"][feed]
        if feed == "issues" and not planned["repositories"]:
            info.update(state="not_connected", reason="No verified SE repository scope is configured.")
            continue
        if not part["done"] and used < MAX_READS:
            used += 1
            try:
                result, error = sf_reader(cfg, discovery_query(aid, feed, part["cursor"]))
                if error:
                    raise ValueError(error)
                error = collect.validate_result({"tool": "query_salesforce"}, result)
                if error:
                    raise ValueError(error)
                page = result["records"]
                owner = "Account__c" if feed == "issues" else "Gong__Primary_Account__c"
                if (len(page) > 100 or any(sources.sfid18(r.get(owner)) != aid
                        or not SFID.fullmatch(str(r.get("Id", "")))
                        or (feed == "gong" and r.get("Gong__Is_Private__c") is not False) for r in page)):
                    raise ValueError("Discovery page identity/privacy scope mismatch")
                ids = [r["Id"] for r in page]
                if ids != sorted(set(ids)) or (ids and part["cursor"] and ids[0] <= part["cursor"]):
                    raise ValueError("Discovery cursor did not advance")
                part["rows"].extend(page)
                part["collected_at"] = iso_now()
                part["fetched_at"].update({sid: part["collected_at"] for sid in ids})
                part.update(cursor=ids[-1] if ids else part["cursor"], done=len(page) < 100, error=None)
            except ValueError as exc:
                part["error"] = str(exc)
        info.update(state="ok" if part["done"] else "partial", discovered=len(part["rows"]), collected_at=part.get("collected_at"),
                    discovery_complete=part["done"], next_cursor=None if part["done"] else part["cursor"],
                    reason="Complete account-linked, all-state keyset read." if part["done"] and feed == "issues"
                    else "Complete account-scoped nonprivate Gong read." if part["done"] else "Discovery incomplete; resume the bounded stage.")
        if part.get("error"):
            info.update(state="failed", reason=part["error"])
        if feed == "issues" and not part["done"]:
            tasks.update({f"{aid}:{t['source_id']}": t for t in planned["tasks"] if t["account_id"] == aid})
        seen_calls, seen_issues = set(), set()
        info["unclassified"] = 0
        for row in part["rows"]:
            if feed == "issues":
                match = ISSUE.fullmatch(str(row.get("GitHub_Issue_URL__c") or ""))
                if not match or match[1].casefold() not in {r.casefold() for r in planned["repositories"]}:
                    info["unclassified"] += 1
                    continue
                repository, number = match.groups()
                task = {"account_id": aid, "source_id": f"{repository}#{number}", "url": row["GitHub_Issue_URL__c"],
                        "endpoint": f"repos/{repository}/issues/{number}", "link_id": row["Id"]}
                tasks[f"{aid}:{task['source_id']}"] = task
                seen_issues.add(task["source_id"])
            else:
                seen_calls.add(row["Id"])
                content = "\n\n".join(row[key] for key in ("Gong__Call_Brief__c", "Gong__Call_Highlights_Next_Steps__c",
                                                        "Gong__Call_Key_Points__c") if isinstance(row.get(key), str) and row[key].strip())
                if content:
                    try:
                        record = source_record(aid, "gong", row["Id"], _call_url({"id": row["Id"]}, cfg),
                                               str(row.get("Gong__Call_Start__c") or "")[:10], content, part["fetched_at"][row["Id"]])
                        doc["accounts"][aid]["sources"][row["Id"]] = {**record, "available": True, "read_state": "fresh"}
                    except (ValueError, TypeError) as exc:
                        info.update(state="partial", reason="Some Gong records could not be verified; previous evidence retained.")
                        info.setdefault("errors", []).append(f"{row['Id']}: {exc}")
                elif row["Id"] in doc["accounts"][aid]["sources"]:
                    doc["accounts"][aid]["sources"][row["Id"]]["available"] = False
        if feed == "gong" and part["done"]:
            for sid, source in doc["accounts"][aid]["sources"].items():
                if source["source_type"] == "gong" and sid not in seen_calls:
                    source["available"] = False
        if feed == "issues" and part["done"]:
            for source in doc["accounts"][aid]["sources"].values():
                if source.get("issue_id") and source["issue_id"] not in seen_issues:
                    source["available"] = False
    return list(tasks.values()), used


def hydrate(cfg, reader=None, sf_reader=None):
    """Bounded resumable collector. reader injection is for synthetic tests, not CLI input."""
    planned = _planned()
    doc = load_sources()
    new_plan = doc.get("plan") != planned["run_id"]
    if new_plan:
        doc.update(plan=planned["run_id"], progress={}, discovery={})
        for aid in planned["accounts"]:
            for source in doc["accounts"].get(aid, {}).get("sources", {}).values():
                source["read_state"] = "retained"
    progress = doc.setdefault("progress", {})
    reader = reader or _gh
    remaining = MAX_READS
    for aid in planned["accounts"]:
        saved = doc["accounts"].setdefault(aid, {"sources": {}})
        saved["coverage"] = planned["coverage"][aid]
    if new_plan:
        for row in planned["calls"]:
            doc["accounts"][row["account_id"]]["sources"][row["source_id"]] = {**row, "available": True, "read_state": "fresh"}
    tasks, reads = _discover(planned, doc, cfg, sf_reader or collect.run_sf)
    remaining -= reads
    for task in tasks:
        aid, sid = task["account_id"], task["source_id"]
        key = f"{aid}:{sid}"
        part = progress.setdefault(key, {"page": 1, "done": False, "comments": []})
        if part["done"] or remaining < 2:
            continue
        saved = doc["accounts"][aid]
        info = saved["coverage"]["issues"]
        try:
            remaining -= 1
            issue = reader(task["endpoint"])
            if (not isinstance(issue, dict) or issue.get("html_url") != task["url"]
                    or issue.get("state") not in ("open", "closed") or "pull_request" in issue
                    or not isinstance(issue.get("comments"), int) or not issue.get("updated_at")):
                raise ValueError("Issue identity, state or comment count did not match the planned read")
            if part.get("version") != issue["updated_at"]:
                part.update(version=issue["updated_at"], page=1, comments=[])
            # No date/status cutoff. Comment pages and source version are explicit.
            remaining -= 1
            comments = reader(f"{task['endpoint']}/comments?per_page=100&page={part['page']}")
            if not isinstance(comments, list) or len(comments) > 100:
                raise ValueError("Issue comments response is not a page")
            for comment in comments:
                expected = task["url"] + f"#issuecomment-{comment.get('id')}"
                if (comment.get("html_url") != expected or comment.get("issue_url") !=
                        "https://api.github.com/" + task["endpoint"]):
                    raise ValueError("Comment does not belong to the planned issue")
            part["comments"].extend(comments)
            part["page"] += 1
            if len(comments) == 100:
                continue
            if len({c["id"] for c in part["comments"]}) != issue["comments"]:
                raise ValueError("Comment count changed during pagination; replan this source")
            collected = iso_now()
            texts = [(sid, task["url"], issue.get("body"), issue["updated_at"], "se_issue")]
            texts.extend((f"{sid}:comment:{c['id']}", c["html_url"], c.get("body"),
                          c.get("updated_at") or c.get("created_at"), "se_comment") for c in part["comments"])
            replacements = {}
            for record_id, url, content, observed, kind in texts:
                if content:
                    record = source_record(aid, kind, record_id, url, str(observed)[:10], content, collected,
                                           issue_state=issue["state"], issue_id=sid, link_id=task["link_id"])
                    # normalize_url strips fragments; restore only this validated comment identity.
                    if kind == "se_comment":
                        record["source_url"] = url
                        record["content_hash"] = digest({k: v for k, v in record.items()
                                                         if k not in ("content_hash", "collected_at")})
                    replacements[record_id] = {**record, "available": True, "read_state": "fresh"}
            for old in saved["sources"].values():
                if old.get("issue_id") == sid:
                    old["available"] = False
            saved["sources"].update(replacements)
            part.update(done=True, error=None, collected_at=collected)
        except (ValueError, TypeError, KeyError) as exc:
            part["error"] = str(exc)
            info.setdefault("errors", []).append(str(exc))
    for aid in planned["accounts"]:
        parts = [progress.get(f"{aid}:{t['source_id']}", {}) for t in tasks if t["account_id"] == aid]
        doc["accounts"][aid]["coverage"]["issues"].update(
            hydrated=sum(p.get("done", False) for p in parts), deferred=sum(not p.get("done", False) for p in parts),
            next_pages={t["source_id"]: progress.get(f"{aid}:{t['source_id']}", {}).get("page", 1)
                        for t in tasks if t["account_id"] == aid
                        and not progress.get(f"{aid}:{t['source_id']}", {}).get("done")})
        if any(not p.get("done") for p in parts):
            doc["accounts"][aid]["coverage"]["issues"]["state"] = "partial"
        dates = [p["collected_at"] for p in parts if p.get("collected_at")]
        info = doc["accounts"][aid]["coverage"]["issues"]
        if dates:
            info["collected_at"] = max(dates + ([info["collected_at"]] if info.get("collected_at") else []))
    atomic_write_json(source_path(), doc)
    publish(doc, planned["accounts"])
    return {"reads": MAX_READS - remaining, "coverage": {a: doc["accounts"][a]["coverage"] for a in planned["accounts"]}}


def verify(candidate, sources, today):
    if not isinstance(candidate, dict) or set(candidate) != FIELDS:
        raise ValueError("Stack claim fields do not match schema 1")
    if any(not isinstance(v, str) or not v.strip() for v in candidate.values()):
        raise ValueError("Stack claim fields must be nonempty strings")
    if candidate["category"] not in CATEGORIES or candidate["usage_status"] not in STATUSES:
        raise ValueError("Invalid stack category or usage status")
    aid = candidate["account_id"]
    record = sources.get(aid, {}).get("sources", {}).get(candidate["source_id"])
    if (not record or record["account_id"] != aid or candidate["content_hash"] != record["content_hash"]
            or record.get("available") is False or record.get("read_state") == "retained"):
        raise ValueError("Wrong account, absent source, or changed source version")
    if record["observed_at"] > today or not record["source_url"]:
        raise ValueError("Invalid source date or citation")
    quote = candidate["evidence_quote"].strip()
    content = record["content"]
    if len(quote) > 1200 or quote not in content:
        raise ValueError("Quote is not an exact bounded excerpt from this source version")
    technology = ALIASES.get(candidate["technology"].casefold(), candidate["technology"].strip())
    if len(technology) > 100 or not re.search(r"(?<!\w)" + re.escape(technology) + r"(?!\w)", quote, re.I):
        raise ValueError("Technology is not explicitly named in the quote")
    # Require complete sentences/lines around the excerpt: do not allow removal of negation.
    start = content.index(quote)
    before = content[:start].rstrip(" \t")
    after = content[start + len(quote):].lstrip()
    boundary = (not before or before[-1] in ".!?\n") and (not after or quote[-1] in ".!?\n")
    context = quote.casefold()
    generic = re.search(r"\b(templates?|examples?|samples?|recommend\w*|suggest\w*|hypothetical|instructions|competitor comparison|if we|could use|might use)\b", context)
    fences = re.findall(r"^\s*(?:```|~~~)", content[:start], re.M)
    headings = re.findall(r"^#{1,6}\s+(.+)$", content[:start], re.M)
    field = headings[-1].casefold() if headings else ""
    sample = bool(len(fences) % 2 or re.search(r"```|~~~|<code\b|^\s*[-*]\s*\[\s\]", quote, re.M)
                  or re.search(r"template|example|sample|products in scope|competitors and alternatives|third party integrations|languages", field))
    negative = re.search(r"\b(no longer|not|never|isn't|aren't|wasn't|weren't|cannot|can't|don't|doesn't|didn't|stopped using|discontinued|used to|previously used)\b", context)
    evaluation = re.search(r"\b(evaluat\w*|piloting|trial\w*|testing|consider\w*)\b", context)
    planned = re.search(r"\b(plan\w* to|will use|will adopt|intend\w* to|propos\w*)\b", context)
    tool = re.escape(technology.casefold())
    # Relationship and named technology must be in the same clause, not elsewhere in a paragraph.
    subject = r"(?:we|the customer|customer|our team|the team|teams|they|engineers|developers|the company)"
    used = re.search(r"\b" + subject + r"\s+(?:currently\s+)?(?:are\s+)?(?:uses?|using|runs?|running|deployed|relies on)\s+" + tool + r"\b", context)
    historical = re.search(r"\b" + subject + r"\s+(?:no longer use|stopped using|used to use|previously used|discontinued)\s+" + tool + r"\b", context)
    evaluating = re.search(r"\b" + subject + r"\s+(?:are\s+)?(?:evaluating|piloting|trialing|testing|considering)\s+" + tool + r"\b", context)
    future = re.search(r"\b" + subject + r"\s+(?:plan to use|plan to adopt|will use|will adopt|intend to use)\s+" + tool + r"\b", context)
    structured = re.fullmatch(r"### (?:Current SCM|Current technology stack)\s*\n+\s*" + tool + r"\s*\.?", quote, re.I)
    status = candidate["usage_status"]
    supported = {"reported_in_use": bool((used or structured) and not (negative or evaluation or planned or generic)),
                 "evaluating": bool(evaluating and not (negative or generic)),
                 "planned": bool(future and not (negative or generic)),
                 "historical": bool(historical and not generic), "mention_only": True}[status]
    verified = boundary and supported and not sample and ("?" not in quote or status == "mention_only")
    return {**candidate, "schema": SCHEMA, "technology": technology,
            **{k: record[k] for k in ("source_type", "source_url", "observed_at", "collected_at")},
            "verification_state": "quote_verified" if verified else "ambiguous",
            "usage_status": status if verified else "mention_only",
            "reason": None if verified else "Context does not safely establish the requested relationship.",
            "id": digest([aid, technology, candidate["source_id"], candidate["content_hash"], quote]),
            "source_current": True}


def publish(doc, ids, accepted=()):
    store = seller.load()
    for aid in ids:
        feeds = store["accounts"].setdefault(aid, {})
        previous = feeds.get("tech_stack", {})
        rows = {r["id"]: r for r in previous.get("rows", []) if r.get("schema") == SCHEMA and r.get("id")}
        rows.update({r["id"]: r for r in accepted if r["account_id"] == aid})
        sources = doc["accounts"].get(aid, {}).get("sources", {})
        for row in rows.values():
            source = sources.get(row["source_id"], {})
            row["source_current"] = source.get("content_hash") == row["content_hash"] and source.get("available") is not False
            row["source_state"] = source.get("read_state", "retained")
        info = doc["accounts"].get(aid, {}).get("coverage", {})
        complete = info and all(v["state"] == "ok" for v in info.values())
        dates = [r["collected_at"] for r in sources.values() if r.get("read_state") == "fresh"]
        dates.extend(v["collected_at"] for v in info.values() if v.get("collected_at"))
        feeds["tech_stack"] = {"rows": list(rows.values()), "state": ("ok" if rows else "no_rows") if complete else "partial", "coverage": info,
                              "collected_at": max(dates) if dates else previous.get("collected_at"),
                              "as_of": max((r["observed_at"] for r in rows.values()), default=None),
                              "reason": ("Complete linked SE / nonprivate Gong discovery. " if complete else "Discovery or hydration incomplete; previous evidence retained. ")
                              + "Quotes describe reported use as of their source date, not today's deployment."}
    atomic_write_json(seller.store_path(), store)


def ingest(candidates, today):
    planned = _planned()
    if not isinstance(candidates, list) or len(candidates) > MAX_CLAIMS:
        raise ValueError("Expected at most 500 stack claims")
    doc = load_sources()
    if doc.get("plan") != planned["run_id"]:
        raise ValueError("Hydrate this evidence plan before extraction")
    if any(c.get("account_id") not in planned["accounts"] for c in candidates if isinstance(c, dict)):
        raise ValueError("Claim is outside the activity cohort")
    accepted = [verify(c, doc["accounts"], today) for c in candidates]
    publish(doc, planned["accounts"], accepted)
    return {"accepted": len(accepted), "ambiguous": sum(r["verification_state"] != "quote_verified" for r in accepted)}
