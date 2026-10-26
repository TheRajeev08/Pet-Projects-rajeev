"""Pre-commit guard for a public repo: block added lines that contain a local account name,
a Salesforce record id, or your own configured identifiers.

Names come only from local data (the latest Salesforce book, account attributes and the Copilot
dashboard model), so the guard knows your book without the repo ever containing it.
"""
import glob
import json
import os
import re
import subprocess

from . import paths
from .sources import latest_raw, parse_attributes, parse_universe

SFDC_RECORD = re.compile(r"(?<![A-Za-z0-9])(00[1356][A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?)(?![A-Za-z0-9])")
SUFFIXES = {"inc", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation", "co", "company", "plc", "gmbh",
            "ag", "sa", "bv", "nv", "pte", "pty", "llp", "lp", "holdings", "group", "technologies", "technology",
            "solutions", "services", "software", "systems", "labs", "india", "the", "and", "of"}
# Words this project itself uses; an account whose whole core name is one of these cannot be guarded by name.
PROJECT_WORDS = {"github", "copilot", "pulse", "customer", "salesforce", "microsoft", "kusto", "enterprise", "security",
                 "advanced", "cloud", "server", "data", "account", "accounts", "health", "model", "status", "digest",
                 "signals", "metered", "volume", "seats", "change", "changes", "dashboard", "test", "demo", "sample"}
MIN_LEN = 4
DICT_PATHS = ("/usr/share/dict/words",)


def _dictionary():
    for p in DICT_PATHS:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                return {w.strip().lower() for w in f if w.strip()}
        except OSError:
            continue
    return set()


def _placeholder(record_id):
    rest = record_id[3:]
    return "XXXX" in rest.upper() or set(rest) <= {"0"}


def _core(name):
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9&.'-]*", name or "")
    toks = [t.strip(".'-") for t in toks]
    while toks and toks[-1].lower().strip(".") in SUFFIXES:
        toks.pop()
    while toks and toks[0].lower() == "the":
        toks.pop(0)
    return " ".join(toks)


def terms_from_names(names, allow=(), dictionary=None):
    """Distinctive name forms to guard. Single-word names that are ordinary dictionary words (an
    account called "Focus" or "Edge") would flag normal code, so they are left to the id check."""
    allow = {a.lower() for a in allow}
    words = _dictionary() if dictionary is None else dictionary
    out = set()
    for n in names:
        if not isinstance(n, str):
            continue
        for t in {n.strip(), _core(n)}:
            low = t.lower()
            if len(t) < MIN_LEN or low in PROJECT_WORDS or low in allow:
                continue
            if " " not in t and low in words:
                continue
            out.add(t)
    return sorted(out, key=lambda s: (-len(s), s.lower()))


def local_names(cfg):
    names = set()
    _, doc = latest_raw(cfg, "p01")
    if doc:
        for v in parse_universe(doc).values():
            names.update([v.get("name"), v.get("parent")])
    _, doc = latest_raw(cfg, "p06")
    if doc:
        for v in parse_attributes(doc).values():
            names.update([v.get("salesforce_account_name"), v.get("salesforce_parent_account_name")])
    for p in (paths.model_path(), paths.copilot_model_path(cfg)):
        try:
            with open(p, encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        for a in m.get("accounts") or []:
            if isinstance(a, dict):
                names.update([a.get("name"), a.get("parent")])
        ents = m.get("enterprises") or []
        for e in ents if isinstance(ents, list) else []:
            if isinstance(e, dict):
                names.update([e.get("name"), e.get("slug")])
    names.discard(None)
    return names


def build_patterns(cfg):
    terms = terms_from_names(local_names(cfg), cfg.get("privacy_allowlist") or ())
    own = [v for v in (cfg.get("digest_to"), cfg.get("owner_sfdc_id")) if v]
    rx = None
    if terms or own:
        alt = "|".join(re.escape(t) for t in terms + own)
        rx = re.compile(rf"(?<![A-Za-z0-9])({alt})(?![A-Za-z0-9])", re.IGNORECASE)
    return rx, len(terms)


def scan_lines(lines, rx):
    """lines: iterable of (path, line_no, text). Returns findings [(path, line_no, kind, match)]."""
    found = []
    for path, no, text in lines:
        for m in SFDC_RECORD.finditer(text):
            if not _placeholder(m.group(1)):
                found.append((path, no, "salesforce id", m.group(1)))
        if rx:
            for m in rx.finditer(text):
                found.append((path, no, "account or personal identifier", m.group(1)))
    return found


def staged_lines(repo):
    out = subprocess.run(["git", "-C", repo, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff"],
                         capture_output=True, text=True, check=True).stdout
    path, no = None, 0
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            no = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            yield path, no, line[1:]
            no += 1


def file_lines(files):
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    yield f, i, line.rstrip("\n")
        except (UnicodeDecodeError, OSError):
            continue


def tracked_files(repo, under=None):
    cmd = ["git", "-C", repo, "ls-files", "-co", "--exclude-standard"]
    if under:
        cmd += ["--", under]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [os.path.join(repo, p) for p in out.splitlines() if p]


def repo_root():
    return subprocess.run(["git", "-C", paths.ROOT, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                          check=True).stdout.strip()


def scan(cfg, all_files=False, targets=None):
    rx, n_terms = build_patterns(cfg)
    repo = repo_root()
    if targets:
        files = []
        for t in targets:
            files += [p for p in glob.glob(os.path.join(t, "**", "*"), recursive=True) if os.path.isfile(p)] \
                if os.path.isdir(t) else [t]
        lines = file_lines(files)
    elif all_files:
        lines = file_lines(tracked_files(repo))
    else:
        lines = staged_lines(repo)
    return scan_lines(lines, rx), n_terms
