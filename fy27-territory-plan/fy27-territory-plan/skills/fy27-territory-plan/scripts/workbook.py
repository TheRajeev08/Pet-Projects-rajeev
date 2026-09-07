import csv, datetime, json, math, os, re, sys, zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ghcp
except ImportError:  # GHCP segmentation is additive; the sheet degrades without it.
    ghcp = None

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

def cell_value(cell, shared):
    value = cell.find("m:v", NS)
    raw = "" if value is None else value.text or ""
    if cell.get("t") == "s":
        return shared[int(raw)] if raw else ""
    if cell.get("t") == "inlineStr":
        text = cell.find(".//m:t", NS)
        return "" if text is None else text.text or ""
    return raw

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))

def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.findall(".//m:t", NS)))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall(".//m:row", NS):
            values = {}
            for position, c in enumerate(row.findall("m:c", NS)):
                ref = c.get("r")
                if ref:
                    col = re.match(r"([A-Z]+)", ref).group(1)
                    index = 0
                    for char in col:
                        index = index * 26 + ord(char) - 64
                    position = index - 1
                values[position] = cell_value(c, shared)
            rows.append([values.get(i, "") for i in range(max(values.keys(), default=-1) + 1)])
        return rows

def read_input(path):
    # Browser uploads keep the original filename, which is often wrong. Sniff the
    # ZIP magic number so an XLSX saved as .csv still parses.
    with open(path, "rb") as f:
        is_zip = f.read(2) == b"PK"
    return read_xlsx(path) if is_zip else read_csv(path)

def num(v):
    try: return float(str(v).replace(",", "").strip() or 0)
    except Exception: return 0

def clean(v):
    return str(v or "").strip()

def pct(v):
    x = num(v)
    return f"{x:.1%}" if abs(x) <= 1 else f"{x:.1f}%"

def date_value(v):
    value = clean(v)
    if re.fullmatch(r"\d+(\.\d+)?", value):
        try:
            return (datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(value))).date().isoformat()
        except Exception:
            return value
    return value

COPILOT_TRUST_RATIO = 0.25


def classify_play(ghe, copilot, ghas=0, regulated=None):
    """Assign the FY27 play from what the account owns today.

    The rule the ladder encodes, and why each rung is where it is:

      * No GitHub Enterprise -> **Scale**. Scale is the migration and displacement
        play; an account that is not on the platform is exactly its target. (The
        previous engine scored *existing* GHE seats as a Scale signal, which pushed
        established customers into a migration motion and left greenfield accounts
        out of it.) `ghe` here must be **true GitHub Enterprise seats** - licence
        seats plus metered users. The SuperDash "GHE/VS" total also counts Visual
        Studio bundle seats, which entitle GHE but do not mean the customer is on it;
        feeding that blended figure in here misreads pure-VS accounts as customers.
      * On GHE with Copilot at or above a quarter of the GHE licence count ->
        **Trust**. Agentic delivery has landed at meaningful scale, so the next
        conversation is governance, security and quality over that generated code.
      * On GHE, below that bar, in a regulated industry -> **Trust**. This is Trust's
        second tier: regulation makes the security and governance conversation the one
        that opens the door even before Copilot has landed. It exists because attach
        alone qualifies too few accounts to carry the play.
      * On GHE, below the bar, unregulated -> **Innovate**. The agentic motion is
        either unstarted or far from saturated, and that headroom is the opportunity.

    The 25% bar is cut at the natural break in the book: accounts with any Copilot
    cluster either at 28% and above or at 9% and below, with nothing in between. An
    account running 10 Copilot seats against 330 GHE licences is a headroom story, not
    a govern-at-scale story, and belongs in Innovate.

    `ghas` is accepted for call-site compatibility but no longer decides membership.
    GHAS used to promote an account to Trust on its own; Copilot is now the sole
    product signal for Trust, so a GHAS customer without Copilot is an Innovate
    account whose security footprint is an asset in the conversation, not a play.

    `regulated` is None when industry is not yet known; the caller refines those
    accounts once Salesforce data is available. The default keeps the account inside
    the play set either way, so ranking is never gated on enrichment that may be
    missing.
    """
    if ghe <= 0:
        return "Scale"
    if copilot >= ghe * COPILOT_TRUST_RATIO:
        return "Trust"
    return "Trust" if regulated else "Innovate"


def play_reason(ghe, copilot, ghas=0, regulated=None, industry=""):
    """One line explaining the play, so the workbook can show its working."""
    if ghe <= 0:
        return "Not on GitHub Enterprise - migration and displacement motion."
    share = (copilot / ghe * 100) if ghe else 0
    if copilot >= ghe * COPILOT_TRUST_RATIO:
        return ("Copilot at %.0f%% of %.0f GHE licences - agentic delivery at scale, "
                "govern it." % (share, ghe))
    if regulated:
        return ("On GHE, Copilot at %.0f%% of %.0f licences, regulated industry (%s) - "
                "governance opens the door." % (share, ghe, industry or "regulated"))
    if regulated is None:
        return ("Copilot at %.0f%% of %.0f GHE licences - industry not yet known, "
                "defaulted to Innovate." % (share, ghe))
    return ("Copilot at %.0f%% of %.0f GHE licences - agentic headroom remains (%s)."
            % (share, ghe, industry or "unregulated"))


def guidance(play):
    return {
        "Innovate": "Lead with an agentic-engineering outcome: start with a focused Copilot cohort, establish champions, and measure velocity, capacity, quality, and governance. Expand from IDE assistance into Copilot coding, review, CLI, cloud-agent, and repeatable agentic workflows.",
        "Trust": "Map the customer's code-quality and governance risk, then connect security and quality signals to engineering outcomes. Establish maintainability controls, agree quality measures with engineering leaders, and use a partner-supported adoption plan to reduce tool sprawl.",
        "Scale": "Build the consolidation case from platform and AI fragmentation, renewal pressure, and developer demand. Inventory the incumbent toolchain, design GitHub governance, execute migration or displacement in waves, and prove productivity gains from the first cohort.",
        "Unclassified": "No product or usage signal qualified this account for a play. Do not run play messaging yet. Verify the account record and its Salesforce hierarchy, confirm whether usage exists under a parent or another enterprise slug, and only then assign a play.",
    }[play]

def next_action(play):
    return {
        "Innovate": {
            "owner": "Account owner",
            "persona": "VP Engineering / AI transformation leader",
            "action": "Confirm a focused Copilot cohort, baseline developer outcomes, and secure an executive sponsor.",
            "exitCriteria": "Named sponsor, cohort, baseline measures, and pilot decision date.",
        },
        "Trust": {
            "owner": "Account owner + security specialist",
            "persona": "CISO / AppSec / engineering quality leader",
            "action": "Validate the highest-cost code security and quality gaps and agree a measurable risk-reduction workshop.",
            "exitCriteria": "Documented risk, agreed success measures, technical workshop, and evaluation owner.",
        },
        "Scale": {
            "owner": "Account owner + platform specialist",
            "persona": "CIO / platform engineering / procurement leader",
            "action": "Map platform fragmentation, renewal pressure, and migration scope into a phased consolidation hypothesis.",
            "exitCriteria": "Incumbent inventory, economic hypothesis, migration cohort, and executive review date.",
        },
        "Unclassified": {
            "owner": "Account owner + RevOps",
            "persona": "Unknown — identify the engineering or platform owner first",
            "action": "Research before outreach: confirm the Salesforce record is current, check for usage under a parent account or separate enterprise slug, and capture the missing renewal date.",
            "exitCriteria": "Account confirmed as genuine greenfield or reclassified into a play with evidence.",
        },
    }[play]

PLAY_LINKS = {
    "Innovate": "https://github.seismic.com/apps/doccenter/f3402112-cd44-44a7-8040-ddee829474cf/doc/%252Fddb198ef0c-d064-a795-2dae-766e09c5aad7%252Flf95900e1b-4075-4f1a-bfd9-424ed518c699//",
    "Trust": "https://github.seismic.com/apps/doccenter/f3402112-cd44-44a7-8040-ddee829474cf/doc/%252Fddb198ef0c-d064-a795-2dae-766e09c5aad7%252Flfc697a2ca-e883-416f-b6ac-f9dde0ae50c8//",
    "Scale": "https://github.seismic.com/apps/doccenter/f3402112-cd44-44a7-8040-ddee829474cf/doc/%252Fddb198ef0c-d064-a795-2dae-766e09c5aad7%252Flf17bb21ee-d0b0-45df-9318-2a69eb40df5d//",
}

POTENTIAL_MODEL = {
    "version": "v2-fixed-bands",
    "weights": {"copilotWhitespace": 0.40, "adoWhitespace": 0.25, "meteredConsumption": 0.35},
    "bands": {"copilotWhitespace": 1000, "adoWhitespace": 1000, "meteredConsumption": 100000},
}

# Power BI report links are pure ID templating, so they are generated locally
# rather than fetched per account. Each play gets the reports that actually
# evidence it, so a drill-down opens the report a seller would reach for next.
DASHBOARDS = {
    "SuperDash": "https://app.powerbi.com/links/ew7fQ8n3Ui?filter=Super_x0020_Account%2Fsalesforce_id+eq+%27{id}%27&ctid=398a6654-997b-47e9-b12b-9515b896b4de",
    "Copilot Telemetry": "https://app.powerbi.com/links/ESWCec4h1W?filter=L3Months_Historic_Account_Summary%2Fsalesforce_account_id+in+%28%27{id}%27%29",
    "Billed Consumption": "https://app.powerbi.com/links/DKWIcF0Dfy?filter=Dim_Account%2Fid+eq+%27{id}%27",
    "Actions": "https://app.powerbi.com/links/UCbLfsxXry?filter=Dim_Account%2Fid+eq+%27{id}%27",
    "Org Research": "https://app.powerbi.com/links/65Ok5pw-px?filter=Canonical_Accounts_All%2Fsalesforce_account_id+eq+%27{id}%27",
    "GHAS Accelerator": "https://app.powerbi.com/links/7q7OsGdH4N",
    "Salesforce": "https://github.lightning.force.com/lightning/r/Account/{id}/view",
}

PLAY_DASHBOARDS = {
    "Innovate": ["Copilot Telemetry", "SuperDash", "Salesforce"],
    "Trust": ["GHAS Accelerator", "SuperDash", "Salesforce"],
    "Scale": ["Actions", "Billed Consumption", "SuperDash", "Salesforce"],
    "Unclassified": ["Org Research", "SuperDash", "Salesforce"],
}

def dashboards(play, sid):
    """Verification links for an account, ordered by relevance to its play."""
    if not sid:
        return []
    return [{"label": label, "url": DASHBOARDS[label].replace("{id}", sid)}
            for label in PLAY_DASHBOARDS.get(play, PLAY_DASHBOARDS["Unclassified"])]

def score_component(value, band):
    value = max(0, num(value))
    return 0.0 if value <= 0 else min(100.0, 100 * math.log10(1 + value) / math.log10(1 + band))

