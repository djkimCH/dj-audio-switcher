#!/usr/bin/env python3
"""Audio Switcher - top-bar menu to switch PipeWire output/input devices,
either as named presets (output + input together) or one at a time.

  audio-switcher.py              run the top-bar indicator (started at login)
  audio-switcher.py --popup      show the keyboard popup (bound to a shortcut)
  audio-switcher.py --list       print devices + presets, then exit
  audio-switcher.py --apply X    apply preset X (number or name), then exit

Presets: ~/.config/audio-switcher/presets.toml (see presets.example.toml).
Docs:    README.md (repo djkimCH/dj-audio-switcher)
"""
import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

APP_ID = "net.djelid.AudioSwitcher"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "audio-switcher" / "presets.toml"
EXAMPLE = Path(__file__).resolve().parent / "presets.example.toml"
SETTINGS = CONFIG.parent / "settings.json"   # app-managed (Advanced menu), not hand-edited
POLL_SECONDS = 2


# ---------------------------------------------------------------- PipeWire

@dataclass(frozen=True)
class Node:
    id: int
    name: str      # stable node.name, e.g. alsa_output.usb-Razer_...
    label: str     # short name shown in menus (node.nick)
    desc: str      # long description
    usb: bool
    device: int = None   # PipeWire device id: a headset's speaker and mic share it

    def matches(self, pattern):
        p = pattern.lower()
        return p in self.name.lower() or p in self.label.lower() or p in self.desc.lower()


@dataclass(frozen=True)
class State:
    sinks: tuple
    sources: tuple
    default_sink: str
    default_source: str
    recording: frozenset = frozenset()   # source names an app is recording from

    def signature(self):
        return (tuple(n.name for n in self.sinks), tuple(n.name for n in self.sources),
                self.default_sink, self.default_source)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_state():
    objs = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout)

    # Ports whose every route reports available=no (e.g. HDMI with nothing
    # plugged in) are hidden, same as GNOME's own sound menu does.
    unavailable = set()
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Device":
            continue
        by_dev = {}
        for r in (o.get("info") or {}).get("params", {}).get("EnumRoute", []):
            for dev in r.get("devices", []):
                by_dev.setdefault(dev, []).append(r.get("available"))
        for dev, avail in by_dev.items():
            if avail and all(a == "no" for a in avail):
                unavailable.add((o["id"], dev))

    sinks, sources, defaults = [], [], {}
    node_props = {o["id"]: ((o.get("info") or {}).get("props") or {})
                  for o in objs if o.get("type") == "PipeWire:Interface:Node"}
    recording = set()
    for o in objs:
        if o.get("type") == "PipeWire:Interface:Link":
            info = o.get("info") or {}
            dst = node_props.get(info.get("input-node-id"), {})
            if dst.get("media.class") == "Stream/Input/Audio":
                recording.add(node_props.get(info.get("output-node-id"), {}).get("node.name", ""))
    for o in objs:
        t = o.get("type")
        if t == "PipeWire:Interface:Metadata" and (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                v = m.get("value")
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except ValueError:
                        v = {}
                if isinstance(v, dict):
                    defaults[m.get("key")] = v.get("name", "")
        elif t == "PipeWire:Interface:Node":
            p = (o.get("info") or {}).get("props") or {}
            cls = p.get("media.class")
            if cls not in ("Audio/Sink", "Audio/Source"):
                continue
            if (_int(p.get("device.id")), _int(p.get("card.profile.device"))) in unavailable:
                continue
            name = p.get("node.name", "")
            desc = p.get("node.description", name)
            node = Node(o["id"], name, p.get("node.nick") or desc, desc,
                        p.get("device.bus") == "usb" or ".usb-" in name, _int(p.get("device.id")))
            (sinks if cls == "Audio/Sink" else sources).append(node)

    sinks.sort(key=lambda n: n.label.lower())
    sources.sort(key=lambda n: n.label.lower())
    return State(tuple(sinks), tuple(sources),
                 defaults.get("default.audio.sink", ""), defaults.get("default.audio.source", ""),
                 frozenset(recording))


def set_default(node):
    subprocess.run(["wpctl", "set-default", str(node.id)], check=False, timeout=5)


# ----------------------------------------------------------------- Presets

@dataclass(frozen=True)
class Preset:
    name: str
    output: tuple   # patterns, first match wins; empty = leave output alone
    input: tuple

    def resolve(self, state):
        """Return (sink, source, missing): nodes to set, list of unmatched sides."""
        sink = _find(state.sinks, self.output)
        source = _find(state.sources, self.input)
        missing = [k for k, pats, n in (("output", self.output, sink), ("input", self.input, source))
                   if pats and n is None]
        return sink, source, missing

    def is_active(self, state):
        sink, source, missing = self.resolve(state)
        return (not missing
                and (sink is None or sink.name == state.default_sink)
                and (source is None or source.name == state.default_source))


def _find(nodes, patterns):
    for pat in patterns:
        for n in nodes:
            if n.matches(pat):
                return n
    return None


def _patterns(v):
    if v is None:
        return ()
    return (v,) if isinstance(v, str) else tuple(str(x) for x in v)


def ensure_config():
    if not CONFIG.exists() and EXAMPLE.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE, CONFIG)


