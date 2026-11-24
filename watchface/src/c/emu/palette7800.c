#include "palette7800.h"

// cos, sin (x1000) of each chroma hue's angle
static const int16_t HUE_COS_SIN[15][2] = {
  {866, -500}, {995, -105}, {951, 309}, {743, 669}, {407, 914},
  {0, 1000}, {-407, 914}, {-743, 669}, {-951, 309}, {-995, -105},
  {-866, -500}, {-588, -809}, {-208, -978}, {208, -978}, {588, -809},
};

static int level(int v) {   // 0..1000 -> 2-bit channel
  int c = v * 255 / 1000;
  if (c < 0) c = 0;
  if (c > 255) c = 255;
  int l = (c + 42) / 85;
  return l > 3 ? 3 : l;
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
