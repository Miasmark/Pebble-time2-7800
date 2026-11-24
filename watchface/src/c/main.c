// 7800 40th Anniversary watch face for Pebble Time 2 (emery, 200x228, 64 colours)
//
// With USE_EMULATOR set (the default), the TV's tube shows a real 7800
// cartridge -- the STUB_ROM resource -- running on the watch's own 6502 and
// MARIA (src/c/emu, silent). The watch passes the time to it through clock
// registers at $0600 (docs/clock-registers.md at the top of this repo) and runs
// EMU_FRAMES_PER_DRAW 7800 frames per watch frame during an animation burst,
// one draw a minute otherwise. Each 30 draws it logs how long a draw took.
//
// With USE_EMULATOR at 0, the hand-built hourly scenes below are drawn instead.
//
// The game screen is a 160x192 display rendered the way MARIA builds a frame:
//
//  * Graphics are 160A objects: 2 bits per pixel, 4 pixels per byte. Pixel
//    value 0 is transparent; 1-3 pick colour 1-3 of the object's palette.
//  * 8 palettes x 3 colours + BACKGRND = 25 colour registers. Register values
//    are real 7800 colour bytes (high nibble hue, low nibble luma) and are
//    converted to the nearest Pebble colour only at output time.
//  * The screen is split into zones (1-16 lines) by a display list list. Each
//    zone has its own display list of objects, which are drawn into a line
//    RAM, in list order, for every scanline in the zone.
//  * A zone can raise a display list interrupt (DLI) that rewrites colour
//    registers before the zone starts. The time digits use 5-line zones with a
//    DLI each to get a yellow-to-red gradient out of one palette; the cloud
//    scene uses DLIs on BACKGRND for a sky gradient.
//
// Behind the clock is a playfield that changes every hour, cycling through
// four original arcade-style scenes (palettes P0 and P6 belong to the scene):
//
//   0  DEEP SPACE    falling three-layer starfield
//   1  SWARM         a wiggling formation, a diver and your shots
//   2  CRYSTAL CAVE  stalactites, dripping water, crystals that glint
//                    (palette-cycling a register, as 8-bit games did)
//   3  CLOUD RUN     parallax clouds over a DLI sky gradient
//
// Around it is an old TV set in 7800 dress: black cabinet, brushed-metal
// control strip with the thin rainbow stripe and four console buttons.
//
// The playfield animates for a few seconds when the face opens or you flick
// your wrist, and steps once a minute otherwise. Set ANIMATE_ALWAYS to 1 for
// non-stop motion (costs battery).

#include <pebble.h>

#include "emu/a7800.h"
#include "emu/palette7800.h"

// 1: the tube shows a 7800 cartridge (the STUB_ROM resource) running on the
//    watch's 6502 + MARIA core (src/c/emu); 0: the hand-built hourly scenes
#define USE_EMULATOR 1
#define EMU_FIRST_LINE 42      // the 192 lines shown: 42-233, the Guide's safe area
#define EMU_FRAMES_PER_DRAW 4  // 7800 frames per watch frame: 4 x 15 fps = real time

#define ANIMATE_ALWAYS 0
#define FRAME_MS 66            // ~15 fps
#define BURST_FRAMES 90        // ~6 seconds per burst
#define PREMINUTE_SECOND 57    // the cartridge's minute change starts at :57 ...
#define PREMINUTE_FRAMES 98    // ... and is over by :03 (6.5 s)
#define NUM_SCENES 4

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

#define MW 160                 // MARIA 160 modes: 160 pixels per line
#define MH 192                 // 192 visible lines
#define SCREEN_X 20            // where the tube sits on the 200x228 watch
#define SCREEN_Y 8
#define TUBE_RADIUS 10         // CRT corner rounding

#define TIME_Y 58
#define SHIP_Y 144
#define BIG_CELL 5
#define BIG_BLOCK_H 4

// ---------------------------------------------------------------------------
// Colour registers
// ---------------------------------------------------------------------------

#define BACKGRND 0
#define PREG(p, c) (1 + (p) * 3 + ((c) - 1))
#define NUM_REGS 25

static uint8_t s_regs_frame[NUM_REGS];   // values loaded during "VBLANK"
static uint8_t s_regs[NUM_REGS];         // live values while drawing (DLIs change these)
static uint8_t s_lut[256];               // 7800 colour byte -> Pebble GColor8

// Palette plan (each object uses exactly one palette, three colours max)
//   P0  scene         set by the current scene
//   P1  text          chrome yellow, red, light blue
//   P2  warm          red, orange, yellow      (stripes, flames, shots, title shadow)
//   P3  cool          green, blue, violet      (stripes)
//   P4  time          fire (rewritten by DLIs), purple shadow
//   P5  HUD           lives tint, red
//   P6  scene         set by the current scene
//   P7  hull          white, grey, light blue  (ship, title)
static const uint8_t PALETTE_INIT[NUM_REGS] = {
  0x00,                 // BACKGRND
  0x00, 0x00, 0x00,     // P0 (scene)
  0x1A, 0x36, 0x9E,     // P1
  0x36, 0x28, 0x1E,     // P2
  0xC8, 0x94, 0x66,     // P3
  0x1E, 0x50, 0x00,     // P4
  0x0E, 0x36, 0x00,     // P5
  0x04, 0x96, 0x0E,     // P6 (scene)
  0x0E, 0x08, 0x9E,     // P7
};

// MARIA colour bytes to Pebble colours: emu/palette7800.c (the same model the
// emulator's output goes through)
static void build_colour_lut(void) {
  palette7800_build(s_lut);
}

// ---------------------------------------------------------------------------
// Zones (display list list) and DLIs
// ---------------------------------------------------------------------------

typedef struct {
  uint8_t height;      // 1..16 lines
  int8_t dli_reg;      // register a DLI writes before this zone, -1 = none
  uint8_t dli_val;
} Zone;

static const Zone ZONES[] = {
  {16, -1, 0}, {16, -1, 0}, {16, -1, 0},      //   0- 47 title, subtitle, stripes
  {10, -1, 0},                                 //  48- 57
  {5, PREG(4, 1), 0x1E},                       //  58- 92 one 5-line zone per digit row,
  {5, PREG(4, 1), 0x1E},                       //         each DLI reloads the fire colour
  {5, PREG(4, 1), 0x1A},
  {5, PREG(4, 1), 0x1A},
  {5, PREG(4, 1), 0x28},
  {5, PREG(4, 1), 0x26},
  {5, PREG(4, 1), 0x36},
  {11, -1, 0},                                 //  93-103 shadow tail
  {16, -1, 0}, {16, -1, 0}, {16, -1, 0},       // 104-151 date, status, playfield
  {16, -1, 0}, {16, -1, 0},                    // 152-183 ship, HUD
  {8, -1, 0},                                  // 184-191
};
#define NUM_ZONES ((int)ARRAY_LENGTH(ZONES))