def load_presets():
    """Return (presets, error_message)."""
    if not CONFIG.exists():
        return [], None
    try:
        data = tomllib.loads(CONFIG.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [], f"presets.toml: {e}"
    presets = []
    for p in data.get("preset", []):
        if isinstance(p, dict) and p.get("name"):
            presets.append(Preset(str(p["name"]), _patterns(p.get("output")), _patterns(p.get("input"))))
    return presets, None


def load_settings():
    try:
        data = json.loads(SETTINGS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(SETTINGS)


def load_keepalive():
    """Advanced > Keep awake: tuple of output node names it is enabled for."""
    ka = load_settings().get("keepalive") or {}
    return tuple(d for d in ka.get("devices", []) if isinstance(d, str))


def toggle_keepalive(node_name):
    data = load_settings()
    ka = data.setdefault("keepalive", {"devices": []})
    ka.pop("idle_minutes", None)
    devices = ka.setdefault("devices", [])
    if node_name in devices:
        devices.remove(node_name)
    else:
        devices.append(node_name)
    save_settings(data)


class KeepAlive:
    """Plays inaudible silence to a headset while an app records from its mic,
    so the headset's own auto-off timer (which only counts incoming sound)
    doesn't fire during calls or dictation. No mic use -> its timer applies."""

    def __init__(self):
        self.proc = None
        self.target = None      # node.name currently kept awake

    def update(self, state, enabled):
        if self.proc and self.proc.poll() is not None:   # stream died (headset off)
            self.proc, self.target = None, None
        want = None
        for sink in state.sinks:
            if sink.name not in enabled:
                continue
            # the headset's mic = a source on the same hardware device
            mic_in_use = any(n.device is not None and n.device == sink.device and n.name in state.recording
                             for n in state.sources)
            if mic_in_use:
                want = sink.name
                break
        if want != self.target:
            self.stop()
            if want:
                self.start(want)

    def start(self, sink_name):
        import ctypes
        import signal

        def die_with_parent():
            ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
        with open("/dev/zero", "rb") as zero:
            self.proc = subprocess.Popen(
                ["pw-cat", "--playback", "--target", sink_name, "--format", "s16", "--rate", "48000",
                 "--channels", "2", "--latency", "500ms",
                 "-P", '{ media.name = "Headset keep-alive" node.dont-reconnect = true }', "-"],
                stdin=zero, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=die_with_parent)
        self.target = sink_name

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.target = None, None


def apply_preset(preset, notify_user=True):
    state = read_state()
    sink, source, missing = preset.resolve(state)
    if missing:
        if notify_user:
            notify(f"{preset.name}: not connected", "Missing " + " and ".join(missing) + " device - nothing changed.",
                   "dialog-warning-symbolic")
        return False
    if sink:
        set_default(sink)
    if source:
        set_default(source)
    if notify_user:
        parts = [f"🔊 {sink.label}" if sink else "", f"🎤 {source.label}" if source else ""]
        notify(preset.name, "   ".join(p for p in parts if p), icon_for(sink))
    return True


# ------------------------------------------------------------------ Helpers

def icon_for(sink):
    return "audio-headphones-symbolic" if sink and sink.usb else "audio-speakers-symbolic"


def notify(title, body, icon):
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Audio Switcher", "-i", icon, "-e",
                          "-h", "string:x-canonical-private-synchronous:audio-switcher", title, body])


