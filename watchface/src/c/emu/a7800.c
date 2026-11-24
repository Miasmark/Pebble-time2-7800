// a7800 -- see a7800.h.

#include "a7800.h"

#include <string.h>

#define DMALIMIT 430

// A TIA or RIOT access runs at 1.19 MHz on hardware: half a cycle more. MAME (the fork included)
// does not charge it in 7800 mode, and the RIOT golden dump is MAME's, so it is off by default.
#ifndef A7800_SLOW_IO
#define A7800_SLOW_IO 0
#endif
#define VBLANK_SET_LINE (A7800_LINES - 5)   // 258

// ---------------------------------------------------------------------------
// RIOT interval timer, as the a7800 toolkit's simulator models it (tools/sim.py
// Bus.riot, checked there against MAME's own dump in tests/golden/riot-mame.bin):
// the timer reads N - ceil(e / interval) until it expires at e = N * interval,
// then counts down once per cycle from $FF with TIMINT bit 7 set. Reading
// INTIM clears TIMINT, and an expired timer goes back to the programmed rate
// from the value it had reached. The 6532 runs on the CPU clock, which keeps
// ticking while MARIA halts the CPU, so the timer reads wall-clock cycles.
// ---------------------------------------------------------------------------

static uint32_t now_cycles(const A7800 *m) {
  return (m->line_base + (uint32_t)m->cpu_t) / 2;
}

static uint8_t riot_timer_read(A7800 *m, bool flags) {
  if (!m->timer_running) {
    return 0x00;
  }
  uint32_t now = now_cycles(m);
  uint32_t e = now - m->timer_t0 + m->timer_bias;
  uint32_t span = (uint32_t)m->timer_value * m->timer_interval;
  bool expired = e >= span;
  uint8_t value;
  if (!expired) {
    value = (uint8_t)(m->timer_value - (e + m->timer_interval - 1) / m->timer_interval);
  } else {
    value = (uint8_t)((0xFF - (e - span) + (m->timer_interval == 1 ? 1 : 0)) & 0xFF);
  }
  if (flags) {
    return (expired && !m->timer_flag_cleared) ? 0x80 : 0x00;
  }
  if (expired) {
    m->timer_t0 = now;
    m->timer_value = value;
    m->timer_flag_cleared = true;
  }
  return value;
}

static void riot_timer_write(A7800 *m, uint8_t reg, uint8_t v) {
  static const uint16_t intervals[4] = {1, 8, 64, 1024};
  m->timer_interval = intervals[reg & 3];
  m->timer_bias = (m->timer_interval == 1) ? 2 : 1;
  m->timer_value = v;
  m->timer_t0 = now_cycles(m);
  m->timer_running = true;
  m->timer_flag_cleared = false;
}

static uint8_t riot_read(A7800 *m, uint8_t reg) {
  if (reg & 0x04) {
    return riot_timer_read(m, (reg & 0x01) != 0);
  }
  switch (reg & 0x03) {
    case 0: return (uint8_t)((m->swcha_in & ~m->swacnt) | (m->pa_out & m->swacnt));
    case 1: return m->swacnt;
    case 2: return (uint8_t)((m->swchb_in & ~m->swbcnt) | (m->pb_out & m->swbcnt));
    default: return m->swbcnt;
  }
}

static void riot_write(A7800 *m, uint8_t reg, uint8_t v) {
  if ((reg & 0x14) == 0x14) {
    riot_timer_write(m, reg, v);
    return;
  }
  if (reg & 0x04) {
    return;   // edge-detect control: no PA7 interrupt here
  }
  switch (reg & 0x03) {
    case 0: m->pa_out = v; break;
    case 1: m->swacnt = v; break;
    case 2: m->pb_out = v; break;
    default: m->swbcnt = v; break;
  }
}

// ---------------------------------------------------------------------------
// TIA (inputs and the console-control register only) and MARIA registers
// ---------------------------------------------------------------------------

