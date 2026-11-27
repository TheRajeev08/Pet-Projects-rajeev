"""Seller enrichment: read-only plans, provenance-checked collection, account coverage.

No CRM writes, email sends, action-state writes or refresh scheduling. Store only beneath
CP_HOME; test with an isolated home. Missing rows are unknown, including successful empties.
"""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
from urllib.parse import quote

from . import collect, paths, sources
from .seller_queries import FEEDS, LABELS, render
from .util import atomic_write_json, decode_dynamic, iso_now, num, read_json

STATES = {"ok", "no_rows", "not_connected", "not_authorized", "failed", "partial", "stale"}
TOOLS = {
    "github_accounts": "get_salesforce_account",
    "issues": "get_salesforce_account_issues",
    "gong": "get_account_gong_calls", "notes": "get_account_notes",
    "partners": "get_account_partners", "hierarchy": "get_account_hierarchy",
}
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
SF_RECORD = "https://github.lightning.force.com/lightning/r/{kind}/{id}/view"
STACK_FIELDS = {
    "coding_languages": "Languages", "coding_languages_other": "Languages",
    "current_ci_tools_in_use_to_test_code": "CI", "current_ci_tools_in_use_to_test_other": "CI",
    "existing_sast_tooling_other": "SAST", "existing_sca_tooling": "SCA",
    "existing_sca_tooling_other": "SCA", "existing_secret_scanning_tool": "Secret scanning",
    "existing_secret_scanning_tool_other": "Secret scanning",
}


def store_path():
    return os.path.join(paths.home(), "seller", "feeds.json")


def plan_path(group):
    return os.path.join(paths.home(), "seller", f"plan-{group}.json")


def number(value):
    return None if isinstance(value, bool) else num(value)


def known_sum(values):
    known = [number(v) for v in values if number(v) is not None]
    return sum(known) if known else None


def source_name(feed):
    return ".".join(FEEDS[feed][:2]) if feed in FEEDS else LABELS[feed]


def coverage(feed, entry, today):
    entry = entry or {}
    state = entry.get("state", "not_connected" if feed == "tech_stack" else "missing")
    observed = entry.get("as_of")
    collected = entry.get("collected_at", "")[:10] or None
    # Monthly facts legitimately describe a closed month. Collection freshness is separate.
    limit = 45 if feed in ("invoices", "model_cost", "selfserve", "mrr") else 7
    reference = collected if feed in ("invoices", "model_cost", "tech_stack") else observed or collected
    if state in ("ok", "no_rows") and reference and (dt.date.fromisoformat(today) - dt.date.fromisoformat(reference)).days > limit:
        state = "stale"
    reason = entry.get("reason")
    if not entry:
        reason = "No verified SE issue or Gong stack evidence collected." if feed == "tech_stack" else "Internal feed has not been collected."
    if state == "no_rows":
        reason = "No contacts returned." if feed == "contacts" else "No rows for this account in the selected scope; this does not prove zero."
        if FEEDS.get(feed, (None,))[0] == "gainsight":
            reason += " Activity may be recorded on another CO-covered hierarchy account."
    return {"source": source_name(feed), "state": state, "as_of": observed, "collected_at": collected,
            "reason": reason, "rows": len(entry.get("rows") or []), "readiness": "Internal feed",
            **({"coverage": entry["coverage"]} if "coverage" in entry else {})}


def load():
    doc = read_json(store_path())
    if doc is None:
        return {"schema": 1, "accounts": {}}
    if not isinstance(doc, dict) or doc.get("schema") != 1 or not isinstance(doc.get("accounts"), dict):
        raise ValueError("Invalid seller feed store")
    return doc


def _task(feed, account_ids, tool, args):
    key = hashlib.sha256(json.dumps([feed, account_ids, tool, args], sort_keys=True).encode()).hexdigest()[:20]
    return {"id": key, "feed": feed, "account_ids": account_ids, "tool": tool, "args": args}


DAILY_ACCOUNTS, TOP_TIER, TAIL_SLOTS = 10, 50, 2
ACTIVITY_FEEDS = (*TOOLS, "contacts")


