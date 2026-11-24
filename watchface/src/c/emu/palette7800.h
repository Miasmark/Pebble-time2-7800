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

// The same colours before quantising, 0-255 a channel.
void palette7800_build_rgb(uint8_t rgb[256][3]);

// A 320-mode pair of pixels, left code a and right code b, as a television
// shows it (artifact colours, artifact_table.h), on the Pebble's 64 colours;
// a == b gives that colour's own entry in the palette.
uint8_t palette7800_pair(const uint8_t rgb[256][3], uint8_t a, uint8_t b);

#endif
