# Claude Buddy — Animation Iteration: Claude Character × Cool S Morph — Design Spec

**Date:** 2026-05-20
**Status:** Brainstormed — ready for implementation planning
**Project:** terminal-claude-code-buddy-m5stack
**Reference:** Builds on `2026-05-18-claude-buddy-m5stack-design.md`. Phases 1 and 2 (status display + PermissionRequest hook) are shipped on `phase2-permission-race`.

## Goal

Replace the device's current text-only visual feedback (green "Auto: <tool>" rectangle, plain "Claude Buddy" text splash) with a generalized, fun animation system built around two visual primitives:

1. **A pixel-art Claude character** in Anthropic orange — the device's default avatar.
2. **The "Cool S" graffiti motif** — the device's signature flourish.

The two morph into each other as the signature motion. The morph is not decorative: it **carries meaning** — Claude is the default state; auto-approve mode swaps Claude *for* the Cool S until auto turns off. The character literally becomes the cooler, more daring version of itself while it's making decisions on the user's behalf.

This iteration also generalizes the animation layer so it covers boot, connect/disconnect, the permission-prompt screen, and every state transition — not just auto-approve.

## Motion budget (load-bearing constraint)

**Calm by default.** Idle animation is minimal: a slow blink and a 2-pixel breathing bob (~4s cycle). Big motion is reserved for events. This is a deliberate choice — the device sits on a desk in the user's peripheral vision while they type; constant motion is hostile to that context. The fun lives in the event reactions, not the resting state.

Audio is also unchanged from Phase 2: existing tones only (auto-toggle confirm, decision beep). No new sounds in this iteration.

## Visual primitives

### 1. Claude pixel character

Custom pixel art designed in this spec. Style spec (per the user-provided reference):

- Anthropic orange body (`#cc7a4f` ± rendering), black "ink" details (eyes), dark background.
- Chunky 16×16 logical pixels, drawn 4× to fill a ~64×64 px sprite footprint on the 320×240 LCD.
- Frame set (compiled as PROGMEM byte arrays in `firmware/src/claude_sprites.h`):
  - **idle** — 2 frames: open-eyes, half-blink. ~4s cycle.
  - **attention** — 1 frame: forward-lean, wide eyes. Held during permission prompts.
  - **celebrate** — 3 frames: arms-up cycle, ~150ms/frame.
  - **dizzy** — 3 frames: stars/swirl cycle, ~150ms/frame.
  - **morph-to-S** — 6 frames: progressive dissolve from Claude silhouette to Cool S silhouette.
  - (`morph-from-S` is the reverse — same frames, played backwards.)

Per-frame budget: 16×16 × 1 bit = 32 bytes raw; with a 16-color palette ~128 bytes/frame. Total sprite asset: ~1.8 KB across 15 frames — comfortably within firmware flash, no LittleFS needed.

The actual pixel grids are designed inline in the implementation plan (one ASCII grid per frame), so they can be red-lined before code.

### 2. Cool S vector

Drawn line-by-line in firmware via `cool_s.cpp`:

```cpp
void drawCoolS(LGFX* tgt, int cx, int cy, int size, uint16_t color);
void drawCoolSPartial(LGFX* tgt, int cx, int cy, int size,
                      uint16_t color, float progress);  // 0..1, strokes 0..14 lines
```

The Cool S is a 14-vertex polygon (the canonical "Stüssy S" outline — two parallel zigzag bands connected by a diagonal middle, with sharp tips at top-left and bottom-right). Drawing it as a vector instead of a sprite means:

- Crisp at any size.
- Stroke-on animation is free (`drawCoolSPartial`).
- One implementation handles all four roles below.

The 14-point coordinate set is defined inline in the implementation plan as a `const POINT[14]` table.

## Where the primitives live on screen

| Element | Default state | During AUTO ON | During permission prompt |
|---|---|---|---|
| Center (64×64 area) | Claude idle | Cool S (morphed in) | Claude attention |
| Top status bar | Counts + msg (existing) | Red `AUTO ON · N` (existing) | Tool + detail (existing) |
| Four corners (24×24) | Dim Cool S emblems, slow pulse | Fully lit Cool S emblems | Hidden (keeps screen readable) |
| Bottom toast strip | Hidden | Hidden (replaced by morph) | Hidden |

The "green Auto: <tool>" rectangle from the current build is **deleted**. Auto-fire feedback comes through the morph flash instead (see below).

## Event-to-animation mapping