def _last_activity(doc, aid):
    feeds = doc["accounts"].get(aid, {})
    stamps = [str(feeds.get(f, {}).get(k) or "") for f in ACTIVITY_FEEDS for k in ("collected_at", "attempted_at")]
    return max(stamps)


def _rotate(ranked, doc):
    """Least-recently attempted first within each tier, so no owned account starves.

    Ranked order (ARR desc, missing ARR last, then name and ID) defines the top tier and breaks
    freshness ties there; the tail breaks ties by Salesforce ID so ARR changes cannot reorder it.
    """
    top, tail = ranked[:TOP_TIER], sorted(ranked[TOP_TIER:], key=lambda a: a["id"])
    tail_slots = min(TAIL_SLOTS, len(tail))
    top_slots = min(DAILY_ACCOUNTS - tail_slots, len(top))
    tail_slots = min(len(tail), DAILY_ACCOUNTS - top_slots)
    stale = lambda tier, n: sorted(tier, key=lambda a: _last_activity(doc, a["id"]))[:n]
    picked = {a["id"] for a in stale(top, top_slots) + stale(tail, tail_slots)}
    cycle = lambda n, k: -(-n // k) if k else None
    return [a for a in ranked if a["id"] in picked], {
        "top_accounts": len(top), "top_slots": top_slots, "top_cycle_days": cycle(len(top), top_slots),
        "other_accounts": len(tail), "other_slots": tail_slots, "other_cycle_days": cycle(len(tail), tail_slots),
        "never_attempted": sum(1 for a in ranked if not _last_activity(doc, a["id"]))}


def _activity_cohort(ranked, today):
    planned = read_json(plan_path("activity")) or {}
    if planned.get("group") != "activity" or planned.get("date") != today or planned.get("rotation") is None:
        raise ValueError("Plan today's activity group first; budgets and mail reuse its account cohort")
    ids = set(planned.get("accounts") or [])
    return [a for a in ranked if a["id"] in ids]


def plan(model, cfg, today, group="analytics", account_id=None, *, resume=False, session=None):
    """Analytics cover the book. Activity refreshes ten owned accounts a day: least-recently
    attempted first, eight slots for the ARR-ranked top fifty and two for the rest of the book.
    Budgets and mail reuse the same day's activity cohort."""
    from .queries import SFDC_ID

    dt.date.fromisoformat(today)
    if group not in ("analytics", "activity", "budgets", "mail"):
        raise ValueError("Unknown seller collection group")
    accounts = [a for a in model["accounts"] if a.get("in_book") is not False]
    if any(not SFDC_ID.fullmatch(a["id"]) for a in accounts):
        raise ValueError("Refresh the canonical Salesforce book before planning internal feeds")
    accounts.sort(key=lambda a: (a.get("arr") is None, -(a.get("arr") or 0), a.get("name") or "", a["id"]))
    if account_id:
        accounts = [a for a in accounts if a["id"] == account_id]
        if not accounts:
            raise ValueError("Requested account is outside the owned book")
    if resume:
        planned = _active_plan(group)
        if (planned["date"] != today or planned.get("account") != account_id
                or planned.get("session") != session
                or not set(planned["accounts"]) <= {a["id"] for a in accounts}):
            raise ValueError("Cannot resume a plan from another date, session or account scope")
        return planned
    doc = load()
    rotation = None
    if not account_id and group == "activity":
        accounts, rotation = _rotate(accounts, doc)
    elif not account_id and group != "analytics":
        accounts = _activity_cohort(accounts, today)
    nonce = secrets.token_hex(8)
    tasks, skipped = [], []
    ids = [a["id"] for a in accounts]
    if group == "analytics":
        for feed in FEEDS:
            for start in range(0, len(ids), 20):
                batch = ids[start:start + 20]
                tasks.append(_task(feed, batch, "query_kusto", render(feed, batch, today, nonce)))
    for a in accounts if group == "activity" else []:
        aid = a["id"]
        for feed, tool in TOOLS.items():
            args = {"account_id": aid}
            if feed in ("issues", "pocs", "gong", "partners"):
                args["limit"] = 100
            tasks.append(_task(feed, [aid], tool, args))
        tasks.append(_task("contacts", [aid], "query_salesforce", {
            "query": f"SELECT Id, Name, Email, Title, AccountId, LastActivityDate FROM Contact WHERE AccountId = '{aid}'"}))
    for a in accounts if group == "budgets" else []:
        aid = a["id"]
        saved = doc["accounts"].get(aid, {}).get("github_accounts") or {}
        links = saved.get("rows", []) if coverage("github_accounts", saved, today)["state"] == "ok" else []
        if not saved:
            links = a.get("seller", {}).get("ready", {}).get("enterprises") or []
        for link in links:
            if link.get("namespace") in ("enterprise", "proxima") and isinstance(link.get("slug"), str):
                tasks.append(_task("budgets", [aid], "get_budgets", {k: link[k] for k in ("namespace", "slug")}))
        if not links:
            skipped.append({"feed": "budgets", "account_id": aid, "reason": "No unambiguous enterprise mapping; budget scope is unknown."})
    for a in accounts if group == "mail" else []:
        aid = a["id"]
        saved = doc["accounts"].get(aid, {}).get("contacts") or {}
        # Only the latest successful, fresh Salesforce contact read counts as verified.
        contacts = (saved.get("rows") or []) if coverage("contacts", saved, today)["state"] == "ok" else []
        addresses = sorted({r["email"].lower() for r in contacts if isinstance(r.get("email"), str) and EMAIL.fullmatch(r["email"])})
        # Never broaden to a company-name or whole-mailbox search when contacts are missing.
        if not addresses:
            skipped.append({"feed": "emails", "account_id": aid, "reason": "No current verified contact email addresses; collect contacts first."})
            continue
        for address in addresses:
            since = (dt.date.fromisoformat(today) - dt.timedelta(days=180)).isoformat()
            search = quote(f'"participants:{address} AND ((received>={since} AND received<={today}) OR (sent>={since} AND sent<={today}))"', safe="")
            url = f"/me/messages?$search={search}&$select=id,subject,conversationId,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,webLink&$top=100"
            task = _task("emails", [aid], "fetch", {"entityUrls": [url]})
            task["contact_emails"] = [address]
            tasks.append(task)
        # Outlook calendar reads cannot be scoped to attendees at source, so meetings come only
        # from account-scoped Gong calls collected in the activity group.
    result = {"schema": 1, "group": group, "date": today, "started_at": iso_now(), "run_id": nonce,
              "account": account_id, "session": session,
              "accounts": ids, "tasks": tasks, "skipped": skipped, "rotation": rotation,
              "note": "Read-only. Prefer Salesforce CLI for contacts. Run exact arguments, then seller-collect. No email bodies. No customer-admin reports."}
    atomic_write_json(plan_path(group), result, indent=2)
    return result


def _list_payload(result, keys):
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        raise ValueError("Unrecognized internal response shape")
    if result.get("error") or result.get("isError"):
        raise ValueError("Internal source returned an error; no rows accepted")
    for key in keys:
        if isinstance(result.get(key), list):
            return result[key]
        if key in result and result[key] is None:
            return []
    if isinstance(result.get("result"), (dict, list)):
        return _list_payload(result["result"], keys)
    raise ValueError("Unrecognized internal response shape; adapter needs verification")


def _pick(row, *keys):
    return next((row[k] for k in keys if row.get(k) is not None and row[k] != ""), None)


def _plain(value):
    return value if isinstance(value, (str, int, float, bool)) else None


def _record(row, feed):
    if not isinstance(row, dict):
        raise ValueError("Internal records must be objects")
    fields = {
        "id": ("id", "Id", "salesforce_id", "gsid", "call_id"), "title": ("title", "Name", "name", "subject", "Subject", "partner_name"),
        "date": ("date", "CreatedDate", "created_date", "created_at", "call_start", "start_time", "ActivityDate"),
        "updated": ("updated_at", "LastModifiedDate"),
        "status": ("status", "Status", "github_state", "GitHub_State__c", "ghas_poc_status", "Status__c", "Stage__c"),
        "url": ("url", "github_issue_url", "github_issue_link", "opportunity_url", "URL__c", "GitHub_Issue_URL__c", "recording_url", "webLink"),
        "description": ("description", "Description", "Description__c", "text", "content", "call_brief", "reason_opp_is_ready", "partner_value_added"),
        "email": ("email", "Email"), "role": ("role", "Title"),
        "parent_id": ("ParentId", "parent_id"), "name": ("name", "Name"),
    }
    out = {key: _plain(_pick(row, *aliases)) for key, aliases in fields.items()}
    if feed == "issues":
        out["se_issue"] = bool(re.fullmatch(r"https://github\.com/github/solutions-engineering/issues/[1-9][0-9]*", str(out.get("url") or ""), re.I))
    if out["id"] is None:
        raise ValueError("Internal record lacks a stable ID; adapter needs verification")
    out["id"] = str(out["id"])
    out["date"] = sources.iso(out["date"])
    out["updated"] = sources.iso(out["updated"])
    out["account_id"] = sources.sfid18(_pick(row, "account_id", "primary_account_id", "AccountId"))
    if feed == "contacts":
        out["last_activity"] = sources.iso(row.get("LastActivityDate"))
    if feed == "gong":
        participants = row.get("participants") or []
        if row.get("participants_emails"):
            participants = [{"email": email.strip()} for email in row["participants_emails"].split(",") if EMAIL.fullmatch(email.strip())]
        if not isinstance(participants, list):
            raise ValueError("Gong participants have an unrecognized shape")
        out["participants"] = [{k: _plain(p.get(k)) for k in ("name", "email", "title", "affiliation")}
                               for p in participants if isinstance(p, dict)]
    if feed == "budgets":
        for key in ("scope", "product", "limit_type", "currency"):
            out[key] = _plain(row.get(key))
        for key in ("target", "spend", "amount"):
            out[key] = number(row.get(key))
    if feed == "pocs":
        out["date"] = sources.iso(row.get("end_date"))
        out["title"] = out["title"] or "Proof of concept"
        out["stack"] = [{"category": category, "value": row[key], "field": key} for key, category in STACK_FIELDS.items()
                        if isinstance(row.get(key), str) and row[key].strip() and row[key].strip().lower() not in ("none", "n/a", "unknown", "other")]
    return out


def _graph(result):
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        result = result["results"]
    envelopes = result if isinstance(result, list) else [result]
    if len(envelopes) != 1 or not isinstance(envelopes[0], dict):
        raise ValueError("Unrecognized WorkIQ response")
    result = envelopes[0]
    if result.get("statusCode", 200) != 200:
        raise ValueError("WorkIQ metadata read failed")
    for key in ("body", "data", "result", "response"):
        if isinstance(result.get(key), dict):
            result = result[key]
            break
    if not isinstance(result.get("value"), list):
        raise ValueError("WorkIQ metadata response lacks a value list")
    if result.get("@odata.nextLink") or len(result["value"]) >= 100:
        raise ValueError("WorkIQ page incomplete; narrow the contact/date window before collection")
    return result["value"]


def decode(task, result):
    feed = task["feed"]
    if feed in FEEDS:
        error = collect.validate_result({"tool": "query_kusto"}, result)
        if error:
            raise ValueError(error)
        out = {}
        for row in sources.kusto_rows({"result": result}):
            aid = sources.sfid18(row.get("account_id"))
            if aid not in task["account_ids"] or aid in out:
                raise ValueError("Duplicate or out-of-scope account in analytics result")
            data = decode_dynamic(row.get("data"))
            if not isinstance(data, list) or row.get("row_count") != len(data) or len(data) >= 10000:
                raise ValueError("Truncated or malformed analytics bag; split the query window")
            if any(not isinstance(r, dict) or sources.sfid18(r.get("account_id")) != aid for r in data):
                raise ValueError("Analytics bag contains an out-of-scope record")
            out[aid] = {"rows": data, "as_of": sources.iso(row.get("as_of"))}
        return {aid: out.get(aid, {"rows": [], "as_of": None}) for aid in task["account_ids"]}
    aid = task["account_ids"][0]
    if feed == "github_accounts":
        if isinstance(result, dict) and isinstance(result.get("account"), dict):
            result = result["account"]
        if not isinstance(result, dict) or sources.sfid18(result.get("Id")) != aid:
            raise ValueError("Enterprise mapping returned outside the selected account")
        records = result.get("github_accounts", [])
        if not isinstance(records, list):
            raise ValueError("Unrecognized enterprise mapping")
        rows = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("slug"), str) or not record["slug"]:
                raise ValueError("Enterprise mapping lacks a verified slug")
            if record.get("namespace") in ("enterprise", "proxima"):
                rows.append({"id": f"{record['namespace']}:{record['slug']}", "slug": record["slug"], "namespace": record["namespace"]})
        return {aid: {"rows": rows, "as_of": None}}
    if feed == "emails":
        records = _graph(result)
        verified = {e.lower() for e in task["contact_emails"]}
        rows = []
        for r in records:
            if not isinstance(r, dict) or not r.get("id"):
                raise ValueError("WorkIQ record lacks a stable ID")
            addresses = [r.get("from", {})] + (r.get("toRecipients") or []) + (r.get("ccRecipients") or [])
            people = {}
            for p in addresses:
                address = (p.get("emailAddress") or {}) if isinstance(p, dict) else {}
                email = str(address.get("address") or "").lower()
                # Keep only verified customer contacts; other recipients are never stored.
                if email in verified:
                    person = people.setdefault(email, {"email": email, "name": None})
                    person["name"] = person["name"] or address.get("name")
            if not people:
                continue
            rows.append({"id": r["id"], "title": r.get("subject"), "date": sources.iso(r.get("receivedDateTime") or r.get("sentDateTime")),
                         "thread": r.get("conversationId"), "url": r.get("webLink"), "people": list(people.values())})
        return {aid: {"rows": rows, "as_of": None}}
    if feed == "contacts":
        err = collect.validate_result({"tool": "query_salesforce"}, result)
        if err:
            raise ValueError(err)
        records = result["records"]
        if any(sources.sfid18(r.get("AccountId")) != aid for r in records):
            raise ValueError("Contacts returned outside the selected account")
    else:
        if feed == "notes" and isinstance(result, dict) and isinstance(result.get("documents"), dict):
            records = [{"id": hashlib.sha256(title.encode()).hexdigest(), "title": title, "description": content}
                       for title, content in result["documents"].items() if isinstance(content, str)]
            if len(records) != result.get("total_documents"):
                raise ValueError("Notes response has incomplete documents")
        else:
            records = _list_payload(result, ("records", feed, "calls", "proofs_of_concept", "accounts", "documents", "relationships", "items"))
        if len(records) >= task["args"].get("limit", 1000):
            raise ValueError("Internal response reached its limit; full coverage is unknown")
        if isinstance(result, dict) and (result.get("has_more") or result.get("next_page") or result.get("nextPageToken") or result.get("omitted_results")
                                         or result.get("total_count", len(records)) > len(records)):
            raise ValueError("Internal source response is paginated; full coverage is unknown")
    for i, row in enumerate(records):
        owner = _pick(row, "account_id", "primary_account_id", "AccountId")
        if feed != "hierarchy" and owner and sources.sfid18(owner) != aid:
            raise ValueError("Activity returned outside the selected account")
        if feed == "pocs" and not _pick(row, "id", "salesforce_id"):
            # The current Revenue PoC schema does not populate SalesforceID. Keep every record.
            records[i] = {**row, "id": f"observed:{i}:{hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]}"}
    if feed == "hierarchy":
        records = [r for r in records if sources.sfid18(_pick(r, "Id", "id")) != aid]
    if feed == "budgets":
        if any(not all(k in r for k in ("budget_name", "target_name", "resource_type", "target_amount", "current_amount", "budget_limit_type")) for r in records):
            raise ValueError("Unrecognized enterprise budget contract")
        scope = f"{task['args']['namespace']}:{task['args']['slug']}"
        records = [{"id": f"{scope}:{i}", "title": r["budget_name"], "product": r["budget_name"],
                    "scope": f"{scope} / {r['resource_type']}: {r['target_name']}", "limit_type": r["budget_limit_type"],
                    "target": r["target_amount"], "spend": r["current_amount"], "currency": None}
                   for i, r in enumerate(records) if str(r["resource_type"]).lower() != "user"]
    return {aid: {"rows": [_record(r, feed) for r in records], "as_of": None}}


