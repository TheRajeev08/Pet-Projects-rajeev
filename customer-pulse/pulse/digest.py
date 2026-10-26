"""Morning email, built from model.json.

`build_digest` returns the exact JSON body for WorkIQ `do_action` (`/me/sendMail`) plus a meta
sidecar listing the ids the email covers. `mark_sent` records those ids in notified.json only
after the session log shows the send succeeded, so each change is emailed once.

Everything in the email comes from fixed templates filled with numbers and names; all text is
HTML-escaped and the only links are https Salesforce record links.
"""
import datetime as dt
import glob
import html
import json
import os

from . import paths
from .collect import events_path, iter_events, since_filter, tool_results
from .model import load_notified, notified_path
from .util import atomic_write_json, human_day, iso_now, read_json

MAX_ITEMS = 10
# The agent relays the body file through a tool call, so it must stay well under the 20 KB read limit.
MAX_BODY_BYTES = 14000
KEEP_NOTIFIED_DAYS = 120
PRI_COLOR = {"High": "#cf222e", "Medium": "#9a6700", "Notify": "#0969da", "Info": "#57606a"}
STATE_LABEL = {"failed": "Failed", "missing": "Missing", "suspect": "Suspect", "stale": "Stale", "ok": "OK"}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def _e(s):
    return html.escape("" if s is None else str(s), quote=True)


def _acct_link(name, url):
    if url and str(url).startswith("https://"):
        return f'<a href="{_e(url)}" style="color:#0969da">{_e(name)}</a>'
    return f"<b>{_e(name)}</b>"


def _pill(priority):
    return f'<b style="color:{PRI_COLOR.get(priority, "#57606a")}">{_e(priority)}</b> · '


def _section(title, rows, more=0, note=None):
    if not rows:
        return ""
    items = "".join(f"<li>{r}</li>" for r in rows)
    extra = f'<p style="margin:4px 0 0;color:#57606a;font-size:12px">+{more} more in the dashboard</p>' if more else ""
    sub = f'<p style="margin:0 0 6px;color:#57606a;font-size:12px">{_e(note)}</p>' if note else ""
    return (f'<h3 style="font-size:15px;margin:18px 0 6px">{_e(title)}</h3>{sub}'
            f'<ul style="padding-left:18px;margin:0;line-height:1.6">{items}</ul>{extra}')


def _cap(rows, limit=MAX_ITEMS):
    return rows[:limit], max(0, len(rows) - limit)


def _event_when(e):
    """Event text without its ' since <day>' suffix, plus when it happened ('on <day>' or 'between <a> and <b>')."""
    text, since = e["text"], e.get("since")
    suffix = f" since {since}" if since else None
    if suffix and text.endswith(suffix):
        return text[:-len(suffix)], f"between {human_day(since)} and {human_day(e['date'])}"
    return text, f"on {human_day(e['date'])}"


def _issue_feeds(model):
    return [f for f in model.get("sources") or [] if f["state"] != "ok"]


def build_digest(model, cfg, status=None, today=None):
    """Return (json_body, meta) for the given model."""
    to = cfg.get("digest_to")
    if not to:
        raise SystemExit("Config is missing 'digest_to' (your own email address).")
    date = today or model["date"]
    urls = {a["id"]: a.get("sfdc_url") for a in model["accounts"]}
    events = [e for e in model["events"] if e.get("new")]
    actions = [a for a in model["actions"] if a.get("new")]
    high = [a for a in actions if a["priority"] == "High"] + [e for e in events if e["priority"] == "High"]
    issues = _issue_feeds(model)
    build_error = (status or {}).get("build_error")
    stale_model = model["date"] != date

    subject = f"Customer Pulse · {date} · {len(high)} high"
    if issues or build_error or stale_model:
        subject += " · data issues"

    parts = [f'<div style="font-family:{FONT};font-size:14px;color:#1f2328;max-width:720px">',
             f'<h2 style="font-size:18px;margin:0 0 4px">Customer Pulse · {_e(date)}</h2>']
    k = model["kpis"]["overall"]
    parts.append(f'<p style="margin:0 0 8px;color:#57606a">{k["accounts"]} accounts · {len(high)} new high · '
                 f'{sum(1 for e in events if e["priority"] != "Info")} new seat changes · '
                 f'{len(actions)} new risks and opportunities</p>')

    if issues or build_error or stale_model:
        rows = []
        if build_error:
            rows.append(f"<b>Build failed</b>: {_e(build_error[:300])}. The dashboard still shows the previous data.")
        if stale_model:
            rows.append(f"<b>Dashboard</b>: last built for {_e(model['date'])}; today's refresh did not complete.")
        for f in issues:
            msg = "; ".join(f["messages"]) or STATE_LABEL[f["state"]]
            rows.append(f'<b>{_e(f["label"])}</b>: {_e(STATE_LABEL[f["state"]])}. {_e(msg)}.')
        parts.append(_section("Data issues", rows))

    def action_row(a, pill=True):
        return (f'{_pill(a["priority"]) if pill else ""}{_acct_link(a["account_name"], urls.get(a["account_id"]))} · '
                f'{_e(a["title"])}. {_e(a["why"])}')

    def event_row(e, pill=True):
        text, when = _event_when(e)
        return (f'{_pill(e["priority"]) if pill else ""}{_acct_link(e["account_name"], urls.get(e["account_id"]))} · '
                f'{_e(text)} {_e(when)}')

    changes = [e for e in events if e["priority"] in ("Medium", "Notify")]
    med = [a for a in actions if a["priority"] != "High"]
    lists = [("High priority", [action_row(a, False) if "rule" in a else event_row(a, False) for a in high]),
             ("Seat changes", [event_row(e) for e in changes]),
             ("New risks and opportunities", [action_row(a) for a in med])]

    tail = []
    if not high and not changes and not med:
        tail.append('<p style="margin:14px 0">No new changes since the last email.</p>')
    asof = [f'{_e(f["label"])} {_e(f.get("as_of") or "—")}' for f in model.get("sources") or []]
    tail.append(f'<p style="margin:18px 0 4px;color:#57606a;font-size:12px">Data as of: {" · ".join(asof)}</p>')
    if model.get("baseline"):
        tail.append('<p style="margin:4px 0;color:#57606a;font-size:12px">First run: earlier changes are in the '
                    'dashboard and are not listed here. From tomorrow, every new change is emailed once.</p>')
    tail.append('<p style="margin:4px 0;color:#57606a;font-size:12px">Open the Customer Pulse canvas in the GitHub '
                'Copilot app for details and drill-downs.</p></div>')

    for limit in (MAX_ITEMS, 6, 3):
        sections = [_section(title, *_cap(rows, limit)) for title, rows in lists]
        body = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": "".join(parts + sections + tail)},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": True,
        }
        if len(json.dumps(body, indent=2)) <= MAX_BODY_BYTES:
            break
    meta = {
        "date": date, "subject": subject, "to": to, "generated_at": iso_now(), "model_generated_at": model.get("generated_at"),
        "ids": sorted({e["id"] for e in model["events"]} | {a["id"] for a in model["actions"]}),
        "counts": {"high": len(high), "changes": len(changes), "actions": len(actions), "issues": len(issues)},
    }
    return body, meta