def open_config():
    ensure_config()
    editor = shutil.which("gnome-text-editor") or shutil.which("xdg-open")
    subprocess.Popen([editor, str(CONFIG)])


def open_sound_settings():
    subprocess.Popen(["gnome-control-center", "sound"])


# ---------------------------------------------------------------- Indicator

def run_indicator():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AI, GLib, Gtk

    ensure_config()
    ind = AI.Indicator.new(APP_ID, "audio-speakers-symbolic", AI.IndicatorCategory.HARDWARE)
    ind.set_status(AI.IndicatorStatus.ACTIVE)
    ind.set_title("Audio Switcher")
    last = {"sig": None}
    keepalive = KeepAlive()

    def radio(label, active, sensitive, callback):
        item = Gtk.CheckMenuItem(label=label)
        item.set_draw_as_radio(True)
        item.set_active(active)
        item.set_sensitive(sensitive)
        item.connect("activate", callback)   # connected after set_active: no feedback
        return item

    def plain(label, callback):
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda *_: callback())
        return item

    def device_submenu(title, nodes, current):
        cur = next((n.label for n in nodes if n.name == current), "-")
        top = Gtk.MenuItem(label=f"{title}:  {cur}")
        sub = Gtk.Menu()
        for n in nodes:
            sub.append(radio(n.label, n.name == current, True,
                             lambda _i, n=n: (set_default(n), refresh(force=True))))
        top.set_submenu(sub)
        return top

    def info_item(label):
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        return item

    def advanced_submenu(state):
        top = Gtk.MenuItem(label="Advanced")
        sub = Gtk.Menu()
        keep = Gtk.MenuItem(label="Keep awake")
        keep_menu = Gtk.Menu()
        keep_menu.append(info_item("For headsets that switch off by themselves:"))
        keep_menu.append(info_item("plays silence while an app uses its mic"))
        keep_menu.append(Gtk.SeparatorMenuItem())
        enabled = load_keepalive()
        for n in state.sinks:
            item = Gtk.CheckMenuItem(label=n.label)
            item.set_active(n.name in enabled)
            item.connect("activate", lambda _i, n=n: (toggle_keepalive(n.name), refresh(force=True)))
            keep_menu.append(item)
        if keepalive.target:
            label = next((n.label for n in state.sinks if n.name == keepalive.target), keepalive.target)
            keep_menu.append(Gtk.SeparatorMenuItem())
            keep_menu.append(info_item(f"Now keeping {label} awake"))
        keep.set_submenu(keep_menu)
        sub.append(keep)
        top.set_submenu(sub)
        return top

    def build(state, presets, error):
        menu = Gtk.Menu()
        if error:
            bad = Gtk.MenuItem(label="⚠ " + error[:80])
            bad.set_sensitive(False)
            menu.append(bad)
        for p in presets:
            _, _, missing = p.resolve(state)
            label = p.name + ("   (not connected)" if missing else "")
            menu.append(radio(label, p.is_active(state), not missing,
                              lambda _i, p=p: (apply_preset(p, notify_user=False), refresh(force=True))))
        if presets or error:
            menu.append(Gtk.SeparatorMenuItem())
        menu.append(device_submenu("Output", state.sinks, state.default_sink))
        menu.append(device_submenu("Input", state.sources, state.default_source))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(plain("Edit presets…", open_config))
        menu.append(plain("Sound settings…", open_sound_settings))
        menu.append(advanced_submenu(state))
        menu.show_all()
        ind.set_menu(menu)

        sink = next((n for n in state.sinks if n.name == state.default_sink), None)
        active = next((p.name for p in presets if p.is_active(state)), None)
        ind.set_icon_full(icon_for(sink), active or "Custom")
        ind.set_title(f"Audio: {active or 'Custom'}")

    def refresh(force=False):
        try:
            state = read_state()
            presets, error = load_presets()
            mtime = CONFIG.stat().st_mtime if CONFIG.exists() else 0
        except Exception as e:  # keep the icon alive whatever happens
            print(f"audio-switcher: refresh failed: {e}", file=sys.stderr)
            return True
        try:
            keepalive.update(state, load_keepalive())
        except Exception as e:
            print(f"audio-switcher: keep-alive failed: {e}", file=sys.stderr)
        sig = (state.signature(), mtime, error, keepalive.target)
        if force or sig != last["sig"]:
            last["sig"] = sig
            build(state, presets, error)
        return True

    refresh()
    GLib.timeout_add_seconds(POLL_SECONDS, refresh)
    try:
        Gtk.main()
    finally:
        keepalive.stop()


