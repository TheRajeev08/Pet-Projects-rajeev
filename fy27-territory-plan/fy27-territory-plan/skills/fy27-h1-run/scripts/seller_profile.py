"""One teammate's territory, quota and run-rate base, held outside the plugin package.

    python3 seller_profile.py show
    python3 seller_profile.py set --json '{"territory": "<your geo>", ...}'
    python3 seller_profile.py render <runDir> [--template <targets.json>]

Why this exists
---------------
Quota used to live in the shipped ``targets.json``. That made every plugin sync a chance to
overwrite a teammate's real numbers with the template, and it has happened. The profile moves
those numbers to ``~/.copilot/fy27-territory-plan/seller-profile.json``, outside anything a
sync or a reinstall touches, and ``render`` projects them into a run-local ``targets.json``
that ``targets.py --targets`` reads. The shipped file goes back to being a pure template.

A field the teammate has not given stays ``null`` all the way through, so the deck renders TBD
rather than inventing a denominator. That is the one rule this script exists to protect:
``set`` will not turn a missing number into a zero, because a zero target reads as "achieved"
on the coverage slide while a null reads as "not yet set".
"""
import json
import os
import sys

PROFILE_DIR = os.path.expanduser("~/.copilot/fy27-territory-plan")
PROFILE_PATH = os.path.join(PROFILE_DIR, "seller-profile.json")
DEFAULT_TEMPLATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "fy27-h1-focus-deck", "scripts", "targets.json")