static uint8_t tia_read(A7800 *m, uint8_t reg) {
  switch (reg & 0x0F) {
    case 0x08: return 0x00;                                   // INPT0-3: second buttons, not pressed
    case 0x09: return 0x00;
    case 0x0A: return 0x00;
    case 0x0B: return 0x00;
    case 0x0C: return (m->fire & 1) ? 0x00 : 0x80;            // INPT4, active low
    case 0x0D: return (m->fire & 2) ? 0x00 : 0x80;            // INPT5
    default: return 0x00;
  }
}

static void tia_write(A7800 *m, uint8_t reg, uint8_t v) {
  // INPTCTRL answers anywhere in $00-$1F until it is locked; after that $01 is
  // the TIA's VBLANK. Sound registers are ignored: this console is silent.
  if (!m->locked) {
    m->inptctrl = v;
    if (v & 1) {
      m->locked = true;
    }
  }
  (void)reg;
}

static uint8_t maria_read(A7800 *m, uint8_t reg) {
  return (reg == 0x08) ? m->mstat : 0x00;
}

static void emit_upto(A7800 *m, int t);

static void maria_write(A7800 *m, uint8_t reg, uint8_t v) {
  if (m->shown) {
    emit_upto(m, m->cpu_t);   // pixels before this write keep the old registers
  }
  if (reg & 3) {
    m->palette[reg] = v;
  }
  switch (reg) {
    case 0x00:
      for (int i = 0; i < 8; i++) m->palette[4 * i] = v;
      break;
    case 0x04:
      m->wsync = true;
      break;
    case 0x0C:
      m->dpp = (uint16_t)((m->dpp & 0x00FF) | (v << 8));
      break;
    case 0x10:
      m->dpp = (uint16_t)((m->dpp & 0xFF00) | v);
      break;
    case 0x14:
      m->charbase = (uint16_t)(v << 8);
      break;
    case 0x1C:
      m->color_kill = (v & 0x80) != 0;
      switch ((v >> 5) & 3) {
        case 2: m->dmaon = true; break;
        case 3: m->dmaon = false; break;
        default: break;   // test modes
      }
      m->cwidth = (v & 0x10) != 0;
      m->kangaroo = (v & 0x04) != 0;
      m->rm = v & 0x03;
      break;
    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// The CPU's bus. Pages with direct pointers (RAM, ROM) never come here.
// ---------------------------------------------------------------------------

static uint8_t cpu_read(void *ctx, uint16_t a) {
  A7800 *m = (A7800 *)ctx;
  if (a < 0x0400) {
    uint8_t low = a & 0xFF;
    if (low < 0x40) {   // TIA and MARIA, in all four pages
      if (low < 0x20) {
        m->slow += A7800_SLOW_IO;
        return tia_read(m, low);
      }
      return maria_read(m, (uint8_t)(low - 0x20));
    }
    if ((a & 0xFE80) == 0x0280) {
      m->slow += A7800_SLOW_IO;
      return riot_read(m, a & 0x1F);
    }
    if (a < 0x0200) {
      return m->ram[0x800 + (a & 0x1FF)];   // $0040-$00FF, $0140-$01FF = $2040.., $2140..
    }
    return 0x00;
  }
  if ((a & 0xFE80) == 0x0480) {
    return m->riot_ram[a & 0x7F];
  }
  if ((a & 0xFFE0) == A7800_CLK_BASE) {
    return m->clock[a & 0x1F];
  }
  return 0x00;   // unmapped (and $2800-$3FFF: MAME maps nothing there)
}

static void cpu_write(void *ctx, uint16_t a, uint8_t v) {
  A7800 *m = (A7800 *)ctx;
  if (a < 0x0400) {
    uint8_t low = a & 0xFF;
    if (low < 0x40) {
      if (low < 0x20) {
        m->slow += A7800_SLOW_IO;
        tia_write(m, low, v);
      } else {
        maria_write(m, (uint8_t)(low - 0x20), v);
      }
      return;
    }
    if ((a & 0xFE80) == 0x0280) {
      m->slow += A7800_SLOW_IO;
      riot_write(m, a & 0x1F, v);
      return;
    }
    if (a < 0x0200) {
      m->ram[0x800 + (a & 0x1FF)] = v;
    }
    return;
  }
  if ((a & 0xFE80) == 0x0480) {
    m->riot_ram[a & 0x7F] = v;
  }
  // ROM, the clock registers and unmapped space ignore writes
}

// MARIA's DMA reads: the same memory, no side effects.
static uint8_t dma_peek(A7800 *m, uint16_t a) {
  const uint8_t *p = m->cpu.rd_page[a >> 8];
  if (p) {
    return p[a & 0xFF];
  }
  if (a < 0x0200 && (a & 0xFF) >= 0x40) {
    return m->ram[0x800 + (a & 0x1FF)];
  }
  if ((a & 0xFE80) == 0x0480) {
    return m->riot_ram[a & 0x7F];
  }
  if ((a & 0xFFE0) == A7800_CLK_BASE) {
    return m->clock[a & 0x1F];
  }
  return 0x00;
}

// ---------------------------------------------------------------------------
// MARIA: one line of DMA into line RAM (maria.cpp draw_scanline)
// ---------------------------------------------------------------------------

static bool is_holey(const A7800 *m, uint16_t addr) {
  return ((m->holey & 2) && (addr & 0x9000) == 0x9000) || ((m->holey & 1) && (addr & 0x8800) == 0x8800);
}

static int write_line_data(A7800 *m, uint8_t data, uint8_t hpos, uint8_t pal) {
  if (!data && !m->kangaroo) {
    return m->write_mode ? 2 : 4;   // all transparent: nothing to write
  }
  uint8_t *buf = m->line_ram[m->active];
  pal = (uint8_t)(pal << 2);
  if (!m->write_mode && !m->kangaroo && hpos <= A7800_WIDTH - 4) {
    // 160A, all four on the line: the common case, unrolled
    uint8_t c;
    if ((c = data >> 6)) buf[hpos] = (uint8_t)(pal | c);
    if ((c = (data >> 4) & 3)) buf[hpos + 1] = (uint8_t)(pal | c);
    if ((c = (data >> 2) & 3)) buf[hpos + 2] = (uint8_t)(pal | c);
    if ((c = data & 3)) buf[hpos + 3] = (uint8_t)(pal | c);
    return 4;
  }
  if (m->write_mode) {
    // 160B: two pixels, each with its own palette bits (P2 from the entry)
    uint8_t c = (uint8_t)((pal & 0x10) | (data & 0x0C) | (data >> 6));
    if (((c & 3) || m->kangaroo) && hpos < A7800_WIDTH) buf[hpos] = c;
    hpos++;
    c = (uint8_t)((pal & 0x10) | ((data & 0x03) << 2) | ((data & 0x30) >> 4));
    if (((c & 3) || m->kangaroo) && hpos < A7800_WIDTH) buf[hpos] = c;
    return 2;
  }
  for (int i = 0; i < 4; i++, hpos++) {
    uint8_t c = (uint8_t)(pal | ((data >> (6 - 2 * i)) & 0x03));
    if (((c & 3) || m->kangaroo) && hpos < A7800_WIDTH) buf[hpos] = c;
  }
  return 4;
}

static int write_line_ram(A7800 *m, uint16_t addr, uint8_t hpos, uint8_t pal) {
  return write_line_data(m, dma_peek(m, addr), hpos, pal);
}

// Returns the MARIA clocks the line's DMA took, or 0 with DMA off.
static int draw_scanline(A7800 *m) {
  memset(m->line_ram[m->active], 0, A7800_WIDTH);
  if (!m->dmaon) {
    return 0;
  }
  int mc = 16;
  uint16_t dl = m->dl;
  while ((dma_peek(m, (uint16_t)(dl + 1)) & 0x5F) != 0 && mc < DMALIMIT) {
    uint8_t b1 = dma_peek(m, (uint16_t)(dl + 1));
    uint16_t gfx;
    int width;
    uint8_t hpos, pal;
    bool ind;
    if (!(b1 & 0x1F)) {   // five-byte header
      uint8_t b3 = dma_peek(m, (uint16_t)(dl + 3));
      gfx = (uint16_t)((dma_peek(m, (uint16_t)(dl + 2)) << 8) | dma_peek(m, dl));
      width = ((b3 ^ 0xFF) & 0x1F) + 1;
      hpos = dma_peek(m, (uint16_t)(dl + 4));
      pal = b3 >> 5;
      m->write_mode = (b1 & 0x80) != 0;
      ind = (b1 & 0x20) != 0;
      dl = (uint16_t)(dl + 5);
      mc += 10;
    } else {
      gfx = (uint16_t)((dma_peek(m, (uint16_t)(dl + 2)) << 8) | dma_peek(m, dl));
      width = ((b1 ^ 0xFF) & 0x1F) + 1;
      hpos = dma_peek(m, (uint16_t)(dl + 3));
      pal = b1 >> 5;
      ind = false;
      dl = (uint16_t)(dl + 4);
      mc += 8;
    }

    bool hole_charged = false;
    bool page_hole = false;          // holey DMA and the page table go by the high
    const uint8_t *page = NULL;      // byte: look them up once a page
    for (int x = 0; x < width; x++) {
      if (mc >= DMALIMIT) {
        break;
      }
      if (ind) {
        uint8_t ch = dma_peek(m, (uint16_t)(gfx + x));
        uint16_t data_addr = (uint16_t)((m->charbase | ch) + (m->offset << 8));
        if (is_holey(m, data_addr)) {
          if (!hole_charged) mc += m->cwidth ? 9 : 6;
          hole_charged = true;
          continue;
        }
        mc += 3;
        hpos = (uint8_t)(hpos + write_line_ram(m, data_addr, hpos, pal));
        if (m->cwidth) {
          hpos = (uint8_t)(hpos + write_line_ram(m, (uint16_t)(data_addr + 1), hpos, pal));
          mc += 6;
        } else {
          mc += 3;
        }
      } else {
        uint16_t data_addr = (uint16_t)(gfx + x + (m->offset << 8));
        if (x == 0 || (data_addr & 0xFF) == 0) {
          page_hole = is_holey(m, data_addr);
          page = m->cpu.rd_page[data_addr >> 8];
        }
        if (page_hole) {
          if (!hole_charged) mc += 3;
          hole_charged = true;
          // the rest of the entry on this page is a hole too: skip to the next page
          x += 0xFF - (data_addr & 0xFF);
          continue;
        }
        uint8_t data = page ? page[data_addr & 0xFF] : dma_peek(m, data_addr);
        hpos = (uint8_t)(hpos + write_line_data(m, data, hpos, pal));
        mc += 3;
      }
    }
  }
  if (m->offset == 0) {
    mc += 8;   // last-line shutdown
  }
  if (mc >= DMALIMIT) {
    mc = (m->offset == 0) ? DMALIMIT + 2 : DMALIMIT - 2;
  }
  return mc;
}

static void fetch_zone(A7800 *m) {
  m->dl = (uint16_t)((dma_peek(m, (uint16_t)(m->dll + 1)) << 8) | dma_peek(m, (uint16_t)(m->dll + 2)));
  uint8_t header = dma_peek(m, m->dll);
  m->offset = header & 0x0F;
  m->holey = (header & 0x60) >> 5;
  m->dli = (header & 0x80) != 0;
}

// ---------------------------------------------------------------------------
// The picture
// ---------------------------------------------------------------------------

static uint8_t pixel(const A7800 *m, int x) {
  uint8_t cell = m->line_ram[m->active ^ 1][x];
  uint8_t c;
  if (m->rm < 2) {
    c = m->palette[cell];
  } else {
    // 320 modes: two pixels per cell; the watch has 160 columns, so show the
    // left one unless it is background, then the right one
    uint8_t d1, d2;
    if (m->rm == 2) {   // 320B, 320D
      d1 = (uint8_t)((cell & 0x10) | (cell & 0x02) | ((cell >> 3) & 1));
      d2 = (uint8_t)((cell & 0x10) | ((cell << 1) & 0x02) | ((cell >> 2) & 1));
    } else {            // 320A, 320C
      d1 = (uint8_t)((cell & 0x1C) | (cell & 0x02));
      d2 = (uint8_t)((cell & 0x1C) | ((cell << 1) & 0x02));
    }
    c = m->palette[(d1 & 3) ? d1 : d2];
  }
  return m->color_kill ? (uint8_t)(c & 0x0F) : c;
}

static bool line_shown(const A7800 *m) {
  return m->emit && m->line >= m->out_first && m->line < m->out_first + m->out_count;
}

static void emit_upto(A7800 *m, int t) {
  int end = t - A7800_PICTURE_HALF + 1;
  if (end > A7800_WIDTH) end = A7800_WIDTH;
  int x = m->out_x;
  if (x >= end) {
    return;
  }
  if (m->rm < 2 && !m->color_kill) {   // the 160 modes: one lookup a pixel
    const uint8_t *cells = m->line_ram[m->active ^ 1];
    for (; x < end; x++) m->out[x] = m->palette[cells[x]];
  } else {
    for (; x < end; x++) m->out[x] = pixel(m, x);
  }
  m->out_x = x;
}

// ---------------------------------------------------------------------------
// The line
// ---------------------------------------------------------------------------

// A loop that only polls MSTAT -- LDA/BIT MSTAT, then BPL/BMI back to itself -- can only
// leave when MSTAT changes, and MSTAT only changes at a MARIA event, never inside run_cpu.
// So when the CPU sits in one and the branch would be taken, jump to the first iteration
// that starts at or after `until`: the same place stepping would have reached, with the
// same registers, at a fraction of the cost. Most 7800 frames spend their slack here.
static int skip_mstat_spin(A7800 *m, int until) {
  M6502 *c = &m->cpu;
  if (c->nmi_pending || c->jammed) {
    return 0;
  }
  const uint8_t *page = c->rd_page[c->pc >> 8];
  unsigned off = c->pc & 0xFF;
  if (!page || off > 0xFB) {
    return 0;
  }
  const uint8_t *i = page + off;
  int len, load_cycles;
  bool bit;
  if ((i[0] == 0xAD || i[0] == 0x2C) && i[1] == 0x28 && i[2] <= 0x03) {
    len = 3; load_cycles = 4; bit = (i[0] == 0x2C);
  } else if ((i[0] == 0xA5 || i[0] == 0x24) && i[1] == 0x28) {
    len = 2; load_cycles = 3; bit = (i[0] == 0x24);
  } else {
    return 0;
  }
  uint8_t br = i[len];
  if ((br != 0x10 && br != 0x30) || (int8_t)i[len + 1] != -(len + 2)) {
    return 0;
  }
  bool stays = (br == 0x10) ? !(m->mstat & 0x80) : (m->mstat & 0x80);
  if (!stays) {
    return 0;
  }
  // the branch goes back within the page the loop starts in (off <= $FB), so +1 cycle.
  // Stepping would stop at the first instruction boundary at or after `until`: either an
  // iteration's start (before the load) or the middle of one (after the load, before the
  // branch). Land on exactly that one, so an NMI is taken where stepping would take it.
  int load_half = 2 * load_cycles;
  int iter_half = load_half + 2 * 3;
  int gap = until - m->cpu_t;
  int j1 = (gap + iter_half - 1) / iter_half;                 // iterations to a loop start
  int t1 = j1 * iter_half;
  int j2 = (gap - load_half > 0) ? (gap - load_half + iter_half - 1) / iter_half : 0;
  int t2 = load_half + j2 * iter_half;                        // ... or to just after a load
  int adv, loads;
  bool mid = t2 < t1;
  if (mid) {
    adv = t2;
    loads = j2 + 1;
    c->instructions += (uint32_t)(2 * j2 + 1);
    c->pc = (uint16_t)(c->pc + len);                          // at the branch
  } else {
    adv = t1;
    loads = j1;
    c->instructions += (uint32_t)(2 * j1);
  }
  m->cpu_t += adv;
  m->stat_cpu_half += (uint32_t)adv;
  c->total_cycles += (uint32_t)(adv / 2);
  if (loads > 0) {
    uint8_t v = m->mstat;
    if (bit) {
      c->p = (uint8_t)((c->p & ~(M6502_FLAG_N | M6502_FLAG_V | M6502_FLAG_Z)) | (v & 0xC0) |
                       ((c->a & v) ? 0 : M6502_FLAG_Z));
    } else {
      c->a = v;
      c->p = (uint8_t)((c->p & ~(M6502_FLAG_N | M6502_FLAG_Z)) | (v & 0x80) | (v ? 0 : M6502_FLAG_Z));
    }
  }
  return 1;
}

static void run_cpu(A7800 *m, int until) {
  while (m->cpu_t < until) {
    if (m->wsync) {
      m->stat_wsync_half += (uint32_t)(until - m->cpu_t);
      m->cpu_t = until;
      return;
    }
    // the CPU runs on whole cycles of its own clock: after a halt that ended
    // between two (lines are 113.5 cycles long), it resumes on the next edge
    if ((m->line_base + (uint32_t)m->cpu_t) & 1) {
      m->cpu_t++;
      if (m->cpu_t >= until) {
        return;
      }
    }
    if (m->skip_spins && skip_mstat_spin(m, until)) {
      continue;
    }
    m->slow = 0;
    int half = 2 * m6502_step(&m->cpu);
    half += m->slow;
    m->cpu_t += half;
    m->stat_cpu_half += (uint32_t)half;
  }
}

static void halt_cpu(A7800 *m, int half) {
  if (m->wsync) {
    return;   // already stalled until the line ends
  }
  m->cpu_t += half;
  m->stat_dma_half += (uint32_t)half;
}

void a7800_run_line(A7800 *m) {
  bool shown = line_shown(m);
  m->shown = shown;
  m->wsync = false;   // line reset
  m->out_x = 0;

  // 8 cycles in: the line's DMA
  run_cpu(m, 16);
  m->active ^= 1;
  bool dma_line = m->line > 16 && m->line <= VBLANK_SET_LINE;
  if (dma_line) {
    int mc = draw_scanline(m);
    if (mc) {
      halt_cpu(m, ((mc + 3) / 4) * 2);
    }
    if (m->dmaon) {
      if (m->offset == 0) {
        m->dll = (uint16_t)(m->dll + 3);
        fetch_zone(m);
      } else {
        m->offset--;
      }
    }
  }
  if (m->dli) {
    m6502_nmi(&m->cpu);
    m->dli = false;
  }

  // 10.5 cycles in: MSTAT's edges, and the frame's first zone
  run_cpu(m, 21);
  if (m->line == VBLANK_SET_LINE) {
    m->mstat = 0x80;
  }
  if (m->line == 16) {
    m->mstat = 0x00;
    if (m->dmaon) {
      m->dll = m->dpp;
      fetch_zone(m);
      halt_cpu(m, ((16 + 3) / 4) * 2);
      if (m->dli) {
        m6502_nmi(&m->cpu);
        m->dli = false;
      }
    }
  }

  run_cpu(m, A7800_HALF_PER_LINE);
  if (shown) {
    emit_upto(m, A7800_HALF_PER_LINE);
    m->emit(m->emit_ctx, m->line, m->out);
  }
  m->shown = false;

  m->cpu_t -= A7800_HALF_PER_LINE;
  m->line_base += A7800_HALF_PER_LINE;
  if (++m->line == A7800_LINES) {
    m->line = 0;
    m->frame++;
  }
}

void a7800_run_frame(A7800 *m) {
  m->stat_dma_half = m->stat_cpu_half = m->stat_wsync_half = 0;
  do {
    a7800_run_line(m);
  } while (m->line != 0);
}

// ---------------------------------------------------------------------------
// Loading and power-on
// ---------------------------------------------------------------------------

static void map_pages(A7800 *m) {
  for (int p = 0; p < 256; p++) {
    m->cpu.rd_page[p] = NULL;
    m->cpu.wr_page[p] = NULL;
  }
  for (int p = 0x18; p < 0x28; p++) {
    m->cpu.rd_page[p] = &m->ram[(p - 0x18) << 8];
    m->cpu.wr_page[p] = &m->ram[(p - 0x18) << 8];
  }
  for (uint32_t p = m->rom_start >> 8; p < 0x100; p++) {
    m->cpu.rd_page[p] = &m->rom[(p << 8) - m->rom_start];
  }
}

int a7800_load(A7800 *m, const uint8_t *image, uint32_t len) {
  memset(m, 0, sizeof(*m));
  const uint8_t *data = image;
  uint32_t size = len;
  if (len >= 128 && memcmp(image + 1, "ATARI7800", 9) == 0) {
    size = ((uint32_t)image[49] << 24) | ((uint32_t)image[50] << 16) | ((uint32_t)image[51] << 8) | image[52];
    uint16_t type = (uint16_t)((image[53] << 8) | image[54]);
    if (type & 0x3F3E) {
      return -2;   // banked, cartridge RAM or extra hardware: not a stub
    }
    data = image + 128;
    if (size > len - 128) {
      return -3;
    }
  }
  if (size == 0 || size > 0xC000 || (size & 0xFF)) {
    return -1;
  }
  m->rom = data;
  m->rom_size = size;
  m->rom_start = 0x10000 - size;
  return 0;
}

void a7800_power_on(A7800 *m) {
  memset(m->ram, 0, sizeof(m->ram));
  memset(m->riot_ram, 0, sizeof(m->riot_ram));
  memset(m->palette, 0, sizeof(m->palette));
  memset(m->line_ram, 0, sizeof(m->line_ram));
  m->swcha_in = 0xFF;
  m->swchb_in = 0x0B;
  m->fire = 0;
  m->inptctrl = 0x16;
  m->locked = false;
  m->swacnt = m->swbcnt = m->pa_out = m->pb_out = 0;
  m->timer_running = false;
  m->dpp = m->dll = m->dl = m->charbase = 0;
  m->offset = m->holey = m->rm = 0;
  m->mstat = 0x80;
  m->dli = m->dmaon = m->wsync = m->cwidth = m->kangaroo = m->color_kill = m->write_mode = false;
  m->active = 0;
  m->skip_spins = true;
  m->clock[A7800_CLK_ID] = 0x5A;

  m6502_init(&m->cpu, cpu_read, cpu_write, m);
  map_pages(m);
  m->cpu.s = 0x19;                     // the reset sequence drops it by 3: $16
  m->cpu.x = 0x16;
  m->cpu.p |= M6502_FLAG_D;
  m6502_reset(&m->cpu);

  // the fork starts a cartridge 972 cycles into frame 0, the reset sequence included
  m->line = 1944 / A7800_HALF_PER_LINE;
  m->cpu_t = 1944 % A7800_HALF_PER_LINE;
  m->line_base = (uint32_t)m->line * A7800_HALF_PER_LINE;
  m->frame = 0;
}

void a7800_set_output(A7800 *m, int first, int count, a7800_line_fn fn, void *ctx) {
  m->out_first = first;
  m->out_count = count;
  m->emit = fn;
  m->emit_ctx = ctx;
}