def renewal_horizon(value):
    if not value:
        return "Unknown"
    try:
        days = (datetime.date.fromisoformat(value[:10]) - datetime.date.today()).days
    except ValueError:
        return "Unknown"
    if days < 0: return "Past due"
    if days <= 90: return "0-90 days"
    if days <= 180: return "91-180 days"
    if days <= 365: return "181-365 days"
    return "365+ days"

def apply_activity(report, activity):
    by_id = activity.get("accounts", {}) if isinstance(activity, dict) else {}
    metadata = activity.get("metadata", {}) if isinstance(activity, dict) else {}
    for account in report["accounts"]:
        item = by_id.get(account["salesforceId"], {})
        account["activity"] = {
            "status": item.get("status", "not enriched"),
            "total": int(num(item.get("total", 0))),
            "inbound": int(num(item.get("inbound", 0))),
            "outbound": int(num(item.get("outbound", 0))),
            "meetings": int(num(item.get("meetings", 0))),
            "lastActivity": clean(item.get("lastActivity")),
            "twoWay": bool(item.get("twoWay", False)),
            "score": round(num(item.get("score", 0)), 2),
            "tier": clean(item.get("tier", "Unranked")),
            "reason": clean(item.get("reason")),
        }
        communication = min(100, max(0, account["activity"]["score"]))
        if account["activity"]["twoWay"]:
            communication = min(100, communication + 20)
        account["communicationScore"] = round(communication, 2) if account["activity"]["status"] == "enriched" else None
        account["engagementScore"] = account["communicationScore"]
        account["engagementState"] = account["activity"]["tier"] if account["activity"]["status"] == "enriched" else "Unknown"
        if account["activity"]["status"] == "enriched":
            account["priorityScore"] = round(((account.get("revenuePotential") or 0) + communication) / 2, 2)
            account["priorityTier"] = "Decision-support ordering"
            account["priorityReason"] = f"Ordered by verified two-way status, potential proxy, engagement, and execution readiness; {account['activity']['reason']}"
        else:
            account["priorityScore"] = account.get("revenuePotential")
            account["priorityTier"] = "Potential-led; engagement unknown"
            account["priorityReason"] = "Potential proxy is available; Salesforce engagement is unknown."
    report["accounts"].sort(key=lambda a: (
        0 if a.get("classified") else 1,
        -(1 if a.get("activity", {}).get("twoWay") else 0),
        -(a.get("revenuePotential") if a.get("revenuePotential") is not None else -1),
        -(a.get("engagementScore") if a.get("engagementScore") is not None else -1),
        -(a.get("executionReadiness") if a.get("executionReadiness") is not None else -1),
        a["name"].lower(),
    ))
    enriched = [a for a in report["accounts"] if a["activity"]["status"] == "enriched"]
    for play in report["playSummary"]:
        scoped = [a for a in enriched if a["primaryPlay"] == play["play"]]
        play["activityPrioritized"] = sum(a["activity"]["tier"] in ("Priority", "High") for a in scoped)
        play["twoWayAccounts"] = sum(a["activity"]["twoWay"] for a in scoped)
    report["activity"] = {
        "status": "enriched" if enriched else "not enriched",
        "enrichedAccounts": len(enriched),
        "unknownAccounts": len(report["accounts"]) - len(enriched),
        "coveragePct": round(100 * len(enriched) / len(report["accounts"]), 1) if report["accounts"] else 0,
        "twoWayAccounts": sum(a["activity"]["twoWay"] for a in enriched),
        "priorityAccounts": sum(a["activity"]["tier"] in ("Priority", "High") for a in enriched),
        "windowDays": int(num(metadata.get("windowDays", 90))),
        "asOf": clean(metadata.get("asOf")) or max((a["activity"]["lastActivity"] for a in enriched), default=""),
        "source": clean(metadata.get("source")) or "Salesforce Task and Event",
    }
    report["governance"]["activityCoveragePct"] = report["activity"]["coveragePct"]
    report["governance"]["activityAsOf"] = report["activity"]["asOf"]
    return report

def apply_contacts(report, contacts):
    by_id = contacts.get("accounts", {}) if isinstance(contacts, dict) else {}
    if not by_id:
        report["contacts"] = {"status": "not enriched", "accountsWithContacts": 0, "totalContacts": 0, "generatedAt": ""}
        return report
    covered = 0
    total = 0
    for account in report["accounts"]:
        found = by_id.get(account["salesforceId"]) or []
        clean_rows = []
        for c in found[:3]:
            name = clean(c.get("name")); title = clean(c.get("title")); email = clean(c.get("email"))
            if not (name and title and email):
                continue
            clean_rows.append({"name": name, "title": title, "email": email, "fit": clean(c.get("fit")) or account["primaryPlay"]})
        account["contacts"] = clean_rows
        if clean_rows:
            covered += 1
            total += len(clean_rows)
    report["contacts"] = {
        "status": "enriched",
        "accountsWithContacts": covered,
        "totalContacts": total,
        "generatedAt": clean(contacts.get("generatedAt")),
        "source": "Salesforce Contact",
    }
    return report


