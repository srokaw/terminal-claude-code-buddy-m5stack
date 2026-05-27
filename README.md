# terminal-claude-code-buddy-m5stack

A desk device that shows the live status of your **terminal** Claude Code
sessions and lets you approve or deny tool-permission prompts — and answer
`AskUserQuestion` prompts — right from the device.

This is a fork of Anthropic's
[`claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy),
re-aimed at people who work in the Claude Code CLI and **don't** want a
dependency on the Claude desktop app. Instead of the desktop app bridging
sessions over BLE, a small **local Python bridge** talks to the device, fed by
**Claude Code hooks**. The device is an **M5Stack Core Basic** (ESP32, 320×240,
three front buttons) rather than the M5StickC Plus.

> Anthropic's original firmware (`src/`, `characters/`, …) and
> [REFERENCE.md](REFERENCE.md) are kept in this repo as a **protocol reference**
> only. You don't need them to run this fork. This project defines its own
> wire protocol (see [Protocol](#protocol) below).

## Architecture

```
Claude Code (terminal × N)         Local bridge (Python)           M5Stack Core Basic
──────────────────────────         ─────────────────────           ──────────────────
 hooks: SessionStart, Stop,  ─sock─▶  • BLE central (bleak)  ─BLE──▶  320×240 animated
 Notification … feed status          • aggregates all          NUS    buddy
 PermissionRequest relays the          sessions' state        proto   3 buttons (A/B/C)
 prompt + waits on the       ◀─sock─  • relays prompt ⇄         ◀──── approve / deny /
 device (native prompt too)             decision                       auto-approve
```

Three components live in this repo:

- **`firmware/`** — M5Stack Core Basic firmware (C++ / Arduino / PlatformIO).
  BLE peripheral over the Nordic UART Service with bonded passkey pairing;
  renders an **animated buddy** on the home screen (see [The buddy](#the-buddy)),
  the permission-prompt takeover, and the `AskUserQuestion` screen.
- **`bridge/`** — Python bridge using `bleak` as the BLE **central**. Maintains
  aggregated state across all terminal sessions, listens on a Unix domain
  socket for hook events, and brokers permission/ask prompts between Claude Code
  and the device.
- **`hooks/`** — Claude Code hook scripts wired into `~/.claude/settings.json`.

## Hardware

**M5Stack Core Basic** (ESP32, 320×240 ILI9342C display, three front buttons
A/B/C). The firmware uses `M5Unified` for display and buttons, so a different
ESP32 board works only if you adapt those drivers.

## Setup

### 1. Flash the firmware

Install
[PlatformIO Core](https://docs.platformio.org/en/latest/core/installation/),
then from `firmware/`:

```bash
pio run -t upload
```

On first connect the device shows a pairing passkey on its screen (DisplayOnly
bonding); enter it on the host when prompted.

### 2. Run the bridge

Use a virtualenv. `bleak` (the BLE library) is **not** typically on your system
Python, so running with a bare `python` will fail with `ModuleNotFoundError: No
module named 'bleak'` — always invoke the venv's interpreter explicitly:

```bash
cd bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # bleak, pytest, pytest-asyncio
.venv/bin/python -m buddy_bridge
```

The bridge listens on `~/.claude-buddy/bridge.sock` and connects to the device
over BLE as a central, auto-reconnecting. The macOS Bluetooth permission prompt
appears on first run; grant it. On startup it logs
`[bridge] listening on …/bridge.sock`.

This foreground form is fine for testing, but if the terminal closes the bridge
dies and sessions silently fall back to keyboard-only (no error surfaces in
Claude Code). For day-to-day use, run it as a launchd agent (next step).

### 3. Run the bridge as a launchd agent (persistent)

A launchd **user** agent (not a root daemon) starts the bridge at login and
restarts it if it ever exits. `bridge/com.claude-buddy.bridge.plist` is a
template; launchd doesn't expand `~`, so fill in the two absolute-path
placeholders and install it. From the repo root:

```bash
# These dirs may not exist on a fresh account; launchd needs both before it can
# write the plist and open the log file, or the job fails to start.
mkdir -p ~/Library/LaunchAgents ~/.claude-buddy

sed -e "s|__REPO_DIR__|$(pwd)|g" -e "s|__HOME__|$HOME|g" \
  bridge/com.claude-buddy.bridge.plist \
  > ~/Library/LaunchAgents/com.claude-buddy.bridge.plist

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.claude-buddy.bridge.plist
launchctl kickstart -k gui/$(id -u)/com.claude-buddy.bridge
```

Manage it:

```bash
launchctl print gui/$(id -u)/com.claude-buddy.bridge | grep -E 'state|pid'  # status
tail -f ~/.claude-buddy/bridge.log                                          # logs
launchctl kickstart -k gui/$(id -u)/com.claude-buddy.bridge                 # restart
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.claude-buddy.bridge.plist  # stop
```

> The plist hardcodes the venv path, so if you move or rename the repo,
> regenerate and reinstall it (the agent will otherwise log a startup failure
> and back off rather than run).

Lock down the runtime directory so other local accounts can't reach the socket:

```bash
chmod 700 ~/.claude-buddy
```

### 4. Wire up the hooks

The hook scripts talk to the bridge over that socket. Merge the `hooks` block
from [`hooks/settings.example.json`](hooks/settings.example.json) into your
`~/.claude/settings.json`. The example points at
`$HOME/terminal-claude-code-buddy-m5stack/hooks/…` (the shell expands `$HOME`);
edit those paths if you cloned the repo elsewhere:

- `SessionStart` / `SessionEnd` — register / deregister a session.
- `UserPromptSubmit` / `Stop` / `Notification` — push status updates.
- `PermissionRequest` — relays the prompt to the device and waits for a button
  decision (45s for permissions, 60s for `AskUserQuestion`). The `timeout: 90`
  in the example settings is Claude Code's outer cap on the hook and must stay
  above those waits.

If the bridge isn't running, the device is disconnected, or the wait times out,
the hook returns no decision and Claude Code's native terminal prompt stands.

## Controls

| Button        | Status screen   | Permission prompt | Ask screen                |
| ------------- | --------------- | ----------------- | ------------------------- |
| **A** (left)  | —               | **Approve**       | pick top row              |
| **B** (mid)   | toggle **Auto** | toggle **Auto**   | pick middle row           |
| **C** (right) | —               | **Deny**          | pick bottom row / `More`  |

On the ask screen the three buttons map directly to the on-screen option rows;
with four options, the bottom row is `More >>` / `<< Back` to page. In a
multi-select question a button press toggles that option, and **long-press B**
submits. **Long-press A** cancels (answer on the laptop instead).

The home screen shows the animated buddy (see [The buddy](#the-buddy) below); a
bottom strip carries the one-line activity message and a `N sessions` count.
Auto-approve mode (toggle **B**, available from the home screen or during a
prompt) shows an **AUTO ON · n** banner with a running
count of auto-approvals. The banner is repainted on toggle and after each
auto-approval, but a routine status redraw clears it until the next one. The
bridge clears its own auto-approve state
when the device disconnects, but the device keeps the flag and re-asserts it on
reconnect — so a disconnect does not durably turn it off. Toggle **B** to turn it
off for real.

## The buddy

The home screen is an animated retro-phosphor **"cool-S"** character (the
schoolyard S doodle), green-on-black. Its animation *is* the status display —
it reflects live session state rather than showing raw counts:

| State      | When                                                              |
| ---------- | ----------------------------------------------------------------- |
| **sleep**  | bridge not connected — slow breathing, a drifting `z`             |
| **idle**   | connected, nothing running — step-by-step construction loop       |
| **busy**   | one or more sessions running — scanline sweep + progress dots     |
| **heart**  | ~3s after you approve a prompt with **A** — turns red, floating hearts |

Three more states — **attention**, **celebrate**, **dizzy** — are implemented
but not yet wired to live triggers in this build. A small bottom strip overlays
the activity message (left) and a `N sessions` count (right); running/waiting
are conveyed by the animation, not by on-screen numbers.

## Behaviors

### Permission prompts (device or keyboard)

When a tool needs approval, Claude Code shows its **native** terminal prompt as
usual, and the `PermissionRequest` hook concurrently relays the prompt to the
device. The hook does not draw its own keyboard UI — it just waits on the bridge
socket for a button decision. Whichever path resolves first wins:

- **Device button** (relayed through the bridge socket) → the hook returns
  `allow` / `deny`, preempting the native prompt.
- **Keyboard** → when Claude Code cancels the hook (it sends `SIGTERM`, e.g.
  once the native prompt is answered), the hook's handler sends `prompt_cancel`
  so the device clears its screen.

On timeout (45s) the hook also sends `prompt_cancel` and returns no decision, so
the native prompt stands.

### AskUserQuestion on the device

`AskUserQuestion` prompts render on the device with the question text and option
labels/descriptions. The firmware supports 1–4 questions, 2–4 options each (paged
at 4), and multi-select. Text is stored in fixed buffers (question 64, label 28,
description 40 bytes) and longer values are truncated. Answers come back as
`{label}` or `{labels:[…]}` carrying the device's stored label string — so if a
label was truncated, the relayed value is the truncated form. Keep option labels
short. Resolving on the keyboard cancels the device screen and vice versa.

### Privacy (load-bearing)

The bridge sends the device only what's needed to decide:

- **Status:** counts and a short one-line message derived from hook metadata.
- **Permission prompts:** the tool call itself — `Bash` command, file path for
  `Edit`/`Write` plus a change *size* like `+12/-3`, URL for `WebFetch`. Known
  tools send the full command/path/URL; an unknown tool's args are summarized
  (large fields replaced by a length) and capped at 200 bytes by the hook. The
  device then truncates whatever it receives to its own 200-byte display buffer.
- **Ask prompts:** question text and option labels/descriptions (truncated on
  the device per the buffer sizes above).

It **never** sends conversation/message text, file contents or diff bodies, or
anything read from transcript files. The bridge works solely from live hook
events and never reads `~/.claude/projects/`.

## Protocol

UTF-8 JSON, one object per line, `\n`-terminated, over the Nordic UART Service
(`6e400001/2/3-b5a3-f393-e0a9-e50e24dcca9e`). Defined by this project — see
[`bridge/buddy_bridge/protocol.py`](bridge/buddy_bridge/protocol.py).

**Bridge → device:**

- `{"evt":"status","running":N,"waiting":N,"total":N,"msg":"…"}`
- `{"evt":"prompt","id":"…","tool":"…","detail":"…","change":"+12/-3"}` — `change` only for `Edit`/`Write`
- `{"evt":"ask","id":"…","multiSelect":bool,"questions":[{"text":"…","options":[{"label":"…","desc":"…"}]}]}`
- `{"evt":"auto_fired","tool":"…"}`
- `{"cmd":"prompt_cancel","id":"…"}` / `{"cmd":"ask_cancel","id":"…"}` / `{"cmd":"get_auto"}`

**Device → bridge:**

- `{"cmd":"permission","id":"…","decision":"allow"|"deny"}`
- `{"cmd":"auto","state":bool}`
- `{"cmd":"prompt_busy","id":"…"}` — device can't show this prompt; yield to native UI
- `{"cmd":"ask_answer","id":"…","answers":[{"label":"…"}|{"labels":[…]}]}`
- `{"cmd":"ask_cancel","id":"…"}`

## Project layout

```
firmware/        — M5Stack Core Basic firmware (PlatformIO)
  src/main.cpp   — state machine, BLE peripheral, 30fps render loop, prompt/ask/passkey screens
  src/buddy_*.{h,cpp}  — cool-S buddy: geometry, 7-state animations, palette, rendering
  test/          — native (host) Unity unit tests for the buddy's pure logic
bridge/          — Python local bridge (BLE central)
  buddy_bridge/  — ble_link, socket_server, permissions broker, protocol, state
  tests/         — pytest suite
  com.claude-buddy.bridge.plist  — launchd agent template (see Setup step 3)
hooks/           — Claude Code hook scripts + settings.example.json
docs/            — design specs and implementation plans (docs/superpowers/)
src/, characters/, tools/, REFERENCE.md
                 — Anthropic's original claude-desktop-buddy, kept as protocol reference
```

## Tests

**Bridge (Python):**

```bash
cd bridge && .venv/bin/python -m pytest
```

(Use `python -m pytest`, not bare `pytest` — the `-m` form puts the bridge
directory on `sys.path` so the `buddy_bridge` package imports.)

**Firmware (native unit tests):** the buddy's pure logic — geometry, state
machine, palette, easing, idle-reveal timing — is unit-tested off-device via a
PlatformIO `native` env, no hardware required:

```bash
cd firmware && pio test -e native
```

## License

Inherited from the upstream fork — see [LICENSE](LICENSE).