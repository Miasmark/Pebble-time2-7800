#include "palette7800.h"
#include "artifact_table.h"

// cos, sin (x1000) of each chroma hue's angle
static const int16_t HUE_COS_SIN[15][2] = {
  {866, -500}, {995, -105}, {951, 309}, {743, 669}, {407, 914},
  {0, 1000}, {-407, 914}, {-743, 669}, {-951, 309}, {-995, -105},
  {-866, -500}, {-588, -809}, {-208, -978}, {208, -978}, {588, -809},
};

static int clamp255(int c) {
  return c < 0 ? 0 : (c > 255 ? 255 : c);
}

static int level255(int c) {   // 0..255 -> 2-bit channel
  int l = (clamp255(c) + 42) / 85;
  return l > 3 ? 3 : l;
}

static int level(int v) {   // 0..1000 -> 2-bit channel
  return level255(v * 255 / 1000);
}

static void code_rgb(int b, int out[3]) {   // 0..1000 a channel, unclamped
  int hue = b >> 4;
  int lum = b & 0x0F;
  int y = 30 + 910 * lum / 15;
  int sat = 200 + 350 * (lum < 6 ? lum : 6) / 6;
  int i = 0, q = 0;
  if (hue) {
    i = sat * HUE_COS_SIN[hue - 1][0] / 1000;
    q = sat * HUE_COS_SIN[hue - 1][1] / 1000;
  }
  out[0] = y + (956 * i + 621 * q) / 1000;
  out[1] = y - (272 * i + 647 * q) / 1000;
  out[2] = y + (-1106 * i + 1703 * q) / 1000;
}

void palette7800_build_rgb(uint8_t rgb[256][3]) {
  for (int b = 0; b < 256; b++) {
    int v[3];
    code_rgb(b, v);
    for (int c = 0; c < 3; c++) rgb[b][c] = (uint8_t)clamp255(v[c] * 255 / 1000);
  }
}

uint8_t palette7800_pair(const uint8_t rgb[256][3], uint8_t a, uint8_t b) {
  int l[3];
  for (int c = 0; c < 3; c++) {
    l[c] = level255(((int)rgb[a][c] + rgb[b][c] + 1) / 2 + ARTIFACT_D[a][c] - ARTIFACT_D[b][c]);
  }
  return (uint8_t)(0xC0 | (l[0] << 4) | (l[1] << 2) | l[2]);
}

void palette7800_build(uint8_t lut[256]) {
  for (int b = 0; b < 256; b++) {
    int hue = b >> 4;
    int lum = b & 0x0F;
    int y = 30 + 910 * lum / 15;
    int sat = 200 + 350 * (lum < 6 ? lum : 6) / 6;
    int i = 0, q = 0;
    if (hue) {
      i = sat * HUE_COS_SIN[hue - 1][0] / 1000;
      q = sat * HUE_COS_SIN[hue - 1][1] / 1000;
    }
    int r = y + (956 * i + 621 * q) / 1000;
    int g = y - (272 * i + 647 * q) / 1000;
    int bl = y + (-1106 * i + 1703 * q) / 1000;
    lut[b] = (uint8_t)(0xC0 | (level(r) << 4) | (level(g) << 2) | level(bl));
  }
}