// ---------------------------------------------------------------------------
// Objects and graphics RAM
// ---------------------------------------------------------------------------

typedef struct {
  const uint8_t *g;    // 160A data, rows top to bottom, wbytes per row
  uint8_t wbytes;      // width in bytes (4 pixels each)
  uint8_t h;
} Gfx;

typedef struct {
  Gfx gfx;
  uint8_t pal;
  int16_t x, y;
} MObj;

// Background objects are rebuilt every animation frame; foreground objects
// (clock, text, HUD, ship) once a minute. Background goes first in every
// display list, so it is drawn underneath.
#define MAX_BG 64
#define MAX_FG 48
#define MAX_PER_ZONE 48   // a real MARIA runs out of DMA time well before this
static MObj s_bg[MAX_BG];
static int s_nbg;
static MObj s_fg[MAX_FG];
static int s_nfg;
static const MObj *s_dl[NUM_ZONES][MAX_PER_ZONE];
static uint8_t s_dl_len[NUM_ZONES];

static uint8_t s_gram[10240];   // "cart RAM" for generated graphics
static int s_gram_used;
static int s_gram_static_base;

static uint8_t *gram_alloc(int bytes) {
  if (s_gram_used + bytes > (int)sizeof(s_gram)) {
    return NULL;
  }
  uint8_t *p = &s_gram[s_gram_used];
  memset(p, 0, bytes);
  s_gram_used += bytes;
  return p;
}

static void gfx_set(uint8_t *g, int wbytes, int x, int y, int c) {
  uint8_t *b = &g[y * wbytes + (x >> 2)];
  int shift = 6 - 2 * (x & 3);
  *b = (*b & ~(3 << shift)) | (c << shift);
}

static void push(MObj *arr, int *n, int max, Gfx gfx, int pal, int x, int y) {
  if (!gfx.g || *n >= max) {
    return;
  }
  arr[(*n)++] = (MObj){gfx, (uint8_t)pal, (int16_t)x, (int16_t)y};
}

#define FG(gfx, pal, x, y) push(s_fg, &s_nfg, MAX_FG, (gfx), (pal), (x), (y))
#define BG(gfx, pal, x, y) push(s_bg, &s_nbg, MAX_BG, (gfx), (pal), (x), (y))

// ---------------------------------------------------------------------------
// Pixel fonts (5x7, bit 4 = leftmost column)
// ---------------------------------------------------------------------------

typedef struct {
  char c;
  uint8_t rows[7];
} Glyph;