def _active_plan(group):
    planned = read_json(plan_path(group))
    if not planned or planned.get("group") != group:
        raise ValueError("Run seller-plan first")
    if (dt.datetime.now(dt.timezone.utc) - collect._parse_ts(planned["started_at"])).total_seconds() > 86400:
        raise ValueError("Seller plan expired; start a fresh plan")
    return planned


def collect_plan(cfg, group, session, statuses=None, results=None, use_sf=True):
    planned = _active_plan(group)
    if planned.get("session") and planned["session"] != session:
        raise ValueError("Collect the seller plan in the session that planned the reads")
    tasks = {t["id"]: t for t in planned["tasks"]}
    statuses = statuses or {}
    if any(key not in tasks or val not in STATES - {"ok", "no_rows"} for key, val in statuses.items()):
        raise ValueError("Status must reference a planned task and a failure/coverage state")
    results = results if results is not None else collect.tool_results(
        collect.iter_events(collect.events_path(session), collect._parse_ts(planned["started_at"])))
    found, errors = {}, {}
    for timestamp, tool, args, result in results:
        if collect._parse_ts(timestamp) is None or collect._parse_ts(timestamp) < collect._parse_ts(planned["started_at"]):
            continue
        for key, task in tasks.items():
            if not tool.endswith(task["tool"]) or args != task["args"]:
                continue
            try:
                found[key] = decode(task, result)
                errors.pop(key, None)
            except ValueError as exc:
                found.pop(key, None)
                errors[key] = str(exc)
    for key, task in tasks.items():
        if use_sf and task["tool"] == "query_salesforce" and key not in found:
            result, err = collect.run_sf(cfg, task["args"]["query"])
            if err:
                errors[key] = "Salesforce CLI read failed; run the planned SOQL through Revenue MCP"
            else:
                try:
                    found[key] = decode(task, result)
                except ValueError as exc:
                    errors[key] = str(exc)
    doc = load()
    groups = {}
    for key, task in tasks.items():
        for aid in task["account_ids"]:
            groups.setdefault((aid, task["feed"]), []).append(key)
    for (aid, feed), keys in groups.items():
        previous = doc["accounts"].setdefault(aid, {}).get(feed) or {}
        missing = [key for key in keys if key not in found or key in statuses or key in errors]
        if missing:
            state = next((statuses[key] for key in missing if key in statuses), "failed")
            reason = next((errors[key] for key in missing if key in errors), "Collection incomplete; previous rows retained with their original date.")
            doc["accounts"][aid][feed] = {**previous, "state": state, "reason": reason, "attempted_at": iso_now()}
            continue
        rows = [r for key in keys for r in found[key][aid]["rows"]]
        if feed not in FEEDS:
            rows = list({r["id"]: r for r in rows}.values())
        dates = [found[key][aid]["as_of"] for key in keys if found[key][aid]["as_of"]]
        doc["accounts"][aid][feed] = {"rows": rows, "state": "ok" if rows else "no_rows",
                                    "as_of": max(dates) if dates else None, "collected_at": iso_now(), "run_id": planned["run_id"],
                                    "provenance": {"account_id": aid, "tasks": keys, "group": group}}
    for skipped in planned.get("skipped", []):
        feeds = doc["accounts"].setdefault(skipped["account_id"], {})
        feeds[skipped["feed"]] = {**feeds.get(skipped["feed"], {}), "state": "partial", "reason": skipped["reason"]}
    atomic_write_json(store_path(), doc)
    return {"tasks": len(tasks), "collected": len(found), "missing": len(tasks) - len(found), "errors": errors,
            "coverage_states": {state: sum(1 for a in doc["accounts"].values() for e in a.values() if e.get("state") == state) for state in STATES}}