# -------------------------------------------------------------------- Popup
# Wayland does not let an app position its own window, so the popup is a
# maximized, tinted see-through overlay on the screen GNOME picks (the active
# one); the menu opens with its tip at the mouse (centre if the mouse is on
# another screen). Click outside or Esc closes.

POPUP_AT = "pointer"   # "pointer" = menu tip at the mouse, "center" = screen centre
DIM_RGBA = (8 / 255, 18 / 255, 38 / 255, 0.35)   # tint over the rest of the screen

POPUP_CSS = b"""
window.audio-switcher-overlay popover { background-color: @theme_bg_color;
                                        border: 2px solid @theme_selected_bg_color; }
"""


def run_popup():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    app = Gtk.Application(application_id=APP_ID + ".Popup", flags=Gio.ApplicationFlags.FLAGS_NONE)
    ui = {"wins": []}

    def close(*_):
        for win, _anchor in ui["wins"]:
            win.destroy()
        ui["wins"] = []

    def build_model(state, presets):
        """Same structure as the top-bar menu; installs the actions it uses."""
        for name in ("preset", "output", "input"):
            app.remove_action(name)

        active = next((str(i) for i, p in enumerate(presets) if p.is_active(state)), "")
        preset_action = Gio.SimpleAction.new_stateful("preset", GLib.VariantType.new("s"),
                                                      GLib.Variant("s", active))

        def on_preset(action, value):
            p = presets[int(value.get_string())]
            if apply_preset(p):
                action.set_state(value)
        preset_action.connect("activate", on_preset)
        app.add_action(preset_action)

        def device_action(name, nodes, current):
            action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new("s"), GLib.Variant("s", current))

            def on_device(a, value):
                node = next((n for n in nodes if n.name == value.get_string()), None)
                if node:
                    set_default(node)
                    a.set_state(value)
            action.connect("activate", on_device)
            app.add_action(action)

        device_action("output", state.sinks, state.default_sink)
        device_action("input", state.sources, state.default_source)

        menu = Gio.Menu()
        section = Gio.Menu()
        for i, p in enumerate(presets):
            _, _, missing = p.resolve(state)
            item = Gio.MenuItem.new(p.name + ("   (not connected)" if missing else ""), None)
            item.set_action_and_target_value("app.preset", GLib.Variant("s", str(i)))
            section.append_item(item)
            if i < 9:
                app.set_accels_for_action(f"app.preset::{i}", [str(i + 1)])
        menu.append_section(None, section)

        devices = Gio.Menu()
        for title, action, nodes, current in (("Output", "output", state.sinks, state.default_sink),
                                              ("Input", "input", state.sources, state.default_source)):
            sub = Gio.Menu()
            for n in nodes:
                item = Gio.MenuItem.new(n.label, None)
                item.set_action_and_target_value(f"app.{action}", GLib.Variant("s", n.name))
                sub.append_item(item)
            cur = next((n.label for n in nodes if n.name == current), "-")
            devices.append_submenu(f"{title}:  {cur}", sub)
        menu.append_section(None, devices)

        extra = Gio.Menu()
        extra.append("Edit presets…", "app.edit")
        menu.append_section(None, extra)
        return menu

    def on_activate(_app):
        if ui["wins"]:             # shortcut pressed again: toggle closed
            close()
            return
        state = read_state()
        presets, _error = load_presets()
        model = build_model(state, presets)

        edit = Gio.SimpleAction.new("edit", None)
        edit.connect("activate", lambda *_: open_config())
        app.add_action(edit)

        provider = Gtk.CssProvider()
        provider.load_from_data(POPUP_CSS)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Gtk.ApplicationWindow(application=app, title="Audio Switcher")
        win.get_style_context().add_class("audio-switcher-overlay")
        visual = screen.get_rgba_visual()
        if visual:
            win.set_visual(visual)
        win.set_app_paintable(True)
        win.set_decorated(False)
        win.set_skip_taskbar_hint(True)
        win.maximize()          # not fullscreen: GNOME then treats it as opaque (black)
        anchor = Gtk.EventBox()
        win.add(anchor)
        ui["wins"].append((win, anchor))
        shown = {"done": False, "ready": False, "armed": False}

        def show_at(x, y):
            """Open the menu, its tip pointing at (x, y)."""
            if shown["done"] or not shown["ready"]:
                return False
            shown["done"] = True
            alloc = anchor.get_allocation()
            if POPUP_AT == "center" or not (0 <= x < alloc.width and 0 <= y < alloc.height):
                x, y = alloc.width // 2, int(alloc.height * 0.38)
            popover = Gtk.Popover.new_from_model(anchor, model)
            popover.set_position(Gtk.PositionType.BOTTOM)
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.connect("closed", lambda *_: GLib.idle_add(close))
            popover.popup()
            return False

        def on_state(_w, ev):
            # GNOME sends no pointer position when the overlay appears, and
            # positions during its ~150 ms zoom-in animation are skewed. So once
            # it is maximized and the zoom is over, make the overlay briefly
            # click-through and back: GNOME then sends a fresh, exact "pointer
            # entered" with the mouse position (verified 2026-10-30, GNOME 46).
            if ev.new_window_state & Gdk.WindowState.MAXIMIZED and not shown["armed"]:
                shown["armed"] = True
                import cairo

                def hide_input():
                    win.input_shape_combine_region(cairo.Region())
                    GLib.timeout_add(30, restore_input)
                    return False

                def restore_input():
                    shown["ready"] = True
                    win.input_shape_combine_region(None)
                    # mouse on another screen: no enter event -> centre
                    GLib.timeout_add(300, lambda: show_at(-1, -1))
                    return False
                GLib.timeout_add(180 if POPUP_AT == "pointer" else 0, hide_input)
            return False

        win.connect("window-state-event", on_state)
        win.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        for sig in ("enter-notify-event", "motion-notify-event"):
            win.connect(sig, lambda _w, ev: show_at(ev.x, ev.y))
        anchor.connect("button-press-event", lambda *_: close())
        win.connect("key-press-event",
                    lambda _w, ev: (close(), True)[1] if ev.keyval == Gdk.KEY_Escape else False)

        def keep_pointer(w):
            # an explicit cursor, else the pointer can vanish over the overlay
            w.get_window().set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "default"))
        win.connect("realize", keep_pointer)

        def dim(_w, cr):
            # painted by hand: an app-paintable window ignores CSS backgrounds
            import cairo
            cr.save()                    # keep the operator from leaking into the menu
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(*DIM_RGBA)
            cr.paint()
            cr.restore()
            return False
        win.connect("draw", dim)

        win.show_all()
        win.present()

    app.connect("activate", on_activate)
    app.run([sys.argv[0]])


