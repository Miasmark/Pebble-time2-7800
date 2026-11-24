// Runs a cartridge on the watch's 7800 core, on the host.
//
//   a78run game.a78 [options]
//     -f N         frames to run (default 60)
//     -s N,M,...   write frames N, M... as PPM (watch colours, 160x192)
//     -o PREFIX    output prefix (default "frame")
//     -r FILE      dump RAM $1800-$27FF after the run
//     -w FIRST     first line of the 192-line window (default 42)
//     -c HHMM      set the clock registers to this time (stands still)
//     -t HHMMSS    set the clock and let it run: a second every 60 frames
//     -l           set CLK_FLAGS bit 4 (the wearer flicked their wrist)
//     -b PCT       battery percent (CLK_BATTERY, default 100)
//     -S STEPS     today's steps (CLK_STEPS, default 0)
//     -g           charging (CLK_FLAGS bit 5)
//     -e N         snapshot every Nth frame (with -s FIRST,LAST as the range)
//     -i FILE      input clip: one byte per frame, b7-4 SWCHA P0 bits, b0 fire
//     -q           quiet
//     -n           step MSTAT polling loops instruction by instruction (no fast-forward)
//     -p HEX       print the median of the 16-bit value at zero-page HEX over
//                  frames 120-200 (probes/dma-count.lua's measurement)
//
// Prints per-frame CPU, DMA and WSYNC time so the watch's budget can be read.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "a7800.h"
#include "palette7800.h"

static uint8_t lut[256];
static uint8_t pix[192][160];
static int window_first = 42;

static void on_line(void *ctx, int line, const uint8_t *colours) {
  (void)ctx;
  int y = line - window_first;
  if (y >= 0 && y < 192) {
    for (int x = 0; x < 160; x++) pix[y][x] = lut[colours[x]];
  }
}

static void write_ppm(const char *path) {
  FILE *f = fopen(path, "wb");
  fprintf(f, "P6 160 192 255\n");
  for (int y = 0; y < 192; y++) {
    for (int x = 0; x < 160; x++) {
      uint8_t v = pix[y][x];
      unsigned char rgb[3] = {(unsigned char)(((v >> 4) & 3) * 85), (unsigned char)(((v >> 2) & 3) * 85),
                              (unsigned char)((v & 3) * 85)};
      fwrite(rgb, 1, 3, f);
    }
  }
  fclose(f);
}