def ready_fields(account, other, dashboard):
    other = other or {}
    missing = dashboard.get("missing_sources") or []
    ready = {}
    for query, keys in {
        "02": ("copilot_aiu_billed_lcm", "copilot_aiu_billed_ltm", "copilot_billed_lcm", "deployment"),
        "09": ("copilot_arr",),
        "16": ("forecast_bill", "forecast_bill_low", "forecast_bill_high"),
        "17": ("ghe_potential_seats", "ghe_som_annual", "ghas_potential_seats", "ghas_som_annual"),
    }.items():
        for key in keys:
            ready[key] = other.get(key) if query not in missing else None
        ready[f"q{query}_as_of"] = (dashboard.get("asof") or {}).get(query)
    ready["readiness"] = "Ready now"
    ready["enterprises"] = []
    for enterprise in dashboard.get("enterprises") or []:
        match = enterprise.get("match") or {}
        candidates = match.get("candidates") or []
        if match.get("confidence") in ("confirmed", "single") and len(candidates) == 1 and candidates[0].get("id") == account["id"]:
            if enterprise.get("namespace") in ("enterprise", "proxima"):
                ready["enterprises"].append({"slug": enterprise.get("slug"), "namespace": enterprise["namespace"]})
    return ready


def enrich(accounts, cfg, today, dashboard=None, store=None):
    dashboard = dashboard if dashboard is not None else read_json(paths.copilot_model_path(cfg)) or {}
    if not isinstance(dashboard, dict) or not isinstance(dashboard.get("accounts", []), list):
        raise ValueError("Copilot model has an invalid shape for seller enrichment")
    other = {a["id"]: a for a in dashboard.get("accounts", [])}
    doc = store if store is not None else load()
    for account in accounts:
        saved = doc["accounts"].get(account["id"], {})
        account["seller"] = {
            "ready": ready_fields(account, other.get(account["id"]), dashboard),
            "feeds": {feed: {"records": saved.get(feed, {}).get("rows") or [], **coverage(feed, saved.get(feed), today)}
                      for feed in LABELS},
        }
    return accounts