static const Glyph FONT[] = {
  {' ', {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}},
  {'-', {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00}},
  {'0', {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}},
  {'1', {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E}},
  {'2', {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}},
  {'3', {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E}},
  {'4', {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}},
  {'5', {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E}},
  {'6', {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}},
  {'7', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08}},
  {'8', {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}},
  {'9', {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C}},
  {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
  {'B', {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E}},
  {'C', {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E}},
  {'D', {0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E}},
  {'E', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}},
  {'F', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10}},
  {'G', {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F}},
  {'H', {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
  {'I', {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E}},
  {'J', {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C}},
  {'K', {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11}},
  {'L', {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F}},
  {'M', {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}},
  {'N', {0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11}},
  {'O', {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
  {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}},
  {'Q', {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D}},
  {'R', {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11}},
  {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}},
  {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
  {'U', {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
  {'V', {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04}},
  {'W', {0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A}},
  {'X', {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11}},
  {'Y', {0x11, 0x11, 0x11, 0x0A, 0x04, 0x04, 0x04}},
  {'Z', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F}},
};

static const uint8_t BIG_DIGITS[10][7] = {
  {0x1F, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1F},
  {0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F},
  {0x1F, 0x01, 0x01, 0x1F, 0x10, 0x10, 0x1F},
  {0x1F, 0x01, 0x01, 0x0F, 0x01, 0x01, 0x1F},
  {0x11, 0x11, 0x11, 0x1F, 0x01, 0x01, 0x01},
  {0x1F, 0x10, 0x10, 0x1F, 0x01, 0x01, 0x1F},
  {0x1F, 0x10, 0x10, 0x1F, 0x11, 0x11, 0x1F},
  {0x1F, 0x01, 0x01, 0x02, 0x04, 0x04, 0x04},
  {0x1F, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x1F},
  {0x1F, 0x11, 0x11, 0x1F, 0x01, 0x01, 0x1F},
};
static const uint8_t BIG_COLON[7] = {0x00, 0x10, 0x10, 0x00, 0x10, 0x10, 0x00};

static const uint8_t *glyph(char c) {
  if (c >= 'a' && c <= 'z') {
    c = c - 'a' + 'A';
  }
  for (size_t i = 0; i < ARRAY_LENGTH(FONT); i++) {
    if (FONT[i].c == c) {
      return FONT[i].rows;
    }
  }
  return FONT[0].rows;
}

static int text_width(const char *s, int scale) {
  int n = (int)strlen(s);
  return n ? n * 6 * scale - scale : 0;
}

// Renders a string into graphics RAM as one 160A object's data
static Gfx make_text(const char *s, int scale, int colour) {
  int w = text_width(s, scale);
  Gfx out = {NULL, (uint8_t)((w + 3) / 4), (uint8_t)(7 * scale)};
  uint8_t *g = gram_alloc(out.wbytes * out.h);
  if (!g) {
    return out;
  }
  for (int i = 0; s[i]; i++) {
    const uint8_t *rows = glyph(s[i]);
    for (int r = 0; r < 7; r++) {
      for (int c = 0; c < 5; c++) {
        if (rows[r] & (0x10 >> c)) {
          for (int dy = 0; dy < scale; dy++) {
            for (int dx = 0; dx < scale; dx++) {
              gfx_set(g, out.wbytes, i * 6 * scale + c * scale + dx, r * scale + dy, colour);
            }
          }
        }
      }
    }
  }
  out.g = g;
  return out;
}

static void fg_text(const char *s, int scale, int colour, int pal, int x, int y) {
  FG(make_text(s, scale, colour), pal, x, y);
}

static void fg_text_centered(const char *s, int scale, int colour, int pal, int y) {
  fg_text(s, scale, colour, pal, (MW - text_width(s, scale)) / 2, y);
}

static int big_width(const char *s) {
  int w = 0;
  for (const char *p = s; *p; p++) {
    w += (*p == ':' ? BIG_CELL : 5 * BIG_CELL) + (p[1] ? BIG_CELL : 0);
  }
  return w;
}

static Gfx make_big_time(const char *s, int colour) {
  int w = big_width(s);
  Gfx out = {NULL, (uint8_t)((w + 3) / 4), (uint8_t)(7 * BIG_CELL)};
  uint8_t *g = gram_alloc(out.wbytes * out.h);
  if (!g) {
    return out;
  }
  int cx = 0;
  for (const char *p = s; *p; p++) {
    const uint8_t *rows = (*p == ':') ? BIG_COLON : BIG_DIGITS[(*p - '0') % 10];
    for (int r = 0; r < 7; r++) {
      for (int c = 0; c < 5; c++) {
        if (rows[r] & (0x10 >> c)) {
          for (int dy = 0; dy < BIG_BLOCK_H; dy++) {     // last line of each cell left
            for (int dx = 0; dx < BIG_CELL; dx++) {      // empty = scanline gap
              gfx_set(g, out.wbytes, cx + c * BIG_CELL + dx, r * BIG_CELL + dy, colour);
            }
          }
        }
      }
    }
    cx += (*p == ':' ? BIG_CELL : 5 * BIG_CELL) + BIG_CELL;
  }
  out.g = g;
  return out;
}

// ---------------------------------------------------------------------------
// Sprites (all original pixel art). chars3 lists the characters that map to
// colours 1, 2 and 3; anything else is transparent. Objects that need more
// than three colours are split across palettes.
// ---------------------------------------------------------------------------

static Gfx make_sprite(const char **rows, int nrows, int scale, const char *chars3) {
  int w = (int)strlen(rows[0]) * scale;
  Gfx out = {NULL, (uint8_t)((w + 3) / 4), (uint8_t)(nrows * scale)};
  uint8_t *g = gram_alloc(out.wbytes * out.h);
  if (!g) {
    return out;
  }
  for (int r = 0; r < nrows; r++) {
    for (int c = 0; rows[r][c]; c++) {
      const char *hit = (rows[r][c] == '.') ? NULL : strchr(chars3, rows[r][c]);
      if (!hit) {
        continue;
      }
      for (int dy = 0; dy < scale; dy++) {
        for (int dx = 0; dx < scale; dx++) {
          gfx_set(g, out.wbytes, c * scale + dx, r * scale + dy, (int)(hit - chars3) + 1);
        }
      }
    }
  }
  out.g = g;
  return out;
}

static const char *SHIP[] = {
  "....W....",
  "...WCW...",
  "...WCW...",
  "..LWWWL..",
  ".RLWWWLR.",
  "WWLWRWLWW",
  "WW.LLL.WW",
  "R...O...R",
  "....Y....",
};

static const char *MINI_SHIP[] = {
  "...W...",
  "..WWW..",
  ".WWWWW.",
  "WW.W.WW",
  "W.....W",
};

// SWARM: a round-bodied drifter with a crest and trailing feelers, 2 frames
static const char *DRIFTER_A[] = {
  "..YYY..",
  ".MMMMM.",
  "MMYMYMM",
  "MMMMMMM",
  "B.B.B.B",
  "B.....B",
};
static const char *DRIFTER_B[] = {
  "..YYY..",
  ".MMMMM.",
  "MMYMYMM",
  "MMMMMMM",
  ".B.B.B.",
  ".B...B.",
};

// CLOUD RUN
static const char *CLOUD_SMALL[] = {
  "..LLL....",
  ".LLLLLL..",
  "LLLLLLLLL",
  ".DDDDDDD.",
};
static const char *CLOUD_BIG[] = {
  "....WWW..........",
  "..WWLLLW..LLL....",
  ".WLLLLLLLLLLLLL..",
  "LLLLLLLLLLLLLLLLL",
  "LLLLLLLLLLLLLLLLL",
  ".DDDDDDDDDDDDDDD.",
};

// CRYSTAL CAVE: violet / teal crystal with a glint pixel
static const char *CRYSTAL[] = {
  "...T...",
  "..TTT.V",
  "V.TGT.V",
  "VVTTTVV",
  "VVTTTVV",
  ".VTTTV.",
  "..VVV..",
};

static const char *STAR_FAR[] = {"A"};
static const char *STAR_MID[] = {"B"};
static const char *STAR_NEAR[] = {"A", "B", "C"};   // streak, bright head at bottom
static const char *SHOT[] = {"C", "C", "C", "C"};
static const char *DROP[] = {"B", "B"};

static Gfx s_star_far, s_star_mid, s_star_near, s_shot, s_drop;
static Gfx s_drifter[2], s_cloud_small, s_cloud_big, s_crystal;
static Gfx s_ceiling, s_floor;

static uint32_t hash32(uint32_t x) {
  x ^= x >> 16;
  x *= 0x7feb352du;
  x ^= x >> 15;
  x *= 0x846ca68bu;
  x ^= x >> 16;
  return x;
}

// Cave ceiling and floor: full-width procedural rock, built once
static void make_cave_rock(void) {
  int wbytes = MW / 4;
  uint8_t *ceil = gram_alloc(wbytes * 14);
  uint8_t *floor = gram_alloc(wbytes * 10);
  if (!ceil || !floor) {
    return;
  }
  for (int seg = 0; seg < MW / 8; seg++) {
    int tip = 3 + (int)(hash32(seg * 31 + 5) % 11);       // stalactite length
    int mound = 2 + (int)(hash32(seg * 17 + 9) % 6);      // floor height
    for (int c = 0; c < 8; c++) {
      int x = seg * 8 + c;
      int taper = 4 - (c < 4 ? 3 - c : c - 4);            // 1..4
      int len = 2 + (tip * taper) / 4;
      for (int y = 0; y < len && y < 14; y++) {
        gfx_set(ceil, wbytes, x, y, (y == len - 1 || c == 0) ? 2 : 1);
      }
      int h = 2 + (mound * taper) / 4;
      for (int y = 0; y < h && y < 10; y++) {
        gfx_set(floor, wbytes, x, 9 - y, (y == h - 1) ? 2 : 1);
      }
    }
  }
  s_ceiling = (Gfx){ceil, (uint8_t)wbytes, 14};
  s_floor = (Gfx){floor, (uint8_t)wbytes, 10};
}

static void build_static_gfx(void) {
  s_star_far = make_sprite(STAR_FAR, 1, 1, "ABC");
  s_star_mid = make_sprite(STAR_MID, 1, 1, "ABC");
  s_star_near = make_sprite(STAR_NEAR, 3, 1, "ABC");
  s_shot = make_sprite(SHOT, 4, 1, "ABC");
  s_drop = make_sprite(DROP, 2, 1, "ABC");
  s_drifter[0] = make_sprite(DRIFTER_A, ARRAY_LENGTH(DRIFTER_A), 1, "MBY");
  s_drifter[1] = make_sprite(DRIFTER_B, ARRAY_LENGTH(DRIFTER_B), 1, "MBY");
  s_cloud_small = make_sprite(CLOUD_SMALL, ARRAY_LENGTH(CLOUD_SMALL), 2, "LWD");
  s_cloud_big = make_sprite(CLOUD_BIG, ARRAY_LENGTH(CLOUD_BIG), 2, "LWD");
  s_crystal = make_sprite(CRYSTAL, ARRAY_LENGTH(CRYSTAL), 1, "VTG");
  make_cave_rock();
}

// ---------------------------------------------------------------------------
// Scenes
// ---------------------------------------------------------------------------

static int s_scene;
static uint32_t s_frame;
static int s_ship_x;

static const int8_t SINE16[16] = {0, 38, 71, 92, 100, 92, 71, 38, 0, -38, -71, -92, -100, -92, -71, -38};

static int tri_wave(uint32_t t, int period, int amp) {   // -amp..amp
  int p = (int)(t % (uint32_t)period);
  int half = period / 2;
  return (p < half ? p : period - p) * 2 * amp / half - amp;
}

// Stateless falling stars: each star's position is a function of the frame
static void bg_stars(int count, int speed_pct) {
  static const uint8_t speeds[] = {5, 12, 34};   // 1/16 px per frame
  int travel = MH + 8;
  for (int i = 0; i < count; i++) {
    int kind = (i % 6 == 0) ? 2 : (i % 3 == 0 ? 1 : 0);
    uint32_t speed = (uint32_t)(speeds[kind] + (hash32(i) & 3)) * speed_pct / 100;
    uint32_t pos = (hash32(i * 7 + 1) % travel) * 16 + s_frame * speed;
    uint32_t cycle = pos / (travel * 16);
    int y = (int)((pos % (travel * 16)) / 16) - 4;
    int x = (int)(hash32(i * 977 + cycle * 131) % (MW - 1));
    Gfx g = kind == 2 ? s_star_near : (kind == 1 ? s_star_mid : s_star_far);
    BG(g, 6, x, y);
  }
}

static void bg_swarm(void) {
  bg_stars(18, 50);
  int sway = tri_wave(s_frame, 64, 10);
  Gfx g = s_drifter[(s_frame / 4) & 1];
  int diver = (int)((s_frame / 72) % 21);
  for (int r = 0; r < 3; r++) {
    for (int c = 0; c < 7; c++) {
      if (r * 7 + c != diver) {
        BG(g, 0, 37 + c * 12 + sway, 120 + r * 8);
      }
    }
  }
  // the diver swoops down in a weave, then rejoins at the top
  int t = (int)(s_frame % 72);
  int home_x = 37 + (diver % 7) * 12 + sway;
  BG(g, 0, home_x + SINE16[(t / 3) & 15] * 24 / 100, 120 + (diver / 7) * 8 + t);
  // player shots rising from the ship
  int shot_y = SHIP_Y - 4 - (int)((s_frame * 6) % 28);
  BG(s_shot, 2, s_ship_x + 8, shot_y);
}

static void bg_cave(void) {
  BG(s_ceiling, 0, 0, 0);
  BG(s_floor, 0, 0, MH - 10);
  BG(s_crystal, 6, 4, 96);
  BG(s_crystal, 6, MW - 11, 112);
  BG(s_crystal, 6, 66, MH - 14);
  // drips falling from a few stalactite tips
  static const uint8_t drip_x[] = {13, 45, 93, 125, 149};
  for (int i = 0; i < 5; i++) {
    int y = 12 + (int)((s_frame * (3 + i % 2) + i * 41) % (MH - 22));
    BG(s_drop, 6, drip_x[i], y);
  }
}

static void bg_clouds(void) {
  for (int i = 0; i < 8; i++) {
    bool big = (i % 3 == 0);
    int speed = big ? 6 : 3;                    // near clouds pass faster
    int travel = MH + 32;
    uint32_t pos = (hash32(i * 13 + 3) % travel) * 4 + s_frame * speed;
    uint32_t cycle = pos / (travel * 4);
    int y = (int)((pos % (travel * 4)) / 4) - 16;
    int x = (int)(hash32(i * 59 + cycle * 7) % (MW - 16)) - 8;
    BG(big ? s_cloud_big : s_cloud_small, 0, x, y);
  }
}

// Scene palettes are register writes during VBLANK
static void scene_registers(void) {
  uint8_t *r = s_regs_frame;
  switch (s_scene) {
    case 0:   // deep space
      r[PREG(6, 1)] = 0x04; r[PREG(6, 2)] = 0x96; r[PREG(6, 3)] = 0x0E;
      break;
    case 1:   // swarm: magenta bodies, blue feelers, yellow crest
      r[PREG(0, 1)] = 0x56; r[PREG(0, 2)] = 0x86; r[PREG(0, 3)] = 0x1E;
      r[PREG(6, 1)] = 0x04; r[PREG(6, 2)] = 0x06; r[PREG(6, 3)] = 0x0A;
      break;
    case 2:   // cave: brown rock, grey edges; violet/teal crystals, glint cycles
      r[PREG(0, 1)] = 0x24; r[PREG(0, 2)] = 0x08; r[PREG(0, 3)] = 0x00;
      r[PREG(6, 1)] = 0x66; r[PREG(6, 2)] = 0xA8;
      r[PREG(6, 3)] = ((s_frame / 5) & 1) ? 0x0E : 0xA8;
      break;
    default:  // clouds: grey, white highlight, dark underside
      r[PREG(0, 1)] = 0x08; r[PREG(0, 2)] = 0x0E; r[PREG(0, 3)] = 0x06;
      break;
  }
}

// Extra DLI work per zone (on top of the fixed ZONES table)
static void scene_dli(int zone) {
  if (s_scene == 3) {         // sky gradient on BACKGRND
    static const uint8_t sky[] = {0x00, 0x00, 0x00, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80,
                                  0x80, 0x80, 0x80, 0x82, 0x82, 0x82, 0x82, 0x82, 0x84};
    s_regs[BACKGRND] = sky[zone < (int)sizeof(sky) ? zone : (int)sizeof(sky) - 1];
  }
}

__attribute__((unused)) static void build_bg(void) {
  s_nbg = 0;
  switch (s_scene) {
    case 0: bg_stars(36, 100); break;
    case 1: bg_swarm(); break;
    case 2: bg_cave(); break;
    default: bg_clouds(); break;
  }
}

// ---------------------------------------------------------------------------
// Foreground (clock and HUD)
// ---------------------------------------------------------------------------

static Window *s_window;
static Layer *s_canvas;
static BatteryChargeState s_battery;
static bool s_connected = true;
static int s_steps;
static AppTimer *s_timer;
static int s_frames_left;
static AppTimer *s_preminute_timer;
static bool s_look;            // this burst came from a wrist flick
// what the cartridge is shown: battery and steps are taken once, as a burst
// starts, so the bar and the score change at most once a burst
static BatteryChargeState s_battery_shown;
static int s_steps_shown;
static uint8_t s_regs_base[NUM_REGS];

static const char *DAYS[] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
static const char *MONTHS[] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};

__attribute__((unused)) static void build_fg(void) {
  time_t now = time(NULL);
  struct tm *t = localtime(&now);

  s_scene = t->tm_hour % NUM_SCENES;
  s_nfg = 0;
  s_gram_used = s_gram_static_base;
  memcpy(s_regs_base, PALETTE_INIT, NUM_REGS);

  const char *title = "ATARI 7800";
  int title_x = (MW - text_width(title, 2)) / 2;
  fg_text(title, 2, 1, 2, title_x + 1, 7);                       // red shadow (P2)
  fg_text(title, 2, 1, 7, title_x, 6);                           // white (P7)
  fg_text_centered("40TH ANNIVERSARY", 1, 1, 1, 25);

  const char *years = "1986 - 2026";
  int yw = text_width(years, 1);
  {
    static const uint8_t heights[] = {3, 3, 2, 2, 1, 1};
    int gap_l = MW / 2 - yw / 2 - 5;
    int gap_r = MW / 2 + yw / 2 + 5;
    int wbytes = MW / 4;
    uint8_t *warm = gram_alloc(wbytes * 17);
    uint8_t *cool = gram_alloc(wbytes * 17);
    if (warm && cool) {
      int yy = 0;
      for (int i = 0; i < 6; i++) {
        uint8_t *g = (i < 3) ? warm : cool;
        int inset = i * 5;
        for (int dy = 0; dy < heights[i]; dy++) {
          for (int x = inset; x < gap_l; x++) gfx_set(g, wbytes, x, yy + dy, (i % 3) + 1);
          for (int x = gap_r; x < MW - inset; x++) gfx_set(g, wbytes, x, yy + dy, (i % 3) + 1);
        }
        yy += heights[i] + 1;
      }
      FG(((Gfx){warm, (uint8_t)wbytes, 17}), 2, 0, 36);
      FG(((Gfx){cool, (uint8_t)wbytes, 17}), 3, 0, 36);
    }
  }
  fg_text_centered(years, 1, 3, 1, 41);

  char tbuf[8];
  strftime(tbuf, sizeof(tbuf), clock_is_24h_style() ? "%H:%M" : "%I:%M", t);
  const char *ts = (!clock_is_24h_style() && tbuf[0] == '0') ? tbuf + 1 : tbuf;
  int tx = (MW - big_width(ts)) / 2;
  FG(make_big_time(ts, 2), 4, tx + 2, TIME_Y + 2);               // shadow first
  FG(make_big_time(ts, 1), 4, tx, TIME_Y);

  char dbuf[16];
  snprintf(dbuf, sizeof(dbuf), "%s %s %d", DAYS[t->tm_wday], MONTHS[t->tm_mon], t->tm_mday);
  fg_text_centered(dbuf, 2, 3, 1, 104);

  if (!s_connected) {
    fg_text_centered("NO LINK", 1, 2, 5, 127);
  }

  s_ship_x = 8 + (t->tm_min * (MW - 16 - 18)) / 59;
  FG(make_sprite(SHIP, ARRAY_LENGTH(SHIP), 2, "WLC"), 7, s_ship_x, SHIP_Y);
  FG(make_sprite(SHIP, ARRAY_LENGTH(SHIP), 2, "ROY"), 2, s_ship_x, SHIP_Y);

  // HUD: lives = battery, one per 20%; tint is a colour register write
  int lives = (s_battery.charge_percent + 19) / 20;
  if (lives < 1) lives = 1;
  if (lives > 5) lives = 5;
  s_regs_base[PREG(5, 1)] = s_battery.is_charging ? 0xC8 : (lives <= 1 ? 0x36 : 0x0E);
  Gfx mini = make_sprite(MINI_SHIP, ARRAY_LENGTH(MINI_SHIP), 1, "W");
  for (int i = 0; i < lives; i++) {
    FG(mini, 5, 10 + i * 8, 175);
  }

  char score[8];
  snprintf(score, sizeof(score), "%06d", s_steps % 1000000);
  int score_x = MW - 10 - text_width(score, 1);
  fg_text(score, 1, 1, 1, score_x, 174);
  fg_text("1UP", 1, 2, 1, score_x - 6 - text_width("1UP", 1), 174);
}

// Builds each zone's display list: background objects first, then foreground
__attribute__((unused)) static void build_display_lists(void) {
  int zy = 0;
  for (int z = 0; z < NUM_ZONES; z++) {
    int zh = ZONES[z].height;
    s_dl_len[z] = 0;
    for (int pass = 0; pass < 2; pass++) {
      const MObj *arr = pass ? s_fg : s_bg;
      int n = pass ? s_nfg : s_nbg;
      for (int i = 0; i < n; i++) {
        const MObj *o = &arr[i];
        if (o->y < zy + zh && o->y + o->gfx.h > zy && s_dl_len[z] < MAX_PER_ZONE) {
          s_dl[z][s_dl_len[z]++] = o;
        }
      }
    }
    zy += zh;
  }
}

// ---------------------------------------------------------------------------
// Output: MARIA frame into the tube, TV cabinet around it
// ---------------------------------------------------------------------------

static uint8_t s_tube_inset[TUBE_RADIUS];

static void build_tube_mask(void) {
  int r2 = 4 * TUBE_RADIUS * TUBE_RADIUS;
  for (int i = 0; i < TUBE_RADIUS; i++) {
    int dy = 2 * TUBE_RADIUS - 2 * i - 1;
    int dx = 0;
    while (dx < TUBE_RADIUS) {
      int ex = 2 * TUBE_RADIUS - 2 * dx - 1;
      if (ex * ex + dy * dy <= r2) {
        break;
      }
      dx++;
    }
    s_tube_inset[i] = (uint8_t)dx;
  }
}

static uint8_t lighten(uint8_t c) {
  int r = (c >> 4) & 3, g = (c >> 2) & 3, b = c & 3;
  r = r < 3 ? r + 1 : 3;
  g = g < 3 ? g + 1 : 3;
  b = b < 3 ? b + 1 : 3;
  return 0xC0 | (r << 4) | (g << 2) | b;
}

static bool is_glare(int x, int y) {
  // two short diagonal reflections on the glass, top left
  int d = x + y;
  return (d >= 18 && d <= 19 && x >= 4 && x <= 15) || (d == 24 && x >= 8 && x <= 13);
}

#if USE_EMULATOR
// ---------------------------------------------------------------------------
// The emulated 7800
// ---------------------------------------------------------------------------

static A7800 s_emu;
static uint8_t *s_rom;
static bool s_emu_ok;
static GBitmap *s_emu_fb;
static uint32_t s_emu_ms, s_emu_draws;

static uint8_t bcd(int v) {
  return (uint8_t)(((v / 10) << 4) | (v % 10));
}

static void emu_set_clock(void) {
  time_t now = time(NULL);
  struct tm *t = localtime(&now);
  uint8_t *c = s_emu.clock;
  c[A7800_CLK_SEC] = bcd(t->tm_sec);
  c[A7800_CLK_MIN] = bcd(t->tm_min);
  c[A7800_CLK_HOUR] = bcd(t->tm_hour);
  c[A7800_CLK_WDAY] = (uint8_t)t->tm_wday;
  c[A7800_CLK_DAY] = bcd(t->tm_mday);
  c[A7800_CLK_MONTH] = bcd(t->tm_mon + 1);
  c[A7800_CLK_YEAR] = bcd(t->tm_year % 100);
  c[A7800_CLK_FLAGS] = (uint8_t)((clock_is_24h_style() ? 0 : 0x80) | (s_connected ? 0x40 : 0) |
                                 (s_battery_shown.is_charging ? 0x20 : 0) | (s_look ? 0x10 : 0));
  c[A7800_CLK_BATTERY] = s_battery_shown.charge_percent;
  int st = s_steps_shown % 1000000;
  c[A7800_CLK_STEPS_HI] = bcd(st / 10000);
  c[A7800_CLK_STEPS_MID] = bcd((st / 100) % 100);
  c[A7800_CLK_STEPS_LO] = bcd(st % 100);
}

#define OV_BANNER_ROWS 56       // the banner's rows: the cartridge draws over them
static void overlay_banner(GBitmap *fb);
static void overlay_date(GBitmap *fb);

// One shown scanline from the emulator, straight into the tube
static void emu_line(void *ctx, int line, const uint8_t *colours) {
  int y = line - EMU_FIRST_LINE;
  if (!s_emu_fb || y < 0 || y >= MH) {
    return;
  }
  int inset = 0;
  if (y < TUBE_RADIUS) {
    inset = s_tube_inset[y];
  } else if (y >= MH - TUBE_RADIUS) {
    inset = s_tube_inset[MH - 1 - y];
  }
  GBitmapDataRowInfo info = gbitmap_get_data_row_info(s_emu_fb, SCREEN_Y + y);
  uint8_t *row = &info.data[SCREEN_X];
  if (y < OV_BANNER_ROWS) {
    // the banner is drawn first; here the cartridge's background colour is
    // see-through, so whatever it draws passes in front of the banner -- except
    // palette 7, the backdrop (the formation hour's stars), which passes behind
    // it and shows only where the banner leaves the tube black
    uint8_t bg = s_emu.palette[0];
    uint8_t b1 = s_emu.palette[29], b2 = s_emu.palette[30], b3 = s_emu.palette[31];
    uint8_t tube = s_lut[0x00];
    for (int x = inset; x < MW - inset; x++) {
      uint8_t c = colours[x];
      bool behind = (c == bg) || ((c == b1 || c == b2 || c == b3) && row[x] != tube);
      uint8_t col = behind ? row[x] : s_lut[c];
      row[x] = is_glare(x, y) ? lighten(col) : col;
    }
    return;
  }
  for (int x = inset; x < MW - inset; x++) {
    uint8_t col = s_lut[colours[x]];
    row[x] = is_glare(x, y) ? lighten(col) : col;
  }
}

static void emu_init(void) {
  ResHandle h = resource_get_handle(RESOURCE_ID_STUB_ROM);
  size_t len = resource_size(h);
  s_rom = malloc(len);
  if (!s_rom || resource_load(h, s_rom, len) != len) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "emu: cannot load STUB_ROM (%d bytes)", (int)len);
    return;
  }
  int err = a7800_load(&s_emu, s_rom, (uint32_t)len);
  if (err) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "emu: not a stub cartridge (%d)", err);
    return;
  }
  a7800_power_on(&s_emu);
  a7800_set_output(&s_emu, EMU_FIRST_LINE, MH, emu_line, NULL);
  s_emu_ok = true;
}

