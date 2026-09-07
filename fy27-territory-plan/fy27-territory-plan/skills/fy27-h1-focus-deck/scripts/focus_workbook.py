"""Companion workbook for the H1 focus deck.

The deck is the argument; this workbook is the evidence behind it. Every number a
reviewer can challenge on a slide is traceable to a row here, including the rate and
basis used for each sized line. It is written as a separate file rather than appended
to the territory-plan workbook so the two skills stay independent.
"""

import json
import os
import sys

SHEET_LIMIT = 31


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def product_lines(account):
    return account.get("lines") or []


def write(path, focus, potential, partners, report):
    try:
        import xlsxwriter
    except ImportError:
        raise SystemExit(
            "The focus workbook needs the 'xlsxwriter' package, which is not installed.\n"
            "Install it, then run the deck again:\n"
            "    python3 -m pip install --user xlsxwriter"
        )

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    accounts = focus.get("accounts") or []
    by_key = (partners or {}).get("accounts", {})

    book = xlsxwriter.Workbook(path)
    book.set_properties({
        "title": "FY27 H1 Focus Accounts",
        "subject": "Evidence behind the H1 focus presentation",
        "author": "GitHub Revenue",
        "comments": "Every sized line carries its rate and basis so the deck survives challenge.",
    })

    navy, blue, amber, slate = "#0F172A", "#1D4ED8", "#B45309", "#475569"
    head = book.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": navy,
                            "border": 1, "border_color": "#CBD5E1", "text_wrap": True,
                            "valign": "vcenter"})
    body = book.add_format({"border": 1, "border_color": "#E2E8F0", "text_wrap": True,
                            "valign": "top"})
    money = book.add_format({"border": 1, "border_color": "#E2E8F0",
                             "num_format": "$#,##0", "valign": "top"})
    count = book.add_format({"border": 1, "border_color": "#E2E8F0",
                             "num_format": "#,##0", "valign": "top"})
    title = book.add_format({"bold": True, "font_size": 16, "font_color": "#FFFFFF",
                             "bg_color": navy, "valign": "vcenter"})
    note = book.add_format({"italic": True, "font_color": slate, "text_wrap": True,
                            "valign": "top"})
    link = book.add_format({"font_color": blue, "underline": 1, "border": 1,
                            "border_color": "#E2E8F0", "valign": "top"})

    def table(sheet, row, headers, rows, widths, formats=None):
        sheet.write_row(row, 0, headers, head)
        sheet.set_row(row, 30)
        for index, width in enumerate(widths):
            sheet.set_column(index, index, width)
        for r, values in enumerate(rows, row + 1):
            for c, value in enumerate(values):
                fmt = (formats or {}).get(c, body)
                sheet.write(r, c, value, fmt)
        sheet.freeze_panes(row + 1, 0)
        if rows:
            sheet.autofilter(row, 0, row + len(rows), len(headers) - 1)

    # 1 — the shortlist itself, in rank order, with the score components exposed so the
    # ordering can be argued with rather than taken on faith.
    sheet = book.add_worksheet("Focus Accounts")
    sheet.set_row(0, 28)
    sheet.merge_range(0, 0, 0, 13, "FY27 H1 FOCUS ACCOUNTS", title)
    sheet.merge_range(1, 0, 1, 13,
                      "Ranked by a composite of potential ARR, active communication and "
                      "recent live triggers. Tier 1 is the top 25%%, Tier 2 the next 35%%.",
                      note)
    rows = []
    for account in accounts:
        current = account.get("current") or {}
        rows.append([
            account.get("rank", ""), account.get("name", ""), account.get("tier", ""),
            account.get("play", ""), account.get("potentialArr", 0) or 0,
            current.get("arr", 0) or 0, account.get("compositeScore", ""),
            account.get("potentialScore", ""), account.get("commScore", ""),
            account.get("triggerScore", ""),
            "Yes" if account.get("twoWay") else "No",
            account.get("lastActivity", "") or "\u2014",
            account.get("renewal", "") or "\u2014",
            len(account.get("triggers") or []),
        ])
    table(sheet, 3,
          ["Rank", "Account", "Tier", "Play", "Potential ARR", "Current ARR", "Composite",
           "Potential score", "Comms score", "Trigger score", "Two-way", "Last activity",
           "Renewal", "Triggers"],
          rows, [7, 30, 17, 12, 15, 14, 11, 14, 13, 13, 10, 14, 13, 10],
          {4: money, 5: money, 13: count})

    # 2 — the sizing audit. This is the sheet that answers "where did that number come
    # from", one row per product line with its rate and basis. The final column carries
    # any data-quality flag on the signal this line was sized from, so an assumption
    # worth challenging is visible next to the dollars it produced rather than buried.
    sheet = book.add_worksheet("Sizing Detail")
    flags_by_account = (potential or {}).get("dataQualityFlags") or {}
    rows = []
    for account in accounts:
        account_flags = flags_by_account.get(account.get("salesforceId")) or []
        for line in product_lines(account):
            metric = (line.get("metric") or "").lower()
            check = "; ".join(
                flag["detail"] for flag in account_flags
                if flag["signal"] == "activeCommitters" and "committer" in metric
            )
            rows.append([
                account.get("name", ""), account.get("tier", ""),
                line.get("product", ""), line.get("metric", ""),
                line.get("quantity", 0) or 0, line.get("rate", 0) or 0,
                line.get("basis", ""), line.get("value", 0) or 0,
                line.get("note", ""), check or "\u2014",
            ])
    table(sheet, 0,
          ["Account", "Tier", "Product", "Metric", "Quantity", "Rate (annual)", "Basis",
           "Sized value", "How this line was derived", "Check before quoting"],
          rows, [30, 17, 14, 14, 12, 14, 16, 14, 62, 62],
          {4: count, 5: money, 7: money})

    # 3 — triggers, with the citation attached. An undated or uncited trigger never
    # reaches this sheet because rank.py drops it upstream.
    sheet = book.add_worksheet("Live Triggers")
    rows = []
    for account in accounts:
        for trigger in account.get("triggers") or []:
            rows.append([
                account.get("name", ""), trigger.get("date", ""),
                trigger.get("type", ""), trigger.get("headline", ""),
                trigger.get("soWhat", ""), trigger.get("url", ""),
            ])
    rows.sort(key=lambda values: values[1], reverse=True)
    table(sheet, 0,
          ["Account", "Date", "Type", "Headline", "So what", "Source"],
          rows, [30, 12, 18, 58, 58, 46], {5: link})

    # 4 — partner and Microsoft leverage, kept separate because coverage is partial and
    # should not be silently blended into the account list.
    sheet = book.add_worksheet("Partners & Microsoft")
    rows = []
    for account in accounts:
        entry = by_key.get(account.get("key")) or {}
        partner_list = entry.get("partners") or []
        if not partner_list:
            rows.append([account.get("name", ""), "\u2014", "\u2014", "\u2014", "\u2014",
                         "No mapped partner"])
            continue
        pdm = ", ".join(entry.get("pdm") or []) or "\u2014"
        csp = "Yes" if (entry.get("microsoft") or {}).get("csp") else "No"
        for partner in partner_list:
            rows.append([
                account.get("name", ""), partner.get("name", ""),
                partner.get("involvement", ""), partner.get("source", ""), pdm, csp,
            ])
    table(sheet, 0,
          ["Account", "Partner", "Involvement", "Source", "GitHub PDM",
           "MSFT CSP involvement"],
          rows, [30, 34, 22, 18, 26, 20])

    # 5 — methodology, so the workbook is self-explaining when it is forwarded without us.
    sheet = book.add_worksheet("Methodology")
    sheet.set_column(0, 0, 26)
    sheet.set_column(1, 1, 110)
    sheet.set_row(0, 28)
    sheet.merge_range(0, 0, 0, 1, "METHODOLOGY", title)
    weights = focus.get("weights") or {}
    totals = potential.get("totals") or {}
    entries = [
        ("Selection", "Two stages. Accounts are first ranked on potential and communication "
                      "to pick a candidate set, triggers are researched for those, then the "
                      "list is re-ranked to the final %d." % focus.get("selectedCount", 0)),
        ("Weights", ", ".join("%s %s" % (name, value) for name, value in weights.items())
                    or "Defaults"),
        ("Potential ARR", "Sized from observed product signals in the export at the rates in "
                          "pricing.json. Each line carries its basis: observed, list or "
                          "derived. Nothing is sized without a signal."),
        ("Coverage", "%d of %d accounts could be sized; %d carry ARR today. The rest need "
                     "discovery before they can be forecast."
                     % (potential.get("accountsSized", 0), potential.get("accountsTotal", 0),
                        potential.get("accountsWithArr", 0))),
        ("Triggers", "Only triggers with both a date and a source URL from the last 18 "
                     "months are counted. Everything else is dropped."),
        ("AIU", "Consumption and AI credits are reported as measured run-rate. They are "
                "deliberately excluded from potential ARR to avoid double-counting revenue "
                "that is already invoiced."),
        ("Book totals", "; ".join(
            "%s %s across %s accounts" % (name, "{:,.0f}".format(item.get("quantity", 0) or 0),
                                          item.get("accounts", 0))
            for name, item in totals.items()) or "\u2014"),
        ("Caution", "Potential is an opportunity size, not a forecast or a commit. It should "
                    "be qualified in discovery before it enters pipeline."),
    ]
    for index, (label, value) in enumerate(entries, 2):
        sheet.write(index, 0, label, head)
        sheet.write(index, 1, value, body)
        sheet.set_row(index, 44)

    book.close()
    return path


def main():
    if len(sys.argv) < 5:
        raise SystemExit("usage: focus_workbook.py <report.json> <potential.json> "
                         "<focus-accounts.json> <runDir> [--partners f] [--out f]")
    report = load(sys.argv[1])
    potential = load(sys.argv[2])
    focus = load(sys.argv[3])
    run_dir = sys.argv[4]
    partners, out = None, None
    args = sys.argv[5:]
    for index, arg in enumerate(args):
        if arg == "--partners" and index + 1 < len(args):
            partners = load(args[index + 1])
        if arg == "--out" and index + 1 < len(args):
            out = args[index + 1]
    path = out or os.path.join(run_dir, "fy27-h1-focus-accounts.xlsx")
    write(path, focus, potential, partners, report)
    print(json.dumps({
        "workbookPath": path,
        "focusAccounts": len(focus.get("accounts") or []),
        "sizedLines": sum(len(product_lines(a)) for a in focus.get("accounts") or []),
        "triggers": sum(len(a.get("triggers") or []) for a in focus.get("accounts") or []),
    }))


if __name__ == "__main__":
    main()