static uint8_t bcd(int v) { return (uint8_t)(((v / 10) << 4) | (v % 10)); }

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s game.a78 [-f N] [-s N,M] [-o PREFIX] [-r FILE] [-w FIRST] [-c HHMM] [-i FILE] [-q]\n", argv[0]);
    return 2;
  }
  int noskip = 0, look = 0, every = 0, battery = 100, charging = 0;
  long steps = 0;
  long run_clock = -1;
  int frames = 60, quiet = 0, clock_hhmm = -1, probe = -1;
  const char *snaps = "", *prefix = "frame", *ramfile = NULL, *inpfile = NULL;
  for (int i = 2; i < argc; i++) {
    if (!strcmp(argv[i], "-f")) frames = atoi(argv[++i]);
    else if (!strcmp(argv[i], "-s")) snaps = argv[++i];
    else if (!strcmp(argv[i], "-o")) prefix = argv[++i];
    else if (!strcmp(argv[i], "-r")) ramfile = argv[++i];
    else if (!strcmp(argv[i], "-w")) window_first = atoi(argv[++i]);
    else if (!strcmp(argv[i], "-c")) clock_hhmm = atoi(argv[++i]);
    else if (!strcmp(argv[i], "-i")) inpfile = argv[++i];
    else if (!strcmp(argv[i], "-q")) quiet = 1;
    else if (!strcmp(argv[i], "-n")) noskip = 1;
    else if (!strcmp(argv[i], "-t")) run_clock = atol(argv[++i]);
    else if (!strcmp(argv[i], "-l")) look = 1;
    else if (!strcmp(argv[i], "-b")) battery = atoi(argv[++i]);
    else if (!strcmp(argv[i], "-S")) steps = atol(argv[++i]);
    else if (!strcmp(argv[i], "-g")) charging = 1;
    else if (!strcmp(argv[i], "-e")) every = atoi(argv[++i]);
    else if (!strcmp(argv[i], "-p")) probe = (int)strtol(argv[++i], NULL, 16);
  }

  FILE *f = fopen(argv[1], "rb");
  if (!f) { perror(argv[1]); return 2; }
  static uint8_t image[0x20000];
  uint32_t len = (uint32_t)fread(image, 1, sizeof(image), f);
  fclose(f);

  uint8_t *clip = NULL;
  long clip_len = 0;
  if (inpfile) {
    FILE *ci = fopen(inpfile, "rb");
    if (!ci) { perror(inpfile); return 2; }
    fseek(ci, 0, SEEK_END); clip_len = ftell(ci); fseek(ci, 0, SEEK_SET);
    clip = malloc((size_t)clip_len);
    if (fread(clip, 1, (size_t)clip_len, ci) != (size_t)clip_len) return 2;
    fclose(ci);
  }

  static A7800 m;
  int err = a7800_load(&m, image, len);
  if (err) { fprintf(stderr, "load failed: %d\n", err); return 1; }
  a7800_power_on(&m);
  if (noskip) m.skip_spins = false;
  palette7800_build(lut);
  a7800_set_output(&m, window_first, 192, on_line, NULL);
  if (clock_hhmm >= 0) {
    m.clock[A7800_CLK_HOUR] = bcd(clock_hhmm / 100);
    m.clock[A7800_CLK_MIN] = bcd(clock_hhmm % 100);
  }

  if (look) m.clock[A7800_CLK_FLAGS] |= 0x10;
  if (charging) m.clock[A7800_CLK_FLAGS] |= 0x20;
  m.clock[A7800_CLK_BATTERY] = (uint8_t)battery;
  m.clock[A7800_CLK_STEPS_HI] = bcd((int)(steps / 10000 % 100));
  m.clock[A7800_CLK_STEPS_MID] = bcd((int)(steps / 100 % 100));
  m.clock[A7800_CLK_STEPS_LO] = bcd((int)(steps % 100));
  int samples[128], nsamples = 0;
  if (probe >= 0 && frames < 201) frames = 201;
  clock_t t0 = clock();
  uint64_t cpu_half = 0, dma_half = 0, wsync_half = 0;
  for (int fr = 0; fr < frames; fr++) {
    if (clip && fr < clip_len) {
      m.swcha_in = (uint8_t)((clip[fr] & 0xF0) | 0x0F);
      m.fire = clip[fr] & 1;
    }
    if (run_clock >= 0) {
      long secs = (run_clock / 10000) * 3600 + ((run_clock / 100) % 100) * 60 + run_clock % 100 + fr / 60;
      m.clock[A7800_CLK_HOUR] = bcd((int)((secs / 3600) % 24));
      m.clock[A7800_CLK_MIN] = bcd((int)((secs / 60) % 60));
      m.clock[A7800_CLK_SEC] = bcd((int)(secs % 60));
    }
    a7800_run_frame(&m);
    if (probe >= 0 && fr + 1 > 120 && fr + 1 <= 200) {
      samples[nsamples++] = m.ram[0x800 + probe] | (m.ram[0x800 + probe + 1] << 8);
    }
    cpu_half += m.stat_cpu_half; dma_half += m.stat_dma_half; wsync_half += m.stat_wsync_half;
    char key[16];
    snprintf(key, sizeof(key), ",%d,", fr);
    char list[512];
    snprintf(list, sizeof(list), ",%s,", snaps);
    int take = strstr(list, key) != NULL;
    if (every > 0) {
      int first = 0, last = -1;
      sscanf(snaps, "%d,%d", &first, &last);
      take = fr >= first && fr <= last && (fr - first) % every == 0;
    }
    if (take) {
      char path[512];
      snprintf(path, sizeof(path), "%s_%04d.ppm", prefix, fr);
      write_ppm(path);
    }
  }
  double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;

  if (probe >= 0) {
    for (int i = 1; i < nsamples; i++)
      for (int j = i; j > 0 && samples[j - 1] > samples[j]; j--) {
        int t = samples[j]; samples[j] = samples[j - 1]; samples[j - 1] = t;
      }
    printf("RESULT median=%d min=%d max=%d n=%d\n", samples[nsamples / 2], samples[0], samples[nsamples - 1], nsamples);
  }
  if (!quiet) {
    double per = (double)A7800_LINES * A7800_HALF_PER_LINE;
    printf("%d frames in %.3fs host (%.0f frames/s)\n", frames, secs, frames / (secs > 0 ? secs : 1e-9));
    printf("per frame: CPU %.1f%%  DMA %.1f%%  WSYNC wait %.1f%%  (of %.0f cycles)\n",
           100.0 * cpu_half / frames / per, 100.0 * dma_half / frames / per,
           100.0 * wsync_half / frames / per, per / 2);
    printf("instructions: %u  pc=$%04X  jammed=%d\n", m.cpu.instructions, m.cpu.pc, m.cpu.jammed);
  }
  if (ramfile) {
    FILE *r = fopen(ramfile, "wb");
    fwrite(m.ram, 1, sizeof(m.ram), r);
    fclose(r);
  }
  free(clip);
  return 0;
}