def analyze(rows, source_name, activity=None, contacts=None):
    headers = rows[0]
    index = {h: i for i, h in enumerate(headers)}
    def get(r, key): return r[index[key]] if index.get(key, len(r)) < len(r) else ""
    raw = []
    excluded_rows = 0
    for r in rows[1:]:
        name = clean(get(r, "salesforce_name"))
        sid = clean(get(r, "salesforce_id"))
        if not name or name.lower() == "total" or not sid:
            excluded_rows += 1
            continue
        raw.append((name, sid, r))
    groups = {}
    for name, sid, r in raw:
        base = re.sub(r"\s*-\s*parent\s*$", "", name, flags=re.I).strip().lower()
        groups.setdefault(base, []).append((name, sid, r))
    name_conflicts = []
    collated = {}
    for base, items in groups.items():
        variants = {re.search(r"\s*-\s*parent\s*$", n, flags=re.I) is not None for n, _, _ in items}
        ids = {sid for _, sid, _ in items}
        # Multiple Salesforce IDs with no parent/child naming signal are distinct
        # companies that collide on name, not duplicates. Keep them separate.
        if len(ids) > 1 and len(variants) == 1:
            name_conflicts.append({"name": items[0][0], "salesforceIds": sorted(ids)})
            for sid in sorted(ids):
                collated[f"{base}|{sid}"] = [i for i in items if i[1] == sid]
        else:
            collated[base] = items
    groups = collated
    accounts = []
    for key, items in groups.items():
        name = next((item[0] for item in items if re.search(r"\s*-\s*parent\s*$", item[0], flags=re.I) is None), items[0][0])
        sid = next((i[1] for i in items if i[0] == name), items[0][1])
        rows_for_account = [x[2] for x in items]
        def total(key): return sum(num(get(r, key)) for r in rows_for_account)
        ghe = total("Total GHE/VS Seats (Vol and Metered)")
        # `ghe` above combines GitHub Enterprise with **Visual Studio bundle** seats.
        # A VS bundle entitles GHE but does not mean the customer is *on* GitHub, so
        # using it to decide a play routes pure-Visual-Studio accounts into Trust or
        # Innovate when they are in fact migration targets. `ghe_true` counts only
        # seats that represent an actual GitHub Enterprise footprint, and it is what
        # the play ladder reads. The blended figure stays for capacity and evidence,
        # where VS bundles are a legitimate migration TAM signal.
        ghe_true = total("Current GHE License Seats") + total("Current GHE Metered Users")
        vs_bundle = max(ghe - ghe_true, 0)
        cf = total("Current CfB Seats (incl. CE & CS)")
        ubb = total("Current Month UBB Users")
        last_ubb = total("Last Month UBB Users")
        committers = total("Active Committers L90d (Cloud Users)")
        ghas = total("GHAS total volume and metered")
        gha = total("GHAzDO Seats")
        teams = total("# Teams seats")
        ado = total("ADO TAM - GHAzDO Accts Only")
        consumption = f"GHE {ghe_true:.0f} + VS bundle {vs_bundle:.0f}; CfB {cf:.0f}; Teams {teams:.0f}; UBB {ubb:.0f} (prior {last_ubb:.0f}); committers L90d {committers:.0f}; GHAS {ghas:.0f}; GHAzDO {gha:.0f}; ADO TAM {ado:.0f}; LM consumption ${total('LM Consumption $'):.0f}"
        evidence, gaps, scores = [], [], {}
        if cf > 0 or ubb > 0 or last_ubb > 0:
            scores["Innovate"] = min(3, 1 + int(last_ubb > 0) + int(ubb > last_ubb or total("GHE/VS to CfB Potential") > 0))
            evidence.append(f"Agentic readiness signal: {cf:.0f} CfB seats, {ubb:.0f} current UBB users, {pct(total('%PR w/CfB CR'))} penetration")
        else: gaps.append("No Copilot/CfB usage signal")
        secret_risk = total("Secret Risk Assessments")
        trust_signal = ghas + total("Current GHAS License Seats") + total("Current GHCS License Seats") + total("Current GHSP License Seats") + secret_risk
        if trust_signal > 0:
            scores["Trust"] = min(3, 1 + int(ghas > 0 or secret_risk > 0))
            evidence.append(f"Trust signal: GHAS {ghas:.0f}, GHCS/GHSP seats {total('Current GHCS License Seats') + total('Current GHSP License Seats'):.0f}, secret risk assessments {secret_risk:.0f}")
        else: gaps.append("Incumbent platform and CI/CD mix require discovery")
        renewal_candidates = sorted({date_value(get(r, "Next Renewal Date (GH)") or get(r, "EA Renewal Date (MSFT)")) for r in rows_for_account} - {""})
        renewal = renewal_candidates[-1] if renewal_candidates else ""
        renewal_conflict = len(renewal_candidates) > 1
        scale_signal = ado + gha + vs_bundle
        if scale_signal > 0 or ghe >= 100 or committers >= 50:
            scores["Scale"] = min(3, 1 + int(ado > 0 or gha > 0))
            evidence.append(f"Scale signal: {ghe_true:.0f} GHE seats + {vs_bundle:.0f} VS bundle seats, {committers:.0f} active committers L90d, ADO TAM {ado:.0f}, GHAzDO {gha:.0f}")
        else: gaps.append("Platform consolidation, incumbent tools, and renewal pressure require discovery")
        # The play itself is decided by the ladder in classify_play, not by these
        # scores. The scores stay because they still drive execution readiness and the
        # evidence strings, but they are a measure of how much we know about an
        # account, not of which motion it belongs in.
        classified = bool(scores) or ghe > 0 or cf > 0 or ghas > 0
        if classified:
            primary = classify_play(ghe_true, cf, ghas)
            play_basis = play_reason(ghe_true, cf, ghas)
            plays = [primary] + [p for p in sorted(scores, key=lambda p: (-scores[p], p))
                                 if p != primary]
        else:
            primary = "Unclassified"
            plays = ["Unclassified"]
            scores["Unclassified"] = 0
            play_basis = "No GHE, Copilot or GHAS signal in the upload."
        readiness = round(min(100, scores.get(primary, 0) / 3 * 80 + min(20, len(evidence) * 7)), 2) if classified else None
        accounts.append({"name": name, "salesforceId": sid, "primaryPlay": primary, "plays": plays, "playBasis": play_basis, "playPendingIndustry": bool(ghe_true > 0 and cf < ghe_true * COPILOT_TRUST_RATIO), "score": scores.get(primary, 0), "classified": classified, "renewal": renewal, "renewalConflict": renewal_conflict, "sourceRows": len(rows_for_account), "consumption": consumption, "evidence": evidence, "discoveryGaps": gaps, "winPlan": " ".join(guidance(p) for p in plays[:2]), "nextAction": next_action(primary), "dashboards": dashboards(primary, sid), "executionReadiness": readiness, "executionReason": ("Play evidence strength and observed product signals; buying intent still requires seller validation." if classified else "Not scored: no product or usage signal qualified this account for a play."), "revenueSignals": {"copilotWhitespace": total("GHE/VS to CfB Potential"), "adoWhitespace": ado, "securityWhitespace": max(ghe_true - ghas, 0), "meteredConsumption": total("LM Consumption $"), "activeCommitters": committers, "ghasSeats": ghas, "gheSeats": ghe_true, "vsBundleSeats": vs_bundle, "copilotSeats": cf, "teamsSeats": teams, "ghazdoSeats": gha}, "activity": {"status": "not enriched", "total": 0, "inbound": 0, "outbound": 0, "meetings": 0, "lastActivity": "", "twoWay": False, "score": 0, "tier": "Unranked", "reason": "Salesforce activity has not been enriched."}, "contacts": []})
    accounts.sort(key=lambda a: (-a["score"], -len(a["plays"]), a["name"].lower()))
    for account in accounts:
        components = account["revenueSignals"]
        parts = {key: score_component(components[key], POTENTIAL_MODEL["bands"][key]) for key in POTENTIAL_MODEL["weights"]}
        has_signal = any(components[key] > 0 for key in POTENTIAL_MODEL["weights"])
        account["potentialComponents"] = {key: round(value, 2) for key, value in parts.items()}
        account["revenuePotential"] = round(sum(parts[key] * POTENTIAL_MODEL["weights"][key] for key in parts), 2) if has_signal else None
        account["potentialScore"] = account["revenuePotential"]
        account["revenueReason"] = (f"Relative potential proxy, not ARR or forecast. Log-scaled against fixed bands (model {POTENTIAL_MODEL['version']}) so scores stay comparable across weekly uploads: " + ", ".join(f"{key} {POTENTIAL_MODEL['weights'][key]:.0%}" for key in POTENTIAL_MODEL["weights"]) + ". Security whitespace is reported but excluded from scoring because it is collinear with Copilot whitespace (both derive from GHE seats).") if has_signal else "Not scored: no Copilot whitespace, ADO TAM, or metered consumption present in the upload."
        seats = num(account["revenueSignals"].get("copilotWhitespace")) + num(account["revenueSignals"].get("adoWhitespace"))
        account["capacityTier"] = "Enterprise" if seats >= 500 else "Mid-market" if seats >= 100 else "Velocity" if seats > 0 else "Unsized"
        account["renewalHorizon"] = renewal_horizon(account["renewal"])
        # Turn the generic play template into an account-specific instruction using
        # this account's largest whitespace signal, renewal timing, and engagement.
        signals = account["revenueSignals"]
        top = max(POTENTIAL_MODEL["weights"], key=lambda k: num(signals.get(k)))
        top_value = num(signals.get(top))
        label = {"copilotWhitespace": "Copilot seat whitespace", "adoWhitespace": "ADO/GHAzDO TAM", "meteredConsumption": "metered consumption"}[top]
        hooks = []
        if top_value > 0:
            hooks.append(f"lead with {label} of {top_value:,.0f} ({account['capacityTier']} motion)")
        if account["renewalHorizon"] in ("Past due", "0-90 days", "91-180 days"):
            hooks.append(f"time it to the {account['renewalHorizon']} renewal on {account['renewal']}")
        elif account["renewalHorizon"] == "Unknown":
            hooks.append("confirm the renewal date before committing to a close plan")
        action = dict(account["nextAction"])
        if hooks:
            action["action"] = action["action"].rstrip(".") + "; " + ", and ".join(hooks) + "."
        account["nextAction"] = action
    summary = []
    for play in ["Innovate", "Trust", "Scale", "Unclassified"]:
        matching = [a for a in accounts if play in a["plays"]]
        primary_count = sum(a["primaryPlay"] == play for a in matching)
        summary.append({"play": play, "link": PLAY_LINKS.get(play, ""), "accounts": primary_count, "qualifiedAccounts": len(matching), "primary": primary_count, "highConfidence": sum(a["score"] >= 2 and a["primaryPlay"] == play for a in matching)})
    total = len(accounts)
    exec_text = f"{total} normalized accounts. {sum(a['classified'] for a in accounts)} qualify for an FY27 play from uploaded product signals and {sum(not a['classified'] for a in accounts)} are unclassified and belong in a discovery queue rather than a play. Prioritize the {sum(a['score'] >= 2 for a in accounts)} high-confidence accounts first, sequencing by renewal horizon and verified two-way engagement."
    dup_groups = [items for items in groups.values() if len(items) > 1]
    exact_groups = [items for items in dup_groups if len({i[1] for i in items}) == 1]
    rollup_groups = [items for items in dup_groups if len({i[1] for i in items}) > 1]
    duplicate_groups = len(dup_groups)
    duplicate_rows = sum(len(items) - 1 for items in dup_groups)
    duplicate_accounts = [{"name": items[0][0], "rows": len(items), "variants": [item[0] for item in items], "kind": "Exact duplicate row" if len({i[1] for i in items}) == 1 else "Parent/child rollup"} for items in dup_groups]
    signal_keys = {
        "GHE/VS seats": "Total GHE/VS Seats (Vol and Metered)",
        "CfB seats": "Current CfB Seats (incl. CE & CS)",
        "UBB users": "Current Month UBB Users",
        "Active committers L90d": "Active Committers L90d (Cloud Users)",
        "GHAS seats": "GHAS total volume and metered",
        "GHAzDO seats": "GHAzDO Seats",
        "ADO TAM": "ADO TAM - GHAzDO Accts Only",
        "LM consumption": "LM Consumption $",
        "Secret risk assessments": "Secret Risk Assessments",
    }
    portfolio_signals = {label: sum(num(get(row, key)) for _, _, row in raw) for label, key in signal_keys.items()}
    stats = {
        "sourceRows": max(0, len(rows) - 1),
        "sourceAccounts": len(raw),
        "duplicateGroups": duplicate_groups,
        "exactDuplicateGroups": len(exact_groups),
        "parentChildGroups": len(rollup_groups),
        "nameConflicts": len(name_conflicts),
        "unclassifiedAccounts": sum(1 for a in accounts if not a["classified"]),
        "duplicateRows": duplicate_rows,
        "excludedRows": excluded_rows,
        "collatedAccounts": total,
    }
    report = {"sourceName": source_name, "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"), "accountCount": total, "stats": stats, "portfolioSignals": portfolio_signals, "duplicateAccounts": duplicate_accounts, "nameConflicts": name_conflicts, "playSummary": summary, "accounts": accounts, "activity": {"status": "not enriched", "enrichedAccounts": 0, "unknownAccounts": total, "coveragePct": 0, "twoWayAccounts": 0, "priorityAccounts": 0, "windowDays": 90, "asOf": "", "source": "Salesforce Task and Event"}, "governance": {"classification": "Decision support only; not ARR, forecast, propensity, or territory-allocation authority.", "potentialDefinition": f"Relative proxy from Copilot whitespace, ADO TAM, and metered consumption, log-scaled against fixed absolute bands (model {POTENTIAL_MODEL['version']}) so scores are comparable across uploads. Blank when no signal exists.", "engagementDefinition": "Salesforce Task/Event evidence from the declared activity window; unmatched accounts are Unknown, not cold.", "readinessDefinition": "Play evidence strength and observed product-signal coverage; blank when the account did not qualify for a play. Seller validation required.", "sortDefinition": "Classified accounts first, then verified two-way, potential proxy, engagement, execution readiness, and account name.", "unclassifiedDefinition": "Accounts with no qualifying Innovate, Trust, or Scale product signal in this upload. They are not assigned a play; treat them as a discovery queue.", "activityCoveragePct": 0, "activityAsOf": ""}, "executiveSummary": exec_text}
    return apply_contacts(apply_activity(report, activity or {}), contacts or {})

PLAY_GUIDANCE = {
    "Innovate": {
        "objective": "Turn developer AI readiness into measurable engineering velocity, quality, and capacity outcomes.",
        "fit": "On GitHub Enterprise with Copilot below a quarter of the licence base, in an unregulated industry - the agentic headroom is the opportunity. GHAS or security footprint is an asset in the conversation, not a reason to leave the play.",
        "motion": "Start with a focused cohort and executive sponsor; baseline outcomes, activate champions, prove value, then expand into repeatable agentic workflows.",
        "actions": "Confirm cohort and sponsor; baseline velocity, capacity, quality, and governance; run a focused adoption workshop; review outcomes and scale.",
        "exit": "Named sponsor, defined cohort, baseline measures, pilot decision date, and expansion hypothesis.",
    },
    "Trust": {
        "objective": "Reduce software supply-chain, code-security, and quality risk while simplifying governance.",
        "fit": "On GitHub Enterprise with Copilot at or above a quarter of the licence base - enough generated code to govern. Second tier: on GHE in a regulated industry, where compliance opens the door before Copilot has landed.",
        "motion": "Connect risk signals to engineering outcomes, agree measurable controls, and build a partner-supported path from assessment to adoption.",
        "actions": "Validate the highest-cost risk; map current controls; agree success measures; run a technical workshop; identify evaluation owner and rollout path.",
        "exit": "Documented risk, agreed success measures, technical workshop, evaluation owner, and dated next step.",
    },
    "Scale": {
        "objective": "Consolidate the developer platform and scale GitHub adoption across teams, repositories, and workflows.",
        "fit": "Not on GitHub Enterprise - the migration and displacement target. Priority to accounts already carrying Copilot or Teams seats, where a GitHub relationship already exists to build the platform case on.",
        "motion": "Build the consolidation case, inventory incumbents, design governance, execute in waves, and prove adoption economics with the first cohort.",
        "actions": "Map platform fragmentation and renewal pressure; quantify migration scope; identify a first wave; align platform and procurement leaders; establish review date.",
        "exit": "Incumbent inventory, economic hypothesis, migration cohort, governance owner, and executive review date.",
    },
    "Unclassified": {
        "objective": "Move accounts from unknown to evidence-based discovery without forcing an unsupported play assignment.",
        "fit": "Accounts with no qualifying Innovate, Trust, or Scale product signal in the uploaded data.",
        "motion": "Use discovery to validate platform, AI, security, and renewal context before assigning a play.",
        "actions": "Confirm current toolchain, product footprint, business priority, renewal timing, and executive owner; capture evidence for reclassification.",
        "exit": "Validated product signal, named stakeholder, documented business problem, and selected play or explicit nurture decision.",
    },
}