// ---- the overlay: the face's banner and date ----
// Drawn straight into the tube in MARIA colour bytes through the same palette,
// so they look like part of the screen. The banner goes in first and the
// cartridge draws over it (its background is see-through in those rows), so a
// lander or a bomber passes in front of the title; the date goes on last, over
// the picture, in zone 10, which the cartridge keeps clear.

#define OV_DATE_Y 137           // zone 10, between the attackers and the cruiser

static void ov_px(GBitmap *fb, int x, int y, uint8_t colour) {
  if (x < 0 || x >= MW || y < 0 || y >= MH) {
    return;
  }
  int inset = 0;
  if (y < TUBE_RADIUS) {
    inset = s_tube_inset[y];
  } else if (y >= MH - TUBE_RADIUS) {
    inset = s_tube_inset[MH - 1 - y];
  }
  if (x < inset || x >= MW - inset) {
    return;
  }
  gbitmap_get_data_row_info(fb, SCREEN_Y + y).data[SCREEN_X + x] = s_lut[colour];
}

static void ov_text(GBitmap *fb, const char *str, int x, int y, int scale, uint8_t colour) {
  for (; *str; str++, x += 6 * scale) {
    const uint8_t *rows = glyph(*str);
    for (int r = 0; r < 7; r++) {
      for (int c = 0; c < 5; c++) {
        if (rows[r] & (0x10 >> c)) {
          for (int dy = 0; dy < scale; dy++) {
            for (int dx = 0; dx < scale; dx++) {
              ov_px(fb, x + c * scale + dx, y + r * scale + dy, colour);
            }
          }
        }
      }
    }
  }
}

