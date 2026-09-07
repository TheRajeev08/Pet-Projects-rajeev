#!/bin/bash
# One-step installer for the FY27 Territory Plan Copilot plugin.
#
# Non-technical teammates should never have to reason about clone paths or
# Python packaging, so this does the whole install and says plainly whether it
# worked. Safe to re-run: an existing install is updated, not duplicated.

set -u

REPO="https://github.com/TheRajeev08/fy27-territory-plan.git"
DEST="$HOME/.copilot/installed-plugins/fy27-territory-plan"
BUNDLE="fy27-territory-plan"
GH_REPO="TheRajeev08/fy27-territory-plan"

echo ""
echo "Installing the FY27 Territory Plan plugin..."
echo ""

if ! command -v git >/dev/null 2>&1; then
    echo "X  Git is not installed on this Mac."
    echo "   Open the App Store and install Xcode command line tools, or ask IT."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "X  Python 3 is not installed on this Mac."
    echo "   Install it from https://www.python.org/downloads/ then run this again."
    exit 1
fi

fetch_plugin() {
    rm -rf "$DEST"
    mkdir -p "$(dirname "$DEST")"
    git clone --quiet "$REPO" "$DEST" 2>/dev/null
}

# The loader only finds plugins at <installed-plugins>/<bundle>/<plugin>/plugin.json,
# so that exact file is the only meaningful definition of "installed".
installed_ok() {
    [ -f "$DEST/fy27-territory-plan/plugin.json" ]
}

if [ -d "$DEST/.git" ]; then
    echo "-> Found an existing copy. Updating it..."
    git -C "$DEST" pull --quiet --ff-only 2>/dev/null
    # A pull reports success even if files were deleted or the checkout was
    # damaged, so re-clone whenever the result is not actually usable.
    if ! installed_ok; then
        echo "   That copy was damaged. Replacing it with a fresh one..."
        fetch_plugin
    fi
else
    echo "-> Downloading the plugin..."
    fetch_plugin
fi

if ! installed_ok; then
    echo ""
    echo "X  Download did not complete."
    echo "   Check your internet connection and run this again."
    echo "   If it keeps failing, send this to Rajeev:"
    echo "       missing $DEST/fy27-territory-plan/plugin.json"
    exit 1
fi

echo "-> Checking the Excel export component..."
if python3 -c "import xlsxwriter" >/dev/null 2>&1; then
    echo "   Already installed."
else
    python3 -m pip install --user --quiet xlsxwriter >/dev/null 2>&1
    if ! python3 -c "import xlsxwriter" >/dev/null 2>&1; then
        echo ""
        echo "!  The plugin is installed, but the Excel export component is not."
        echo "   Everything else will work. To fix it, run:"
        echo "       python3 -m pip install --user xlsxwriter"
        echo ""
    else
        echo "   Installed."
    fi
fi

echo "-> Checking the presentation component..."
if python3 -c "import pptx" >/dev/null 2>&1; then
    echo "   Already installed."
else
    python3 -m pip install --user --quiet python-pptx >/dev/null 2>&1
    if ! python3 -c "import pptx" >/dev/null 2>&1; then
        echo ""
        echo "!  The plugin is installed, but the presentation component is not."
        echo "   The territory plan will still work; only the H1 focus deck needs this."
        echo "   To fix it, run:"
        echo "       python3 -m pip install --user python-pptx"
        echo ""
    else
        echo "   Installed."
    fi
fi