STYLE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>
<fonts count="4"><font><sz val="10"/><name val="Aptos"/></font><font><b/><sz val="18"/><color rgb="FFFFFFFF"/><name val="Aptos Display"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font><font><b/><sz val="10"/><color rgb="FF0F172A"/><name val="Aptos"/></font></fonts>
<fills count="10"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF0F172A"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1D4ED8"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF7C3AED"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF475569"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF1F5F9"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE0F2FE"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFEF3C7"/></patternFill></fill></fills>
<borders count="3"><border/><border><left style="thin" color="FFD1D5DB"/><right style="thin" color="FFD1D5DB"/><top style="thin" color="FFD1D5DB"/><bottom style="thin" color="FFD1D5DB"/></border><border><bottom style="medium" color="FF1D4ED8"/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="19">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="7" borderId="0"/><xf numFmtId="0" fontId="2" fillId="2" borderId="0"/>
<xf numFmtId="0" fontId="2" fillId="3" borderId="0"/><xf numFmtId="0" fontId="2" fillId="4" borderId="0"/><xf numFmtId="0" fontId="2" fillId="5" borderId="0"/><xf numFmtId="0" fontId="2" fillId="6" borderId="0"/>
<xf numFmtId="0" fontId="3" fillId="8" borderId="1"/><xf numFmtId="0" fontId="3" fillId="9" borderId="1"/>
<xf numFmtId="0" fontId="3" fillId="7" borderId="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="0" fontId="0" fillId="7" borderId="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment wrapText="1" vertical="top" horizontal="left"/></xf>
<xf numFmtId="0" fontId="0" fillId="8" borderId="1"/><xf numFmtId="0" fontId="0" fillId="9" borderId="1"/>
<xf numFmtId="0" fontId="3" fillId="7" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
</cellXfs></styleSheet>"""


def _column_name(n):
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xml_escape(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


LICENCE_HEADERS = ["GHE Seats", "Copilot Seats", "Copilot Attach %", "Active Committers",
                   "GHAS Committers", "Plan"]

# Every account sheet, from one definition. The play sheets used to carry no licence
# column at all, so the widest sheets in the workbook were sized on the SuperDash upload
# - exactly the org-wide signal live licensing exists to correct.
#
# Licence columns sit immediately after the engagement columns on purpose: one row now
# answers "are we talking to them" and "what do they actually own" side by side.
ACCOUNT_HEADERS = [
    "Account", "Salesforce ID", "Primary Play", "All Plays", "Capacity", "Renewal",
    "Renewal Horizon", "Potential Proxy", "Priority Score", "Engagement Tier", "Two-way",
    "Last Activity", "Meetings",
] + LICENCE_HEADERS + [
    "Readiness", "Action Owner", "Target Persona", "Next Action", "Exit Criteria",
    "How to Win", "Evidence", "Key Contacts", "Verify In",
]

# The per-play sheets drop the Salesforce ID (internal) and How to Win (on the sheet
# header already). Derived rather than retyped, so adding a column cannot silently put
# one sheet's headers over another sheet's data.
COMPACT_DROP = ("Salesforce ID", "How to Win")
COMPACT_INDEX = [i for i, h in enumerate(ACCOUNT_HEADERS) if h not in COMPACT_DROP]
COMPACT_HEADERS = [ACCOUNT_HEADERS[i] for i in COMPACT_INDEX]

_WIDTH = {
    "Account": 28, "Salesforce ID": 20, "Primary Play": 14, "All Plays": 18,
    "Capacity": 13, "Renewal": 13, "Renewal Horizon": 17, "Potential Proxy": 14,
    "Priority Score": 14, "Engagement Tier": 15, "Two-way": 11, "Last Activity": 14,
    "Meetings": 10, "GHE Seats": 11, "Copilot Seats": 14, "Copilot Attach %": 16,
    "Active Committers": 17, "GHAS Committers": 16, "Plan": 12, "Readiness": 12,
    "Action Owner": 17, "Target Persona": 24, "Next Action": 42, "Exit Criteria": 32,
    "How to Win": 50, "Evidence": 46, "Key Contacts": 34, "Verify In": 16,
}


def _widths(headers):
    return [_WIDTH.get(h, 18) for h in headers]


def _licence_cells(entry):
    """The five licence facts plus the plan, for one account.

    Returns blanks - never zeros - where there is no reading. A zero here claims the
    account has no seats, which is a different and much worse thing to tell a seller
    than "we did not manage to look this one up". They would deprioritise a live
    account on the strength of it.

    Definitions match `ghcp.seat_facts`, so the play sheets and the Sprint Focus sheet
    cannot disagree about the same account.
    """
    if not entry:
        return ["", "", "", "", "", ""]
    enterprise = int(num(entry.get("enterpriseSeatsConsumed")) or 0)
    team = int(num(entry.get("teamSeatsConsumed")) or 0)
    ghe = enterprise + team
    copilot = int(num(entry.get("copilotSeats")) or 0)
    return [
        ghe,
        copilot,
        f"{copilot / ghe:.0%}" if ghe else "",
        int(num(entry.get("maxCommitters")) or 0),
        int(num(entry.get("ghasMeteredCommitters")) or 0),
        (entry.get("planType") or "").title(),
    ]


def _account_row(a, compact=False, licensing=None):
    activity = a.get("activity") or {}
    contacts = "; ".join(f'{c.get("name", "")} ({c.get("title", "")})' for c in a.get("contacts") or [])
    verify = (a.get("dashboards") or [{}])[0].get("url", "")
    entry = (licensing or {}).get(a.get("salesforceId") or "")
    base = [
        a.get("name", ""), a.get("salesforceId", ""), a.get("primaryPlay", ""),
        "; ".join(a.get("plays") or []), a.get("capacityTier", ""),
        a.get("renewal", ""), a.get("renewalHorizon", ""),
        "" if a.get("revenuePotential") is None else a.get("revenuePotential"),
        "" if a.get("priorityScore") is None else a.get("priorityScore"),
        activity.get("tier", ""), "Yes" if activity.get("twoWay") else ("No" if activity.get("status") == "enriched" else "Unknown"),
        activity.get("lastActivity", ""), activity.get("meetings", 0),
    ] + _licence_cells(entry) + [
        a.get("executionReadiness", ""), a.get("nextAction", {}).get("owner", ""),
        a.get("nextAction", {}).get("persona", ""), a.get("nextAction", {}).get("action", ""),
        a.get("nextAction", {}).get("exitCriteria", ""), a.get("winPlan", ""),
        "; ".join(a.get("evidence") or []), contacts, verify,
    ]
    if compact:
        return [base[i] for i in COMPACT_INDEX]
    return base


def _sprint_rows(sprint):
    rows = []
    for rank, a in enumerate((sprint or {}).get("accounts") or [], 1):
        rows.append([
            rank, a.get("name", ""), a.get("primaryPlay", ""), a.get("sprintScore", ""),
            "; ".join(t.get("label", t.get("type", "")) for t in a.get("triggers") or []),
            a.get("whyNow", ""), a.get("renewal", ""), a.get("revenuePotential", ""),
            "; ".join(f'{c.get("name", "")} ({c.get("title", "")})' for c in a.get("contacts") or []),
            "; ".join(n.get("headline", "") for n in a.get("news") or []),
            (a.get("dashboards") or [{}])[0].get("url", ""),
        ])
    return rows


# The Sprint Focus sheet is the meeting-booking queue. When an H1 focus run has produced a
# ranked focus list, that list is the sprint plan: it already carries the seller's overrides,
# the agreed play, the next action and the pipeline. Re-scoring the raw book here would
# contradict the deck built from the same run, so the focus list wins when it is present.
SPRINT_HEADERS_FOCUS = [
    "Rank", "Account", "Play", "Tier", "Motion", "Score", "Why Now",
    "Next Action", "Persona", "Owner", "Exit Criteria", "Win Plan",
    "Open Pipeline", "H1 Pipeline", "Potential", "Renewal", "Key Contacts", "Discovery Gaps",
]
SPRINT_WIDTHS_FOCUS = [7, 30, 11, 20, 13, 8, 52, 52, 30, 28, 46, 60, 14, 14, 14, 14, 34, 44]

SPRINT_HEADERS_SCORE = [
    "Rank", "Account", "Primary Play", "Sprint Score", "Triggers", "Why Now",
    "Renewal", "Potential", "Key Contacts", "News", "Verify In",
]
SPRINT_WIDTHS_SCORE = [8, 28, 14, 13, 30, 60, 14, 13, 36, 45, 16]

# GHCP is the sprint priority, so the queue leads with the two Copilot motions: attaching
# seats to GHE licences already paid for, and getting the users on existing seats to consume
# the credits those seats include. Every GHCP figure below is derived from licensing and
# billing data by ghcp.py; nothing here is asserted.
SPRINT_HEADERS_GHCP = [
    "GHCP Segment", "GHCP #", "H1 #", "Account", "Play", "Tier", "Motion",
    "GHE Seats", "Copilot Seats", "Attach %", "Seat Headroom",
    "Credits/User/Mo", "Allowance Used", "Dormant Seats",
    "Seat Prize (annual)", "Prize Basis", "GHCP Next Step",
    "Why Now", "Persona", "Owner", "Exit Criteria", "Open Pipeline",
    "Key Contacts", "Win Plan",
]
SPRINT_WIDTHS_GHCP = [22, 8, 7, 30, 11, 20, 13, 11, 13, 10, 13, 15, 14, 13,
                      17, 30, 62, 46, 28, 26, 44, 14, 34, 58]

_LED_LABEL = {1: "Microsoft led", 2: "Partner led", 3: "Seller led"}


def _money(value):
    """Pipeline figures are rendered as text so a blank never reads as a zero commitment."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return ""
    return f"${amount:,.0f}" if amount else ""


def _trigger_text(trigger):
    """Focus triggers are dated news/partnership events; scored triggers carry a label."""
    if not isinstance(trigger, dict):
        return str(trigger)
    headline = trigger.get("headline") or trigger.get("label") or trigger.get("type") or ""
    date = trigger.get("date") or ""
    detail = trigger.get("detail") or ""
    text = f"{date}: {headline}".strip(": ") if date else headline
    return f"{text} - {detail}" if detail and detail != headline else text