QUARTERS = ("Q1", "Q2")
BUCKET1_PRODUCTS = ("GHE", "GHAS")
BUCKET2_PRODUCTS = ("Copilot", "Actions", "GHAzDO")
RUN_RATE_PRODUCTS = ("Copilot", "Actions", "GHAzDO")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _num(value):
    """Coerce to float, but keep None as None. '' and 'null' mean 'not set', not zero."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "")
        if cleaned == "" or cleaned.lower() in ("null", "none", "tbd"):
            return None
        value = cleaned
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def empty_profile():
    return {
        "territory": "",
        "fiscalYear": "FY27",
        "half": "H1",
        "focusQuarter": "Q1",
        "runRate": {p: None for p in RUN_RATE_PRODUCTS},
        "targets": {
            "Bucket 1": {p: {q: None for q in QUARTERS} for p in BUCKET1_PRODUCTS},
            "Bucket 2": {p: {q: None for q in QUARTERS} for p in BUCKET2_PRODUCTS},
        },
        "attained": {b: {q: None for q in QUARTERS} for b in ("Bucket 1", "Bucket 2")},
    }


def seed_from_template(template_path):
    """First run: lift any real numbers already sitting in the shipped targets.json.

    A teammate who has been editing targets.json by hand should not lose their quota the day
    they upgrade. In the published package every one of these is null, so a fresh install
    seeds an empty profile and the interview asks for everything.
    """
    profile = empty_profile()
    template = _read_json(template_path)
    if not template:
        return profile

    profile["territory"] = (template.get("territory") or "").strip()
    for key in ("fiscalYear", "half", "focusQuarter"):
        if template.get(key):
            profile[key] = template[key]

    for product, value in ((template.get("runRate") or {}).get("products") or {}).items():
        if product in profile["runRate"]:
            profile["runRate"][product] = _num(value)

    for bucket, cfg in (template.get("buckets") or {}).items():
        if bucket not in profile["targets"]:
            continue
        for product, quarters in (cfg.get("targets") or {}).items():
            if product not in profile["targets"][bucket]:
                continue
            for quarter in QUARTERS:
                profile["targets"][bucket][product][quarter] = _num((quarters or {}).get(quarter))
        for quarter in QUARTERS:
            profile["attained"][bucket][quarter] = _num((cfg.get("attained") or {}).get(quarter))

    return profile


def load(template_path=DEFAULT_TEMPLATE, persist_seed=True):
    """Return the profile, seeding it from the template the first time."""
    existing = _read_json(PROFILE_PATH)
    if isinstance(existing, dict):
        merged = empty_profile()
        _merge(merged, existing)
        return merged
    profile = seed_from_template(template_path)
    if persist_seed:
        _write_json(PROFILE_PATH, profile)
    return profile


def _merge(base, patch):
    """Deep-merge a patch, coercing money to numbers and never inventing a zero."""
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        elif key in ("territory", "fiscalYear", "half", "focusQuarter"):
            base[key] = ("" if value is None else str(value)).strip()
        else:
            base[key] = _num(value) if not isinstance(value, dict) else value
    return base


def missing_fields(profile):
    """Everything still unset, phrased the way the interview should ask for it."""
    gaps = []
    if not (profile.get("territory") or "").strip():
        gaps.append("territory (prints on the title slide)")
    for product in RUN_RATE_PRODUCTS:
        if (profile.get("runRate") or {}).get(product) is None:
            gaps.append("last full month's %s consumption revenue (run-rate base)" % product)
    for bucket, products in (("Bucket 1", BUCKET1_PRODUCTS), ("Bucket 2", BUCKET2_PRODUCTS)):
        for product in products:
            for quarter in QUARTERS:
                cell = ((profile.get("targets") or {}).get(bucket) or {}).get(product) or {}
                if cell.get(quarter) is None:
                    gaps.append("%s %s %s target" % (bucket, product, quarter))
    for bucket in ("Bucket 1", "Bucket 2"):
        for quarter in QUARTERS:
            if ((profile.get("attained") or {}).get(bucket) or {}).get(quarter) is None:
                gaps.append("%s %s attained-to-date" % (bucket, quarter))
    return gaps


def render(profile, template_path, out_path):
    """Project the profile onto the shipped template and write <runDir>/targets.json."""
    template = _read_json(template_path)
    if not template:
        raise SystemExit("seller_profile: cannot read targets template at %s" % template_path)

    template["territory"] = (profile.get("territory") or "").strip()
    for key in ("fiscalYear", "half", "focusQuarter"):
        if profile.get(key):
            template[key] = profile[key]

    run_rate = template.setdefault("runRate", {}).setdefault("products", {})
    for product in RUN_RATE_PRODUCTS:
        value = (profile.get("runRate") or {}).get(product)
        if value is None:
            run_rate.pop(product, None)
        else:
            run_rate[product] = value

    for bucket, products in (("Bucket 1", BUCKET1_PRODUCTS), ("Bucket 2", BUCKET2_PRODUCTS)):
        cfg = (template.get("buckets") or {}).get(bucket)
        if not cfg:
            continue
        targets = cfg.setdefault("targets", {})
        for product in products:
            cell = ((profile.get("targets") or {}).get(bucket) or {}).get(product) or {}
            targets[product] = {q: cell.get(q) for q in QUARTERS}
        attained = (profile.get("attained") or {}).get(bucket) or {}
        cfg["attained"] = {q: (attained.get(q) or 0) for q in QUARTERS}

    template["_source"] = "Rendered from seller-profile.json - edit the profile, not this file."
    _write_json(out_path, template)
    return template


def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    command, args = sys.argv[1], sys.argv[2:]
    template_path = _opt(args, "--template", DEFAULT_TEMPLATE)

    if command == "show":
        profile = load(template_path)
        print(json.dumps({
            "profilePath": PROFILE_PATH,
            "profile": profile,
            "missing": missing_fields(profile),
        }, indent=2))
        return 0

    if command == "set":
        raw = _opt(args, "--json")
        if not raw:
            print("seller_profile set requires --json '<partial profile>'", file=sys.stderr)
            return 2
        try:
            patch = json.loads(raw)
        except ValueError as exc:
            print("seller_profile: --json is not valid JSON (%s)" % exc, file=sys.stderr)
            return 2
        profile = _merge(load(template_path), patch)
        _write_json(PROFILE_PATH, profile)
        print(json.dumps({
            "profilePath": PROFILE_PATH,
            "missing": missing_fields(profile),
        }, indent=2))
        return 0

    if command == "render":
        if not args or args[0].startswith("--"):
            print("seller_profile render requires a run directory", file=sys.stderr)
            return 2
        run_dir = args[0]
        if not os.path.isdir(run_dir):
            print("seller_profile: no such run directory %s" % run_dir, file=sys.stderr)
            return 2
        profile = load(template_path)
        out_path = os.path.join(run_dir, "targets.json")
        render(profile, template_path, out_path)
        gaps = missing_fields(profile)
        print(json.dumps({
            "targetsPath": out_path,
            "territory": profile.get("territory") or "",
            "missing": gaps,
            "note": ("%d field(s) unset - those render TBD, never 0." % len(gaps)) if gaps
                    else "Profile complete.",
        }, indent=2))
        return 0

    print("seller_profile: unknown command %r" % command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
