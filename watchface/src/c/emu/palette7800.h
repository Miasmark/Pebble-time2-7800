// MARIA colour bytes (hue << 4 | luma) to the Pebble's 64 colours.
//
// An NTSC approximation via YIQ: hue 0 is grey, hues 1-15 step 24 degrees from
// gold, chroma eased in over the darkest lumas so dark colours stay dark. Each
// channel is then quantised to the Pebble's 2 bits. The result is a GColor8
// byte (0b11RRGGBB).

#ifndef PALETTE7800_H
#define PALETTE7800_H

#include <stdint.h>

void palette7800_build(uint8_t lut[256]);

#endif
