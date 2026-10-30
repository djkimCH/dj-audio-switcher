# Audio Switcher

Top-bar menu (GNOME, AppIndicator) to switch the audio **output and input together** via named
presets, or one device at a time. Plus one keyboard shortcut that opens the same menu near the
mouse. Built 2026-10-30 on X1Ubuntu (no existing tool did presets).

**Features**

- **Presets:** one click switches speaker + mic together (e.g. "Razer headset", "Screen + webcam").
- **Mix & match:** Output ▸ / Input ▸ submenus for any single device.
- **Shortcut menu:** `Super+T` dims the screen and opens the same menu near the mouse.
- **Headset keeps switching off by itself?** Many wireless headsets (e.g. Razer BlackShark
  V3 Pro: 15 min) power off when no sound is played, even while you talk into the mic: on Linux,
  PipeWire stops sending data to an idle output. **Advanced ▸ Keep awake** fixes this per device;
  see [§3](#3-keep-awake-headset-auto-off-fix).

## 1. Use

| Where | What |
|-------|------|
| Top-bar icon | Speaker icon (headphones icon when a USB headset is the output). Click → presets (● = active), `Output: …` / `Input: …` submenus for mix & match, `Edit presets…`, `Sound settings…`, `Advanced ▸ Keep awake` |
| `Super+T` | Same menu (without Advanced), near the mouse. Arrows + Enter, Right/Left into/out of submenus, number keys `1`–`9` = preset, Esc / click outside = close |
| CLI | `audio-switcher.py --list` (devices + presets), `--apply 2` or `--apply "Razer headset"` |

A preset whose device is not connected is shown as `(not connected)` and changes nothing.
Playing sound moves to the new device immediately (WirePlumber moves streams that follow the default).

**Apps:** Teams / Zoom / Meet / browsers must use **"System default"** (or "Default") for speaker and
mic — an app pinned to a specific device ignores the switch.

## 2. Presets

File: `~/.config/audio-switcher/presets.toml` (machine-local, created from
[presets.example.toml](presets.example.toml) on first run; the example holds X1Ubuntu's devices, so adapt it on other machines, see §6). Changes are picked up within ~2 s.

```toml
[[preset]]
name   = "Razer headset"
output = "BlackShark"     # text contained in the device name, case-insensitive
input  = "BlackShark"     # omit to leave this side alone; list = fallbacks
```

Device names: `audio-switcher.py --list`. HDMI/DP outputs carry the monitor's name (EDID, e.g.
`Odyssey G95NC`) when a monitor with speakers is connected — match on that, not on "HDMI 1",
because port numbers can shift with docks/MST.

## 3. Keep awake (headset auto-off fix)

**Problem:** wireless headsets with a power-saving timer switch off after N minutes "without
sound". On Linux that also happens **while you use the mic** (calls, dictation): PipeWire suspends
an output after 5 s of silence, so the headset receives nothing and its timer runs out.

**Fix:** top-bar menu → **Advanced ▸ Keep awake** → tick the headset. Off by default, per device.
While **an app records from that headset's mic** (call, dictation; GNOME shows the mic symbol in
the top bar), the tool plays **inaudible silence** to the headset, so its timer never runs out.
This works whether or not the headset is the selected device. As soon as no app uses the mic, the
silence stops and the headset's own timer applies again, which saves battery. While active, the
submenu shows "Now keeping … awake".

Stored in `~/.config/audio-switcher/settings.json` (managed by the menu).

## 4. Install / uninstall (per user, no sudo)

```bash
./install.sh              # autostart + shortcut <Super>t + start
./install.sh '<Super>j'   # other shortcut ("" = none)
./install.sh --uninstall  # remove autostart + shortcut (keeps presets)
```

Needs `pw-dump`, `wpctl` (PipeWire/WirePlumber), `python3` ≥ 3.11,
`gir1.2-ayatanaappindicator3-0.1` and the GNOME *AppIndicator* extension (Ubuntu default).
Creates `~/.config/autostart/audio-switcher.desktop` and a GNOME custom shortcut
(Settings → Keyboard → Custom Shortcuts → "Audio Switcher").

## 5. How it works

- **State:** `pw-dump` every 2 s (~20 ms). Ports PipeWire reports as unavailable (empty HDMI
  ports, unplugged jack) are hidden — same list as GNOME's own sound menu.
- **Switch:** `wpctl set-default <id>`; ids are looked up fresh by stable `node.name`.
- **Keep awake:** a `pw-cat --playback --target <headset>` stream fed from `/dev/zero`, started
  and stopped by the 2 s poll. "Recording" = a PipeWire link from the headset's mic (same
  device id as the ticked output) to an app's input stream. The stream
  dies with the menu process (`PR_SET_PDEATHSIG`).
- **Menu:** separate Python process, not a GNOME Shell extension → a bug can't crash the session
  and GNOME upgrades don't require porting (relies only on Ubuntu's AppIndicator extension).
- **Shortcut popup:** Wayland doesn't let apps position windows or ask where the mouse is. So
  `--popup` opens a maximized, see-through overlay (35 % blue tint, painted with cairo) on the
  screen GNOME picks, and shows a GTK popover menu (orange accent border) with its tip at the
  mouse. GNOME sends no pointer position when the overlay appears, and positions during its
  ~150 ms zoom-in are skewed. So after ~180 ms the overlay briefly becomes click-through and back,
  which makes GNOME send a fresh "pointer entered" event. If none comes (mouse on another
  screen), the menu opens centered. `POPUP_AT = "center"` at the top of the script = always centered.
- **Dead ends (don't retry):** fullscreen overlays render opaque black and the pointer
  disappears. A GNOME Shell extension would do this cleanly, but it can crash the session and
  needs porting on every GNOME upgrade. Real blur behind the menu is impossible for apps.

## 6. Known limitations

- **Fresh install on another machine: all presets greyed out**
  ([issue #1](https://github.com/djkimCH/dj-audio-switcher/issues/1)). The example presets are
  X1Ubuntu's devices, so elsewhere they show as "(not connected)". Output/Input submenus work.
  **Workaround:** run `python3 audio-switcher.py --list`, then use "Edit presets…" and put
  parts of the listed names into `output` / `input` (e.g. `output = "Jabra"`). Delete presets
  you don't need.
- The tip is not always exactly at the mouse (accepted 2026-10-30 as good enough). The
  placement still drifts somewhat, more the farther the mouse is from the screen center.
- The overlay appears on the screen GNOME chooses. If the mouse is on the other screen, the
  menu opens centered on GNOME's screen.

## 7. Where things live

- Code: this repo (`djkimCH/dj-audio-switcher`), checked out at `/data/AI-Workspace/git/dj-audio-switcher`. Autostart + shortcut point here.
- Per-machine notes (presets, test results): `UbuntuHelper/Ubuntu@{machine}/docs/audio-switcher-setup.md`.

## 8. License

MIT, see [LICENSE](LICENSE).
