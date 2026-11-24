// Klaus Dormann's 6502 functional test against the watch's m6502 core.
//
//   cc -O2 -Iwatchface/src/c/emu tests/klaus_run.c watchface/src/c/emu/m6502.c -o build/klaus_run
//   build/klaus_run 6502_functional_test.bin [success_pc]
//
// The test loops on `JMP *` when it finishes; $3469 is success in the stock
// build. Anything else is the address of the failing check.

#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "m6502.h"

static uint8_t mem[65536];

static uint8_t rd_cb(void *ctx, uint16_t a) { (void)ctx; return mem[a]; }
static void wr_cb(void *ctx, uint16_t a, uint8_t v) { (void)ctx; mem[a] = v; }

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s image.bin [success_pc_hex]\n", argv[0]);
    return 2;
  }
  unsigned success = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 16) : 0x3469;
  FILE *f = fopen(argv[1], "rb");
  if (!f || fread(mem, 1, sizeof(mem), f) != sizeof(mem)) {
    fprintf(stderr, "cannot read %s\n", argv[1]);
    return 2;
  }
  fclose(f);

  M6502 cpu;
  m6502_init(&cpu, rd_cb, wr_cb, NULL);
  for (int i = 0; i < 256; i++) {
    cpu.rd_page[i] = &mem[i << 8];
    cpu.wr_page[i] = &mem[i << 8];
  }
  cpu.pc = 0x0400;

  clock_t t0 = clock();
  uint16_t last = 0xFFFF;
  for (;;) {
    m6502_step(&cpu);
    if (cpu.pc == last) break;   // JMP * trap
    last = cpu.pc;
    // a trap is JMP * : the PC repeats after one instruction
    if (mem[cpu.pc] == 0x4C && mem[cpu.pc + 1] == (cpu.pc & 0xFF) && mem[cpu.pc + 2] == (cpu.pc >> 8)) {
      break;
    }
  }
  double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
  printf("stopped at $%04X after %u instructions, %u cycles (%.2fs, %.1f emulated MHz)\n",
         cpu.pc, cpu.instructions, cpu.total_cycles, secs, cpu.total_cycles / secs / 1e6);
  if (cpu.pc == success) {
    printf("PASS\n");
    return 0;
  }
  printf("FAIL\n");
  return 1;
}