static void ov_text_centered(GBitmap *fb, const char *str, int y, int scale, uint8_t colour) {
  ov_text(fb, str, (MW - text_width(str, scale)) / 2, y, scale, colour);
}

static void overlay_banner(GBitmap *fb) {
  // the banner's rows start black (the tube) under the title, stripes and years
  for (int y = 0; y < OV_BANNER_ROWS; y++) {
    for (int x = 0; x < MW; x++) ov_px(fb, x, y, 0x00);
  }
  // ATARI 7800, white with a red drop shadow
  const char *title = "ATARI 7800";
  int title_x = (MW - text_width(title, 2)) / 2;
  ov_text(fb, title, title_x + 1, 7, 2, 0x36);
  ov_text(fb, title, title_x, 6, 2, 0x0E);
  ov_text_centered(fb, "40TH ANNIVERSARY", 25, 1, 0x1A);

  // rainbow speed stripes either side of the years, shorter toward the bottom
  static const uint8_t heights[6] = {3, 3, 2, 2, 1, 1};
  static const uint8_t colours[6] = {0x36, 0x28, 0x1E, 0xC8, 0x94, 0x66};
  const char *years = "1986 - 2026";
  int yw = text_width(years, 1);
  int gap_l = MW / 2 - yw / 2 - 5;
  int gap_r = MW / 2 + yw / 2 + 5;
  int y = 36;
  for (int i = 0; i < 6; i++) {
    int inset = i * 5;
    for (int dy = 0; dy < heights[i]; dy++) {
      for (int x = inset; x < gap_l; x++) ov_px(fb, x, y + dy, colours[i]);
      for (int x = gap_r; x < MW - inset; x++) ov_px(fb, x, y + dy, colours[i]);
    }
    y += heights[i] + 1;
  }
  ov_text_centered(fb, years, 41, 1, 0x9E);
}