| Trigger | Animation |
|---|---|
| Boot | Big Cool S strokes itself on (13 lines over ~600ms via `drawCoolSPartial`), dissolves into Claude saying "Hello." Total ~1.5s. Replaces current text-only splash. |
| BLE central connects (bridge link up) | Brief Claude `celebrate` (1s), then idle. |
| BLE central disconnects | Claude `dizzy` (1s), then idle (corners dim further to signal disconnected). |
| `evt:status` heartbeat | No animation. Status bar updates. |
| `evt:react` heartbeat (new event, see Protocol) | Switch Claude to the named pose for 1s, then back to idle. |
| `evt:prompt` arrives | Claude → `attention`; corners brighten; centerpiece freezes until decision. |
| Decision sent (allow) | Claude → `celebrate` for 1s, then idle. Existing tone. |
| Decision sent (deny) | Claude → `dizzy` for 1s, then idle. Existing tone. |
| Button B (auto toggle) → ON | Center morphs Claude → Cool S over ~500ms (6 frames). Top bar turns red. Corners fully light. |
| `evt:auto_fired` while AUTO ON | Center briefly morphs Cool S → Claude → `celebrate` (1 frame) → Cool S, ~600ms total. Counter ticks up in the top bar. |
| Button B (auto toggle) → OFF | Reverse morph: Cool S → Claude over ~500ms. Top bar back to status. Corners dim. |
| Idle for ~30s while AUTO is OFF | Single rare delight beat from `CS_IDLE_CLAUDE`: brief morph through to S and back (~1s total). Caps at one per 30s window so it's still calm. Does NOT fire while AUTO is ON — that would dilute the morph's meaning as the AUTO indicator. |

## State machine

The firmware's `loop()` extends from today's "render-on-event" model to a small explicit animation FSM:

```
enum CenterState {
  CS_IDLE_CLAUDE,
  CS_ATTENTION,
  CS_CELEBRATE,
  CS_DIZZY,
  CS_MORPHING_TO_S,
  CS_IDLE_S,             // only reachable while autoApprove == true
  CS_MORPHING_FROM_S,
};
```

Transition rules are derived directly from the event-mapping table above. Frame timing comes from a single `static uint32_t nextFrameMs` ticked in `loop()`. The state machine lives in `firmware/src/animation.cpp` so `main.cpp` stays the BLE glue layer.

**Invariants:**

- `CS_IDLE_S` is reachable only when `autoApprove == true`. Turning auto off while in `CS_IDLE_S` triggers `CS_MORPHING_FROM_S`.
- Prompt arrival from any state interrupts to `CS_ATTENTION`. After decision, return to the appropriate idle (`CS_IDLE_S` if auto, else `CS_IDLE_CLAUDE`).
- The rare idle-morph (every ~30s) is a `CS_MORPHING_TO_S` → `CS_MORPHING_FROM_S` round trip and only fires from `CS_IDLE_CLAUDE` (never from `CS_IDLE_S`, to keep the AUTO meaning unambiguous).

## Bridge ⇄ device protocol changes

One new bridge → device event:

```json
{"evt":"react","kind":"celebrate"|"dizzy"|"attention"}
```

Used by the bridge to poke specific reactions in response to hook events that don't already have a dedicated event — e.g. `Stop` hook → `celebrate` briefly. The bridge is the policy layer; the firmware just plays what it's told.

All other events (`status`, `prompt`, `prompt_cancel`, `auto_fired`) are unchanged. No changes to device → bridge messages.

Privacy line (from the original spec) is unchanged: `react.kind` is metadata, not transcript content.

## File layout (firmware)

```
firmware/src/
├── main.cpp               (BLE + button glue — slimmed)
├── animation.cpp / .h     (NEW — center FSM, frame ticker, corner pulse)
├── claude_sprites.h       (NEW — PROGMEM frame arrays + palette)
├── cool_s.cpp / .h        (NEW — vector draw + partial stroke)
└── render.cpp / .h        (NEW — render helpers shared between status / prompt / animation)
```

`main.cpp` keeps the BLE callbacks and button polling. Everything pixel-pushing moves into the new files. This is partly to keep `main.cpp` readable, partly because the existing file has grown large enough that touching it for visual changes is risky — the boundaries here pay off immediately.

`bridge/buddy_bridge/protocol.py` gains one encoder (`encode_react(kind)`); no decoder side changes since `react` is bridge-originated.

## Out of scope (explicit)

- New sounds beyond the existing tones.
- Multiple character packs (single canonical Claude pixel character only).
- Idle-time ambient particles or weather effects.
- Animated GIF playback / LittleFS asset loading (we deliberately stay PROGMEM-only).
- Bridge protocol changes beyond the single `react` event.
- Anything tied to Phase 2.5 (`AskUserQuestion` mirroring) — that's a separate spec, though Phase 2.5 can opportunistically use `react`.

## Risks

- **Sprite art quality is the spec author's responsibility.** ASCII pixel grids in the plan need to actually look like a character; if a frame falls flat, iterate in the plan, not in code. Mitigation: every frame's grid is presented for user red-line before any C is written.
- **ESP32 frame rate on the 320×240 LCD with PROGMEM sprites and vector Cool S.** Worst case is the morph: 6 sprite frames + corner pulses + status bar repaint at ~10–12 fps. Mitigation: the morph is short (500ms), corners only redraw on state change, and we measure frame budget in the plan's first task before committing.
- **Color rendering on the ILI9342 panel.** Anthropic orange may shift toward red/yellow depending on the panel batch. Mitigation: pick the RGB565 value by eye on the actual hardware in the first plan task, not from a desktop preview.
