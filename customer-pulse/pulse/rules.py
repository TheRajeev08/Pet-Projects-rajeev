"""Risks and opportunities derived from the latest account summaries and seat events.

Every action is data-only: fixed templates filled with numbers, never free text from sources.
"""
import datetime as dt
import hashlib

from .util import human_day


_LABEL = {"ghe": "GHE", "ghas": "GHAS", "ghcp": "Copilot"}


def action_id(rule, acct):
    return "a_" + hashlib.sha1(f"{rule}|{acct}".encode()).hexdigest()[:12]


def _fmt(n):
    if n is None:
        return "—"
    return f"{n:,.0f}"


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def _count(n, noun):
    return f"{_fmt(n)} {noun if n == 1 else noun + 's'}"


DROP_VERB = {"dropped": "dropped", "product_to_zero": "went to zero"}


def _action(rule, kind, priority, acct, family, title, why, metrics=None, tag=None, reasons=None):
    return {
        "id": action_id(rule, acct), "rule": rule, "kind": kind, "priority": priority,
        "account_id": acct, "family": family, "title": title, "why": why,
        "metrics": metrics or {}, "tag": tag, "reasons": reasons or [],
    }


def _over(a, b, th):
    """True when a exceeds b by at least max(overage_abs, overage_pct * b)."""
    return a is not None and b is not None and a - b >= max(th["overage_abs"], th["overage_pct"] * b)