static void overlay_date(GBitmap *fb) {

  // the date, between the attackers' station and the cruiser's lane
  time_t now = time(NULL);
  struct tm *t = localtime(&now);
  char date[16];
  snprintf(date, sizeof(date), "%s %s %d", DAYS[t->tm_wday], MONTHS[t->tm_mon], t->tm_mday);
  ov_text_centered(fb, date, OV_DATE_Y, 2, 0x9E);
}

static void emu_draw(GContext *ctx) {
  if (!s_emu_ok) {
    return;
  }
  s_emu_fb = graphics_capture_frame_buffer(ctx);
  if (!s_emu_fb) {
    return;
  }
  emu_set_clock();
  uint16_t ms0, ms1;
  time_t s0, s1;
  time_ms(&s0, &ms0);
  // run the frames; only the last one is drawn
  a7800_set_output(&s_emu, EMU_FIRST_LINE, MH, NULL, NULL);
  for (int i = 0; i < EMU_FRAMES_PER_DRAW - 1; i++) {
    a7800_run_frame(&s_emu);
  }
  overlay_banner(s_emu_fb);
  a7800_set_output(&s_emu, EMU_FIRST_LINE, MH, emu_line, NULL);
  a7800_run_frame(&s_emu);
  overlay_date(s_emu_fb);
  time_ms(&s1, &ms1);
  graphics_release_frame_buffer(ctx, s_emu_fb);
  s_emu_fb = NULL;

  s_emu_ms += (uint32_t)((s1 - s0) * 1000 + (int)ms1 - (int)ms0);
  if (++s_emu_draws == 30) {
    APP_LOG(APP_LOG_LEVEL_INFO, "emu: %d ms per draw of %d frames (avg of 30)",
            (int)(s_emu_ms / 30), EMU_FRAMES_PER_DRAW);
    s_emu_ms = 0;
    s_emu_draws = 0;
  }
}
#endif