def _focus_sprint_rows(focus, contacts=None):
    """Turn the ranked H1 focus list into the meeting-booking queue."""
    by_id = (contacts or {}).get("accounts", {}) if isinstance(contacts, dict) else {}
    rows = []
    for a in (focus or {}).get("accounts") or []:
        action = a.get("nextAction") or {}
        triggers = [_trigger_text(t) for t in (a.get("triggers") or [])]
        why = "; ".join(t for t in triggers if t) or a.get("playPriorityReason", "")
        open_pipe = sum(float(o.get("amount") or 0) for o in (a.get("openPipeline") or [])
                        if not o.get("stale"))
        # The focus stage does not carry contacts; fall back to the run's Salesforce
        # enrichment so the queue always answers "who do I call".
        people = a.get("contacts") or by_id.get(a.get("salesforceId")) or []
        who = "; ".join(f'{c.get("name", "")} ({c.get("title", "")})'.strip() for c in people)
        rows.append([
            a.get("rank", ""), a.get("name", ""), a.get("play", ""), a.get("tier", ""),
            _LED_LABEL.get(int(a.get("msftTier") or 3), "Seller led"),
            a.get("compositeScore", ""),
            why,
            action.get("action", ""), action.get("persona", ""), action.get("owner", ""),
            action.get("exitCriteria", "") or action.get("exit", ""),
            a.get("winPlan", ""),
            _money(open_pipe), _money(a.get("h1PipelineValue")), _money(a.get("potentialArr")),
            a.get("renewal", "") or "",
            who or "No contact on file - find one before booking",
            "; ".join(a.get("discoveryGaps") or []),
        ])
    return rows


def _ghcp_sprint_rows(focus, contacts=None, licensing=None):
    """Order the queue by GHCP opportunity and carry the seat and credit facts on each row."""
    if ghcp is None or not licensing:
        return []
    accounts = (focus or {}).get("accounts") or []
    if not accounts:
        return []
    lic_accounts = (licensing or {}).get("accounts") or {}
    graded = ghcp.build(accounts, lic_accounts)
    by_key = {a.get("key"): a for a in accounts}
    by_id = (contacts or {}).get("accounts", {}) if isinstance(contacts, dict) else {}

    rows = []
    for g in graded:
        a = by_key.get(g["key"], {})
        action = a.get("nextAction") or {}
        triggers = [_trigger_text(t) for t in (a.get("triggers") or [])]
        why = "; ".join(t for t in triggers if t) or a.get("playPriorityReason", "")
        open_pipe = sum(float(o.get("amount") or 0) for o in (a.get("openPipeline") or [])
                        if not o.get("stale"))
        people = a.get("contacts") or by_id.get(a.get("salesforceId")) or []
        who = "; ".join(f'{c.get("name", "")} ({c.get("title", "")})'.strip() for c in people)
        used = g["aiuAllowanceUsed"]
        rows.append([
            g["segment"], g["segmentRank"], g["h1Rank"], g["name"],
            a.get("play", ""), a.get("tier", ""),
            _LED_LABEL.get(int(a.get("msftTier") or 3), "Seller led"),
            g["gheSeats"] or "", g["copilotSeats"] or "",
            f'{g["attachRate"]:.0%}' if g["attachRate"] is not None else "",
            g["headroom"] or "",
            f'{g["aiuCreditsPerUserMonth"]:,}' if g["aiuCreditsPerUserMonth"] is not None else "",
            f"{used:.0%}" if used is not None else "",
            g["aiuDormantSeats"] or "",
            _money(g["prize"]), g["rateBasis"], g["nextStep"],
            why, action.get("persona", ""), action.get("owner", ""),
            action.get("exitCriteria", "") or action.get("exit", ""),
            _money(open_pipe),
            who or "No contact on file - find one before booking",
            a.get("winPlan", ""),
        ])
    return rows


PRIORITY_HEADERS = ["GHCP Segment", "H1 #", "GHCP #", "Account", "Play", "Tier", "Motion",
                    "GHE Seats", "H1 Pipeline"]


def priority_rows(focus, licensing):
    """The must-win accounts in H1 rank order.

    The queue is sorted by Copilot opportunity, which deliberately scatters the H1 priority
    order - the top-ranked account can sit near the bottom because its GHE has not landed yet.
    This block restores the priority view on the same sheet and carries each account's GHCP
    position, so the two orderings can be read against each other rather than in separate tabs.
    """
    accounts = (focus or {}).get("accounts") or []
    if not accounts:
        return []
    placing = {}
    if ghcp is not None and licensing:
        for g in ghcp.build(accounts, (licensing or {}).get("accounts") or {}):
            placing[g["key"]] = (g["segment"], g["segmentRank"], g["gheSeats"])
    must_win = [a for a in accounts if str(a.get("tier", "")).startswith("Tier 1")]
    # No tiering in this run: fall back to the top of the composite rank rather than nothing.
    if not must_win:
        must_win = sorted(accounts, key=lambda a: a.get("rank") or 9999)[:10]
    must_win.sort(key=lambda a: a.get("rank") or 9999)

    rows = []
    for a in must_win:
        segment, seg_rank, ghe = placing.get(a.get("key"), ("", "", None))
        rows.append([
            segment, a.get("rank") or "", seg_rank or "", a.get("name", ""),
            a.get("play", ""), a.get("tier", ""),
            _LED_LABEL.get(int(a.get("msftTier") or 3), "Seller led"),
            ghe or "",
            _money(float(a.get("h1PipelineValue") or 0)),
        ])
    return rows


def ghcp_summary_rows(focus, licensing):
    """Segment subtotals for the block above the queue, plus a plain-language note each."""
    if ghcp is None or not licensing:
        return [], []
    accounts = (focus or {}).get("accounts") or []
    if not accounts:
        return [], []
    graded = ghcp.build(accounts, (licensing or {}).get("accounts") or {})
    t = ghcp.totals(graded)
    rows, notes = [], []
    for segment in ghcp.SEGMENT_ORDER:
        s = t[segment]
        rows.append([
            segment, s["accounts"], s["gheSeats"], s["copilotSeats"], s["headroom"],
            # A computed subtotal of zero is a fact, not an absent figure, so it is shown.
            _money(s["prize"]) or "$0", s["dormantSeats"],
        ])
        notes.append(f"{segment} - {ghcp.SEGMENT_BLURB[segment]}")
    b = t["_book"]
    rows.append([
        f'All {b["accounts"]} accounts', b["accounts"], "", b["copilotSeats"], b["headroom"],
        _money(b["prize"]), b["dormantSeats"],
    ])
    notes.append(
        f'AIU overage revenue is ${b["overageValue"]:,.0f} today: no account has exhausted its '
        f'included allowance, so {b["dormantSeats"]:,.0f} of {b["copilotSeats"]:,} Copilot seats '
        "are not yet returning the value they were bought for. Activation protects the renewal "
        "and earns the right to expand seats."
    )
    return rows, notes


GHCP_SUMMARY_HEADERS = ["GHCP Segment", "Accounts", "GHE Seats", "Copilot Seats",
                        "Seat Headroom", "Seat Prize (annual)", "Dormant Seats"]
# Deliberately the queue's own widths: both tables occupy the same columns, and the queue is
# the sheet's main content, so it owns the layout.
GHCP_SUMMARY_WIDTHS = SPRINT_WIDTHS_GHCP[:len(GHCP_SUMMARY_HEADERS)]


def sprint_sheet_spec(sprint, focus, contacts=None, licensing=None):
    """Pick the sprint source and return (headers, rows, widths, source label)."""
    ghcp_rows = _ghcp_sprint_rows(focus, contacts, licensing)
    if ghcp_rows:
        return (SPRINT_HEADERS_GHCP, ghcp_rows, SPRINT_WIDTHS_GHCP,
                "Ranked H1 focus list, re-ordered around GHCP: seat expansion first, then AIU "
                "activation, then accounts where GHE has to land before Copilot has anything "
                "to attach to. Seller overrides applied.")
    focus_rows = _focus_sprint_rows(focus, contacts)
    if focus_rows:
        return (SPRINT_HEADERS_FOCUS, focus_rows, SPRINT_WIDTHS_FOCUS,
                "Ranked H1 focus list (focus-accounts.json), seller overrides applied. "
                "GHCP segmentation unavailable - no licensing data in this run.")
    return (SPRINT_HEADERS_SCORE, _sprint_rows(sprint), SPRINT_WIDTHS_SCORE,
            "Trigger-scored shortlist (sprint-focus.json).")


def _cell(value, style=11, hyperlink=None):
    return {"value": value, "style": style, "hyperlink": hyperlink}


def _sheet_xml(rows, widths=None, freeze_row=None, filter_row=None, merges=None, tab_color="1D4ED8"):
    hyperlinks, rels = [], []
    xml_rows = []
    for ri, row in enumerate(rows, 1):
        attrs = f' r="{ri}"'
        if ri == 1:
            attrs += ' ht="30" customHeight="1"'
        cells = []
        for ci, raw in enumerate(row, 1):
            item = raw if isinstance(raw, dict) else _cell(raw)
            value, style, link = item.get("value", ""), item.get("style", 11), item.get("hyperlink")
            ref = f"{_column_name(ci)}{ri}"
            if value is None or value == "":
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cell = f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
            else:
                cell = f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>'
            if link:
                rid = f"rId{len(rels) + 1}"
                rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{_xml_escape(link)}" TargetMode="External"/>')
                hyperlinks.append(f'<hyperlink ref="{ref}" r:id="{rid}"/>')
            cells.append(cell)
        xml_rows.append(f'<row{attrs}>{"".join(cells)}</row>')
    cols = ""
    if widths:
        cols = "<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + "</cols>"
    view = f'<sheetViews><sheetView workbookViewId="0" tabSelected="1"><selection activeCell="A1" sqref="A1"/></sheetView></sheetViews>'
    pane = f'<pane ySplit="{freeze_row}" topLeftCell="A{freeze_row + 1}" activePane="bottomLeft" state="frozen"/>' if freeze_row else ""
    # CT_Selection's attribute is `pane`, not `activePane`; the latter is only valid on
    # CT_Pane, and strict readers reject the sheet outright.
    views = f'<sheetViews><sheetView workbookViewId="0">{pane}<selection pane="bottomLeft" activeCell="A{(freeze_row or 0) + 1}" sqref="A{(freeze_row or 0) + 1}"/></sheetView></sheetViews>'
    filt = f'<autoFilter ref="A{filter_row}:{"%s%d" % (_column_name(len(rows[filter_row - 1])), len(rows))}"/>' if filter_row else ""
    merge_xml = f'<mergeCells count="{len(merges or [])}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges or []) + "</mergeCells>" if merges else ""
    hyperlink_xml = f"<hyperlinks>{''.join(hyperlinks)}</hyperlinks>" if hyperlinks else ""
    rel_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rels)}</Relationships>' if rels else None
    xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetPr><tabColor rgb="FF{tab_color}"/></sheetPr>{views}{cols}<sheetData>{"".join(xml_rows)}</sheetData>{merge_xml}{hyperlink_xml}{filt}</worksheet>'
    return xml, rel_xml


