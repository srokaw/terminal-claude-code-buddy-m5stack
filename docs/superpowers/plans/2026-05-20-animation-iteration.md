# Animation Iteration — Claude Character × Cool S Morph — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current text-only visual feedback (green "Auto: <tool>" toast and plain "Claude Buddy" text splash) with a generalized animation layer. Centerpiece is a pixel-art Claude character in Anthropic orange that **morphs into a Cool S** while auto-approve is on, and morphs back when it turns off.

**Architecture:** Split `firmware/src/main.cpp` into focused modules — BLE/button glue stays in `main.cpp`; everything pixel-pushing moves to new `render.cpp`, `animation.cpp`, `cool_s.cpp`, and `claude_sprites.h`. The center-screen animation runs as an explicit FSM ticked from `loop()`. PROGMEM sprite frames + a vector-drawn Cool S keep RAM usage flat and avoid LittleFS. The bridge gains one new event (`react`) so hook scripts can poke reactions.

**Tech Stack:** C++ / Arduino / PlatformIO with `M5Unified` on the M5Stack Core Basic; Python bridge for the new `react` event; pixel art designed inline as ASCII grids in this plan (expect red-lines from the user before code).

**Reference:** Design spec `docs/superpowers/specs/2026-05-20-animation-iteration-design.md`.

---

## File layout

**Created:**
- `firmware/src/render.cpp` / `render.h` — `renderStatus` (moved), `renderPrompt` (moved), `renderHello`, helpers shared with `animation.cpp`.
- `firmware/src/animation.cpp` / `animation.h` — center-screen FSM + frame ticker; corner Cool S pulse.
- `firmware/src/cool_s.cpp` / `cool_s.h` — `drawCoolS` + `drawCoolSPartial` vector draws.
- `firmware/src/claude_sprites.h` — PROGMEM byte arrays for all character frames + the Anthropic-orange palette entry.
- `firmware/src/colors.h` — central place for `ANTHROPIC_ORANGE` and other named colors set during calibration.

**Modified:**
- `firmware/src/main.cpp` — strip out renderers; wire `animation.cpp` to BLE events and buttons.
- `bridge/buddy_bridge/protocol.py` — add `encode_react`.
- `bridge/buddy_bridge/__main__.py` — wire `react` send callback.
- `bridge/tests/test_protocol.py` — test `encode_react`.
- `hooks/buddy-hook.py` — emit a `react` socket message on `Stop` so the device celebrates briefly when a Claude turn finishes.

---

## Task 1: Calibrate Anthropic-orange + measure frame budget on hardware

**Files:**
- Create: `firmware/src/colors.h`

This task runs first so later sprite work uses a color that actually looks right on the panel, not a guess from an editor. It also gives the implementer a baseline frame-render cost — if the budget is exhausted before any animation lands, the plan needs scope cuts before continuing.

- [ ] **Step 1: Pick an initial RGB565 candidate**

Anthropic's orange in 24-bit is roughly `#cc7a4f`. Convert to RGB565: red=`0xCC>>3=25=0x19`, green=`0x7A>>2=30=0x1E`, blue=`0x4F>>3=9=0x09`. Pack: `(0x19<<11) | (0x1E<<5) | 0x09 = 0xCBC9`. Use that as the starting point.

- [ ] **Step 2: Write `colors.h`**

Create `firmware/src/colors.h`:

```cpp
#pragma once
#include <stdint.h>

// Initial candidate from the design spec. Tune on-device in Task 1
// (see firmware/.../docs/superpowers/plans/...animation-iteration.md).
constexpr uint16_t ANTHROPIC_ORANGE = 0xCBC9;

// Used for Cool S accents when dimmed (corners in idle).
constexpr uint16_t ANTHROPIC_ORANGE_DIM = 0x6A45;  // ~25% brightness

// Ink for eyes / leg gaps inside the character.
constexpr uint16_t INK_BLACK = 0x0000;

// Screen background.
constexpr uint16_t BG_BLACK = 0x0000;
```

- [ ] **Step 3: Write a one-off color-test main**