__attribute__((unused)) static void maria_draw(GContext *ctx) {
  GBitmap *fb = graphics_capture_frame_buffer(ctx);
  if (!fb) {
    return;
  }
  memcpy(s_regs_frame, s_regs_base, NUM_REGS);
  scene_registers();
  memcpy(s_regs, s_regs_frame, NUM_REGS);

  uint8_t line[MW];
  int y = 0;
  for (int z = 0; z < NUM_ZONES; z++) {
    if (ZONES[z].dli_reg >= 0) {
      s_regs[ZONES[z].dli_reg] = ZONES[z].dli_val;   // DLI before the zone
    }
    scene_dli(z);
    for (int l = 0; l < ZONES[z].height; l++, y++) {
      memset(line, 0, sizeof(line));
      for (int k = 0; k < s_dl_len[z]; k++) {
        const MObj *o = s_dl[z][k];
        int row = y - o->y;
        if (row < 0 || row >= o->gfx.h) {
          continue;
        }
        const uint8_t *src = &o->gfx.g[row * o->gfx.wbytes];
        for (int b = 0; b < o->gfx.wbytes; b++) {
          uint8_t byte = src[b];
          if (!byte) {
            continue;
          }
          for (int p = 0; p < 4; p++) {
            int c = (byte >> (6 - 2 * p)) & 3;
            int x = o->x + b * 4 + p;
            if (c && x >= 0 && x < MW) {     // 0 = transparent
              line[x] = (uint8_t)((o->pal << 2) | c);
            }
          }
        }
      }

      int inset = 0;
      if (y < TUBE_RADIUS) {
        inset = s_tube_inset[y];
      } else if (y >= MH - TUBE_RADIUS) {
        inset = s_tube_inset[MH - 1 - y];
      }
      GBitmapDataRowInfo info = gbitmap_get_data_row_info(fb, SCREEN_Y + y);
      for (int x = inset; x < MW - inset; x++) {
        uint8_t v = line[x];
        int reg = v ? PREG(v >> 2, v & 3) : BACKGRND;
        uint8_t col = s_lut[s_regs[reg]];
        info.data[SCREEN_X + x] = is_glare(x, y) ? lighten(col) : col;
      }
    }
  }
  graphics_release_frame_buffer(ctx, fb);
}

static void draw_label(GContext *ctx, const char *s, int x, int y, GColor colour) {
  graphics_context_set_fill_color(ctx, colour);
  for (; *s; s++, x += 6) {
    const uint8_t *rows = glyph(*s);
    for (int r = 0; r < 7; r++) {
      for (int c = 0; c < 5; c++) {
        if (rows[r] & (0x10 >> c)) {
          graphics_fill_rect(ctx, GRect(x + c, y + r, 1, 1), 0, GCornerNone);
        }
      }
    }
  }
}

static void draw_cabinet(GContext *ctx, GRect b) {
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  // cabinet shell: black with a grey edge
  graphics_context_set_fill_color(ctx, GColorDarkGray);
  graphics_fill_rect(ctx, b, 8, GCornersAll);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, grect_inset(b, GEdgeInsets(2)), 8, GCornersAll);

  // speaker grille on the left, channel and volume knobs on the right
  graphics_context_set_fill_color(ctx, GColorDarkGray);
  for (int y = 40; y < 170; y += 4) {
    graphics_fill_rect(ctx, GRect(6, y, 8, 2), 0, GCornerNone);
  }
  for (int y = 120; y < 170; y += 4) {
    graphics_fill_rect(ctx, GRect(b.size.w - 14, y, 8, 2), 0, GCornerNone);
  }
  for (int k = 0; k < 2; k++) {
    GPoint c = GPoint(b.size.w - 10, 52 + k * 30);
    graphics_context_set_fill_color(ctx, GColorLightGray);
    graphics_fill_circle(ctx, c, 6);
    graphics_context_set_fill_color(ctx, GColorDarkGray);
    graphics_fill_circle(ctx, c, 4);
    graphics_context_set_stroke_color(ctx, GColorWhite);
    // the channel knob's pointer turns with the scene
    int dir = k ? 1 : (s_scene * 2 - 3);
    graphics_draw_line(ctx, c, GPoint(c.x + (k ? 3 : dir), c.y - 3));
  }

  // tube bezel and recess
  GRect bezel = GRect(SCREEN_X - 4, SCREEN_Y - 4, MW + 8, MH + 8);
  graphics_context_set_fill_color(ctx, GColorDarkGray);
  graphics_fill_rect(ctx, bezel, 8, GCornersAll);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, grect_inset(bezel, GEdgeInsets(2)), 8, GCornersAll);

  // brushed-metal control strip, as on the console's front
  GRect plate = GRect(8, 205, b.size.w - 16, 20);
  graphics_context_set_fill_color(ctx, GColorLightGray);
  graphics_fill_rect(ctx, plate, 3, GCornersAll);
  // the rainbow runs left to right across the strip, red to violet
  GColor rainbow[10] = {GColorRed, GColorOrange, GColorChromeYellow, GColorYellow,
                        GColorSpringBud, GColorGreen, GColorCyan, GColorVividCerulean,
                        GColorBlue, GColorVividViolet};
  int rx = plate.origin.x + 3;
  int rw = plate.size.w - 6;
  for (int i = 0; i < 10; i++) {
    int x0 = rx + rw * i / 10;
    int x1 = rx + rw * (i + 1) / 10;
    graphics_context_set_fill_color(ctx, rainbow[i]);
    graphics_fill_rect(ctx, GRect(x0, 207, x1 - x0, 5), 0, GCornerNone);
  }

  draw_label(ctx, "7800", plate.origin.x + 6, 215, GColorBlack);

  // power LED doubles as the phone-link light
  graphics_context_set_fill_color(ctx, s_connected ? GColorGreen : GColorRed);
  graphics_fill_rect(ctx, GRect(92, 217, 3, 3), 0, GCornerNone);

  // POWER, PAUSE, SELECT, RESET
  graphics_context_set_fill_color(ctx, GColorBlack);
  for (int i = 0; i < 4; i++) {
    graphics_fill_rect(ctx, GRect(100 + i * 22, 216, 18, 6), 2, GCornersAll);
  }
}