# ---------------------------------------------------------------------- CLI

def run_list():
    state = read_state()
    print("Outputs:")
    for n in state.sinks:
        print(f"  {'*' if n.name == state.default_sink else ' '} {n.label:28} {n.name}")
    print("Inputs:")
    for n in state.sources:
        print(f"  {'*' if n.name == state.default_source else ' '} {n.label:28} {n.name}")
    presets, error = load_presets()
    print(f"Presets ({CONFIG}):" + (f"  ERROR {error}" if error else ""))
    for i, p in enumerate(presets, 1):
        sink, source, missing = p.resolve(state)
        status = "ACTIVE" if p.is_active(state) else ("missing " + "+".join(missing) if missing else "ok")
        print(f"  {i}. {p.name:24} -> {sink.label if sink else '-'} / {source.label if source else '-'}   [{status}]")


def run_apply(which):
    presets, error = load_presets()
    if error:
        sys.exit(error)
    match = (presets[int(which) - 1] if which.isdigit() and 0 < int(which) <= len(presets)
             else next((p for p in presets if p.name.lower() == which.lower()), None))
    if not match:
        sys.exit(f"No preset '{which}'")
    sys.exit(0 if apply_preset(match) else 1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        run_indicator()
    elif args[0] == "--popup":
        run_popup()
    elif args[0] == "--list":
        run_list()
    elif args[0] == "--apply" and len(args) == 2:
        run_apply(args[1])
    else:
        sys.exit(__doc__)
