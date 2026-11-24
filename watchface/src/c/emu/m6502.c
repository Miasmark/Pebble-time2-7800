// m6502 -- see m6502.h.

#include "m6502.h"

#include <string.h>

#include "m6502_tables.h"

#define FC M6502_FLAG_C
#define FZ M6502_FLAG_Z
#define FI M6502_FLAG_I
#define FD M6502_FLAG_D
#define FB M6502_FLAG_B
#define FU M6502_FLAG_U
#define FV M6502_FLAG_V
#define FN M6502_FLAG_N

// ANE ($8B) and LXA ($AB) OR the accumulator with a chip-dependent constant
// first; $EE is the value most often measured (and what the Harte tests use).
#define UNSTABLE_MAGIC 0xEE

// watch builds are size-optimised; the bus helpers must still inline
#if defined(__GNUC__)
#define HOT static inline __attribute__((always_inline))
#else
#define HOT static inline
#endif

HOT uint8_t rd(M6502 *c, uint16_t a) {
  const uint8_t *p = c->rd_page[a >> 8];
  return p ? p[a & 0xFF] : c->read(c->ctx, a);
}

HOT void wr(M6502 *c, uint16_t a, uint8_t v) {
  uint8_t *p = c->wr_page[a >> 8];
  if (p) {
    p[a & 0xFF] = v;
  } else {
    c->write(c->ctx, a, v);
  }
}

HOT void push(M6502 *c, uint8_t v) {
  wr(c, 0x0100 | c->s, v);
  c->s--;
}

HOT uint8_t pop(M6502 *c) {
  c->s++;
  return rd(c, 0x0100 | c->s);
}

HOT void setnz(M6502 *c, uint8_t v) {
  c->p = (uint8_t)((c->p & ~(FN | FZ)) | (v & FN) | (v ? 0 : FZ));
}

HOT void setc(M6502 *c, int on) {
  c->p = (uint8_t)(on ? (c->p | FC) : (c->p & ~FC));
}

static void adc(M6502 *c, uint8_t v) {
  unsigned carry = c->p & FC;
  if (c->p & FD) {
    // NMOS decimal: Z from the binary sum, N and V from the intermediate
    unsigned lo = (c->a & 0x0F) + (v & 0x0F) + carry;
    if (lo > 9) lo += 6;
    unsigned hi = (c->a >> 4) + (v >> 4) + (lo > 0x0F);
    uint8_t bin = (uint8_t)(c->a + v + carry);
    uint8_t p = c->p & ~(FN | FV | FZ | FC);
    if (!bin) p |= FZ;
    if (hi & 0x08) p |= FN;
    if (~(c->a ^ v) & (c->a ^ (hi << 4)) & 0x80) p |= FV;
    if (hi > 9) hi += 6;
    if (hi > 0x0F) p |= FC;
    c->a = (uint8_t)((hi << 4) | (lo & 0x0F));
    c->p = p;
  } else {
    unsigned sum = c->a + v + carry;
    uint8_t p = c->p & ~(FV | FC);
    if (~(c->a ^ v) & (c->a ^ sum) & 0x80) p |= FV;
    if (sum > 0xFF) p |= FC;
    c->p = p;
    c->a = (uint8_t)sum;
    setnz(c, c->a);
  }
}

static void sbc(M6502 *c, uint8_t v) {
  unsigned borrow = (c->p & FC) ? 0 : 1;
  unsigned diff = (unsigned)c->a - v - borrow;
  uint8_t p = c->p & ~(FN | FV | FZ | FC);
  if ((c->a ^ v) & (c->a ^ diff) & 0x80) p |= FV;
  if (diff < 0x100) p |= FC;
  if (!(uint8_t)diff) p |= FZ;
  if (diff & 0x80) p |= FN;
  if (c->p & FD) {
    // NMOS decimal: flags as binary, result BCD-adjusted
    int lo = (c->a & 0x0F) - (v & 0x0F) - (int)borrow;
    int hi = (c->a >> 4) - (v >> 4);
    if (lo & 0x10) {
      lo -= 6;
      hi--;
    }
    if (hi & 0x10) hi -= 6;
    c->a = (uint8_t)((hi << 4) | (lo & 0x0F));
  } else {
    c->a = (uint8_t)diff;
  }
  c->p = p;
}

