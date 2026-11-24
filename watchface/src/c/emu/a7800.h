// a7800 -- a silent Atari 7800 for the watch: Sally (m6502), MARIA, the RIOT,
// the TIA's input side, 4K of RAM and a linear cartridge of up to 48K.
//
// Timing follows the a7800 fork of MAME (Miasmark/mame-Atari8bit7800,
// src/mame/atari/maria.cpp and a7800.cpp), scaled to half CPU cycles so the
// 113.5-cycle line is a whole number (227):
//
//   0    line reset: WSYNC lets go
//   16   MARIA's DMA for the line (8 CPU cycles in); the CPU halts for
//        (maria_clocks + 3) / 4 cycles, with the fork's costs: 16 to start,
//        8 per 4-byte header, 10 per 5-byte header, 3 per graphics byte,
//        3 more per character (6 for two-byte characters), 3/6/9 for an
//        object's first hole, 8 at the end of a zone, capped at 430
//   21   MSTAT's edge (lines 16 and 258), and on line 16 the frame's first
//        zone fetch (16 MARIA clocks) and its DLI
//   67   the picture: pixel x is shown at 67 + x, from the line RAM built on
//        the previous line, through the colour registers as they are then
//
// Not modelled: sound (TIA and POKEY writes are ignored), the border colour,
// colour-RAM access smear, bank switching, the BIOS. The CPU is
// instruction-granular, so register writes land at instruction boundaries.
//
// Starts as the fork's no-BIOS mode does: INPTCTRL $16, X = SP = $16,
// decimal and interrupt-disable set, MARIA's DMA off, 972 cycles into frame 0.
//
// Clock registers (docs/clock-registers.md) answer at $0600-$060F, a block
// nothing in the console, a cartridge mapper or an add-on uses (POKEY, the XM
// and XBoarD sit at $0400-$047F and $0800, the High Score Cart at $1000 and
// $3000, cartridges from $4000), so every cartridge size keeps them.

#ifndef A7800_H
#define A7800_H

#include <stdbool.h>
#include <stdint.h>

#include "m6502.h"

#define A7800_LINES 263          // NTSC
#define A7800_HALF_PER_LINE 227  // 113.5 CPU cycles
#define A7800_PICTURE_HALF 67    // pixel 0 is shown 33.5 cycles into the line
#define A7800_WIDTH 160

#define A7800_CLK_BASE 0x0600

// clock register offsets from A7800_CLK_BASE (all BCD unless noted)
#define A7800_CLK_SEC 0x0
#define A7800_CLK_MIN 0x1
#define A7800_CLK_HOUR 0x2        // 00-23
#define A7800_CLK_WDAY 0x3        // 0 = Sunday (binary)
#define A7800_CLK_DAY 0x4
#define A7800_CLK_MONTH 0x5
#define A7800_CLK_YEAR 0x6        // 00-99
#define A7800_CLK_FLAGS 0x7       // b7 12-hour preferred, b6 phone linked, b5 charging
#define A7800_CLK_BATTERY 0x8     // 0-100 (binary)
#define A7800_CLK_STEPS_HI 0x9    // steps, 6 BCD digits, high byte first
#define A7800_CLK_STEPS_MID 0xA
#define A7800_CLK_STEPS_LO 0xB
#define A7800_CLK_ID 0xF          // reads $5A when the clock registers are present

// One shown scanline: `colours` are 160 MARIA colour bytes (hue << 4 | luma).
typedef void (*a7800_line_fn)(void *ctx, int line, const uint8_t *colours);

typedef struct A7800 {
  M6502 cpu;

  uint8_t ram[0x1000];          // $1800-$27FF; $2040-$20FF and $2140-$21FF also at $0040/$0140
  uint8_t riot_ram[0x80];
  const uint8_t *rom;           // linear cartridge ROM, ending at $FFFF
  uint32_t rom_size;
  uint32_t rom_start;

  uint8_t clock[16];

  // inputs, set by the caller before a frame
  uint8_t swcha_in;             // joystick bits as SWCHA reads them, active low ($FF = centred)
  uint8_t swchb_in;             // console switches, active low ($0B = nothing pressed)
  uint8_t fire;                 // b0 P0 button, b1 P1 button (1 = pressed)

  // TIA control
  uint8_t inptctrl;
  bool locked;

  // RIOT
  uint8_t swacnt, swbcnt, pa_out, pb_out;
  bool timer_running;
  uint32_t timer_t0;            // CPU cycle the timer was (re)started on
  uint16_t timer_value;
  uint16_t timer_interval;
  uint8_t timer_bias;
  bool timer_flag_cleared;

  // MARIA
  uint8_t palette[32];          // index (palette << 2) | colour; colour 0 is BACKGRND
  uint16_t dpp, dll, dl, charbase;
  uint8_t offset, holey, rm, mstat;
  bool dli, dmaon, wsync, cwidth, kangaroo, color_kill, write_mode;
  uint8_t line_ram[2][A7800_WIDTH];
  uint8_t active;

  // timing
  int line;                     // 0 .. A7800_LINES-1
  int cpu_t;                    // half cycles into the line the CPU has reached
  int slow;                     // half cycles of 1.19 MHz accesses in this instruction
  uint32_t frame;
  uint32_t line_base;           // half cycles from power-on to the start of this line

  // output
  int out_first, out_count;     // which lines to emit
  a7800_line_fn emit;
  void *emit_ctx;
  uint8_t out[A7800_WIDTH];
  int out_x;
  bool shown;                   // this line is being emitted
  bool skip_spins;              // fast-forward MSTAT polling loops (on after power-on)

  // per-frame statistics
  uint32_t stat_dma_half, stat_cpu_half, stat_wsync_half;
} A7800;

// Loads a .a78 (with its 128-byte header) or a raw image. Linear carts only,
// up to 48K. Returns 0, or a negative error.
int a7800_load(A7800 *m, const uint8_t *image, uint32_t len);

// The fork's no-BIOS power-on state. Call after a7800_load.
void a7800_power_on(A7800 *m);

// Emit lines [first, first + count) through `fn`.
void a7800_set_output(A7800 *m, int first, int count, a7800_line_fn fn, void *ctx);

// Runs scanlines until the next frame starts (line 0).
void a7800_run_frame(A7800 *m);

// Runs one scanline.
void a7800_run_line(A7800 *m);

#endif