def write_xlsx_minimal(path, report, sprint=None, focus=None, contacts=None, licensing=None):
    """Dependency-free XLSX writer, used when xlsxwriter is unavailable.

    This was previously also named write_xlsx, so the xlsxwriter version below silently
    shadowed it and the fallback could never run.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    accounts = report.get("accounts") or []
    _sprint_headers, _sprint_spec_rows, _sprint_widths, _sprint_source = sprint_sheet_spec(sprint, focus, contacts, licensing)
    # The GHCP layout carries no score column at all, so this is best-effort.
    _score_col = next((_sprint_headers.index(h) for h in ("Score", "Sprint Score", "Seat Prize (annual)")
                       if h in _sprint_headers), None)
    _sprint_top_score = (_sprint_spec_rows[0][_score_col]
                         if _sprint_spec_rows and _score_col is not None else "")
    summary = report.get("playSummary") or []
    stats = report.get("stats") or {}
    activity = report.get("activity") or {}

    account_headers = ACCOUNT_HEADERS
    compact_headers = COMPACT_HEADERS
    # Found by name. These used to be hardcoded indices, and the header list had drifted
    # one column away from the row builder, so the fallback workbook put every All
    # Accounts heading over the wrong data and hyperlinked the contacts column.
    all_link_col = account_headers.index("Verify In")
    compact_link_col = compact_headers.index("Verify In")
    lic_accounts = (licensing or {}).get("accounts") or {}
    sheet_defs = {}

    summary_rows = [
        [_cell("FY27 TERRITORY PLAN", 1)],
        [_cell("A governed account strategy for Innovate, Trust, Scale, and meeting-booking execution", 2)],
        [_cell(f"Source: {report.get('sourceName', '')}  |  Refreshed: {report.get('generatedAt', '')}", 15)],
        [],
        [_cell("PORTFOLIO AT A GLANCE", 3)],
        [_cell("Normalized Accounts", 9), _cell(report.get("accountCount", 0), 10), _cell("Source Rows", 9), _cell(stats.get("sourceRows", 0), 10), _cell("Rows Collapsed", 9), _cell(stats.get("duplicateRows", 0), 10)],
        [_cell("Two-way Accounts", 9), _cell(activity.get("twoWayAccounts", 0), 10), _cell("Activity Coverage", 9), _cell(f'{activity.get("coveragePct", 0)}%', 10), _cell("Unclassified", 9), _cell(stats.get("unclassifiedAccounts", 0), 10)],
        [],
        [_cell("PRIMARY PLAY COVERAGE", 3)],
        [_cell("Play", 4), _cell("Primary Accounts", 4), _cell("Qualified incl. Secondary", 4), _cell("High Confidence", 4), _cell("Activity Prioritized", 4), _cell("Two-way Accounts", 4), _cell("Execution intent", 4)],
    ]
    for p in summary:
        guidance = PLAY_GUIDANCE.get(p.get("play"), {})
        summary_rows.append([
            _cell(p.get("play", ""), {"Innovate": 4, "Trust": 5, "Scale": 6, "Unclassified": 7}.get(p.get("play"), 7)),
            _cell(p.get("accounts", 0), 13), _cell(p.get("qualifiedAccounts", 0), 13),
            _cell(p.get("highConfidence", 0), 13), _cell(p.get("activityPrioritized", 0), 13),
            _cell(p.get("twoWayAccounts", 0), 13), _cell(guidance.get("objective", ""), 14),
        ])
    summary_rows += [
        [],
        [_cell("HOW I WILL EXECUTE", 3)],
        [_cell("1. Segment", 4), _cell("Assign each normalized account to a primary play using observed product signals; retain secondary plays for expansion context.", 14)],
        [_cell("2. Prioritize", 4), _cell("Sequence accounts by verified two-way engagement, meeting activity, potential proxy, renewal horizon, and execution readiness.", 14)],
        [_cell("3. Mobilize", 4), _cell("Use the play-specific motion, target persona, named contacts, and evidence to create a relevant meeting hypothesis.", 14)],
        [_cell("4. Advance", 4), _cell("Exit discovery only when the sponsor, business problem, success measure, dated next step, and owner are explicit.", 14)],
        [],
        [_cell("SPRINT FOCUS", 3)],
        [_cell("Meeting-booking shortlist", 9), _cell(len(_sprint_spec_rows), 10), _cell("Top score", 9), _cell(_sprint_top_score, 10), _cell("Scoring note", 9), _cell("Decision-support ranking, not forecast or propensity.", 14)],
        [],
        [_cell("GOVERNANCE", 3)],
        [_cell("This workbook is decision support. Potential is a relative proxy, not ARR or forecast. Activity and two-way status reflect available Salesforce evidence; Unknown is not cold. Seller validation is required before committing territory or forecast decisions.", 15)],
    ]
    sheet_defs["Executive Summary"] = (summary_rows, [24, 16, 24, 16, 23, 16, 55], None, None, ["A1:G1", "A2:G2", "A5:G5", "A9:G9", "A16:G16", "A22:G22", "A25:G25"], "1D4ED8")

    all_rows = [[_cell(h, 4) for h in account_headers]]
    for a in accounts:
        row = _account_row(a, licensing=lic_accounts)
        out = []
        for i, value in enumerate(row):
            link = value if i == all_link_col and value else None
            out.append(_cell("Open dashboard" if link else value, 14 if link else (12 if len(all_rows) % 2 == 0 else 11), link))
        all_rows.append(out)
    sheet_defs["All Accounts"] = (all_rows, _widths(account_headers), 1, 1, [], "1D4ED8")

    for play in ("Innovate", "Trust", "Scale", "Unclassified"):
        guidance = PLAY_GUIDANCE[play]
        matching = [a for a in accounts if a.get("primaryPlay") == play]
        rows = [
            [_cell(play.upper(), {"Innovate": 4, "Trust": 5, "Scale": 6, "Unclassified": 7}[play])],
            [_cell(guidance["objective"], 15)],
            [_cell("Account fit", 9), _cell(guidance["fit"], 14)],
            [_cell("Recommended motion", 9), _cell(guidance["motion"], 14)],
            [_cell("Seller actions", 9), _cell(guidance["actions"], 14)],
            [_cell("Exit criteria", 9), _cell(guidance["exit"], 14)],
            [],
            [_cell(h, 4) for h in compact_headers],
        ]
        for a in matching:
            vals = _account_row(a, compact=True, licensing=lic_accounts)
            rows.append([_cell("Open dashboard" if i == compact_link_col and value else value, 14 if i == compact_link_col and value else (12 if len(rows) % 2 == 0 else 11), value if i == compact_link_col and value else None) for i, value in enumerate(vals)])
        _span = _column_name(len(compact_headers) - 1)
        sheet_defs[play] = (rows, _widths(compact_headers), 8, 8,
                            ["A1:%s1" % _span, "A2:%s2" % _span], {"Innovate": "1D4ED8", "Trust": "0F766E", "Scale": "7C3AED", "Unclassified": "475569"}[play])

    headers_s, data_s, widths_s = _sprint_headers, _sprint_spec_rows, _sprint_widths
    link_col = headers_s.index("Verify In") if "Verify In" in headers_s else -1
    sprint_rows = [[_cell(h, 4) for h in headers_s]]
    for row in data_s:
        sprint_rows.append([_cell("Open dashboard" if i == link_col and value else value, 14 if i == link_col and value else (12 if len(sprint_rows) % 2 == 0 else 11), value if i == link_col and value else None) for i, value in enumerate(row)])
    sheet_defs["Sprint Focus"] = (sprint_rows, widths_s, 1, 1, [], "F59E0B")

    methodology = [
        [_cell("METHODOLOGY & GOVERNANCE", 1)],
        [_cell("How to read this workbook", 3)],
        [_cell("The Executive Summary explains the portfolio strategy. All Accounts is the governed operating list. Each play tab combines the play's objective and execution motion with its primary-play accounts. Sprint Focus is the meeting-booking queue. Use Methodology to interpret signals and limitations.", 14)],
        [],
        [_cell("Play", 4), _cell("Fit criteria", 4), _cell("Execution principle", 4)],
    ]
    for play in ("Innovate", "Trust", "Scale", "Unclassified"):
        methodology.append([_cell(play, 7), _cell(PLAY_GUIDANCE[play]["fit"], 14), _cell(PLAY_GUIDANCE[play]["motion"], 14)])
    methodology += [
        [],
        [_cell("Ranking and signals", 3)],
        [_cell("Priority score", 9), _cell("Decision-support ordering combines verified two-way status, potential proxy, engagement, and execution readiness. It is not propensity, forecast, or ARR.", 14)],
        [_cell("Sprint score", 9), _cell("Meeting-booking trigger score combines two-way recency, open opportunity, renewal proximity, GitHub recommendation, Gong recency, potential, and named contact readiness.", 14)],
        [_cell("Potential proxy", 9), _cell(report.get("governance", {}).get("potentialDefinition", ""), 14)],
        [_cell("Activity", 9), _cell(report.get("governance", {}).get("engagementDefinition", ""), 14)],
        [_cell("Unclassified", 9), _cell(report.get("governance", {}).get("unclassifiedDefinition", ""), 14)],
        [],
        [_cell("Source coverage", 3)],
        [_cell("Normalized accounts", 9), _cell(report.get("accountCount", 0), 10)],
        [_cell("Parent/child rollup groups", 9), _cell(stats.get("parentChildGroups", 0), 10)],
        [_cell("Duplicate rows collapsed", 9), _cell(stats.get("duplicateRows", 0), 10)],
        [_cell("Activity window", 9), _cell(f'{activity.get("windowDays", 0)} days', 10)],
        [_cell("Last activity refresh", 9), _cell(activity.get("asOf", "") or "Not stated", 10)],
        [],
        [_cell("Interpretation rule", 3)],
        [_cell("Evidence earns a meeting hypothesis; it does not prove buying intent. Validate the account context, stakeholder, business outcome, and next step in Salesforce before treating a row as a committed plan.", 15)],
    ]
    sheet_defs["Methodology"] = (methodology, [25, 74, 74], None, None, ["A1:C1", "A2:C2", "A8:C8", "A16:C16", "A24:C24"], "64748B")

    sheet_names = list(sheet_defs)
    n = len(sheet_names)
    overrides = ('<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                 + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1))
                 + "".join(f'<Override PartName="/xl/worksheets/_rels/sheet{i}.xml.rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' for i, (name, spec) in enumerate(sheet_defs.items(), 1) if _sheet_xml(*spec)[1]))
    content_types = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>'
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    sheet_tags = "".join(f'<sheet name="{_xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheet_names, 1))
    wb = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView xWindow="0" yWindow="0" windowWidth="18000" windowHeight="12000"/></bookViews><sheets>{sheet_tags}</sheets></workbook>'
    wb_rel_tags = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
    wb_rels = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{wb_rel_tags}</Relationships>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1)) + "".join(f'<Override PartName="/xl/worksheets/_rels/sheet{i}.xml.rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' for i, (name, spec) in enumerate(sheet_defs.items(), 1) if _sheet_xml(*spec)[1]) + '</Types>')
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", STYLE_XML)
        for i, name in enumerate(sheet_names, 1):
            xml, rel_xml = _sheet_xml(*sheet_defs[name])
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
            if rel_xml:
                z.writestr(f"xl/worksheets/_rels/sheet{i}.xml.rels", rel_xml)

def write_xlsx(path, report, sprint=None, focus=None, contacts=None, licensing=None):
    try:
        import xlsxwriter
    except ImportError:
        # Fall back to the dependency-free writer rather than failing the whole export.
        return write_xlsx_minimal(path, report, sprint, focus, contacts, licensing)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    accounts = report.get("accounts") or []
    # The exec summary card must count whatever the Sprint Focus sheet actually shows.
    sprint_rows_count = sprint_sheet_spec(sprint, focus, contacts, licensing)[1]
    stats = report.get("stats") or {}
    activity = report.get("activity") or {}
    summary = {p.get("play"): p for p in report.get("playSummary") or []}
    wb = xlsxwriter.Workbook(path)
    wb.set_properties({
        "title": "FY27 Territory Plan",
        "subject": "Governed account strategy and execution plan",
        "author": "GitHub Revenue",
        "comments": "Decision-support workbook; seller validation required.",
    })

    navy = "#0F172A"
    blue = "#1D4ED8"
    teal = "#0F766E"
    purple = "#7C3AED"
    slate = "#475569"
    amber = "#F59E0B"
    light = "#F8FAFC"
    border = "#CBD5E1"
    text = "#0F172A"
    white = "#FFFFFF"

    title_fmt = wb.add_format({"bold": True, "font_size": 22, "font_color": white, "bg_color": navy, "align": "left", "valign": "vcenter"})
    subtitle_fmt = wb.add_format({"font_size": 11, "font_color": "#DBEAFE", "bg_color": navy, "text_wrap": True, "valign": "vcenter"})
    section_fmt = wb.add_format({"bold": True, "font_size": 12, "font_color": white, "bg_color": navy, "align": "left", "valign": "vcenter"})
    label_fmt = wb.add_format({"bold": True, "font_size": 9, "font_color": "#475569", "bg_color": "#E2E8F0", "border": 1, "border_color": border, "align": "center", "valign": "vcenter", "text_wrap": True})
    metric_fmt = wb.add_format({"bold": True, "font_size": 20, "font_color": navy, "bg_color": white, "border": 1, "border_color": border, "align": "center", "valign": "vcenter"})
    body_fmt = wb.add_format({"font_size": 10, "font_color": text, "bg_color": white, "border": 1, "border_color": border, "valign": "top", "text_wrap": True})
    body_alt_fmt = wb.add_format({"font_size": 10, "font_color": text, "bg_color": "#F8FAFC", "border": 1, "border_color": border, "valign": "top", "text_wrap": True})
    number_fmt = wb.add_format({"font_size": 10, "font_color": text, "bg_color": white, "border": 1, "border_color": border, "num_format": "0.0", "align": "right"})
    integer_fmt = wb.add_format({"font_size": 10, "font_color": text, "bg_color": white, "border": 1, "border_color": border, "num_format": "#,##0", "align": "right"})
    note_fmt = wb.add_format({"font_size": 10, "font_color": "#334155", "bg_color": "#EFF6FF", "border": 1, "border_color": "#93C5FD", "text_wrap": True, "valign": "top"})
    link_fmt = wb.add_format({"font_size": 10, "font_color": blue, "underline": True, "bg_color": white, "border": 1, "border_color": border, "text_wrap": True})
    score_fmt = wb.add_format({"font_size": 10, "font_color": text, "bg_color": "#DCFCE7", "border": 1, "border_color": border, "num_format": "0.0", "align": "right"})
    play_formats = {
        "Innovate": wb.add_format({"bold": True, "font_color": white, "bg_color": blue, "border": 1, "border_color": blue}),
        "Trust": wb.add_format({"bold": True, "font_color": white, "bg_color": teal, "border": 1, "border_color": teal}),
        "Scale": wb.add_format({"bold": True, "font_color": white, "bg_color": purple, "border": 1, "border_color": purple}),
        "Unclassified": wb.add_format({"bold": True, "font_color": white, "bg_color": slate, "border": 1, "border_color": slate}),
    }
    guidance = PLAY_GUIDANCE

    def safe(v):
        return "" if v is None else v

    # One row builder for both writers. Two hand-maintained copies is how the fallback
    # writer ended up a column out of step with its own headers.
    lic_accounts = (licensing or {}).get("accounts") or {}

    def account_values(a):
        return _account_row(a, licensing=lic_accounts)

    def setup(ws, tab_color, widths):
        ws.set_tab_color(tab_color)
        ws.hide_gridlines(2)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_margins(left=0.25, right=0.25, top=0.5, bottom=0.5)
        for col, width in enumerate(widths):
            ws.set_column(col, col, width)

    def write_table(ws, start_row, headers, rows, widths, tab_color, table_name):
        setup(ws, tab_color, widths)
        ws.freeze_panes(start_row + 1, 0)
        if not rows:
            # add_table refuses a zero-row table and silently writes nothing, which is how
            # an absent sprint input produced a completely blank sheet. Write the header
            # so the sheet still explains itself.
            ws.write_row(start_row, 0, headers, section_fmt)
            return
        ws.add_table(start_row, 0, start_row + len(rows), len(headers) - 1, {
            "name": table_name,
            "style": "Table Style Medium 2",
            "columns": [{"header": h} for h in headers],
            "data": rows,
        })
        for r in range(start_row + 1, start_row + len(rows) + 1):
            ws.set_row(r, 38)

    # Executive Summary: an actual visual dashboard, not a data dump.
    ws = wb.add_worksheet("Executive Summary")
    setup(ws, blue, [22, 17, 22, 17, 22, 17, 22, 17, 2, 20, 18, 18])
    ws.set_row(0, 34)
    ws.set_row(1, 30)
    ws.merge_range("A1:H1", "FY27 TERRITORY PLAN", title_fmt)
    ws.merge_range("A2:H2", "A governed account strategy for Innovate, Trust, Scale, and meeting-booking execution", subtitle_fmt)
    ws.merge_range("A3:H3", f"Source: {report.get('sourceName', '')}   |   Refreshed: {report.get('generatedAt', '')}", subtitle_fmt)
    ws.merge_range("A5:H5", "PORTFOLIO AT A GLANCE", section_fmt)
    ghcp_book = {}
    if ghcp is not None and licensing and (focus or {}).get("accounts"):
        ghcp_book = ghcp.totals(
            ghcp.build((focus or {}).get("accounts") or [],
                       (licensing or {}).get("accounts") or {}))["_book"]
    cards = [
        ("Normalized Accounts", report.get("accountCount", 0)),
        ("Rows Collapsed", stats.get("duplicateRows", 0)),
        ("Two-way Accounts", activity.get("twoWayAccounts", 0)),
        ("Activity Coverage", f'{activity.get("coveragePct", 0)}%'),
        ("Unclassified", stats.get("unclassifiedAccounts", 0)),
        ("Sprint Candidates", len(sprint_rows_count)),
        ("Parent/Child Groups", stats.get("parentChildGroups", 0)),
        ("Activity Window", f'{activity.get("windowDays", 0)} days'),
    ]
    if ghcp_book:
        # GHCP is the sprint priority, so its two measures belong on the front page.
        cards += [
            ("Copilot Seat Headroom", ghcp_book["headroom"]),
            ("Headroom Value", f'${ghcp_book["prize"]:,.0f}'),
            ("Copilot Seats Live", ghcp_book["copilotSeats"]),
            ("Dormant Seats", ghcp_book["dormantSeats"]),
        ]
    for i, (label, value) in enumerate(cards):
        col = (i % 4) * 2
        row = 5 + (i // 4) * 2
        ws.write(row, col, label, label_fmt)
        ws.write(row, col + 1, value, metric_fmt)
        ws.set_row(row, 25)
    # Four extra GHCP cards add a card band, so everything below shifts by two rows.
    off = 2 if ghcp_book else 0
    ws.merge_range(9 + off, 0, 9 + off, 7, "PRIMARY PLAY COVERAGE", section_fmt)
    play_rows = []
    for play in ("Innovate", "Trust", "Scale", "Unclassified"):
        p = summary.get(play, {})
        play_rows.append([play, p.get("accounts", 0), p.get("qualifiedAccounts", 0), p.get("highConfidence", 0), p.get("twoWayAccounts", 0)])
    write_table(ws, 10 + off, ["Play", "Primary Accounts", "Qualified incl. Secondary", "High Confidence", "Two-way Accounts"], play_rows, [18, 16, 22, 16, 16], blue, "PlayCoverage")
    ws.merge_range(16 + off, 0, 16 + off, 7, "HOW I WILL EXECUTE", section_fmt)
    execution = [
        ("1. Segment", "Assign each normalized account to a primary play from observed product signals; retain secondary plays for expansion context."),
        ("2. Prioritize", "Sequence accounts by verified two-way engagement, meeting activity, potential proxy, renewal horizon, and execution readiness."),
        ("3. Mobilize", "Use the play-specific motion, target persona, named contacts, and evidence to create a relevant meeting hypothesis."),
        ("4. Advance", "Exit discovery only when the sponsor, business problem, success measure, dated next step, and owner are explicit."),
    ]
    for r, (label, statement) in enumerate(execution, 18 + off):
        ws.write(r, 0, label, play_formats["Innovate"])
        ws.merge_range(r, 1, r, 7, statement, note_fmt)
        ws.set_row(r, 34)
    ws.merge_range(22 + off, 0, 22 + off, 7, "VISUAL SUMMARY", section_fmt)
    chart = wb.add_chart({"type": "doughnut"})
    chart.add_series({"name": "Primary accounts", "categories": ["Executive Summary", 11 + off, 0, 14 + off, 0], "values": ["Executive Summary", 11 + off, 1, 14 + off, 1], "points": [{"fill": {"color": blue}}, {"fill": {"color": teal}}, {"fill": {"color": purple}}, {"fill": {"color": slate}}]})
    chart.set_title({"name": "Primary Play Mix"})
    chart.set_legend({"position": "bottom"})
    chart.set_style(10)
    ws.insert_chart(23 + off, 0, chart, {"x_scale": 1.05, "y_scale": 1.1})
    chart2 = wb.add_chart({"type": "column"})
    chart2.add_series({"name": "Accounts", "categories": ["Executive Summary", 11 + off, 0, 14 + off, 0], "values": ["Executive Summary", 11 + off, 1, 14 + off, 1], "fill": {"color": blue}, "border": {"none": True}})
    chart2.set_title({"name": "Accounts by Primary Play"})
    chart2.set_legend({"none": True})
    chart2.set_y_axis({"major_gridlines": {"visible": False}})
    ws.insert_chart(23 + off, 4, chart2, {"x_scale": 1.05, "y_scale": 1.1})
    ws.merge_range(39 + off, 0, 39 + off, 7, "GOVERNANCE", section_fmt)
    ws.merge_range(40 + off, 0, 42 + off, 7, "Decision support only: potential is a relative proxy, not ARR or forecast. Activity and two-way status reflect available Salesforce evidence; Unknown is not cold. Seller validation is required before committing territory, forecast, or investment decisions.", note_fmt)
    ws.set_row(39 + off, 24)
    ws.set_row(40 + off, 32)

    account_headers = ACCOUNT_HEADERS
    all_rows = []
    for a in accounts:
        vals = account_values(a)
        all_rows.append(vals)
    ws = wb.add_worksheet("All Accounts")
    write_table(ws, 0, account_headers, all_rows, _widths(account_headers), blue, "AllAccounts")
    _ah = account_headers
    ws.set_column(_ah.index("Next Action"), _ah.index("Evidence"), 42, body_fmt)
    ws.set_column(_ah.index("Key Contacts"), _ah.index("Verify In"), 28, link_fmt)
    _score = _ah.index("Priority Score")
    ws.conditional_format(1, _score, len(all_rows), _score, {"type": "3_color_scale", "min_color": "#FEE2E2", "mid_color": "#FEF3C7", "max_color": "#DCFCE7"})

    for play in ("Innovate", "Trust", "Scale", "Unclassified"):
        ws = wb.add_worksheet(play)
        _last = len(COMPACT_HEADERS) - 1
        _span = _column_name(_last)
        setup(ws, {"Innovate": blue, "Trust": teal, "Scale": purple, "Unclassified": slate}[play], _widths(COMPACT_HEADERS))
        ws.set_row(0, 28)
        ws.merge_range("A1:%s1" % _span, play.upper(), wb.add_format({"bold": True, "font_size": 18, "font_color": white, "bg_color": {"Innovate": blue, "Trust": teal, "Scale": purple, "Unclassified": slate}[play]}))
        g = guidance[play]
        ws.merge_range("A2:%s2" % _span, g["objective"], subtitle_fmt)
        for r, (label, value) in enumerate((("Account fit", g["fit"]), ("Recommended motion", g["motion"]), ("Seller actions", g["actions"]), ("Exit criteria", g["exit"])), 3):
            ws.write(r, 0, label, label_fmt)
            ws.merge_range(r, 1, r, _last, value, note_fmt)
            ws.set_row(r, 34)
        matching = [a for a in accounts if a.get("primaryPlay") == play]
        compact_headers = COMPACT_HEADERS
        compact_rows = []
        for a in matching:
            vals = account_values(a)
            compact_rows.append([vals[i] for i in COMPACT_INDEX])
        write_table(ws, 8, compact_headers, compact_rows, _widths(compact_headers), {"Innovate": blue, "Trust": teal, "Scale": purple, "Unclassified": slate}[play], f"{play.replace(' ', '')}Accounts")
        ws.set_column(compact_headers.index("Next Action"), compact_headers.index("Key Contacts"), 42, body_fmt)
        _vi = compact_headers.index("Verify In")
        ws.set_column(_vi, _vi, 28, link_fmt)
        if compact_rows:
            _cs = compact_headers.index("Priority Score")
            ws.conditional_format(9, _cs, 8 + len(compact_rows), _cs, {"type": "3_color_scale", "min_color": "#FEE2E2", "mid_color": "#FEF3C7", "max_color": "#DCFCE7"})

    ws = wb.add_worksheet("Sprint Focus")
    sprint_headers, sprint_rows, sprint_widths, sprint_source = sprint_sheet_spec(
        sprint, focus, contacts, licensing)
    summary_rows, summary_notes = (ghcp_summary_rows(focus, licensing)
                                   if "GHCP #" in sprint_headers else ([], []))

    def prose(row, text):
        """Write a full-width line of prose without letting it blow up the row height.

        A wrapping format in a single 22-character column makes Excel auto-fit the row to a
        dozen lines, which pushed the account tables so far down the sheet that they looked
        missing. Merging across the readable width and pinning the height keeps the text where
        the reader expects it.
        """
        span = 7
        ws.merge_range(row, 0, row, span, text, note_fmt)
        chars_per_line = sum(sprint_widths[:span + 1]) if sprint_widths else 120
        lines = max(1, math.ceil(len(text) / max(chars_per_line, 40)))
        ws.set_row(row, 14 * lines + 6)

    table_start = 2
    if summary_rows:
        # Subtotals sit above the queue rather than interleaved, so the queue stays a single
        # sortable, filterable table. The two tables share columns, so the summary carries no
        # wide prose column of its own - the explanations go in overflow rows beneath it.
        write_table(ws, 3, GHCP_SUMMARY_HEADERS, summary_rows, GHCP_SUMMARY_WIDTHS,
                    amber, "GhcpSummary")
        note_row = 5 + len(summary_rows)
        for offset, text in enumerate(summary_notes):
            prose(note_row + offset, text)
        table_start = note_row + len(summary_notes) + 2

        prio = priority_rows(focus, licensing)
        if prio:
            ws.write(table_start - 1, 0,
                     "PRIORITY ACCOUNTS - H1 RANK ORDER (must-win; GHCP # is the account's "
                     "position within its segment in the queue below)", section_fmt)
            write_table(ws, table_start, PRIORITY_HEADERS, prio,
                        SPRINT_WIDTHS_GHCP[:len(PRIORITY_HEADERS)], teal, "PriorityAccounts")
            table_start += len(prio) + 3

        ws.write(table_start - 1, 0, "THE QUEUE - work top-down within each segment", section_fmt)
    write_table(ws, table_start, sprint_headers, sprint_rows, sprint_widths, amber, "SprintFocus")
    ws.set_row(0, 26)
    ws.write(0, 0, "SPRINT FOCUS - GHCP BOOKING QUEUE" if summary_rows
             else "SPRINT FOCUS - MEETING BOOKING QUEUE", section_fmt)
    prose(1, f"Source: {sprint_source} Work top-down; every row needs a dated next step and a named owner before it counts.")
    if sprint_rows:
        bar_col = next((sprint_headers.index(h) for h in
                        ("Seat Headroom", "Score", "Sprint Score") if h in sprint_headers), None)
        if bar_col is not None:
            ws.conditional_format(table_start + 1, bar_col,
                                  table_start + len(sprint_rows), bar_col,
                                  {"type": "data_bar", "bar_color": amber})
    for idx, header in enumerate(sprint_headers):
        if header in ("Why Now", "Next Action", "Win Plan", "Exit Criteria", "Discovery Gaps",
                      "Triggers", "Key Contacts", "News", "GHCP Next Step", "Prize Basis"):
            ws.set_column(idx, idx, sprint_widths[idx], body_fmt)
    if "Verify In" in sprint_headers:
        ws.set_column(sprint_headers.index("Verify In"), sprint_headers.index("Verify In"), 28, link_fmt)

    ws = wb.add_worksheet("Methodology")
    setup(ws, slate, [24, 70, 70])
    ws.set_row(0, 32)
    ws.merge_range("A1:C1", "METHODOLOGY & GOVERNANCE", title_fmt)
    ws.merge_range("A2:C2", "How to read the territory plan and interpret the signals", subtitle_fmt)
    ws.merge_range("A4:C5", "The Executive Summary explains the portfolio strategy. All Accounts is the governed operating list. Each play tab combines the play's objective and execution motion with its primary-play accounts. Sprint Focus is the meeting-booking queue.", note_fmt)
    ws.write_row("A7", ["Play", "Fit criteria", "Execution principle"], section_fmt)
    for r, play in enumerate(("Innovate", "Trust", "Scale", "Unclassified"), 7):
        ws.write(r, 0, play, play_formats[play])
        ws.write(r, 1, guidance[play]["fit"], body_fmt)
        ws.write(r, 2, guidance[play]["motion"], body_fmt)
        ws.set_row(r, 48)
    ws.merge_range("A13:C13", "RANKING AND SIGNALS", section_fmt)
    method_rows = [
        ("Priority score", "Decision-support ordering combines verified two-way status, potential proxy, engagement, and execution readiness. It is not propensity, forecast, or ARR."),
        ("Sprint score", "Meeting-booking trigger score combines two-way recency, open opportunity, renewal proximity, GitHub recommendation, Gong recency, potential, and named contact readiness."),
        ("Potential proxy", report.get("governance", {}).get("potentialDefinition", "")),
        ("Activity", report.get("governance", {}).get("engagementDefinition", "")),
        ("Unclassified", report.get("governance", {}).get("unclassifiedDefinition", "")),
    ]
    for r, (label, value) in enumerate(method_rows, 13):
        ws.write(r, 0, label, label_fmt)
        ws.merge_range(r, 1, r, 2, value, body_fmt)
        ws.set_row(r, 38)
    ws.merge_range("A20:C20", "SOURCE COVERAGE", section_fmt)
    source_rows = [
        ("Normalized accounts", report.get("accountCount", 0)),
        ("Parent/child rollup groups", stats.get("parentChildGroups", 0)),
        ("Duplicate rows collapsed", stats.get("duplicateRows", 0)),
        ("Activity window", f'{activity.get("windowDays", 0)} days'),
        ("Last activity refresh", activity.get("asOf", "") or "Not stated"),
    ]
    for r, (label, value) in enumerate(source_rows, 20):
        ws.write(r, 0, label, label_fmt)
        ws.write(r, 1, value, metric_fmt)
    ws.merge_range("A27:C28", "Interpretation rule: evidence earns a meeting hypothesis; it does not prove buying intent. Validate the account context, stakeholder, business outcome, and dated next step in Salesforce before treating a row as a committed plan.", note_fmt)

    wb.close()


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    # --from-report rebuilds the workbook from the report already in the run directory.
    # The normal path re-derives the report from the raw SuperDash export, which discards
    # any play overrides applied since, so rebuilding a sheet must never go through it.
    if sys.argv[1] == "--from-report":
        output_dir = sys.argv[2]
        report = _load_json(os.path.join(output_dir, "fy27-territory-plan.json"))
        if not report:
            raise SystemExit(f"No fy27-territory-plan.json in {output_dir}.")
    else:
        input_path, output_dir = sys.argv[1], sys.argv[2]
        activity = {}
        if len(sys.argv) > 3 and os.path.exists(sys.argv[3]):
            with open(sys.argv[3], encoding="utf-8") as f: activity = json.load(f)
        source_name = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else os.path.basename(input_path)
        rows = read_input(input_path)
        if not rows or "salesforce_name" not in rows[0]:
            raise SystemExit("Unsupported workbook: expected an Export tab with salesforce_name.")
        contacts = _load_json(os.path.join(output_dir, "salesforce-contacts.json")) or {}
        report = analyze(rows, source_name, activity, contacts)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "fy27-territory-plan.json"), "w", encoding="utf-8") as f:
            json.dump(report, f)
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "fy27-territory-plan.json")
    workbook_path = os.path.join(output_dir, "FY27 Territory Plan.xlsx")
    sprint = _load_json(os.path.join(output_dir, "sprint-focus.json"))
    focus = _load_json(os.path.join(output_dir, "focus-accounts.json"))
    sheet_contacts = _load_json(os.path.join(output_dir, "salesforce-contacts.json"))
    # Licensing drives the GHCP segmentation. Absent, the queue falls back to H1 rank order.
    licensing = _load_json(os.path.join(output_dir, "licensing.json"))
    write_xlsx(workbook_path, report, sprint, focus, sheet_contacts, licensing)
    _headers, sprint_rows, _w, sprint_source = sprint_sheet_spec(sprint, focus, sheet_contacts, licensing)
    print(json.dumps({"reportPath": report_path, "workbookPath": workbook_path,
                      "accountCount": report["accountCount"],
                      "sprintRows": len(sprint_rows), "sprintSource": sprint_source}))

if __name__ == "__main__":
    main()