def digest_paths(date):
    folder = paths.digest_dir()
    return os.path.join(folder, f"{date}.json"), os.path.join(folder, f"{date}.meta.json")


def write_digest(body, meta):
    body_path, meta_path = digest_paths(meta["date"])
    atomic_write_json(body_path, body, indent=2)
    atomic_write_json(meta_path, meta, indent=2)
    return body_path, meta_path


def latest_meta():
    metas = sorted(glob.glob(os.path.join(paths.digest_dir(), "*.meta.json")))
    return read_json(metas[-1]) if metas else None


def needs_send(meta):
    """False when this date's email already went out and nothing new has appeared since (a repeat run)."""
    doc, _ = load_notified()
    sent_today = any(s.get("date") == meta["date"] for s in (doc or {}).get("sent") or [])
    c = meta["counts"]
    return not sent_today or bool(c["high"] or c["changes"] or c["actions"])


def _status_ok(result):
    if isinstance(result, dict) and "statusCode" in result:
        try:
            return 200 <= int(result["statusCode"]) < 300
        except (TypeError, ValueError):
            return False
    return True


def find_send(meta, session, events=None):
    """Return the timestamp of a successful WorkIQ sendMail for this digest in the session log, else None."""
    since = dt.datetime.fromisoformat(meta["generated_at"])
    events = since_filter(events, since) if events is not None else iter_events(events_path(session), since)
    for ts, name, args, result in tool_results(events):
        if not name.endswith("do_action") or (args.get("actionUrl") or "").rstrip("/") != "/me/sendMail":
            continue
        body = args.get("jsonBody")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                continue
        msg = (body or {}).get("message") or {}
        to = [((r or {}).get("emailAddress") or {}).get("address", "").lower() for r in msg.get("toRecipients") or []]
        if msg.get("subject") == meta["subject"] and to == [meta["to"].lower()] and _status_ok(result):
            return ts
    return None


def mark_sent(meta, session, force=False, events=None):
    """Record every id in the digest as notified. Returns the send timestamp (or 'forced')."""
    sent_at = find_send(meta, session, events=events)
    if not sent_at and not force:
        raise SystemExit("No successful WorkIQ /me/sendMail with this digest's subject was found in this session's log "
                         "after the digest was built. Send it first, or pass --force if it was sent another way.")
    doc, items = load_notified()
    doc = doc or {}
    day = meta["date"]
    for i in meta["ids"]:
        cur = items.get(i) or {"first": day}
        cur["last_seen"] = day
        items[i] = cur
    doc["items"] = items
    cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=KEEP_NOTIFIED_DAYS)).isoformat()
    doc["items"] = {k: v for k, v in items.items() if (v.get("last_seen") or v.get("first") or day) >= cutoff}
    doc.setdefault("sent", []).append({"date": day, "subject": meta["subject"], "at": sent_at or iso_now(),
                                       "verified": bool(sent_at), "ids": len(meta["ids"])})
    doc["sent"] = doc["sent"][-90:]
    atomic_write_json(notified_path(), doc)
    return sent_at or "forced"