# Three registrations are needed and every one of them is silent when missing:
#   config.json installedPlugins - the app's actual registry of what is installed
#   settings.json enabledPlugins - turns the plugin on
#   settings.json extraKnownMarketplaces - tells the app where the bundle came from
# Copying files into installed-plugins/ registers nothing. Without the config.json
# entry the app never loads the plugin at all, while the settings keys still make it
# look enabled. No error is logged anywhere.
register_plugin() {
    python3 - "$HOME/.copilot/config.json" "$DEST/fy27-territory-plan" "$BUNDLE" <<'PYEOF'
import collections, hashlib, json, os, re, sys
path, cache, bundle = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    manifest = json.load(open(os.path.join(cache, "plugin.json"), encoding="utf-8"))
except (ValueError, OSError):
    sys.exit(1)
header, body = "", "{}"
if os.path.exists(path):
    raw = open(path, encoding="utf-8").read()
    match = re.search(r'^\s*\{', raw, flags=re.M)   # the file opens with // comments
    if not match:
        sys.exit(1)
    header, body = raw[:match.start()], raw[match.start():]
try:
    data = json.loads(body, object_pairs_hook=collections.OrderedDict)
except ValueError:
    sys.exit(1)
if not isinstance(data, dict):
    sys.exit(1)
plugins = data.get("installedPlugins")
if not isinstance(plugins, list):
    plugins = []
entry = collections.OrderedDict([
    ("name", manifest["name"]),
    ("marketplace", bundle),
    ("installed_at", __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")),
    ("enabled", True),
    ("version", manifest["version"]),
    ("cache_path", cache),
    # Deterministic, so re-running the installer does not churn the file.
    ("source_sha", hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()),
])
existing = next((p for p in plugins if p.get("name") == manifest["name"]), None)
if existing is not None:
    entry["installed_at"] = existing.get("installed_at", entry["installed_at"])
    if existing == entry:
        sys.exit(0)                  # already correct: leave the file untouched
    plugins = [p for p in plugins if p.get("name") != manifest["name"]]
plugins.append(entry)
data["installedPlugins"] = plugins
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(header + json.dumps(data, indent=2) + "\n")
os.replace(tmp, path)                # atomic: never leaves a half-written config
PYEOF
}

echo "-> Registering the plugin..."
if register_plugin; then
    echo "   Registered."
else
    echo ""
    echo "!  Could not register the plugin automatically."
    echo "   Send this to Rajeev: register_plugin failed"
    echo ""
fi

echo "-> Enabling the plugin..."
SETTINGS="$HOME/.copilot/settings.json"
KEY="fy27-territory-plan@fy27-territory-plan"
if python3 - "$SETTINGS" "$KEY" "$BUNDLE" "$GH_REPO" <<'PYEOF'
import collections, json, os, sys
path, key, bundle, repo = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    data = json.load(open(path, encoding="utf-8"),
                     object_pairs_hook=collections.OrderedDict) if os.path.exists(path) else collections.OrderedDict()
except (ValueError, OSError):
    sys.exit(1)                      # unreadable or malformed: leave it alone
if not isinstance(data, dict):
    sys.exit(1)
changed = False

enabled = data.get("enabledPlugins")
if not isinstance(enabled, dict):
    enabled = collections.OrderedDict()
    data["enabledPlugins"] = enabled
    changed = True
if enabled.get(key) is not True:
    enabled[key] = True
    changed = True

markets = data.get("extraKnownMarketplaces")
if not isinstance(markets, dict):
    markets = collections.OrderedDict()
    data["extraKnownMarketplaces"] = markets
    changed = True
wanted = {"source": {"source": "github", "repo": repo}}
if markets.get(bundle) != wanted:
    markets[bundle] = wanted
    changed = True

if not changed:
    sys.exit(0)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)                # atomic: never leaves a half-written settings file
PYEOF
then
    echo "   Enabled."
else
    echo ""
    echo "!  Could not enable the plugin automatically."
    echo "   The skills stay hidden until it is enabled, so turn it on in the"
    echo "   Copilot plugin settings, or add both of these to $SETTINGS:"
    echo "       \"enabledPlugins\": { \"$KEY\": true }"
    echo "       \"extraKnownMarketplaces\": { \"$BUNDLE\": { \"source\": { \"source\": \"github\", \"repo\": \"$GH_REPO\" } } }"
    echo ""
fi

echo ""
echo "==================================================="
echo " Done. Two more steps:"
echo ""
echo "   1. Quit the Copilot app completely (Cmd + Q) and reopen it"
echo "   2. Type:  Build my FY27 H1 territory plan and leadership deck"
echo ""
echo " That one sentence builds everything - the plan, the sprint queue"
echo " and both decks. Asking for them separately is the one reliable"
echo " way to end up with an incomplete Sprint Focus tab."
echo "==================================================="
echo ""
