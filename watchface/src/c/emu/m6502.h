// m6502 -- a small NMOS 6502 interpreter for the 7800's "Sally" (6502C).
//
// Portable C99, no allocation, no globals: the same file builds for the
// Pebble watch and for the host test harness.
//
// Instruction-level, not cycle-level: each instruction runs to completion and
// reports how many cycles it took (base count, plus page-crossing and branch
// penalties). The machine around it uses those counts for MARIA's DMA,
// WSYNC and the RIOT timer. Dummy bus cycles (the extra reads and the first
// write of a read-modify-write) are not issued, so an I/O register with a
// read side effect sees one access per instruction, not two.
//
// Covered: all documented opcodes, NMOS decimal mode (flags as the NMOS part
// sets them), and the undocumented opcodes, the "unstable" ones (ANE, LXA,
// SHA, SHX, SHY, TAS) with the commonly measured behaviour. JAM halts the CPU
// until reset.

#ifndef M6502_H
#define M6502_H

#include <stdint.h>

#define M6502_FLAG_C 0x01
#define M6502_FLAG_Z 0x02
#define M6502_FLAG_I 0x04
#define M6502_FLAG_D 0x08
#define M6502_FLAG_B 0x10
#define M6502_FLAG_U 0x20
#define M6502_FLAG_V 0x40
#define M6502_FLAG_N 0x80

typedef uint8_t (*m6502_read_fn)(void *ctx, uint16_t addr);
typedef void (*m6502_write_fn)(void *ctx, uint16_t addr, uint8_t value);

typedef struct M6502 {
  uint16_t pc;
  uint8_t a, x, y, s, p;

  uint8_t nmi_pending;   // set by m6502_nmi(), taken at the next instruction boundary
  uint8_t irq_line;      // level-triggered, taken when I is clear
  uint8_t jammed;        // a JAM opcode ran; only m6502_reset() clears it

  // Memory: a page whose pointer is non-NULL is plain memory and is read or
  // written directly; NULL pages go through the callbacks. Point a ROM page's
  // write pointer at NULL and make the callback ignore it.
  const uint8_t *rd_page[256];
  uint8_t *wr_page[256];
  m6502_read_fn read;
  m6502_write_fn write;
  void *ctx;

  uint32_t total_cycles;
  uint32_t instructions;
} M6502;

// Clears the page tables and registers; set up memory, then call m6502_reset().
void m6502_init(M6502 *c, m6502_read_fn read, m6502_write_fn write, void *ctx);

// The reset sequence: S drops by 3 (no writes), I set, PC from $FFFC. 7 cycles.
void m6502_reset(M6502 *c);

// Edge-triggered NMI.
static inline void m6502_nmi(M6502 *c) { c->nmi_pending = 1; }

// Runs one instruction (or takes one pending interrupt). Returns its cycles.
int m6502_step(M6502 *c);

// Runs instructions until at least `budget` cycles have passed. Returns the
// cycles actually used (which can exceed the budget by part of an instruction).
int m6502_run(M6502 *c, int budget);

#endif
