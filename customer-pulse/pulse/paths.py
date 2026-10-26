"""Shared locations. Config, state and data live outside the repo (default ~/CustomerPulse,
override with CP_HOME) so that nothing customer-related can be committed, and so every
automation run, whatever worktree it lands in, reads and extends the same history."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY_DIR = os.path.join(ROOT, "queries")

DEFAULTS = {
    "owner_sfdc_id": None,
    "owner_name": None,
    "history_days": 35,
    "sfdc_base_url": "https://github.lightning.force.com",
    "sf_target_org": "pulse-github",
    "workflow_id": None,
    "copilot_home": "~/CopilotConsumptionDashboard",
    "digest_to": None,
    "data_dir": None,
    "thresholds": {
        "consumed_abs": 5,
        "consumed_pct": 0.05,
        "consumed_7d_abs": 10,
        "consumed_7d_pct": 0.10,
        "consumed_high_abs": 10,
        "consumed_high_pct": 0.10,
        "contracted_high_abs": 10,
        "contracted_high_pct": 0.10,
        "contracted_high_min_abs": 3,
        "migration_abs": 2,
        "migration_pct": 0.02,
        "breaker_drop_pct": 0.20,
        "revert_days": 3,
        "renewal_days": 120,
        "ghe_adoption_low": 0.50,
        "ghas_committers_low": 0.50,
        "copilot_risk_share": 0.25,
        "copilot_penetration_low": 0.30,
        "copilot_penetration_min_ghe": 20,
        "changes_window_days": 7,
        "renotify_after_days": 7,
        "whitespace_min_seats": 20,
        "ghe_adoption_min_seats": 10,
        "ghas_min_seats": 5,
        "overage_abs": 5,
        "overage_pct": 0.05,
        "healthy_rows_ratio": 0.80,
        "suspect_alert_days": 3,
    },
}


def home():
    return os.path.abspath(os.path.expanduser(os.environ.get("CP_HOME", "~/CustomerPulse")))


def config_path():
    for p in (os.path.join(home(), "config.json"), os.path.join(ROOT, "config.local.json")):
        if os.path.exists(p):
            return p
    return None


def load_config(required=True):
    p = config_path()
    cfg = {}
    if p:
        with open(p) as f:
            cfg = json.load(f)
    elif required:
        raise SystemExit(
            f"Missing config. Run `python3 pulse.py init-config --owner-id <SFDC user id> --owner-name "
            f"\"<name as in Salesforce>\"` or copy config.example.json to {os.path.join(home(), 'config.json')}.")
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if k != "thresholds"})
    merged["thresholds"] = dict(DEFAULTS["thresholds"], **(cfg.get("thresholds") or {}))
    if required:
        for key in ("owner_sfdc_id", "owner_name"):
            if not merged.get(key):
                raise SystemExit(f"Config {p} is missing '{key}'.")
    return merged


def data_dir(cfg=None):
    cfg = cfg if cfg is not None else load_config(required=False)
    return os.path.abspath(os.path.expanduser(cfg.get("data_dir") or os.path.join(home(), "data")))


def state_dir():
    return os.path.join(home(), "state")


def raw_dir(cfg=None):
    return os.path.join(data_dir(cfg), "raw")


def history_dir(cfg=None):
    return os.path.join(data_dir(cfg), "history")


def model_path():
    return os.path.join(home(), "model.json")


def status_path():
    return os.path.join(home(), "status.json")


def digest_dir():
    return os.path.join(home(), "digest")


def lock_path():
    return os.path.join(home(), ".lock")


def copilot_model_path(cfg):
    return os.path.join(os.path.abspath(os.path.expanduser(cfg.get("copilot_home") or "~/CopilotConsumptionDashboard")), "model.json")
