"""Merge and validate trigger JSON returned by the research sub-agents.

    python3 merge_triggers.py <focus-candidates.json> <trigger-dir> <out.json>
    python3 merge_triggers.py <focus-candidates.json> b1.json b2.json <out.json>

The last argument is always the output path. Everything between the candidates file
and it is a trigger source, which may be a directory or individual batch files.
Writing the merged output over one of the input batches is refused outright.

Agents return one JSON blob per batch, often wrapped in markdown fences and often
keyed by whatever identifier they found easiest (Salesforce id, account name, or
the plan's own key). This normalises all of that onto the candidate key, and
enforces the evidence bar the deck promises: every trigger must carry a known
type, an ISO date, and an http source URL. Anything that fails is dropped rather
than discounted, and the drop count is reported so a bad batch is visible.
"""
import glob
import json
import os
import re
import sys

# Must stay in step with TRIGGER_TYPE_WEIGHT in rank.py — a type this file accepts
# but rank.py does not know scores at the "other" floor, and a type rank.py knows
# but this file rejects is silently lost.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from rank import TRIGGER_TYPE_WEIGHT
    VALID = set(TRIGGER_TYPE_WEIGHT)
except Exception:
    VALID = {"funding", "acquisition", "security_incident", "leadership_change",
             "ai_launch", "product_launch", "expansion", "partnership",
             "earnings", "recognition", "other"}

FIELDS = ("type", "date", "headline", "url", "soWhat")


def main(candidates_path, trigger_sources, out_path):
    # Guard the CLI shape that silently destroys data: passing a list of batch files
    # and letting the last one land in argv[3] overwrites an input with the merged
    # output. Refuse rather than write.
    resolved_out = os.path.abspath(out_path)
    for source in trigger_sources:
        if os.path.isfile(source) and os.path.abspath(source) == resolved_out:
            raise SystemExit(
                "Refusing to write the merged output over an input batch file:\n  %s\n"
                "Pass the trigger DIRECTORY (or a file list) followed by a NEW output path."
                % resolved_out)

    with open(candidates_path, "r", encoding="utf-8") as fh:
        candidates = json.load(fh)
    accounts = (candidates.get("candidates") or candidates.get("accounts")
                or (candidates if isinstance(candidates, list) else []))
    by_sf = {a["salesforceId"]: a for a in accounts if a.get("salesforceId")}
    by_name = {str(a.get("name", "")).strip().lower(): a for a in accounts}
    by_key = {a["key"]: a for a in accounts if a.get("key")}

    # Accept a directory or an explicit list of batch files, because both are natural
    # to type and guessing wrong used to mean losing a batch.
    paths = []
    for source in trigger_sources:
        if os.path.isdir(source):
            paths.extend(sorted(glob.glob(os.path.join(source, "*.json"))))
        elif os.path.isfile(source):
            paths.append(source)
        else:
            raise SystemExit("No such trigger file or directory: %s" % source)
    if not paths:
        raise SystemExit("No trigger JSON files found in: %s" % ", ".join(trigger_sources))

    out, dropped, unmatched = {}, 0, []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            continue
        try:
            payload = json.loads(match.group(0)).get("accounts", {})
        except json.JSONDecodeError:
            continue
        for raw_key, items in payload.items():
            account = (by_sf.get(raw_key) or by_name.get(str(raw_key).strip().lower())
                       or by_key.get(raw_key))
            if not account:
                unmatched.append(raw_key)
                continue
            keep = []
            for trigger in items or []:
                if (str(trigger.get("type", "")).lower() in VALID
                        and re.match(r"^\d{4}-\d{2}-\d{2}$", str(trigger.get("date", "")))
                        and str(trigger.get("url", "")).startswith("http")):
                    keep.append({k: trigger[k] for k in FIELDS if k in trigger})
                else:
                    dropped += 1
            if keep:
                out.setdefault(account["key"], []).extend(keep)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"accounts": out}, fh, indent=1)
    print(json.dumps({
        "path": out_path,
        "accountsWithTriggers": len(out),
        "triggers": sum(len(v) for v in out.values()),
        "dropped": dropped,
        "unmatchedKeys": unmatched[:10],
    }))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    # Everything between the candidates file and the final argument is a trigger source.
    main(sys.argv[1], sys.argv[2:-1], sys.argv[-1])