def internal_signals(accounts):
    out = []
    for a in accounts:
        for feed, source in (("risks", "gainsight"), ("support", "support")):
            entry = a["seller"]["feeds"][feed]
            if entry["state"] != "ok":
                continue
            for r in entry["records"]:
                status = str(r.get("status") or "").lower()
                if not status or status.startswith("closed") or status == "solved":
                    continue
                if feed == "support" and str(r.get("priority") or "").lower() not in ("urgent", "high", "p1", "p2"):
                    continue
                day = sources.iso(r.get("date"))
                if not day or not r.get("id"):
                    continue
                url = (f"https://github.us2.gainsightcloud.com/v1/ui/cockpit#/list/cta/{quote(str(r['id']), safe='')}"
                       if feed == "risks" else f"https://github.zendesk.com/agent/tickets/{quote(str(r['id']), safe='')}")
                out.append({"id": f"seller:{source}:{a['id']}:{r['id']}", "account_id": a["id"], "account_name": a["name"],
                            "source": source, "source_id": str(r["id"]), "date": day, "title": r.get("title") or LABELS[feed],
                            "evidence": r.get("reason") or f"Current source status: {r['status']}; priority: {r.get('priority') or 'No data'}",
                            "url": url, "priority": "High", "verified": True, "new": False, "status": "open",
                            "play": "Resolve account risk" if feed == "risks" else "Review support escalation",
                            "reason": r.get("reason"), "confidence": "Source record",
                            "themes": [], "competitors": [], "read_only": True, "as_of": entry["as_of"]})
    return out