static void canvas_update(Layer *layer, GContext *ctx) {
  draw_cabinet(ctx, layer_get_bounds(layer));
#if USE_EMULATOR
  emu_draw(ctx);
#else
  maria_draw(ctx);
#endif
}

// ---------------------------------------------------------------------------
// Animation and services
// ---------------------------------------------------------------------------

static void redraw_bg(void) {
#if !USE_EMULATOR
  build_bg();
  build_display_lists();
#endif
  layer_mark_dirty(s_canvas);
}

static void frame_tick(void *data) {
  s_timer = NULL;
  s_frame++;
  redraw_bg();
  if (--s_frames_left <= 0) {
    s_look = false;
  }
  if (ANIMATE_ALWAYS || s_frames_left > 0) {
    s_timer = app_timer_register(FRAME_MS, frame_tick, NULL);
  }
}

static void latch_shown_values(void) {
  s_battery_shown = s_battery;
  s_steps_shown = s_steps;
}

static void start_burst_frames(int frames) {
  if (!s_timer) {
    latch_shown_values();     // a new burst: the bar and score may move now
  }
  if (frames > s_frames_left) {
    s_frames_left = frames;
  }
  if (!s_timer) {
    s_timer = app_timer_register(FRAME_MS, frame_tick, NULL);
  }
}

#if USE_EMULATOR
// The cartridge plays the minute change by itself, from :57 to :03 by the
// clock registers; the watch only has to be animating then. One timer, re-armed
// each time it fires, rather than a tick every second.
static void preminute_fired(void *data);

static void schedule_preminute(void) {
  time_t now;
  uint16_t ms;
  time_ms(&now, &ms);
  struct tm *t = localtime(&now);
  int delay = ((PREMINUTE_SECOND - t->tm_sec + 60) % 60) * 1000 - ms;
  if (delay <= 0) {
    delay += 60000;
  }
  s_preminute_timer = app_timer_register((uint32_t)delay, preminute_fired, NULL);
}

static void preminute_fired(void *data) {
  s_preminute_timer = NULL;
  start_burst_frames(PREMINUTE_FRAMES);
  schedule_preminute();
}
#endif

static void start_burst(void) {
  latch_shown_values();
  s_frames_left = BURST_FRAMES;
  if (!s_timer) {
    s_timer = app_timer_register(FRAME_MS, frame_tick, NULL);
  }
}

static void refresh(void) {
#if !USE_EMULATOR
  build_fg();
#endif
  redraw_bg();
}

static void update_steps(void) {
#if defined(PBL_HEALTH)
  s_steps = (int)health_service_sum_today(HealthMetricStepCount);
#endif
}

static void tick_handler(struct tm *tick_time, TimeUnits units_changed) {
  update_steps();
#if USE_EMULATOR
  return;   // the :57 burst has already drawn this minute's change
#endif
  if (!s_timer) {
    s_frame += 12;    // keep the playfield drifting between bursts
  }
  refresh();
}

static void tap_handler(AccelAxisType axis, int32_t direction) {
  s_look = true;              // the cartridge sees CLK_FLAGS bit 4 for this burst
  start_burst_frames(BURST_FRAMES);
}

static void battery_handler(BatteryChargeState state) {
  s_battery = state;
#if USE_EMULATOR
  return;   // shown from the next burst
#endif
  refresh();
}

static void connection_handler(bool connected) {
  if (s_connected && !connected) {
    vibes_double_pulse();
  }
  s_connected = connected;
#if USE_EMULATOR
  return;   // the vibration says it now; the power light catches up at the next burst
#endif
  refresh();
}

#if defined(PBL_HEALTH)
static void health_handler(HealthEventType event, void *context) {
  if (event == HealthEventMovementUpdate || event == HealthEventSignificantUpdate) {
    update_steps();
#if !USE_EMULATOR
    refresh();
#endif
  }
}
#endif

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  s_canvas = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_canvas, canvas_update);
  layer_add_child(root, s_canvas);
  refresh();
}

static void window_unload(Window *window) {
  layer_destroy(s_canvas);
}

static void init(void) {
  build_colour_lut();
  build_tube_mask();
  build_static_gfx();
  s_gram_static_base = s_gram_used;

  s_battery = battery_state_service_peek();
  s_connected = connection_service_peek_pebble_app_connection();
  update_steps();
#if USE_EMULATOR
  emu_init();
#endif

  s_window = window_create();
  window_set_background_color(s_window, GColorBlack);
  window_set_window_handlers(s_window, (WindowHandlers) {
    .load = window_load,
    .unload = window_unload,
  });
  window_stack_push(s_window, true);

  tick_timer_service_subscribe(MINUTE_UNIT, tick_handler);
  battery_state_service_subscribe(battery_handler);
  connection_service_subscribe((ConnectionHandlers) {
    .pebble_app_connection_handler = connection_handler,
  });
  accel_tap_service_subscribe(tap_handler);
#if USE_EMULATOR
  schedule_preminute();
#endif
#if defined(PBL_HEALTH)
  health_service_events_subscribe(health_handler, NULL);
#endif
  start_burst();
}

static void deinit(void) {
  if (s_timer) {
    app_timer_cancel(s_timer);
  }
#if USE_EMULATOR
  if (s_preminute_timer) {
    app_timer_cancel(s_preminute_timer);
  }
#endif
  tick_timer_service_unsubscribe();
  battery_state_service_unsubscribe();
  connection_service_unsubscribe();
  accel_tap_service_unsubscribe();
#if defined(PBL_HEALTH)
  health_service_events_unsubscribe();
#endif
  window_destroy(s_window);
#if USE_EMULATOR
  free(s_rom);
#endif
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
