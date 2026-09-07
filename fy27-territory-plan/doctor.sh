#!/bin/bash
# Checks whether the FY27 Territory Plan plugin is actually installed.
#
# Every way this plugin can fail to load is silent: the files can be on disk, the
# plugin can report as enabled, and the skills can still be invisible with nothing
# logged anywhere. This prints the real state of each registration so a teammate can
# see which one is missing instead of guessing.
#
#   bash doctor.sh
#
# Exits non-zero if anything is wrong, so it can gate a support conversation.

set -u

DEST="$HOME/.copilot/installed-plugins/fy27-territory-plan"
PLUGIN="$DEST/fy27-territory-plan"
CONFIG="$HOME/.copilot/config.json"
SETTINGS="$HOME/.copilot/settings.json"
KEY="fy27-territory-plan@fy27-territory-plan"
BUNDLE="fy27-territory-plan"

FAILED=0
pass() { echo "  OK    $1"; }
fail() { echo "  X     $1"; echo "        -> $2"; FAILED=1; }

echo ""
echo "FY27 Territory Plan - install check"
echo "==================================="
echo ""

FIXLINE="Run: curl -fsSL https://raw.githubusercontent.com/TheRajeev08/fy27-territory-plan/main/install.sh | bash"

# 1. Files present
if [ -f "$PLUGIN/plugin.json" ]; then
    VERSION=$(python3 -c "import json;print(json.load(open('$PLUGIN/plugin.json'))['version'])" 2>/dev/null || echo "?")
    pass "files on disk (version $VERSION)"
else
    fail "files on disk" "$FIXLINE"
    VERSION="?"
fi

# 2. All four skills present
MISSING=""
for s in fy27-h1-run fy27-territory-plan fy27-crm-enrichment fy27-h1-focus-deck; do
    [ -f "$PLUGIN/skills/$s/SKILL.md" ] || MISSING="$MISSING $s"
done
if [ -z "$MISSING" ]; then
    pass "all four skills present"
else
    fail "skills missing:$MISSING" "$FIXLINE"
fi

# 3. Registered in config.json - the app's real registry. Without this the plugin is
#    never loaded, no matter what the other two say.
python3 - "$CONFIG" "$PLUGIN" <<'PYEOF'
import json, os, re, sys
path, plugin = sys.argv[1], sys.argv[2]
if not os.path.exists(path):
    sys.exit(2)
raw = open(path, encoding="utf-8").read()
match = re.search(r'^\s*\{', raw, flags=re.M)
if not match:
    sys.exit(3)
try:
    data = json.loads(raw[match.start():])
except ValueError:
    sys.exit(3)
entry = next((p for p in data.get("installedPlugins", [])
              if p.get("name") == "fy27-territory-plan"), None)
if entry is None:
    sys.exit(4)
if not entry.get("enabled"):
    sys.exit(5)
if os.path.normpath(entry.get("cache_path", "")) != os.path.normpath(plugin):
    sys.exit(6)
PYEOF
case $? in
    0) pass "registered in config.json" ;;
    2) fail "config.json not found" "Open the Copilot app once, then run this again." ;;
    3) fail "config.json is unreadable" "Send this to Rajeev - do not edit the file yourself." ;;
    4) fail "not registered in config.json" "$FIXLINE" ;;
    5) fail "registered but disabled in config.json" "$FIXLINE" ;;
    6) fail "registered, but pointing at the wrong folder" "$FIXLINE" ;;
esac

# 4. settings.json: enablement and marketplace
python3 - "$SETTINGS" "$KEY" "$BUNDLE" <<'PYEOF'
import json, os, sys
path, key, bundle = sys.argv[1], sys.argv[2], sys.argv[3]
if not os.path.exists(path):
    sys.exit(2)
try:
    data = json.load(open(path, encoding="utf-8"))
except ValueError:
    sys.exit(3)
if data.get("enabledPlugins", {}).get(key) is not True:
    sys.exit(4)
if bundle not in (data.get("extraKnownMarketplaces") or {}):
    sys.exit(5)
PYEOF
case $? in
    0) pass "enabled in settings.json" ;;
    2) fail "settings.json not found" "Open the Copilot app once, then run this again." ;;
    3) fail "settings.json is unreadable" "Send this to Rajeev - do not edit the file yourself." ;;
    4) fail "not enabled in settings.json" "$FIXLINE" ;;
    5) fail "marketplace not registered in settings.json" "$FIXLINE" ;;
esac

# 5. Python components the run needs
for mod in xlsxwriter pptx; do
    if python3 -c "import $mod" >/dev/null 2>&1; then
        pass "$mod installed"
    else
        pkg="xlsxwriter"; [ "$mod" = "pptx" ] && pkg="python-pptx"
        fail "$mod missing" "Run: python3 -m pip install --user $pkg"
    fi
done

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "Everything is installed correctly."
    echo ""
    echo "If the skills still do not appear, quit Copilot completely with Cmd + Q"
    echo "and reopen it. Closing the window is not enough."
else
    echo "Something is missing - see the -> lines above."
fi
echo ""
exit "$FAILED"