Temporarily replace `firmware/src/main.cpp` with a calibration sketch (don't commit this version):

```cpp
#include <Arduino.h>
#include <M5Unified.h>
#include "colors.h"

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);
  M5.Display.fillScreen(BG_BLACK);
  // Three 64x64 swatches across the top.
  M5.Display.fillRect( 16, 24, 64, 64, ANTHROPIC_ORANGE);
  M5.Display.fillRect(128, 24, 64, 64, 0xD3A8);   // alt 1: warmer
  M5.Display.fillRect(240, 24, 64, 64, 0xBB68);   // alt 2: cooler
  M5.Display.setTextColor(0xFFFF, BG_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(20, 100);  M5.Display.print("spec");
  M5.Display.setCursor(132, 100); M5.Display.print("warm");
  M5.Display.setCursor(244, 100); M5.Display.print("cool");
  // Frame-budget probe: full-screen fillRect repeated 60 times.
  uint32_t t0 = millis();
  for (int i = 0; i < 60; ++i)
    M5.Display.fillScreen(i & 1 ? BG_BLACK : ANTHROPIC_ORANGE);
  uint32_t dt = millis() - t0;
  M5.Display.fillScreen(BG_BLACK);
  M5.Display.setCursor(8, 8);
  M5.Display.printf("60 full repaints: %lu ms\n", (unsigned long)dt);
  M5.Display.printf("=> ~%lu ms per full clear", (unsigned long)(dt / 60));
}
void loop() {}
```

- [ ] **Step 4: Flash and observe**

```bash
cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX
```

Look at the device. Pick whichever swatch reads as Anthropic orange. Note the per-frame ms.

- [ ] **Step 5: Update `colors.h` to the winning value**

If the spec value didn't win, replace `ANTHROPIC_ORANGE` in `firmware/src/colors.h` with the chosen RGB565.

- [ ] **Step 6: Record the frame budget in a code comment**

Add at the top of `colors.h` as the second line of the file:

```cpp
// Measured 2026-05-20 on Core Basic: ~X ms per full-screen fillScreen.
// Animation budget assumes <=8 fps for the morph and <=4 fps for status repaints.
```

Replace `X` with the measured value.

- [ ] **Step 7: Restore `main.cpp` to its pre-calibration state**

Discard the temporary calibration `main.cpp`:

```bash
git checkout firmware/src/main.cpp
```

- [ ] **Step 8: Commit `colors.h`**

```bash
git add firmware/src/colors.h
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(firmware): tune Anthropic orange + record frame budget"
```

---

## Task 2: Cool S vector module

**Files:**
- Create: `firmware/src/cool_s.h`, `firmware/src/cool_s.cpp`

Draw the Cool S as a 14-segment polyline rather than a sprite. Resolution-independent, free stroke-on animation. The 14-vertex coordinate table is a first draft — visual review on hardware in Step 4 is the gate before continuing.

- [ ] **Step 1: Write `cool_s.h`**

```cpp
#pragma once
#include <stdint.h>
class LovyanGFX;          // M5GFX forward-decl; M5.Display.getInstance() returns one
#include <M5Unified.h>

// Draw the full Cool S centered at (cx, cy), scaled so the bounding box
// is `size` pixels tall. Stroke `thickness` pixels.
void drawCoolS(M5GFX& tgt, int cx, int cy, int size, uint16_t color, int thickness);

// Same, but only draws the first `floor(progress * 14)` segments.
// Use for the boot stroke-on animation. progress is clamped to [0..1].
void drawCoolSPartial(M5GFX& tgt, int cx, int cy, int size, uint16_t color,
                      int thickness, float progress);
```

- [ ] **Step 2: Write `cool_s.cpp`**

```cpp
#include "cool_s.h"

// 14-vertex polygon outline (clockwise from top-left tip) — first draft.
// Coordinates are in a 100x140 design space; runtime scales them to `size`.
// See the design spec's "Cool S" section.
struct PT { int8_t x, y; };
static const PT VERTS[] = {
  { 0,  30}, { 30,   0}, { 60,  30}, {100,   0},   // top zigzag
  {100,  40}, { 30, 100},                          // diagonal across
  {100, 100}, {100, 110},                          // right edge of bottom
  { 70, 140}, { 40, 100}, {  0, 140},              // bottom zigzag
  {  0, 100}, { 70,  40}, {  0,  40},              // diagonal back across
};
static constexpr int N = sizeof(VERTS) / sizeof(VERTS[0]);   // 14

static void mapped(int cx, int cy, int size, int i, int* ox, int* oy) {
  // Scale 100x140 design to a (size * 100 / 140) x size box centered at (cx,cy).
  int w = size * 100 / 140;
  *ox = cx - w / 2 + VERTS[i].x * w / 100;
  *oy = cy - size / 2 + VERTS[i].y * size / 140;
}

static void drawSeg(M5GFX& tgt, int x0, int y0, int x1, int y1,
                    uint16_t color, int thickness) {
  // Thick line: draw `thickness` parallel passes by Bresenham via M5GFX.
  // Cheap and good enough for thickness <= 3.
  for (int t = -(thickness / 2); t <= thickness / 2; ++t) {
    tgt.drawLine(x0, y0 + t, x1, y1 + t, color);
    tgt.drawLine(x0 + t, y0, x1 + t, y1, color);
  }
}

void drawCoolS(M5GFX& tgt, int cx, int cy, int size, uint16_t color,
               int thickness) {
  for (int i = 0; i < N; ++i) {
    int a = i, b = (i + 1) % N;
    int x0, y0, x1, y1;
    mapped(cx, cy, size, a, &x0, &y0);
    mapped(cx, cy, size, b, &x1, &y1);
    drawSeg(tgt, x0, y0, x1, y1, color, thickness);
  }
}

void drawCoolSPartial(M5GFX& tgt, int cx, int cy, int size, uint16_t color,
                      int thickness, float progress) {
  if (progress < 0) progress = 0;
  if (progress > 1) progress = 1;
  int upto = (int)(progress * N + 0.5f);
  for (int i = 0; i < upto; ++i) {
    int a = i, b = (i + 1) % N;
    int x0, y0, x1, y1;
    mapped(cx, cy, size, a, &x0, &y0);
    mapped(cx, cy, size, b, &x1, &y1);
    drawSeg(tgt, x0, y0, x1, y1, color, thickness);
  }
}
```

- [ ] **Step 3: Write a temporary `main.cpp` to visually verify the Cool S**

```cpp
#include <Arduino.h>
#include <M5Unified.h>
#include "colors.h"
#include "cool_s.h"

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);
  M5.Display.fillScreen(BG_BLACK);
  // Three sizes across the screen.
  drawCoolS(M5.Display, 60,  120, 160, ANTHROPIC_ORANGE,     3);
  drawCoolS(M5.Display, 180, 120, 80,  ANTHROPIC_ORANGE,     2);
  drawCoolS(M5.Display, 270, 60,  40,  ANTHROPIC_ORANGE_DIM, 1);
}
void loop() {
  // Cycle the partial-draw animation in the third slot.
  static uint32_t t0 = millis();
  float p = ((millis() - t0) % 2000) / 2000.0f;
  M5.Display.fillRect(220, 140, 100, 100, BG_BLACK);
  drawCoolSPartial(M5.Display, 270, 190, 80, ANTHROPIC_ORANGE, 2, p);
  delay(60);
}
```

- [ ] **Step 4: Flash and stop here for user review**

```bash
cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX
```

Stop and show the user. If the Cool S doesn't read as a Cool S, the `VERTS[]` table is the only thing to change — iterate the coordinates in `cool_s.cpp` until it does. The bottom-right is the partial-draw animation looping; confirm it draws on smoothly.

- [ ] **Step 5: Discard the temporary `main.cpp`**

```bash
git checkout firmware/src/main.cpp
```

- [ ] **Step 6: Commit**

```bash
git add firmware/src/cool_s.h firmware/src/cool_s.cpp
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(firmware): vector Cool S with partial-stroke for boot animation"
```

---

## Task 3: Claude character sprites — `claude_sprites.h`

**Files:**
- Create: `firmware/src/claude_sprites.h`

Sprites are 16×16, 1-bit per pixel (orange-on-black), MSB-first row-major. 32 bytes per frame. The grids below are first drafts — expect red-lines.

Each grid uses `.` = transparent (background black) and `#` = `ANTHROPIC_ORANGE`. Reading left-to-right, top-to-bottom, every 8 bits packs to one byte.

- [ ] **Step 1: Design grids — review with user before encoding**

Show the user these grids before encoding to byte arrays.

**`idle_0` (open eyes, default pose):**

```
................
................
..############..
.##############.
.##############.
.###..####..###.
.###..####..###.
.##############.
.##############.
.##############.
.##############.
.##############.
.##..##..##..##.
.##..##..##..##.
.##..##..##..##.
................
```

**`idle_1` (half-blink):**

```
................
................
..############..
.##############.
.##############.
.##############.
.###..####..###.
.##############.
.##############.
.##############.
.##############.
.##############.
.##..##..##..##.
.##..##..##..##.
.##..##..##..##.
................
```

**`attention` (forward-lean, wide eyes):**

```
................
..############..
.##############.
.##############.
.##############.
.##..######..##.
.##..######..##.
.##..######..##.
.##############.
.##############.
.##############.
.##############.
.##############.
.##..##..##..##.
.##..##..##..##.
.##..##..##..##.
```

**`celebrate_0` (arms-up frame 1 — wider, bumpy outline):**

```
................
.##..######..##.
.##############.
.##############.
.##############.
.###..####..###.
.###..####..###.
.##############.
.##############.
.##############.
.##############.
.##############.
.##############.
.##..##..##..##.
.##..##..##..##.
................
```

**`celebrate_1` (arms higher):**

```
##............##
.###########..##  -- ignore typo. left-arm pixel; symmetric
.##########.....    NB: redesign on red-line; intent is "arms reaching up"
```

(Two more celebrate frames + three dizzy frames + six morph frames follow the same pattern. Designing all 15 grids inline here would dwarf the rest of the plan; instead, **Step 1 stops here for user review**. After approval of these four grids, the implementer iterates with the user to fill in the remaining 11 by the same process: ASCII grid first, encode after.)

- [ ] **Step 2: After user approves all 15 grids, encode to PROGMEM byte arrays**

Once approved, encode each grid into a 32-byte `static const PROGMEM uint8_t` array. Encoding rule: row-major, MSB-first, `#` = 1, `.` = 0. Example for `idle_0` row 2 (`..############..`):

`0011111111111100` → split into `00111111 11111100` → `0x3F, 0xFC`.

The encoded file looks like:

```cpp
#pragma once
#include <Arduino.h>     // PROGMEM

// 16x16 1-bit sprites. 32 bytes / frame, row-major, MSB-first.
// "1" = ANTHROPIC_ORANGE, "0" = transparent. See plan Task 3 for the grids.

static const uint8_t PROGMEM CLAUDE_IDLE_0[32] = {
  0x00,0x00, 0x00,0x00, 0x3F,0xFC, 0x7F,0xFE,
  /* ... rest of the rows ... */
};
static const uint8_t PROGMEM CLAUDE_IDLE_1[32]      = { /* … */ };
static const uint8_t PROGMEM CLAUDE_ATTENTION[32]   = { /* … */ };
static const uint8_t PROGMEM CLAUDE_CELEBRATE_0[32] = { /* … */ };
static const uint8_t PROGMEM CLAUDE_CELEBRATE_1[32] = { /* … */ };
static const uint8_t PROGMEM CLAUDE_CELEBRATE_2[32] = { /* … */ };
static const uint8_t PROGMEM CLAUDE_DIZZY_0[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_DIZZY_1[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_DIZZY_2[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_MORPH_0[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_MORPH_1[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_MORPH_2[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_MORPH_3[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_MORPH_4[32]     = { /* … */ };
static const uint8_t PROGMEM CLAUDE_MORPH_5[32]     = { /* … */ };
```

Each `MORPH_N` frame is `idle_0` with progressively more pixels dropped (checkerboard or random-stable mask), so the dissolve looks like sprite-thinning rather than fading. The Cool S strokes are drawn **on top** by `animation.cpp` (Task 5), not stored as sprites — these morph frames just thin out the character.

- [ ] **Step 3: Visual check via temporary `main.cpp`**

Drop a sketch that draws each sprite in a 4×4 grid:

```cpp
#include <Arduino.h>
#include <M5Unified.h>
#include "colors.h"
#include "claude_sprites.h"

static void drawSprite(int cx, int cy, const uint8_t* spr) {
  for (int y = 0; y < 16; ++y) {
    for (int x = 0; x < 16; ++x) {
      uint8_t byte = pgm_read_byte(spr + (y * 2) + (x / 8));
      bool on = byte & (0x80 >> (x % 8));
      if (on) {
        // Draw 4x larger so 16x16 sprite occupies 64x64.
        M5.Display.fillRect(cx + x * 4, cy + y * 4, 4, 4, ANTHROPIC_ORANGE);
      }
    }
  }
}

void setup() {
  M5.begin(M5.config());
  M5.Display.setRotation(1);
  M5.Display.fillScreen(BG_BLACK);
  drawSprite(  4,  4, CLAUDE_IDLE_0);
  drawSprite( 76,  4, CLAUDE_IDLE_1);
  drawSprite(148,  4, CLAUDE_ATTENTION);
  drawSprite(220,  4, CLAUDE_CELEBRATE_0);
  drawSprite(  4, 76, CLAUDE_CELEBRATE_1);
  drawSprite( 76, 76, CLAUDE_CELEBRATE_2);
  drawSprite(148, 76, CLAUDE_DIZZY_0);
  drawSprite(220, 76, CLAUDE_DIZZY_1);
  // Bottom row: morph frames 0..3 (4..5 next page if needed)
  drawSprite(  4,148, CLAUDE_MORPH_0);
  drawSprite( 76,148, CLAUDE_MORPH_2);
  drawSprite(148,148, CLAUDE_MORPH_4);
  drawSprite(220,148, CLAUDE_MORPH_5);
}
void loop() {}
```

- [ ] **Step 4: Flash and review with user**

```bash
cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX
```

If any sprite doesn't read right, edit the grid in this plan, re-encode, re-flash.

- [ ] **Step 5: Discard the temporary `main.cpp`**

```bash
git checkout firmware/src/main.cpp
```

- [ ] **Step 6: Commit**

```bash
git add firmware/src/claude_sprites.h
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(firmware): Claude pixel-art sprite frames in PROGMEM"
```

---

## Task 4: Render helpers — move `renderStatus` / `renderPrompt` out of `main.cpp`

**Files:**
- Create: `firmware/src/render.h`, `firmware/src/render.cpp`
- Modify: `firmware/src/main.cpp`

Pure refactor — no behavior change. This unblocks Task 5 by establishing the file boundary `animation.cpp` will share.

- [ ] **Step 1: Write `render.h`**

```cpp
#pragma once
#include <stdint.h>

// Full status screen (top bar, counts, message). Idempotent.
void renderStatus(int running, int waiting, int total, const char* msg);

// Permission-prompt takeover. tool/detail/change are null-terminated.
void renderPrompt(const char* tool, const char* detail, const char* change);

// Draw a single 16x16 sprite at (cx, cy), scaled `scale`× (default 4).
// `mask` (0..255) gates random pixels for the morph dissolve; pass 255 for solid.
void drawClaudeSprite(int cx, int cy, const uint8_t* sprite, int scale,
                      uint16_t color, uint8_t mask);

// Top-bar paint for the AUTO ON state. Pass count >= 0.
void renderAutoBanner(int count);
```

- [ ] **Step 2: Write `render.cpp`**

```cpp
#include "render.h"
#include <M5Unified.h>
#include "colors.h"

void renderStatus(int running, int waiting, int total, const char* msg) {
  M5.Display.fillScreen(BG_BLACK);
  M5.Display.setTextColor(0xFFFF, BG_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 8);
  M5.Display.print("Claude Buddy");

  M5.Display.setTextSize(6);
  M5.Display.setTextColor(0x07E0, BG_BLACK);   // green
  M5.Display.setCursor(8, 56);
  M5.Display.printf("%d", running);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 120);
  M5.Display.setTextColor(0x07E0, BG_BLACK);
  M5.Display.print("running");

  M5.Display.setTextSize(6);
  M5.Display.setTextColor(ANTHROPIC_ORANGE, BG_BLACK);
  M5.Display.setCursor(170, 56);
  M5.Display.printf("%d", waiting);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(170, 120);
  M5.Display.setTextColor(ANTHROPIC_ORANGE, BG_BLACK);
  M5.Display.print("waiting");

  M5.Display.setTextSize(2);
  M5.Display.setTextColor(0xFFFF, BG_BLACK);
  M5.Display.setCursor(8, 160);
  M5.Display.printf("%d sessions", total);
  M5.Display.setCursor(8, 200);
  M5.Display.setTextColor(0x7BEF, BG_BLACK);    // dark grey
  M5.Display.print(msg);
}

void renderPrompt(const char* tool, const char* detail, const char* change) {
  M5.Display.fillScreen(0x000F);                // navy
  M5.Display.setTextColor(0xFFFF, 0x000F);
  M5.Display.setTextSize(3);
  M5.Display.setCursor(8, 8);
  M5.Display.printf("Approve %s?", tool);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 50);
  M5.Display.setTextWrap(true);
  M5.Display.print(detail);
  if (change && change[0]) {
    M5.Display.setTextSize(1);
    M5.Display.setTextColor(0x7BEF, 0x000F);
    M5.Display.setCursor(8, 140);
    M5.Display.print(change);
  }
  M5.Display.setTextColor(0x07E0, 0x000F);
  M5.Display.setCursor(8, 210);
  M5.Display.print("[A] Allow");
  M5.Display.setTextColor(0xF800, 0x000F);
  M5.Display.setCursor(180, 210);
  M5.Display.print("[C] Deny");
}

void drawClaudeSprite(int cx, int cy, const uint8_t* sprite, int scale,
                      uint16_t color, uint8_t mask) {
  // Stable per-pixel mask: use a 4x4 ordered Bayer threshold so increasing
  // `mask` progressively reveals more pixels in a stable pattern.
  static const uint8_t BAYER[16] = {
    0, 8, 2,10, 12,4,14,6, 3,11,1,9, 15,7,13,5
  };
  for (int y = 0; y < 16; ++y) {
    for (int x = 0; x < 16; ++x) {
      uint8_t byte = pgm_read_byte(sprite + (y * 2) + (x / 8));
      bool on = byte & (0x80 >> (x % 8));
      if (!on) continue;
      uint8_t b = BAYER[(y & 3) * 4 + (x & 3)] * 16;  // 0..240
      if (b > mask) continue;
      M5.Display.fillRect(cx + x * scale, cy + y * scale, scale, scale, color);
    }
  }
}

void renderAutoBanner(int count) {
  M5.Display.fillRect(0, 0, 320, 28, 0xF800);   // red
  M5.Display.setTextColor(0xFFFF, 0xF800);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 6);
  M5.Display.printf("AUTO ON · %d", count);
}
```

- [ ] **Step 3: Remove the inlined versions from `main.cpp`**

In `firmware/src/main.cpp`, delete the two functions `static void renderStatus(...)` (lines ~46–82 in the current file) and `static void renderPrompt(...)` (lines ~88–114). Remove the `static` keyword from the existing forward declarations of those names and add the new include at the top:

```cpp
#include "render.h"
```

Drop any `static void renderStatus(...)` / `static void renderPrompt(...)` forward declarations that are no longer needed; the header provides them.

- [ ] **Step 4: Build**

`cd firmware && /opt/homebrew/bin/pio run -e m5stack-core`
Expected: clean compile.

- [ ] **Step 5: Flash and confirm no visible regression**

`cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX`

Run through the existing flows (status screen renders, permission prompt renders). Everything should look identical to before this task.

- [ ] **Step 6: Commit**

```bash
git add firmware/src/render.h firmware/src/render.cpp firmware/src/main.cpp
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "refactor(firmware): extract renderers into render.{h,cpp}"
```

---

## Task 5: Animation FSM — `animation.cpp`

**Files:**
- Create: `firmware/src/animation.h`, `firmware/src/animation.cpp`

The center-screen state machine. Driven by event hooks the BLE callbacks call; advanced by a single `animationTick()` called every loop iteration.

- [ ] **Step 1: Write `animation.h`**

```cpp
#pragma once
#include <stdint.h>

enum CenterState {
  CS_IDLE_CLAUDE,
  CS_ATTENTION,
  CS_CELEBRATE,
  CS_DIZZY,
  CS_MORPHING_TO_S,
  CS_IDLE_S,                 // only entered while autoApprove is true
  CS_MORPHING_FROM_S,
};

void animationInit();          // call once from setup() after M5.begin
void animationTick();          // call every loop()
void animationOnAutoChanged(bool newState);  // BLE / button → FSM
void animationOnPromptArrived();
void animationOnPromptResolved(bool allowed);
void animationOnReact(const char* kind);     // "celebrate" | "dizzy" | "attention"
void animationOnAutoFired();                 // brief flash in CS_IDLE_S
void animationOnConnected();
void animationOnDisconnected();

// Corners are part of the center anim layer for paint synchronization.
void animationRedrawCorners();

// Boot splash — blocks the loop for ~1.5s. Call from setup().
void animationPlayBootSplash();
```

- [ ] **Step 2: Write `animation.cpp`**

```cpp
#include "animation.h"
#include <M5Unified.h>
#include <string.h>
#include "colors.h"
#include "claude_sprites.h"
#include "cool_s.h"
#include "render.h"

// ----- private state -----

static CenterState st = CS_IDLE_CLAUDE;
static uint32_t    stateEnteredMs = 0;
static int         frameIdx = 0;
static bool        autoOn = false;
static int         autoCount = 0;

// Pose held during ATTENTION (set on prompt arrived; cleared on resolved).
static bool inPrompt = false;

// Position constants — center the sprite at (CX, CY), 64px square at scale=4.
static constexpr int CX = 160 - 32;
static constexpr int CY = 120 - 32;
static constexpr int SPRITE_SCALE = 4;

// Corner Cool S positions.
struct Corner { int cx, cy; };
static constexpr Corner CORNERS[4] = {{20, 50}, {300, 50}, {20, 220}, {300, 220}};

// Animation timing.
static constexpr int FRAME_MS_FAST    = 120;     // celebrate / dizzy / morph
static constexpr int FRAME_MS_IDLE    = 2000;    // blink interval
static constexpr int CELEBRATE_LEN    = 3;
static constexpr int DIZZY_LEN        = 3;
static constexpr int MORPH_LEN        = 6;
static constexpr uint32_t IDLE_MORPH_INTERVAL_MS = 30000;

// Last time we kicked an idle delight-morph (CS_IDLE_CLAUDE only).
static uint32_t lastIdleMorphMs = 0;

// ----- helpers -----

static void clearCenter() {
  M5.Display.fillRect(CX - 8, CY - 8, 80, 80, BG_BLACK);
}

static void drawCornersDim() {
  for (auto& c : CORNERS) drawCoolS(M5.Display, c.cx, c.cy, 32,
                                    ANTHROPIC_ORANGE_DIM, 1);
}
static void drawCornersBright() {
  for (auto& c : CORNERS) drawCoolS(M5.Display, c.cx, c.cy, 32,
                                    ANTHROPIC_ORANGE, 2);
}
static void clearCorners() {
  for (auto& c : CORNERS) M5.Display.fillRect(c.cx - 22, c.cy - 28, 44, 56,
                                              BG_BLACK);
}

void animationRedrawCorners() {
  clearCorners();
  if (autoOn) drawCornersBright(); else drawCornersDim();
}

static void enter(CenterState ns) {
  st = ns;
  stateEnteredMs = millis();
  frameIdx = 0;
}

// Render the sprite for the current state at frame `frameIdx`.
// `mask` controls morph dissolve (0..255; 255 = full sprite).
static void drawCenter(uint8_t mask = 255) {
  clearCenter();
  switch (st) {
    case CS_IDLE_CLAUDE:
      drawClaudeSprite(CX, CY,
        frameIdx == 0 ? CLAUDE_IDLE_0 : CLAUDE_IDLE_1,
        SPRITE_SCALE, ANTHROPIC_ORANGE, mask);
      break;
    case CS_ATTENTION:
      drawClaudeSprite(CX, CY, CLAUDE_ATTENTION,
                       SPRITE_SCALE, ANTHROPIC_ORANGE, mask);
      break;
    case CS_CELEBRATE: {
      const uint8_t* f = (frameIdx == 0) ? CLAUDE_CELEBRATE_0
                       : (frameIdx == 1) ? CLAUDE_CELEBRATE_1
                                         : CLAUDE_CELEBRATE_2;
      drawClaudeSprite(CX, CY, f, SPRITE_SCALE, ANTHROPIC_ORANGE, mask);
      break;
    }
    case CS_DIZZY: {
      const uint8_t* f = (frameIdx == 0) ? CLAUDE_DIZZY_0
                       : (frameIdx == 1) ? CLAUDE_DIZZY_1
                                         : CLAUDE_DIZZY_2;
      drawClaudeSprite(CX, CY, f, SPRITE_SCALE, ANTHROPIC_ORANGE, mask);
      break;
    }
    case CS_MORPHING_TO_S:
    case CS_MORPHING_FROM_S: {
      // Blend: Claude sprite mask 255→0 (or 0→255), Cool S partial 0→1.
      int n = (st == CS_MORPHING_TO_S) ? frameIdx : (MORPH_LEN - 1 - frameIdx);
      uint8_t m = (uint8_t)(255 - (n * 255) / (MORPH_LEN - 1));
      float prog = (float)n / (float)(MORPH_LEN - 1);
      drawClaudeSprite(CX, CY,
        n < 3 ? CLAUDE_IDLE_0 : CLAUDE_MORPH_3,
        SPRITE_SCALE, ANTHROPIC_ORANGE, m);
      drawCoolSPartial(M5.Display, CX + 32, CY + 32, 64,
                       ANTHROPIC_ORANGE, 2, prog);
      break;
    }
    case CS_IDLE_S:
      drawCoolS(M5.Display, CX + 32, CY + 32, 64, ANTHROPIC_ORANGE, 2);
      break;
  }
}

// ----- public API -----

void animationInit() {
  enter(CS_IDLE_CLAUDE);
  drawCornersDim();
  drawCenter();
  lastIdleMorphMs = millis();
}

void animationTick() {
  uint32_t now = millis();
  uint32_t age = now - stateEnteredMs;
  switch (st) {
    case CS_IDLE_CLAUDE: {
      // Blink: toggle frame every FRAME_MS_IDLE.
      int wantFrame = (age / FRAME_MS_IDLE) & 1;
      if (wantFrame != frameIdx) {
        frameIdx = wantFrame;
        drawCenter();
      }
      // Rare idle delight: morph through and back every ~30s.
      if (!autoOn && (now - lastIdleMorphMs) > IDLE_MORPH_INTERVAL_MS) {
        lastIdleMorphMs = now;
        enter(CS_MORPHING_TO_S);
        drawCenter();
      }
      break;
    }
    case CS_ATTENTION:
      // Static — no animation while a prompt is on screen.
      break;
    case CS_CELEBRATE:
    case CS_DIZZY: {
      int len = (st == CS_CELEBRATE) ? CELEBRATE_LEN : DIZZY_LEN;
      int wantFrame = age / FRAME_MS_FAST;
      if (wantFrame >= len) {
        // End of celebrate/dizzy → idle (or S if AUTO).
        if (autoOn) { enter(CS_IDLE_S); }
        else        { enter(CS_IDLE_CLAUDE); }
        drawCenter();
      } else if (wantFrame != frameIdx) {
        frameIdx = wantFrame;
        drawCenter();
      }
      break;
    }
    case CS_MORPHING_TO_S: {
      int wantFrame = age / FRAME_MS_FAST;
      if (wantFrame >= MORPH_LEN) {
        if (autoOn) {
          enter(CS_IDLE_S);                         // stay as S
        } else {
          enter(CS_MORPHING_FROM_S);                // delight beat
        }
        drawCenter();
      } else if (wantFrame != frameIdx) {
        frameIdx = wantFrame;
        drawCenter();
      }
      break;
    }
    case CS_MORPHING_FROM_S: {
      int wantFrame = age / FRAME_MS_FAST;
      if (wantFrame >= MORPH_LEN) {
        enter(CS_IDLE_CLAUDE);
        drawCenter();
      } else if (wantFrame != frameIdx) {
        frameIdx = wantFrame;
        drawCenter();
      }
      break;
    }
    case CS_IDLE_S:
      // Static. No idle motion while AUTO is on (S is the indicator).
      break;
  }
}

void animationOnAutoChanged(bool newState) {
  autoOn = newState;
  animationRedrawCorners();
  if (newState && st == CS_IDLE_CLAUDE) {
    enter(CS_MORPHING_TO_S);
    drawCenter();
  } else if (!newState && st == CS_IDLE_S) {
    enter(CS_MORPHING_FROM_S);
    drawCenter();
  }
}

void animationOnPromptArrived() {
  inPrompt = true;
  enter(CS_ATTENTION);
  drawCenter();
}

void animationOnPromptResolved(bool allowed) {
  inPrompt = false;
  enter(allowed ? CS_CELEBRATE : CS_DIZZY);
  drawCenter();
}

void animationOnReact(const char* kind) {
  if (inPrompt) return;     // don't disrupt the prompt screen
  if (!kind) return;
  if (!strcmp(kind, "celebrate")) { enter(CS_CELEBRATE); drawCenter(); }
  else if (!strcmp(kind, "dizzy")) { enter(CS_DIZZY); drawCenter(); }
  else if (!strcmp(kind, "attention")) { enter(CS_ATTENTION); drawCenter(); }
}

void animationOnAutoFired() {
  autoCount++;
  // Brief flash through CLAUDE_CELEBRATE_0 even while in CS_IDLE_S.
  if (st == CS_IDLE_S) {
    drawClaudeSprite(CX, CY, CLAUDE_CELEBRATE_0, SPRITE_SCALE,
                     ANTHROPIC_ORANGE, 255);
    delay(150);
    drawCenter();                  // back to Cool S
  }
}

void animationOnConnected() {
  animationOnReact("celebrate");
}

void animationOnDisconnected() {
  animationOnReact("dizzy");
}

void animationPlayBootSplash() {
  M5.Display.fillScreen(BG_BLACK);
  // Stroke the Cool S on over ~600ms.
  for (int i = 0; i <= 14; ++i) {
    M5.Display.fillRect(80, 40, 160, 160, BG_BLACK);
    drawCoolSPartial(M5.Display, 160, 120, 160, ANTHROPIC_ORANGE, 3,
                     (float)i / 14.0f);
    delay(45);
  }
  delay(200);
  // Dissolve into Claude.
  for (int m = 0; m <= 8; ++m) {
    M5.Display.fillRect(80, 40, 160, 160, BG_BLACK);
    drawCoolSPartial(M5.Display, 160, 120, 160, ANTHROPIC_ORANGE, 3,
                     1.0f - (m / 8.0f));
    drawClaudeSprite(160 - 32, 120 - 32, CLAUDE_IDLE_0,
                     SPRITE_SCALE, ANTHROPIC_ORANGE, (uint8_t)(m * 32));
    delay(60);
  }
  // Hello title above the character.
  M5.Display.setTextColor(0xFFFF, BG_BLACK);
  M5.Display.setTextSize(3);
  M5.Display.setCursor(76, 20);
  M5.Display.print("Hello");
  delay(700);
}
```

- [ ] **Step 3: Build (with `main.cpp` not yet wired — expect "unused" warnings only)**

`cd firmware && /opt/homebrew/bin/pio run -e m5stack-core`
Expected: clean compile (or only "defined but not used" warnings, which Task 6 resolves).

- [ ] **Step 4: Commit**

```bash
git add firmware/src/animation.h firmware/src/animation.cpp
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(firmware): center-screen animation FSM + boot splash"
```

---

## Task 6: Wire `animation.cpp` into `main.cpp`

**Files:**
- Modify: `firmware/src/main.cpp`

Replace the existing inline auto-fired toast + AUTO banner with calls into the animation module. The status bar (top of screen) and the prompt takeover are untouched.

- [ ] **Step 1: Add the include and the boot call**

In `firmware/src/main.cpp` near the other includes:

```cpp
#include "animation.h"
```

At the end of `setup()`, just before the closing brace, add:

```cpp
  animationInit();
  animationPlayBootSplash();
```

Remove the existing "Claude Buddy / starting..." text-only splash (the few `M5.Display.print` lines at the top of `setup()` that draw "Claude Buddy" and "starting...").

- [ ] **Step 2: Replace the `evt:auto_fired` handler with `animationOnAutoFired()`**

Find the block in `RxCallbacks::onWrite`:

```cpp
    } else if (doc["evt"] == "auto_fired") {
      autoCount++;
      autoFlashUntil = millis() + 1500;
      // ... fillRect green toast + AUTO banner refresh ...
    }
```

Replace it with:

```cpp
    } else if (doc["evt"] == "auto_fired") {
      animationOnAutoFired();
    } else if (doc["evt"] == "react") {
      animationOnReact(doc["kind"] | "");
    }
```

- [ ] **Step 3: Replace the prompt-arrival path**

Find:

```cpp
      if (autoApprove) { sendDecision("allow"); }
      else             { renderPrompt(promptTool, promptDetail, promptChange); }
```

Replace with:

```cpp
      if (autoApprove) {
        animationOnAutoFired();
        sendDecision("allow");
      } else {
        animationOnPromptArrived();
        renderPrompt(promptTool, promptDetail, promptChange);
      }
```

- [ ] **Step 4: Replace `sendDecision` to notify the FSM**

In `sendDecision()`, after the `sendNotify(buf)` line and before clearing `promptId`, add:

```cpp
  animationOnPromptResolved(strcmp(decision, "allow") == 0);
```

- [ ] **Step 5: Replace `toggleAuto`'s banner with `animationOnAutoChanged`**

Replace the body of `toggleAuto` after `sendNotify(buf);` with:

```cpp
  animationOnAutoChanged(autoApprove);
  if (autoApprove) M5.Speaker.tone(880, 120);   // existing toggle beep
```

Delete the inline `fillRect`/`setTextColor`/`printf("AUTO ON …")` block and any `autoCount` / `autoFlashUntil` logic in `loop()`. The animation module owns the AUTO banner now: extend `animationOnAutoChanged(true)` to also call `renderAutoBanner(0)`, and extend `animationOnAutoFired()` to call `renderAutoBanner(autoCount)` after the brief flash. Both edits are in `animation.cpp`; add them when wiring `toggleAuto`.

- [ ] **Step 6: Add `animationTick()` to `loop()`**

Right after `M5.update();` at the top of `loop()`:

```cpp
  animationTick();
```

Delete the `if (autoFlashUntil != 0 && millis() > autoFlashUntil) { … }` block — its job is now `animationTick`.

- [ ] **Step 7: Wire connect/disconnect**

In `ServerCallbacks::onConnect`, add:

```cpp
    animationOnConnected();
```

In `ServerCallbacks::onDisconnect`, add:

```cpp
    animationOnDisconnected();
```

- [ ] **Step 8: Build and flash**

```bash
cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX
```

Expected: clean compile. Boot splash plays (Cool S strokes on, dissolves into Claude). Status screen has Claude sprite in the middle and dim Cool S in corners.

- [ ] **Step 9: Commit**

```bash
git add firmware/src/main.cpp
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(firmware): wire animation FSM into BLE/button events"
```

---

## Task 7: Bridge `react` event + hook integration

**Files:**
- Modify: `bridge/buddy_bridge/protocol.py`
- Modify: `bridge/buddy_bridge/__main__.py`
- Modify: `bridge/buddy_bridge/socket_server.py`
- Modify: `bridge/tests/test_protocol.py`
- Modify: `hooks/buddy-hook.py`

Adds the `react` event so hook scripts can poke specific Claude reactions (e.g. `Stop` → `celebrate`).

- [ ] **Step 1: Add a failing test**

Append to `bridge/tests/test_protocol.py`:

```python
def test_encode_react():
    from buddy_bridge.protocol import encode_react
    out = encode_react("celebrate")
    assert out == b'{"evt":"react","kind":"celebrate"}\n'
    assert encode_react("dizzy") == b'{"evt":"react","kind":"dizzy"}\n'
```

- [ ] **Step 2: Run and confirm failure**

`cd bridge && .venv/bin/python -m pytest tests/test_protocol.py::test_encode_react -v`
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement `encode_react`**

Append to `bridge/buddy_bridge/protocol.py`:

```python
def encode_react(kind: str) -> bytes:
    """Encode a react event for the device — triggers a Claude reaction.

    `kind` is one of "celebrate", "dizzy", "attention". Other values are
    encoded as-is; the firmware ignores anything it doesn't recognize.
    """
    obj = {"evt": "react", "kind": kind}
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
```

- [ ] **Step 4: Add a send callback and socket-server route**

In `bridge/buddy_bridge/__main__.py`, extend the protocol import line to include `encode_react`. Then after `send_auto_fired`, add:

```python
    def send_react(kind: str) -> None:
        spawn(link.send(encode_react(kind)))
```

In `bridge/buddy_bridge/socket_server.py`, accept a new socket event type. At the top of `handle()`'s dispatch, add a branch:

```python
                elif etype == "react":
                    kind = event.get("kind", "")
                    if kind in ("celebrate", "dizzy", "attention"):
                        # Route via a stashed callback on the server object;
                        # simpler: pass send_react in via serve(...).
                        pass
```

Then change the `serve()` signature to accept a `send_react` callback and store it (mirroring how `broker` is passed today). Pass it from `__main__.py`. The dispatch becomes:

```python
                elif etype == "react" and send_react is not None:
                    kind = event.get("kind", "")
                    if kind in ("celebrate", "dizzy", "attention"):
                        send_react(kind)
```

Update the call site in `__main__.py`:

```python
    server = await serve(SOCK_PATH, reg, on_change=push, broker=broker,
                         send_react=send_react)
```

And add the parameter to `serve()`:

```python
async def serve(sock_path: str, reg: SessionRegistry,
                on_change: Callable[[], None],
                broker=None, send_react=None) -> asyncio.AbstractServer:
```

- [ ] **Step 5: Run tests**

`cd bridge && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Hook — emit `react` on `Stop`**

`hooks/buddy-hook.py` already handles `SessionStart`/`SessionEnd`/`Stop`/`Notification`. Find the `Stop` branch and just before it sends its existing event, add a `react` send:

```python
    if event == "Stop":
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(SOCK_PATH)
                s.sendall(b'{"type":"react","kind":"celebrate"}\n')
        except OSError:
            pass
```

(If `Stop` already builds a single payload to send, just add `react` as a second `sendall` on the same connection rather than opening a new one.)

- [ ] **Step 7: Commit**

```bash
git add bridge/buddy_bridge/protocol.py bridge/buddy_bridge/__main__.py \
        bridge/buddy_bridge/socket_server.py bridge/tests/test_protocol.py \
        hooks/buddy-hook.py
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(bridge,hooks): react event + Stop->celebrate wiring"
```

---

## Task 8: End-to-end verification on hardware

**Files:** none — verification only.

- [ ] **Step 1: Confirm bridge and firmware are current**

```bash
cd bridge && .venv/bin/python -m buddy_bridge      # in one terminal
```
```bash
cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload \
  --upload-port /dev/cu.usbserial-XXXX             # in another
```

- [ ] **Step 2: Boot splash**

Power-cycle the device. Watch for:
- Cool S strokes itself on (13 segments, ~600ms).
- Dissolves into the Claude pixel character.
- "Hello" text briefly above it.

- [ ] **Step 3: Idle**

Status screen shows: counts at top, Claude character in the middle (slow blink every ~2s), dim Cool S in all four corners.

- [ ] **Step 4: Rare idle morph**

Leave the device for ~30s with AUTO off. Watch for a single brief morph through to Cool S and back (~1s total). Should be subtle, not distracting.

- [ ] **Step 5: Permission prompt arrives**

Trigger a permission-gated tool call from Claude Code. Device should:
- Show the existing prompt screen (`renderPrompt`).
- Claude character pose in the middle area updates to `attention` (forward-lean, wide eyes).
- Corners brighten to full-intensity Cool S.

Press **A** → tool is allowed; Claude returns to status with a brief `celebrate` (3 frames). Press **C** on a second prompt → `dizzy`.

- [ ] **Step 6: Auto-approve ON morph**

Press **B**. Watch for:
- Red AUTO ON banner at top.
- Center morphs Claude → Cool S over ~700ms (6 frames at 120ms).
- Corners fully lit.

- [ ] **Step 7: Auto-fire flash**

Trigger a gated tool call while AUTO is on. Watch for:
- Brief flash through Claude `celebrate` (~150ms) and back to Cool S in the center.
- Counter increments in the top banner.
- Existing tone beep.

- [ ] **Step 8: Auto-approve OFF reverse morph**

Press **B** again. Watch for:
- AUTO banner clears.
- Center morphs Cool S → Claude over ~700ms.
- Corners dim.

- [ ] **Step 9: Connect / disconnect reactions**

Stop the bridge (Ctrl-C). Device should `dizzy` briefly (corners dim further is fine).
Restart the bridge. Device should `celebrate` briefly when it reconnects.

- [ ] **Step 10: `react` from Stop hook**

In a Claude Code session, send a message and wait for Claude's turn to finish. The `Stop` hook should fire `react`/`celebrate`; the device flashes a celebrate pose briefly (~360ms).

- [ ] **Step 11: Full repaint timing**

Watch for tearing or hangs during morph. If the morph stutters past ~100ms per frame, revisit `FRAME_MS_FAST` and consider drawing into a sprite buffer to reduce flicker (out of scope of this plan unless it's bad).

- [ ] **Step 12: Final commit and push**

```bash
git add -A
git status                # confirm only intended changes
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "test: animation iteration end-to-end verification complete" 2>/dev/null || true
git push
```

---

## Notes for the implementer

- **Pixel art is reviewed before code.** Task 3 stops mid-design for user review. Do not encode byte arrays from the first-draft grids in this plan without confirming with the user — the grids are explicit "first draft, red-line expected."
- **No new sounds.** The existing tones (auto-toggle confirm, decision beep) stay. Don't add chirps to celebrate/dizzy/morph.
- **The animation module owns the center 64×64-ish region + corners + AUTO banner.** Anything else (status counts, prompt screen, ask screen if it landed first) is owned by `render.cpp` / `main.cpp`. Don't paint outside the boundary in `animation.cpp`.
- **Idle morph only fires from `CS_IDLE_CLAUDE`.** That's load-bearing — the spec says the morph means "AUTO is on," so we never spontaneously trigger it while AUTO is off and then *stay* in S. The implementation enforces this by routing `CS_MORPHING_TO_S` → `CS_MORPHING_FROM_S` (not `CS_IDLE_S`) when `autoOn` is false.
- **`drawClaudeSprite`'s `mask` parameter uses Bayer dithering**, not `random()`. This is so the dissolve looks stable across frames (the same pixel either revealed or hidden in adjacent frames), not noisy.
- **Frame rate goal:** ~8–10 fps during morph, ~0.5 fps in idle (just blink). If a real measurement shows we can't make 8 fps for the morph, shorten the morph to 4 frames instead of 6 rather than adding a sprite buffer.
- **`hooks/buddy-hook.py`** (the Phase 1 event hook) is the right place for `react` emission, not `buddy-permission-hook.py`. Permission/ask hooks already produce the right reaction via the BLE event path (`animationOnPromptArrived` etc).
