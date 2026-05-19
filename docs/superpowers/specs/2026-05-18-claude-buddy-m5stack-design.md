# Claude Buddy for M5Stack Core Basic — Design Spec

**Date:** 2026-05-18
**Status:** Approved design — ready for implementation planning
**Supersedes:** `2026-05-18-m5stack-claude-buddy-design.md` (invalidated by the
bridge-verification finding below).

## Goal

A desk device that shows the live status of **terminal** Claude Code sessions
and lets the user approve or deny tool-permission prompts from the device — for
a user who works in the terminal CLI and does **not** want to run Claude Code
from the desktop app.

Hardware: **M5Stack Core Basic** (ESP32, 320×240 display, 3 front buttons,
no IMU).

## Why a local bridge instead of a firmware port

The original plan was to port Anthropic's
[`claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy)
firmware and rely on the **Claude desktop app** to bridge sessions over BLE.

**The requirement that rules that out:** the user works in the terminal CLI and
does not want a dependency on the Claude desktop app running. A design that
needs the desktop app as the bridge fails that requirement regardless of how
well the app performs. So the project builds its own local bridge; Anthropic's
firmware is retained only as a **protocol reference**.

### What the diagnostic probe established (and what it did not)

A minimal BLE peripheral was flashed to the Core Basic and paired to the
desktop app's Hardware Buddy. Confirmed by direct observation:

- The M5Stack BLE path works: it flashes, runs the BLE stack, advertises the
  Nordic UART Service, and survives bonded/passkey pairing.
- The bridge protocol is JSON-over-NUS, captured live:
  - Heartbeat: `{"total","running","waiting","msg","entries","tokens","tokens_today"}`
  - Permission prompt (nested in heartbeat): `"prompt":{"id","tool","hint"}`
  - Turn event: `{"evt":"turn","role","content":[...]}`
  - Central→device poll: `{"cmd":"status"}`
  - Device→central decision: `{"cmd":"permission","id","decision":"once"|"deny"}`
- The bridged data includes **verbatim transcript content** — message text,
  file listings, summaries — not just metadata.

Not established: the precise rule by which the desktop app decides which
sessions to bridge. One test of a live terminal session showed `running:0`,
but the mechanism was not fully characterized. **This design does not depend on
that question** — it replaces the desktop app entirely.

## Architecture — three components

```
Claude Code (terminal × N)         Local bridge (Python)            M5Stack Core Basic
──────────────────────────         ─────────────────────           ──────────────────
 hooks: SessionStart, Stop,  ─sock─▶  • BLE central (bleak)  ─BLE──▶  320×240 status
 Notification feed status            • aggregates all          NUS    screen
 PreToolUse races: renders             sessions' state        proto   3 buttons (A/C/B)
 a /dev/tty prompt AND        ◀─sock─  • relays prompt⇄          ◀──── approve / deny /
 awaits bridge; first wins              decision                       auto-approve
```

### 1. Firmware (`firmware/`)

- C++ / Arduino / PlatformIO, board `m5stack-core-esp32`, library `M5Unified`.
- **Grown from the working diagnostic probe** — not ported from the StickC
  monolith. The probe is already a working Core Basic BLE peripheral that
  advertises the Nordic UART Service and survives bonded/passkey pairing.
- BLE peripheral: Nordic UART Service (`6e400001/2/3-b5a3-f393-e0a9-e50e24dcca9e`),
  bonded pairing with a DisplayOnly passkey (shown on the 320×240 screen).
- Renders the aggregate status screen and permission-prompt takeover.
- 3 buttons: **A = Approve**, **C = Deny**, **B = toggle Auto-approve**.

### 2. Bridge (`bridge/`)

- Python, using `bleak` as the BLE **central** (connects to the M5Stack).
- Runs as a `launchd` user agent: auto-starts at login, auto-reconnects BLE.
- Maintains aggregated state across **all** terminal Claude Code sessions.
- Listens on a Unix domain socket (e.g. `~/.claude-buddy/bridge.sock`) for
  events from hook scripts.
- Relays: session state → device heartbeats; permission prompt → device;
  device button decision → back to the waiting `PreToolUse` hook.

### 3. Hooks (`hooks/`)

Small scripts wired into `~/.claude/settings.json`:

- `SessionStart` / `SessionEnd` — register/deregister a session with the bridge.
- `Stop` / `Notification` — push status updates (idle, waiting for input, etc.).
- `PreToolUse` — runs the **permission race** (see below).

## Protocol (device ⇄ bridge)

The bridge owns both ends, so the protocol is defined by this project. UTF-8
JSON, one object per line, `\n`-terminated, over the Nordic UART Service.

**Bridge → device:**

- `{"evt":"status","running":N,"waiting":N,"total":N,"msg":"..."}` — live
  aggregate status.
- `{"evt":"prompt","id":"...","tool":"...","detail":"<full command/path/URL>","change":"+12/-3"}`
  — a pending permission prompt. `detail` is the complete, untruncated tool
  call; `change` is present only for `Edit` / `Write`. No file contents (see
  Privacy).
- `{"cmd":"prompt_cancel","id":"..."}` — clear a prompt resolved on the
  keyboard.

**Device → bridge:**

- `{"cmd":"permission","id":"...","decision":"allow"|"deny"}` — the user's
  decision.
- `{"cmd":"auto","state":true|false}` — auto-approve toggle changed.

## Privacy — what the bridge may and may not send (load-bearing)

The probe showed the desktop app's bridge streams **verbatim transcript
content** over BLE — message text, file listings, summaries. This project draws
a precise line instead, so the user can still approve safely while away from
the keyboard without leaking their work.

**Sent to the device:**

- Live session status: counts (`running` / `waiting` / total) and a one-line
  status message derived from hook metadata.
- For a pending permission prompt, the **complete pending tool call**,
  untruncated: the full `Bash` command, the full file path for `Edit` / `Write`,
  the full URL for `WebFetch`, etc. This is the action being approved — the
  user needs all of it. (The reference protocol's 44-char `hint` is too short
  and is not used.)
- For `Edit` / `Write`, the file path plus a change *size* (e.g. `+12/-3 lines`).

**Never sent to the device:**

- Conversation / message text, summaries, the heartbeat `entries[]` transcript
  chatter.
- File *contents* or diff bodies.
- Anything read from Claude Code transcript files (`~/.claude/projects/`). The
  bridge works solely from live hook events and never reads or replays
  transcripts.

Rationale: the tool call and its arguments are exactly what the user approves —
parity with Claude Code's own native prompt. The surrounding conversation is
not needed for that decision and is not transmitted.

## Key behaviors

### Multi-session aggregation

The bridge tracks every terminal session. The device shows an aggregate
(e.g. `3 running · 1 waiting`) plus the most recent activity line. Permission
prompts from any session are queued and presented one at a time.

### Permission race (device or keyboard)

When a tool needs approval, the `PreToolUse` hook:

1. Sends the prompt (`id`, `tool`, `hint`) to the bridge → device shows it.
2. Opens `/dev/tty` in raw mode and prints a compact prompt:
   `Approve Bash: ls -la …?  [a]llow  [d]eny  — or press a button on your buddy`.
3. Waits on **both** inputs concurrently. First responder wins:
   - keyboard keypress → hook returns the decision; bridge sends
     `prompt_cancel` so the device clears it.
   - device button (relayed via the bridge socket) → hook returns the decision.
4. Returns the `PreToolUse` permission decision (`allow` / `deny`) to Claude
   Code, preempting Claude's native prompt.

Fallbacks:

- Bridge not running, or device disconnected → keyboard-only (the race simply
  has one runner).
- No controlling tty (non-interactive run) → hook returns `ask`, yielding to
  Claude Code's native prompt.

### Auto-approve mode

- Toggled by device button **B**; device reports state via `{"cmd":"auto"}`.
- While on, the bridge auto-approves incoming prompts without waiting.
- Reliable reset is now possible because the bridge receives real
  `SessionStart` / `SessionEnd` hook events. Reset triggers: a hard wall-clock
  timeout (e.g. 15 min), when all sessions have ended, and manual toggle-off.
- Default off. While on, the device shows an unmissable `AUTO` banner and beeps
  on every auto-approval.
- Accepted risk: while on, all tool calls are approved unattended; scope cannot
  be narrowed because prompt metadata is limited to `tool` + `hint`.

## Repo strategy

**Fork** `anthropics/claude-desktop-buddy` on GitHub, then **rename** the fork
(e.g. `claude-buddy-m5stack`). Forking is required to keep the visible
"forked from anthropics/claude-desktop-buddy" link; renaming a fork preserves
that link while giving the project its own name.

Notes:

- A fork of a public repo is public. Accepted.
- The fork inherits Anthropic's LICENSE. Our additions live under it.
- Anthropic's original files are **kept** in the repo as the protocol
  reference, not deleted.

Resulting layout:

```
claude-buddy-m5stack/   (fork of anthropics/claude-desktop-buddy)
├── src/, characters/, …   ← Anthropic's original — kept as protocol reference
├── firmware/              ← M5Stack Core Basic firmware (grown from the probe)
├── bridge/                ← Python bridge + launchd agent
├── hooks/                 ← Claude Code hook scripts
└── docs/                  ← this spec + the implementation plan
```

## Phasing

- **Phase 1 — Status display (read-only).** Bridge + `SessionStart` /
  `SessionEnd` / `Stop` / `Notification` hooks; firmware shows live aggregate
  session status on 320×240. No approvals.
- **Phase 2 — Permission race.** `PreToolUse` hook, device approve/deny,
  auto-approve toggle.
- **Phase 3 (optional) — Animated pet.** Port the desk-pet character.

## Milestones

- **M0** — Fork + rename the repo; clone locally. *(The diagnostic probe — BLE
  path, pairing, protocol capture — is already done; see "Why a local bridge
  instead of a firmware port".)*
- **M1** — Bridge skeleton: connects to the M5Stack as BLE central, opens the
  Unix socket. Probe firmware confirms the link.
- **M2** — Phase 1 hooks push real session state through the bridge; firmware
  renders the aggregate status screen on 320×240.
- **M3** — `PreToolUse` permission race works end-to-end: approve/deny from
  device or keyboard, first responder wins, loser side cleared.
- **M4** — Auto-approve toggle with banner, beep, and reset triggers.
- **M5** — `launchd` agent: bridge auto-starts and auto-reconnects.
- **M6 (optional)** — Phase 3 animated pet.

## Resolved / carried-over risks (from the adversarial review)

- **AXP192 PMIC, linker stubs, IMU-entangled clock/nap code, 135→320 sprite
  rework** — *resolved*: these were all artifacts of porting the StickC
  monolith. The firmware is now grown from the probe, so they do not arise.
- **"No new-session event" broke auto-approve reset** — *resolved*: the bridge
  receives real `SessionStart`/`SessionEnd` hook events.
- **Bridge-verification gated too late** — *resolved*: done first, via the
  probe, before any porting investment.
- **Carried over**: M5Unified display/button API must be learned for the
  firmware; RAM budget for a 320×240 render path must be checked; the bonded
  passkey must render on the device screen.

## Out of scope (Phase 1)

- Permission approvals (Phase 2).
- Auto-approve (Phase 2).
- The animated pet / GIF packs (Phase 3).
- Any dependency on the Claude desktop app.
- Folder-push asset transfer.
