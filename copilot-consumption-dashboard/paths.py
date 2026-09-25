"""Shared locations. Config, data and the rendered dashboard live outside the repo so that
(a) nothing sensitive can be committed and (b) every automation run, whatever worktree it
lands in, reads and extends the same history."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser(os.environ.get("CCD_HOME", "~/CopilotConsumptionDashboard"))


def config_path():
    for p in (os.path.join(HOME, "config.json"), os.path.join(ROOT, "config.local.json")):
        if os.path.exists(p):
            return p
    return None


def load_config():
    p = config_path()
    if not p:
        sys.exit(f"Missing config. Copy config.example.json to {os.path.join(HOME, 'config.json')} and fill in your Salesforce owner id/name.")
    with open(p) as f:
        return json.load(f)


def data_dir():
    p = config_path()
    cfg = json.load(open(p)) if p else {}
    return os.path.expanduser(cfg.get("data_dir") or os.path.join(HOME, "data"))


def dashboard_path():
    return os.path.join(HOME, "dashboard.html")
