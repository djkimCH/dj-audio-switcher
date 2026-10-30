#!/usr/bin/env bash
# Install Audio Switcher for the current user (no sudo).
#   install.sh [SHORTCUT]       default shortcut: <Super>t   ("" = no shortcut)
#   install.sh --uninstall
# Creates: ~/.config/autostart/audio-switcher.desktop
#          ~/.config/audio-switcher/presets.toml (from example, only if missing)
#          GNOME custom shortcut "Audio Switcher" -> audio-switcher.py --popup
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$DIR/audio-switcher.py"
AUTOSTART="$HOME/.config/autostart/audio-switcher.desktop"
CONF="$HOME/.config/audio-switcher/presets.toml"
KB_SCHEMA=org.gnome.settings-daemon.plugins.media-keys
KB_BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings

find_shortcut_path() {
    for p in $(gsettings get $KB_SCHEMA custom-keybindings | tr -d "[]',"); do
        if [[ "$(gsettings get $KB_SCHEMA.custom-keybinding:"$p" command)" == *audio-switcher.py* ]]; then
            echo "$p"; return
        fi
    done
}

remove_shortcut() {
    local p; p="$(find_shortcut_path)"
    [[ -z "$p" ]] && return
    local list; list="$(gsettings get $KB_SCHEMA custom-keybindings | sed "s#, '$p'##; s#'$p', ##; s#'$p'##")"
    gsettings set $KB_SCHEMA custom-keybindings "$list"
    gsettings reset-recursively $KB_SCHEMA.custom-keybinding:"$p"
    echo "Removed shortcut $p"
}

if [[ "${1:-}" == "--uninstall" ]]; then
    pkill -f "$APP" || true
    rm -f "$AUTOSTART"
    remove_shortcut
    echo "Uninstalled (presets kept in $CONF)."
    exit 0
fi

SHORTCUT="${1-<Super>t}"

for cmd in pw-dump wpctl python3; do
    command -v "$cmd" >/dev/null || { echo "Missing: $cmd"; exit 1; }
done
python3 -c "import gi; gi.require_version('AyatanaAppIndicator3','0.1')" 2>/dev/null \
    || { echo "Missing: gir1.2-ayatanaappindicator3-0.1 (sudo apt install gir1.2-ayatanaappindicator3-0.1)"; exit 1; }

mkdir -p "$(dirname "$CONF")" "$(dirname "$AUTOSTART")"
[[ -f "$CONF" ]] || { cp "$DIR/presets.example.toml" "$CONF"; echo "Created $CONF - adjust device names (audio-switcher.py --list)"; }

cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Audio Switcher
Comment=Top-bar menu to switch audio output/input presets
Exec=/usr/bin/python3 $APP
Icon=audio-speakers-symbolic
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
echo "Autostart: $AUTOSTART"

if [[ -n "$SHORTCUT" ]]; then
    p="$(find_shortcut_path)"
    if [[ -z "$p" ]]; then
        i=0
        while gsettings get $KB_SCHEMA custom-keybindings | grep -q "$KB_BASE/custom$i/"; do i=$((i+1)); done
        p="$KB_BASE/custom$i/"
        list="$(gsettings get $KB_SCHEMA custom-keybindings)"
        if [[ "$list" == "@as []" || "$list" == "[]" ]]; then list="['$p']"; else list="${list%]}, '$p']"; fi
        gsettings set $KB_SCHEMA custom-keybindings "$list"
    fi
    gsettings set $KB_SCHEMA.custom-keybinding:"$p" name 'Audio Switcher'
    gsettings set $KB_SCHEMA.custom-keybinding:"$p" command "/usr/bin/python3 $APP --popup"
    gsettings set $KB_SCHEMA.custom-keybinding:"$p" binding "$SHORTCUT"
    echo "Shortcut: $SHORTCUT -> popup  (change: Settings > Keyboard > Custom Shortcuts)"
fi

pgrep -f "python3 $APP\$" >/dev/null || { setsid /usr/bin/python3 "$APP" >/dev/null 2>&1 & echo "Started."; }