def account_actions(acc, events, th, today):
    """Return the actions for one account summary (see model.account_summary)."""
    acct = acc["id"]
    ghe, ghas, ghcp = acc["products"]["ghe"], acc["products"]["ghas"], acc["products"]["ghcp"]
    out, risks = [], []

    ghe_seats = ghe.get("contracted") or 0
    ubu = ghe.get("ubu")
    adoption = ghe.get("adoption")
    if ghe_seats >= th["ghe_adoption_min_seats"] and adoption is not None and adoption < th["ghe_adoption_low"]:
        if adoption == 0:
            # Zero usually means nothing is reported (GHE Server without license sync, or unredeemed seats).
            title, why = "No GHE usage reported", (f"No billable users reported against {_fmt(ghe_seats)} contracted "
                                                   "GHE seats. Check deployment and GHE Server license sync.")
        else:
            title, why = "Low GHE seat adoption", f"{_pct(adoption)} of {_fmt(ghe_seats)} contracted GHE seats are in use."
        risks.append(_action("ghe_low_adoption", "risk", "Medium", acct, "ghe", title, why,
                             {"adoption": adoption, "contracted": ghe_seats, "ubu": ubu}))

    ghas_seats = ghas.get("contracted") or 0
    committers = ghas.get("committers")
    if ghas_seats >= th["ghas_min_seats"] and committers is not None and committers < th["ghas_committers_low"] * ghas_seats:
        risks.append(_action("ghas_low_usage", "risk", "Medium", acct, "ghas", "GHAS seats under-used",
                             f"{_count(committers, 'active committer')} on {_fmt(ghas_seats)} GHAS seats ({_pct(committers / ghas_seats)}).",
                             {"committers": committers, "contracted": ghas_seats}))

    assigned = ghcp.get("assigned")
    at_risk = ghcp.get("at_risk")
    category = (ghcp.get("health") or {}).get("category")
    share = (at_risk / assigned) if assigned and at_risk is not None else None
    if category == "Red" or (share is not None and share >= th["copilot_risk_share"]):
        bits = []
        if category == "Red":
            bits.append("Copilot health is Red")
        if share is not None and share >= th["copilot_risk_share"]:
            bits.append(f"{_fmt(at_risk)} of {_fmt(assigned)} assigned users ({_pct(share)}) are at risk")
        risks.append(_action("copilot_at_risk", "risk", "Medium", acct, "ghcp", "Copilot adoption at risk",
                             "; ".join(bits) + ".", {"assigned": assigned, "at_risk": at_risk, "category": category}))

    cutoff = (today - dt.timedelta(days=30)).isoformat()
    drops = [e for e in events if e["account_id"] == acct and not e.get("reverted") and e["date"] >= cutoff
             and e["measure"] == "contracted" and e["kind"] in ("dropped", "product_to_zero")]
    renew = acc.get("renewing_in_days")
    if renew is not None and 0 <= renew <= th["renewal_days"] and (risks or drops):
        reasons = [r["title"] + ": " + r["why"] for r in risks]
        reasons += [f"Contracted {_LABEL.get(e['family'], e['family'])} seats {DROP_VERB[e['kind']]} on {human_day(e['date'])} "
                    f"({_fmt(e['from'])} → {_fmt(e['to'])})." for e in drops]
        out.append(_action("renewal_risk", "risk", "High", acct, "all", f"Renewal in {renew} days with risk signals",
                           f"Renews {human_day(acc.get('renewal'), year=True)}. " + " ".join(reasons[:3]),
                           {"renewing_in_days": renew, "renewal": acc.get("renewal")}, reasons=reasons))
    else:
        out += risks

    # Billable users only count volume-licensed users, so the check also holds for mixed billing.
    if ghe.get("billing") in ("volume", "mixed") and _over(ubu, ghe_seats, th):
        out.append(_action("ghe_overage", "opportunity", "High", acct, "ghe", "GHE usage above contract",
                           f"{_fmt(ubu)} billable users vs {_fmt(ghe_seats)} contracted seats (+{_fmt(ubu - ghe_seats)}). "
                           "True-up or expansion conversation.", {"ubu": ubu, "contracted": ghe_seats}))
    if ghas_seats and _over(committers, ghas_seats, th):
        out.append(_action("ghas_overage", "opportunity", "High", acct, "ghas", "GHAS committers above seats",
                           f"{_fmt(committers)} active committers vs {_fmt(ghas_seats)} GHAS seats (+{_fmt(committers - ghas_seats)}).",
                           {"committers": committers, "contracted": ghas_seats}))
    if ghe_seats >= th["whitespace_min_seats"] and not ghas_seats and not committers:
        out.append(_action("ghas_whitespace", "opportunity", "Medium", acct, "ghas", "GHAS whitespace",
                           f"{_fmt(ghe_seats)} GHE seats and no GHAS. Lead with Secret Protection and Code Security.",
                           {"ghe_seats": ghe_seats}))
    cp_seats = max(ghcp.get("contracted") or 0, assigned or 0)
    if ghe_seats >= th["copilot_penetration_min_ghe"] and cp_seats < th["copilot_penetration_low"] * ghe_seats:
        if cp_seats:
            why = f"Copilot covers {_fmt(cp_seats)} of {_fmt(ghe_seats)} GHE seats ({_pct(cp_seats / ghe_seats)})."
        else:
            why = f"{_fmt(ghe_seats)} GHE seats and no Copilot. Start with a Copilot Business pilot."
        out.append(_action("copilot_penetration", "opportunity", "Medium", acct, "ghcp",
                           "Copilot expansion" if cp_seats else "Copilot whitespace", why,
                           {"copilot_seats": cp_seats, "ghe_seats": ghe_seats}))
    cq = ghas.get("code_quality")
    if cq and ((cq.get("committers") or 0) > 0 or (cq.get("repos_active") or 0) > 0):
        out.append(_action("code_quality_usage", "opportunity", "Medium", acct, "ghas", "Code Quality in use",
                           f"{_fmt(cq.get('committers'))} Code Quality committers across {_fmt(cq.get('repos_active'))} active repos "
                           f"({_fmt(cq.get('autofixes_l30d'))} autofixes in 30 days).", dict(cq), tag="New"))
    legacy_as = (ghas.get("by_type") or {}).get("Advanced Security", {}).get("seats") or 0
    if legacy_as and renew is not None and 0 <= renew <= th["renewal_days"]:
        out.append(_action("ghas_unbundle", "opportunity", "Medium", acct, "ghas", "Move legacy GHAS to Secret Protection + Code Security",
                           f"{_fmt(legacy_as)} legacy Advanced Security seats renew in {renew} days.",
                           {"legacy_seats": legacy_as, "renewing_in_days": renew}))
    return out


PRIORITY_RANK = {"High": 0, "Medium": 1, "Notify": 2, "Info": 3}


def all_actions(accounts, events, th, today):
    out = []
    for acc in accounts:
        for a in account_actions(acc, events, th, today):
            a["account_name"], a["arr"] = acc["name"], acc.get("arr")
            out.append(a)
    out.sort(key=lambda a: (PRIORITY_RANK[a["priority"]], 0 if a["kind"] == "risk" else 1, -(a.get("arr") or 0)))
    return out
