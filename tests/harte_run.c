// Runs packed Harte SingleStepTests against the watch's m6502 core.
//
//   cc -O2 -I<watch>/src/c/emu tests/harte_run.c <watch>/src/c/emu/m6502.c -o build/harte_run
//   build/harte_run build/harte            (all opcodes)
//   build/harte_run build/harte a9 6d      (some)
//
// Checks registers, every listed RAM byte and the cycle count.

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "m6502.h"

static uint8_t mem[65536];

static uint8_t rd_cb(void *ctx, uint16_t a) { (void)ctx; return mem[a]; }
static void wr_cb(void *ctx, uint16_t a, uint8_t v) { (void)ctx; mem[a] = v; }

typedef struct {
  uint16_t pc;
  uint8_t s, a, x, y, p, n;
  uint16_t addr[64];
  uint8_t val[64];
} State;

static const uint8_t *read_state(const uint8_t *b, State *s) {
  s->pc = (uint16_t)(b[0] | (b[1] << 8));
  s->s = b[2]; s->a = b[3]; s->x = b[4]; s->y = b[5]; s->p = b[6]; s->n = b[7];
  b += 8;
  for (int i = 0; i < s->n; i++, b += 3) {
    s->addr[i] = (uint16_t)(b[0] | (b[1] << 8));
    s->val[i] = b[2];
  }
  return b;
}

static int run_file(const char *path, int op, int *failed_out, int verbose) {
  FILE *f = fopen(path, "rb");
  if (!f) return -1;
  fseek(f, 0, SEEK_END);
  long len = ftell(f);
  fseek(f, 0, SEEK_SET);
  uint8_t *buf = malloc((size_t)len);
  if (fread(buf, 1, (size_t)len, f) != (size_t)len) { fclose(f); free(buf); return -1; }
  fclose(f);

  uint32_t count = buf[0] | (buf[1] << 8) | (buf[2] << 16) | ((uint32_t)buf[3] << 24);
  const uint8_t *b = buf + 4;
  int failed = 0, shown = 0;
  M6502 cpu;
  for (uint32_t t = 0; t < count; t++) {
    State in, out;
    b = read_state(b, &in);
    b = read_state(b, &out);
    int want_cycles = *b++;

    m6502_init(&cpu, rd_cb, wr_cb, NULL);
    for (int i = 0; i < 256; i++) {
      cpu.rd_page[i] = &mem[i << 8];
      cpu.wr_page[i] = &mem[i << 8];
    }
    for (int i = 0; i < in.n; i++) mem[in.addr[i]] = in.val[i];
    cpu.pc = in.pc; cpu.s = in.s; cpu.a = in.a; cpu.x = in.x; cpu.y = in.y; cpu.p = in.p;

    int cycles = m6502_step(&cpu);

    int bad = cpu.pc != out.pc || cpu.s != out.s || cpu.a != out.a || cpu.x != out.x ||
              cpu.y != out.y || cpu.p != out.p || cycles != want_cycles;
    for (int i = 0; i < out.n; i++) {
      if (mem[out.addr[i]] != out.val[i]) bad = 1;
    }
    if (bad) {
      failed++;
      if (verbose && shown < 3) {
        shown++;
        printf("  %02x test %u: got pc=%04x s=%02x a=%02x x=%02x y=%02x p=%02x cyc=%d\n",
               op, t, cpu.pc, cpu.s, cpu.a, cpu.x, cpu.y, cpu.p, cycles);
        printf("          want pc=%04x s=%02x a=%02x x=%02x y=%02x p=%02x cyc=%d\n",
               out.pc, out.s, out.a, out.x, out.y, out.p, want_cycles);
        printf("          from pc=%04x s=%02x a=%02x x=%02x y=%02x p=%02x\n",
               in.pc, in.s, in.a, in.x, in.y, in.p);
        for (int i = 0; i < out.n; i++) {
          if (mem[out.addr[i]] != out.val[i]) {
            printf("          mem[%04x]=%02x want %02x\n", out.addr[i], mem[out.addr[i]], out.val[i]);
          }
        }
      }
    }
    for (int i = 0; i < out.n; i++) mem[out.addr[i]] = 0;
    for (int i = 0; i < in.n; i++) mem[in.addr[i]] = 0;
  }
  free(buf);
  *failed_out = failed;
  return (int)count;
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s DIR [op ...]\n", argv[0]);
    return 2;
  }
  int total_failed_ops = 0, total_tests = 0, total_failed = 0;
  for (int op = 0; op < 256; op++) {
    char hex[3];
    snprintf(hex, sizeof(hex), "%02x", op);
    if (argc > 2) {
      int want = 0;
      for (int i = 2; i < argc; i++) if (!strcmp(argv[i], hex)) want = 1;
      if (!want) continue;
    }
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s.bin", argv[1], hex);
    int failed = 0;
    int n = run_file(path, op, &failed, argc > 2);
    if (n < 0) continue;
    total_tests += n;
    total_failed += failed;
    if (failed) {
      total_failed_ops++;
      printf("%02x: %d of %d failed\n", op, failed, n);
    }
  }
  printf("%d tests, %d failed, %d opcodes with failures\n", total_tests, total_failed, total_failed_ops);
  return total_failed ? 1 : 0;
}