HOT void compare(M6502 *c, uint8_t reg, uint8_t v) {
  setc(c, reg >= v);
  setnz(c, (uint8_t)(reg - v));
}

static void interrupt(M6502 *c, uint16_t vector, int brk) {
  push(c, c->pc >> 8);
  push(c, c->pc & 0xFF);
  push(c, (uint8_t)((c->p | FU | (brk ? FB : 0)) & ~(brk ? 0 : FB)));
  c->p |= FI;
  c->pc = (uint16_t)(rd(c, vector) | (rd(c, vector + 1) << 8));
}

void m6502_init(M6502 *c, m6502_read_fn read, m6502_write_fn write, void *ctx) {
  memset(c, 0, sizeof(*c));
  c->read = read;
  c->write = write;
  c->ctx = ctx;
  c->p = FU | FI;
  c->s = 0xFD;
}

void m6502_reset(M6502 *c) {
  c->s = (uint8_t)(c->s - 3);
  c->p |= FI | FU;
  c->jammed = 0;
  c->nmi_pending = 0;
  c->pc = (uint16_t)(rd(c, 0xFFFC) | (rd(c, 0xFFFD) << 8));
  c->total_cycles += 7;
}

int m6502_step(M6502 *c) {
  if (c->jammed) {
    c->total_cycles += 2;
    return 2;
  }
  if (c->nmi_pending) {
    c->nmi_pending = 0;
    interrupt(c, 0xFFFA, 0);
    c->total_cycles += 7;
    return 7;
  }
  if (c->irq_line && !(c->p & FI)) {
    interrupt(c, 0xFFFE, 0);
    c->total_cycles += 7;
    return 7;
  }

  uint8_t op = rd(c, c->pc++);
  int cyc = M6502_CYCLES[op];
  uint16_t ea = 0;
  uint16_t base = 0;
  uint8_t v, t;

  // ---- addressing --------------------------------------------------------
  switch (M6502_MODE[op]) {
    case AM_IMP:
    case AM_ACC:
      break;
    case AM_IMM:
    case AM_REL:
      ea = c->pc++;
      break;
    case AM_ZP:
      ea = rd(c, c->pc++);
      break;
    case AM_ZPX:
      ea = (uint8_t)(rd(c, c->pc++) + c->x);
      break;
    case AM_ZPY:
      ea = (uint8_t)(rd(c, c->pc++) + c->y);
      break;
    case AM_ABS:
      ea = (uint16_t)(rd(c, c->pc) | (rd(c, (uint16_t)(c->pc + 1)) << 8));
      c->pc += 2;
      break;
    case AM_ABX:
      base = (uint16_t)(rd(c, c->pc) | (rd(c, (uint16_t)(c->pc + 1)) << 8));
      c->pc += 2;
      ea = (uint16_t)(base + c->x);
      if (M6502_PAGE_PENALTY[op] && ((base ^ ea) & 0xFF00)) cyc++;
      break;
    case AM_ABY:
      base = (uint16_t)(rd(c, c->pc) | (rd(c, (uint16_t)(c->pc + 1)) << 8));
      c->pc += 2;
      ea = (uint16_t)(base + c->y);
      if (M6502_PAGE_PENALTY[op] && ((base ^ ea) & 0xFF00)) cyc++;
      break;
    case AM_IZX: {
      uint8_t z = (uint8_t)(rd(c, c->pc++) + c->x);
      ea = (uint16_t)(rd(c, z) | (rd(c, (uint8_t)(z + 1)) << 8));
      break;
    }
    case AM_IZY: {
      uint8_t z = rd(c, c->pc++);
      base = (uint16_t)(rd(c, z) | (rd(c, (uint8_t)(z + 1)) << 8));
      ea = (uint16_t)(base + c->y);
      if (M6502_PAGE_PENALTY[op] && ((base ^ ea) & 0xFF00)) cyc++;
      break;
    }
    case AM_IND: {
      uint16_t ptr = (uint16_t)(rd(c, c->pc) | (rd(c, (uint16_t)(c->pc + 1)) << 8));
      c->pc += 2;
      // the page-wrap bug: the high byte comes from the same page
      ea = (uint16_t)(rd(c, ptr) | (rd(c, (uint16_t)((ptr & 0xFF00) | ((ptr + 1) & 0xFF))) << 8));
      break;
    }
  }

  // ---- operation ---------------------------------------------------------
  switch (M6502_OP[op]) {
    // loads and stores
    case OP_LDA: c->a = rd(c, ea); setnz(c, c->a); break;
    case OP_LDX: c->x = rd(c, ea); setnz(c, c->x); break;
    case OP_LDY: c->y = rd(c, ea); setnz(c, c->y); break;
    case OP_LAX: c->a = c->x = rd(c, ea); setnz(c, c->a); break;
    case OP_STA: wr(c, ea, c->a); break;
    case OP_STX: wr(c, ea, c->x); break;
    case OP_STY: wr(c, ea, c->y); break;
    case OP_SAX: wr(c, ea, c->a & c->x); break;

    // arithmetic and logic
    case OP_ADC: adc(c, rd(c, ea)); break;
    case OP_SBC: sbc(c, rd(c, ea)); break;
    case OP_AND: c->a &= rd(c, ea); setnz(c, c->a); break;
    case OP_ORA: c->a |= rd(c, ea); setnz(c, c->a); break;
    case OP_EOR: c->a ^= rd(c, ea); setnz(c, c->a); break;
    case OP_CMP: compare(c, c->a, rd(c, ea)); break;
    case OP_CPX: compare(c, c->x, rd(c, ea)); break;
    case OP_CPY: compare(c, c->y, rd(c, ea)); break;
    case OP_BIT:
      v = rd(c, ea);
      c->p = (uint8_t)((c->p & ~(FN | FV | FZ)) | (v & (FN | FV)) | ((c->a & v) ? 0 : FZ));
      break;

    // read-modify-write
    case OP_ASL:
      if (M6502_MODE[op] == AM_ACC) {
        setc(c, c->a & 0x80); c->a <<= 1; setnz(c, c->a);
      } else {
        v = rd(c, ea); setc(c, v & 0x80); v <<= 1; wr(c, ea, v); setnz(c, v);
      }
      break;
    case OP_LSR:
      if (M6502_MODE[op] == AM_ACC) {
        setc(c, c->a & 1); c->a >>= 1; setnz(c, c->a);
      } else {
        v = rd(c, ea); setc(c, v & 1); v >>= 1; wr(c, ea, v); setnz(c, v);
      }
      break;
    case OP_ROL:
      if (M6502_MODE[op] == AM_ACC) {
        t = c->a; c->a = (uint8_t)((t << 1) | (c->p & FC)); setc(c, t & 0x80); setnz(c, c->a);
      } else {
        t = rd(c, ea); v = (uint8_t)((t << 1) | (c->p & FC)); setc(c, t & 0x80);
        wr(c, ea, v); setnz(c, v);
      }
      break;
    case OP_ROR:
      if (M6502_MODE[op] == AM_ACC) {
        t = c->a; c->a = (uint8_t)((t >> 1) | ((c->p & FC) << 7)); setc(c, t & 1); setnz(c, c->a);
      } else {
        t = rd(c, ea); v = (uint8_t)((t >> 1) | ((c->p & FC) << 7)); setc(c, t & 1);
        wr(c, ea, v); setnz(c, v);
      }
      break;
    case OP_INC: v = (uint8_t)(rd(c, ea) + 1); wr(c, ea, v); setnz(c, v); break;
    case OP_DEC: v = (uint8_t)(rd(c, ea) - 1); wr(c, ea, v); setnz(c, v); break;

    // undocumented read-modify-write combinations
    case OP_SLO:
      v = rd(c, ea); setc(c, v & 0x80); v <<= 1; wr(c, ea, v);
      c->a |= v; setnz(c, c->a);
      break;
    case OP_RLA:
      t = rd(c, ea); v = (uint8_t)((t << 1) | (c->p & FC)); setc(c, t & 0x80); wr(c, ea, v);
      c->a &= v; setnz(c, c->a);
      break;
    case OP_SRE:
      v = rd(c, ea); setc(c, v & 1); v >>= 1; wr(c, ea, v);
      c->a ^= v; setnz(c, c->a);
      break;
    case OP_RRA:
      t = rd(c, ea); v = (uint8_t)((t >> 1) | ((c->p & FC) << 7)); setc(c, t & 1); wr(c, ea, v);
      adc(c, v);
      break;
    case OP_DCP: v = (uint8_t)(rd(c, ea) - 1); wr(c, ea, v); compare(c, c->a, v); break;
    case OP_ISC: v = (uint8_t)(rd(c, ea) + 1); wr(c, ea, v); sbc(c, v); break;

    // undocumented immediates
    case OP_ANC:
      c->a &= rd(c, ea); setnz(c, c->a); setc(c, c->a & 0x80);
      break;
    case OP_ALR:
      c->a &= rd(c, ea); setc(c, c->a & 1); c->a >>= 1; setnz(c, c->a);
      break;
    case OP_ARR: {
      uint8_t and_ = c->a & rd(c, ea);
      uint8_t carry_in = (uint8_t)((c->p & FC) << 7);
      uint8_t r = (uint8_t)((and_ >> 1) | carry_in);
      if (c->p & FD) {
        uint8_t p = c->p & ~(FN | FV | FZ | FC);
        if (carry_in) p |= FN;
        if (!r) p |= FZ;
        if ((and_ ^ r) & 0x40) p |= FV;
        if ((and_ & 0x0F) + (and_ & 0x01) > 5) r = (uint8_t)((r & 0xF0) | ((r + 6) & 0x0F));
        if ((and_ & 0xF0) + (and_ & 0x10) > 0x50) {
          r = (uint8_t)(r + 0x60);
          p |= FC;
        }
        c->p = p;
        c->a = r;
      } else {
        c->a = r;
        setnz(c, r);
        setc(c, r & 0x40);
        c->p = (uint8_t)((c->p & ~FV) | ((((r >> 6) ^ (r >> 5)) & 1) ? FV : 0));
      }
      break;
    }
    case OP_SBX: {
      uint8_t ax = c->a & c->x;
      v = rd(c, ea);
      setc(c, ax >= v);
      c->x = (uint8_t)(ax - v);
      setnz(c, c->x);
      break;
    }
    case OP_ANE: c->a = (uint8_t)((c->a | UNSTABLE_MAGIC) & c->x & rd(c, ea)); setnz(c, c->a); break;
    case OP_LXA: c->a = c->x = (uint8_t)((c->a | UNSTABLE_MAGIC) & rd(c, ea)); setnz(c, c->a); break;
    case OP_LAS: c->a = c->x = c->s = rd(c, ea) & c->s; setnz(c, c->a); break;

    // undocumented stores that AND with the address high byte + 1
    case OP_SHA:
    case OP_SHX:
    case OP_SHY:
    case OP_TAS: {
      uint8_t reg;
      switch (M6502_OP[op]) {
        case OP_SHA: reg = c->a & c->x; break;
        case OP_SHX: reg = c->x; break;
        case OP_SHY: reg = c->y; break;
        default: c->s = c->a & c->x; reg = c->s; break;   // TAS
      }
      v = (uint8_t)(reg & ((base >> 8) + 1));
      if ((base ^ ea) & 0xFF00) {
        ea = (uint16_t)((v << 8) | (ea & 0xFF));   // a page crossing takes the value as high byte
      }
      wr(c, ea, v);
      break;
    }

    // branches
    case OP_BPL: case OP_BMI: case OP_BVC: case OP_BVS:
    case OP_BCC: case OP_BCS: case OP_BNE: case OP_BEQ: {
      int take;
      switch (M6502_OP[op]) {
        case OP_BPL: take = !(c->p & FN); break;
        case OP_BMI: take = (c->p & FN) != 0; break;
        case OP_BVC: take = !(c->p & FV); break;
        case OP_BVS: take = (c->p & FV) != 0; break;
        case OP_BCC: take = !(c->p & FC); break;
        case OP_BCS: take = (c->p & FC) != 0; break;
        case OP_BNE: take = !(c->p & FZ); break;
        default: take = (c->p & FZ) != 0; break;
      }
      if (take) {
        uint16_t dest = (uint16_t)(c->pc + (int8_t)rd(c, ea));
        cyc += ((dest ^ c->pc) & 0xFF00) ? 2 : 1;
        c->pc = dest;
      }
      break;
    }

    // jumps and subroutines
    case OP_JMP: c->pc = ea; break;
    case OP_JSR: {
      // the chip reads the target's low byte, pushes the return address and only then reads
      // the high byte, so a stack that overlaps the operand changes where JSR goes
      uint16_t hi_at = (uint16_t)(c->pc - 1);
      push(c, hi_at >> 8);
      push(c, hi_at & 0xFF);
      c->pc = (uint16_t)((ea & 0xFF) | (rd(c, hi_at) << 8));
      break;
    }
    case OP_RTS:
      c->pc = pop(c);
      c->pc = (uint16_t)((c->pc | (pop(c) << 8)) + 1);
      break;
    case OP_RTI:
      c->p = (uint8_t)((pop(c) | FU) & ~FB);
      c->pc = pop(c);
      c->pc = (uint16_t)(c->pc | (pop(c) << 8));
      break;
    case OP_BRK:
      c->pc++;
      interrupt(c, 0xFFFE, 1);
      break;

    // stack
    case OP_PHA: push(c, c->a); break;
    case OP_PHP: push(c, c->p | FB | FU); break;
    case OP_PLA: c->a = pop(c); setnz(c, c->a); break;
    case OP_PLP: c->p = (uint8_t)((pop(c) | FU) & ~FB); break;

    // registers
    case OP_TAX: c->x = c->a; setnz(c, c->x); break;
    case OP_TAY: c->y = c->a; setnz(c, c->y); break;
    case OP_TXA: c->a = c->x; setnz(c, c->a); break;
    case OP_TYA: c->a = c->y; setnz(c, c->a); break;
    case OP_TSX: c->x = c->s; setnz(c, c->x); break;
    case OP_TXS: c->s = c->x; break;
    case OP_INX: c->x++; setnz(c, c->x); break;
    case OP_INY: c->y++; setnz(c, c->y); break;
    case OP_DEX: c->x--; setnz(c, c->x); break;
    case OP_DEY: c->y--; setnz(c, c->y); break;

    // flags
    case OP_CLC: c->p &= ~FC; break;
    case OP_SEC: c->p |= FC; break;
    case OP_CLI: c->p &= ~FI; break;
    case OP_SEI: c->p |= FI; break;
    case OP_CLV: c->p &= ~FV; break;
    case OP_CLD: c->p &= ~FD; break;
    case OP_SED: c->p |= FD; break;

    case OP_NOP: break;   // the operand forms skip their bytes; no dummy read is issued

    case OP_JAM:
    default:
      c->jammed = 1;
      c->pc--;
      cyc = 2;
      break;
  }

  c->total_cycles += (uint32_t)cyc;
  c->instructions++;
  return cyc;
}

int m6502_run(M6502 *c, int budget) {
  int used = 0;
  while (used < budget) {
    used += m6502_step(c);
  }
  return used;
}
