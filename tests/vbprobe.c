// How far into the frame does the cartridge's main loop get? Reports, over a
// running minute change, the line on which the PC first reaches ADDR (the
// "done with vertical blank work" loop) after leaving the MSTAT wait.
//
//   vbprobe game.a78 HHMMSS ADDR [frames]
#include <stdio.h>
#include <stdlib.h>
#include "a7800.h"

static uint8_t bcd(int v) { return (uint8_t)(((v / 10) << 4) | (v % 10)); }

int main(int argc, char **argv) {
  if (argc < 4) { fprintf(stderr, "usage: vbprobe game.a78 HHMMSS ADDR [frames]\n"); return 2; }
  FILE *f = fopen(argv[1], "rb");
  static uint8_t img[0x20000];
  size_t len = fread(img, 1, sizeof img, f);
  fclose(f);
  static A7800 m;
  if (a7800_load(&m, img, (uint32_t)len)) { fprintf(stderr, "load failed\n"); return 1; }
  a7800_power_on(&m);
  long t = atol(argv[2]);
  long secs = (t / 10000) * 3600 + (t / 100 % 100) * 60 + t % 100;
  unsigned addr = (unsigned)strtoul(argv[3], NULL, 16);
  int frames = argc > 4 ? atoi(argv[4]) : 600;
  int worst = 0, worst_frame = 0, hist[300] = {0};
  for (int fr = 0; fr < frames; fr++) {
    long s = secs + fr / 60;
    m.clock[A7800_CLK_SEC] = bcd((int)(s % 60));
    m.clock[A7800_CLK_MIN] = bcd((int)(s / 60 % 60));
    m.clock[A7800_CLK_HOUR] = bcd((int)(s / 3600 % 24));
    m.clock[A7800_CLK_BATTERY] = 100;
    int seen = -1;
    do {
      a7800_run_line(&m);
      if (seen < 0 && m.cpu.pc >= addr && m.cpu.pc < addr + 5) seen = m.line;
    } while (m.line != 0);
    if (seen >= 0 && fr > 2) {
      int l = seen < 100 ? seen + 263 : seen;   // lines after 0 belong to this vblank
      hist[l - 200 < 0 ? 0 : (l - 200 > 299 ? 299 : l - 200)]++;
      if (l > worst) { worst = l; worst_frame = fr; }
    }
  }
  printf("worst: line %d (%d past line 0) at frame %d\n", worst, worst - 263, worst_frame);
  return 0;
}
