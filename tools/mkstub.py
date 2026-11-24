#!/usr/bin/env python3
"""Build the clock cartridge: 32K of 7800 that stages every minute change by itself.

    python tools/mkstub.py --toolkit ../a7800-toolkit -o build/clockstub.a78

Everything it draws comes from the clock registers (docs/clock-registers.md),
read every frame -- never from a frame count, because the watch runs only short
bursts of frames and sleeps in between. Each hour (hour mod 4) has its own show,
from :57 to :02:

    0  formation   two attackers fly in, three volleys, the old minute explodes,
                   the new one fades in, they leave -- all through a falling
                   three-layer star field
    1  drill rig   a rig bores up through the rock under the clock, leaving a
                   shaft, the old minute shakes and crumbles, the rig pushes the
                   new one up into place and sinks away, and the rock grows
                   back over the shaft
    2  bomber run  the ground scrolls; a bomber climbs in, its bomb takes out the
                   old minute, the new one fades in as it flies off
    3  lander      a lander comes down, lifts the old minute away, and sets the
                   new one down gently in a puff of dust, with the Earth rising
                   over the lunar hills behind

Under it all: a cruiser that crosses the bottom once an hour, a step a minute,
and a lives-and-score bar in character mode -- one life per 20% of battery
(white; green while charging; red on the last one) and today's steps as the
1UP score. The cruiser and the lives are the hour's vehicle: ships, rigs,
planes or landers. The show is the hour's plus CLK_CHANNEL, which the watch
moves on at each wrist flick; whenever the show changes (a flick, the top of
the hour, power-on) the picture goes to 0.4 s of TV static first.

Each show has a backdrop -- deep space, earth, farmland, the moon: a
background colour, palette 7 and a list of items drawn first in each zone. The
stars and the farmland move and are rebuilt every frame; the still ones are
built once into each set of lists and kept at their heads.

Within a second it counts frames, starting again whenever the seconds register
changes, so it cannot drift from the watch's clock. Real MARIA display lists
are built a frame ahead, in two sets that take turns (the display list list
and the colours switch over in vertical blank), from 160A graphics in ROM laid
out line-planar for 16-line zones. Things that move up and down are placed to the
line by splitting them over two zones with shifted graphics pointers, and holey
DMA (on in every zone) blanks the pages either side of the graphics -- the way
7800 games position sprites vertically. Assembled with the a7800 toolkit's
tools/asm.py, given a header by its newgame.header().

Around the shows: the hour in 12- or 24-hour form as the watch is set; the
best day's steps as the HI score, and NEW HI the first time today beats it;
GAME OVER when the battery's lives run out; STAGE CLEAR as the cruiser drives
off the end of the hour; faint snow below the clock while the phone is away;
and no shows at all while the watch is in Quiet Time (CLK_FLAGS bit 3).

32K, linear ($8000-$FFFF), with two graphics blocks, at $E000 and $A000. No
signature: the watch starts it without a BIOS, as the a7800 fork of MAME does
in its no-BIOS mode.
"""
import argparse
import math
import os
import sys

ROM_BASE = 0x8000      # code from here (a 32K cartridge, $8000-$FFFF)
GFX2 = 0xA000          # a second 16-page graphics block: the bar's characters,
                       # the words, the snow. Graphics blocks sit at $A000, $C000
                       # or $E000: holey DMA blanks the 4K either side of them
NOISE = 0xD000         # 16 pages of random bytes for channel-change static
GFX = 0xE000           # 16 pages: page $E0 + (15 - line) holds line `line` of every glyph
TABLES = 0xF000        # the display list list and tables
DL_ZONES = 16          # 16 x 16 lines covers every DMA line (17-258)
DLBASE = 0x1900        # 64 bytes (15 entries) per zone's display list: $1900-$1CFF
DLBASE_B = 0x2200      # the second set, for zones 1-11: built while the first is shown
DL_STRIDE = 64
HUD_ZONE = 12          # lines 210-225 (watch rows 168-183)
MAP1, MAP2 = 0x1D00, 0x1D10    # character maps for the bar's lives and 1UP score
MAP3 = 0x1D40                  # ... and the HI score

BIG_DIGITS = [
    [0x1F, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1F],
    [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F],
    [0x1F, 0x01, 0x01, 0x1F, 0x10, 0x10, 0x1F],
    [0x1F, 0x01, 0x01, 0x0F, 0x01, 0x01, 0x1F],
    [0x11, 0x11, 0x11, 0x1F, 0x01, 0x01, 0x01],
    [0x1F, 0x10, 0x10, 0x1F, 0x01, 0x01, 0x1F],
    [0x1F, 0x10, 0x10, 0x1F, 0x11, 0x11, 0x1F],
    [0x1F, 0x01, 0x01, 0x02, 0x04, 0x04, 0x04],
    [0x1F, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x1F],
    [0x1F, 0x11, 0x11, 0x1F, 0x01, 0x01, 0x1F],
]
COLON = [0x00, 0x04, 0x04, 0x00, 0x04, 0x04, 0x00]

# 16 x 16 pictures; digits are colours 1-3 of whatever palette draws them, '.' transparent
SHIP = [   # starts 4 lines down
    ".......33.......",
    "......3113......",
    "......1221......",
    ".....122221.....",
    "....12222221....",
    "...1221111221...",
    "..122122221221..",
    ".11.1.1111.1.11.",
    ".3...3....3...3.",
]
ROCK = [   # palette 5: dark brown, tan, grey
    "2211122221112222",
    "2111112211111221",
    "1111111111111111",
    "1222211122222111",
    "2222221222222211",
    "2223221222232211",
    "1222211122222111",
    "1111111111111111",
    "2211122222111222",
    "2111223222112221",
    "1111122221111111",
    "1222111111222211",
    "2222211112222221",
    "2232211122232221",
    "1222111111222211",
    "1111111111111111",
]
DRILL = [  # palette 1: white, grey, light blue; the bit's two turns
    [".......33.......", "......3223......", ".....322223.....", "......3223......",
     ".....322223.....", "......2222......", ".....111111.....", "....11111111....",
     "...1122222211...", "...1211331121...", "...1211331121...", "...1222222221...",
     "..112222222211..", "..1.1.1.1.1.1...", "..1111111111111.", "...1.1.1.1.1.1.."],
    [".......22.......", "......2332......", ".....233332.....", "......2332......",
     ".....233332.....", "......2222......", ".....111111.....", "....11111111....",
     "...1122222211...", "...1211331121...", "...1211331121...", "...1222222221...",
     "..112222222211..", "...1.1.1.1.1.1..", "..1111111111111.", "..1.1.1.1.1.1..."],
]
PLANE = [  # palette 1, seen from above
    ".......33.......",
    "......3113......",
    "......1221......",
    "......1221......",
    ".....112211.....",
    "1111111221111111",
    "1222222222222221",
    "1111111221111111",
    "......1221......",
    "......1221......",
    "......1221......",
    ".....112211.....",
    "....11122111....",
    "......1..1......",
]
BOMB = [   # palette 3: red, yellow, white; starts 6 lines down
    ".......33.......",
    ".......22.......",
    "......2112......",
    ".......11.......",
    ".......11.......",
]
BOULDER = [  # palette 5; starts 4 lines down
    ".....111111.....",
    "...1122222211...",
    "..122233322221..",
    "..122333332221..",
    ".11222333222211.",
    ".12222222222221.",
    ".11222222222211.",
    "..111222222111..",
    "....11111111....",
]
DUNE = [   # palette 6: gold rim, sand (colour 2 is black, for the bar); starts 5 lines down
    "......1111......",
    "....11111111....",
    "..111133331111..",
    ".11133333333311.",
    "1113333333333311",
    "3333333333333333",
]
LANDER = [  # palette 1
    "......2222......",
    "....22111122....",
    "...2113333112...",
    "...2133333312...",
    "...2111111112...",
    "....22222222....",
    "...2.1.22.1.2...",
    "..2..1....1..2..",
    ".2...1....1...2.",
    "22............22",
]
FLAME = [  # palette 3, under the lander's engine; two flickers
    ["................"] * 7 + ["......3333......", "......2332......", ".......22.......",
                                ".......21.......", ".......11.......", "........1......."],
    ["................"] * 7 + ["......3333......", ".......33.......", "......1221......",
                                "......1..1......", "................", "................"],
]
DUST = [   # palette 5; starts 10 lines down
    "....2.....2.....",
    "..2212..21122...",
    ".122221.2222221.",
    "1111111111111111",
]

# the backdrops, palette 7 unless noted (each show sets its own palette 7)
ROOTS = [  # drill: dark red-brown, tan
    ".2.....2....2...",
    ".2....22....2...",
    ".22...2....22...",
    "..2...2....2....",
    "..2..22....2....",
    "..21.2.....21...",
    "...2.2......2...",
    "...2.1......2...",
    "...1........1...",
    "...1........1...",
]
PEBBLES = [  # drill: grey stones shaded tan
    "................",
    "..33............",
    ".3332.......3...",
    ".2322......332..",
    "..22........2...",
    "................",
    "........33......",
    ".......3332.....",
    ".......2222.....",
    "........22......",
    "................",
    "...3............",
    "..322...........",
    "...2..........3.",
    "..............2.",
]
FOSSIL = [  # drill: an ammonite
    "................",
    ".....222222.....",
    "...2233333322...",
    "..233222222332..",
    ".23322333322332.",
    ".23223322233232.",
    ".23232322323232.",
    ".23232332323232.",
    ".23223222332232.",
    ".2332233332232..",
    "..233222222332..",
    "...2233333322...",
    ".....222222.....",
]
TREES = [  # bomber, from above: green
    "................",
    "...11.....11....",
    "..1111...1111...",
    "..1111...1111...",
    "...11.....11....",
    "......11........",
    ".....1111.......",
    ".....1111.......",
    "......11........",
]
FIELD = [  # bomber: furrows
    "2222222222222222",
    "................",
    "2222222222222222",
    "................",
    "2222222222222222",
    "................",
    "2222222222222222",
    "................",
    "2222222222222222",
    "................",
    "2222222222222222",
]
HOUSE = [  # bomber: a red roof and its ridge, a tree beside it
    "................",
    "...33333333.....",
    "...33333333..11.",
    "...22222222.1111",
    "...33333333.1111",
    "...33333333..11.",
]
SPARK = ["...", ".3.", "333", ".3."][1:]   # lander: a bright star, one byte wide


def strata_tile():
    """80 x 16, repeating every 80 pixels: a wavy dark vein (colour 1) over a tan
    band two lines deep, all in the top 8 lines -- placed 8 lines down a zone, so
    holey DMA skips the other 8."""
    lines = [[0] * 80 for _ in range(16)]
    for x in range(80):
        y = 3 + int(round(1.5 * math.sin(x * 2 * math.pi / 80) + 0.6 * math.sin(x * 6 * math.pi / 80)))
        lines[y][x] = 1
        lines[y + 1][x] = 2
        if (x // 4) % 4:
            lines[y + 2][x] = 2
        if x % 11 == 3 and y + 3 < 8:
            lines[y + 3][x] = 2
        if x % 17 == 9:
            lines[y - 1][x] = 1
    return lines


def horizon_tile():
    """80 x 16, palette 5 greys, repeating every 80 pixels: the lunar horizon --
    a sunlit rim over rolling hills, the ground below darkening toward the
    viewer, with craters."""
    lines = [[0] * 80 for _ in range(16)]
    top = [4 + int(round(1.8 * math.sin(x * 2 * math.pi / 80) + 0.9 * math.sin(x * 6 * math.pi / 80 + 1)))
           for x in range(80)]
    for x in range(80):
        lines[top[x]][x] = 3                       # sunlit rim
        for y in range(top[x] + 1, 16):
            lines[y][x] = 2 if y < 11 else 1
    for cx, cy, rx, ry in ((14, 9, 7, 1), (50, 12, 9, 2), (68, 8, 4, 1), (32, 14, 5, 1)):
        for x in range(cx - rx, cx + rx + 1):
            for y in range(cy - ry, cy + ry + 1):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                    lines[y][x % 80] = 1
            yb = cy + ry + 1                       # lit far wall
            if yb < 16 and abs(x - cx) < rx:
                lines[yb][x % 80] = 3
    return lines


def regolith_tile():
    """40 x 16, palette 5, repeating every 40 pixels: the dark foreground ground,
    speckled, with a few small craters."""
    lines = [[1] * 40 for _ in range(16)]
    for cx, cy, rx, ry in ((9, 4, 5, 1), (28, 11, 6, 2), (18, 14, 3, 1)):
        for x in range(cx - rx, cx + rx + 1):
            for y in range(cy - ry, cy + ry + 1):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                    lines[y % 16][x % 40] = 0 if abs(y - cy) < ry or ry == 1 else 1
            yb = cy + ry + 1
            if abs(x - cx) < rx:
                lines[yb % 16][x % 40] = 2
    seed = 9
    for _ in range(24):                            # specks, kept dim behind the date
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x, y = (seed >> 8) % 40, (seed >> 16) % 16
        if lines[y][x] == 1:
            lines[y][x] = 2
    return lines


def value_noise(seed, scale):
    """Smooth 2D noise in 0..1 from a hashed lattice, bilinearly interpolated."""
    def h(i, j):
        n = (i * 374761393 + j * 668265263 + seed * 2147483647) & 0xFFFFFFFF
        n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
        return (n & 0xFFFF) / 65535.0
    def f(x, y):
        x, y = x / scale, y / scale
        i, j = int(math.floor(x)), int(math.floor(y))
        fx, fy = x - i, y - j
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        top = h(i, j) * (1 - fx) + h(i + 1, j) * fx
        bot = h(i, j + 1) * (1 - fx) + h(i + 1, j + 1) * fx
        return top * (1 - fy) + bot * fy
    return f


def earth_rows():
    """56 x 28: the top of the Earth, rising -- ocean (1), land (2), cloud (3),
    lit from the left with the night side (right) dark inside a thin rim; the
    bottom few rows go behind the hills."""
    rows = [[0] * 56 for _ in range(28)]
    land = value_noise(7, 7.0)
    land2 = value_noise(8, 3.5)
    cloud = value_noise(19, 4.0)
    streak = value_noise(23, 9.0)
    r = 28.0
    for y in range(28):
        for x in range(56):
            dx, dy = x + 0.5 - 28, 28 - (y + 0.5)
            if dx * dx + dy * dy > r * r:
                continue
            u, v = dx / r, dy / r
            if u > 0.62 * math.sqrt(max(0.0, 1 - v * v)):
                # night: dark, but the limb keeps a thin blue rim of
                # atmosphere so the Earth's outline stays round
                if dx * dx + dy * dy > (r - 1.3) ** 2:
                    rows[y][x] = 1
                continue
            c = 1
            if 0.65 * land(x, y) + 0.35 * land2(x, y) > 0.56:
                c = 2
            # clouds: wisps drawn out along the latitude, and the polar cap
            if cloud(x * 0.6, y * 1.6) * 0.7 + streak(x, y) * 0.3 > 0.66 or v > 0.93:
                c = 3
            rows[y][x] = c
    return rows


EARTH = earth_rows()

# wide tiles, after the characters in the glyph pages: (name, 16 lines, bytes wide)
WIDE = [("STRATA", strata_tile(), 20), ("HORIZON", horizon_tile(), 20),
        ("REGOLITH", regolith_tile(), 10),
        ("EARTH_T", EARTH[:16], 14), ("EARTH_B", EARTH[16:] + [[0] * 56] * 4, 14)]


def pw(palette, nbytes):
    """A display list entry's palette | width byte (a 4-byte header: 1-31 bytes;
    a width field of 0 would mark a 5-byte header instead)."""
    assert 1 <= nbytes <= 31, nbytes
    return (palette << 5) | ((32 - nbytes) & 0x1F)


# the words, in the clock's square 5x7 font, each font pixel 2 wide and 2 tall,
# coloured like the digits (palette 0): yellow, orange, red from the top
LETTERS = {
    "G": [0x1F, 0x10, 0x10, 0x17, 0x11, 0x11, 0x1F], "A": [0x1F, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11], "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
    "O": [0x1F, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1F], "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
    "R": [0x1F, 0x11, 0x11, 0x1F, 0x14, 0x12, 0x11], "S": [0x1F, 0x10, 0x10, 0x1F, 0x01, 0x01, 0x1F],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04], "C": [0x1F, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
    "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F], "N": [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11], "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "I": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F],
}


def word_lines(word):
    """(16 lines, bytes wide) for a word, 12 pixels a letter."""
    width = len(word) * 12 - 2
    nb = (width + 3) // 4
    lines = [[0] * (nb * 4) for _ in range(16)]
    for k, ch in enumerate(word):
        for r, bits in enumerate(LETTERS[ch]):
            colour = 3 if r < 2 else (2 if r < 5 else 1)
            for c in range(5):
                if bits & (0x10 >> c):
                    for dy in range(2):
                        for dx in range(2):
                            lines[1 + 2 * r + dy][k * 12 + c * 2 + dx] = colour
    return lines, nb


def snow_tile():
    """128 x 16, palette 1 (white, grey, light blue): sparse snow for a lost
    phone link."""
    lines = [[0] * 128 for _ in range(16)]
    seed = 1986
    for _ in range(60):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x, y = (seed >> 8) % 128, (seed >> 16) % 16
        lines[y][x] = (1, 2, 2, 3)[(seed >> 4) & 3]
    return lines


GLYPHS = []      # (name, 16 lines x 16 pixels)


def blank():
    return [[0] * 16 for _ in range(16)]


def picture(rows, top=0):
    lines = blank()
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch not in ".":
                lines[top + r][c] = int(ch)
    return lines


def glyph_lines(rows):
    """A 5x7 font glyph as 16 lines of 16 pixels: each font pixel 3 wide and 2 tall;
    rows 0-1 colour 3, 2-4 colour 2, 5-6 colour 1."""
    lines = blank()
    for r, bits in enumerate(rows):
        colour = 3 if r < 2 else (2 if r < 5 else 1)
        for c in range(5):
            if bits & (0x10 >> c):
                for dy in range(2):
                    for dx in range(3):
                        lines[1 + 2 * r + dy][c * 3 + dx] = colour
    return lines


def shot_lines(phase):
    """A 2-pixel bolt, 4 lines tall, `phase` * 4 lines down: white head, yellow tail."""
    lines = blank()
    for i, colour in enumerate((3, 3, 2, 2)):
        lines[phase * 4 + i][7] = lines[phase * 4 + i][8] = colour
    return lines


def boom_lines(frame):
    """Two frames of a blast: spokes, then scattered sparks."""
    lines = blank()
    cx, cy = 7.5, 7.5
    if frame == 0:
        for k in range(8):
            a = k * math.pi / 4 + 0.2
            for r in range(1, 8):
                x, y = int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a) * 0.9))
                if 0 <= x < 16 and 0 <= y < 16:
                    lines[y][x] = 3 if r < 3 else (2 if r < 5 else 1)
        for y in range(6, 10):
            for x in range(6, 10):
                lines[y][x] = 3
    else:
        seed = 7800
        for _ in range(60):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            a = (seed >> 8) % 360 * math.pi / 180
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            r = 3 + (seed >> 8) % 5
            x, y = int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))
            if 0 <= x < 16 and 0 <= y < 16:
                lines[y][x] = 1 if r > 5 else 2
    return lines


def star_lines(layer):
    """The formation hour's backdrop, palette 7, drawn one byte wide: a far star is
    one dim pixel, a middle one a grey pixel, a near one a 2-line streak falling
    head first (white head under a grey tail)."""
    lines = blank()
    if layer < 2:
        lines[0][0] = layer + 1
    else:
        lines[0][0], lines[1][0] = 2, 3
    return lines


# the star field: (x, base line, layer), 8 per layer. Lines count from zone 0's
# top and wrap at 256; a star shows while its line is 16-191 (zones 1-11)
def star_table():
    seed, out = 1986, []
    for layer in range(3):
        for i in range(8):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            x = 2 + i * 20 + (seed >> 12) % 17          # one per 20-pixel column
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            out.append((x, (seed >> 9) & 0xFF, layer))
    return out


STARS = star_table()

# palette | width bytes for display list entries
PW_STAR = 0xFF            # palette 7, 1 byte
PW_P7 = 0xFC              # palette 7, 4 bytes (a 16-pixel glyph)
PW_WIDE7 = pw(7, 20)      # the strata
PW_HORIZON = pw(5, 20)
PW_REGOLITH = pw(5, 10)
PW_EARTH = pw(7, 14)

# layers: 0-2 the stars, 3 still, 4 the bomber's ground
L_STILL, L_GROUND = 3, 4


def bomber_ground():
    """The farmland: fields, trees and houses in palette 7, and the boulders
    (palette 5) and mounds (palette 6) between them -- all on the ground layer,
    so they scroll and wrap together, down to the bar."""
    seed, out, kinds = 2026, [], ["FIELD", "TREES", "FIELD", "TREES", "HOUSE"]
    for i in range(18):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = 4 + ((i * 53 + (seed >> 10) % 40) % 140)
        out.append((x, i * 14 + (seed >> 20) % 6, L_GROUND, kinds[i % 5], PW_P7))
    for j, x in enumerate((12, 130, 50, 146, 84, 30, 110, 66, 140, 20)):
        boulder = j % 2 == 0
        out.append((x, j * 26 + 7, L_GROUND, "BOULDER" if boulder else "DUNE",
                    pw(5, 4) if boulder else pw(6, 4)))
    return out


# per show: (the first line it may use, items (x, line, layer, glyph or wide tile, pw))
BACKDROPS = [
    (16, [(x, y, l, "STAR%d" % l, PW_STAR) for x, y, l in STARS]),
    # the drill: the clock's zone stays black -- above ground, so the time reads
    # well -- and the earth starts at the rock
    (80, [(0, 80, L_STILL, "BAND", pw(6, 20)), (80, 80, L_STILL, "BAND", pw(6, 20)),
          (4, 112, L_STILL, "ROOTS", PW_P7), (140, 112, L_STILL, "ROOTS", PW_P7),
          (0, 120, L_STILL, "STRATA", PW_WIDE7), (80, 120, L_STILL, "STRATA", PW_WIDE7),
          (0, 152, L_STILL, "STRATA", PW_WIDE7), (80, 152, L_STILL, "STRATA", PW_WIDE7),
          (144, 98, L_STILL, "PEBBLES", PW_P7), (8, 129, L_STILL, "PEBBLES", PW_P7),
          (70, 132, L_STILL, "PEBBLES", PW_P7), (112, 164, L_STILL, "PEBBLES", PW_P7),
          (24, 160, L_STILL, "PEBBLES", PW_P7), (128, 126, L_STILL, "FOSSIL", PW_P7)]),
    # the bomber: the black band goes in last in zone 5, over any ground that
    # has scrolled up that far, so the clock sits on black as in every show
    (80, bomber_ground() + [(0, 80, L_STILL, "BAND", pw(6, 20)), (80, 80, L_STILL, "BAND", pw(6, 20))]),
    # the lander: stars, then Earthrise -- the top of the Earth over the hills
    # (zone 9), the dark ground in front (zones 10-11); the Earth goes in first so
    # the hills cover its foot
    (16, [(x, y, L_STILL, g, PW_STAR) for x, y, g in (
             (6, 22, "SPARK"), (150, 30, "STAR0"), (40, 60, "STAR0"), (132, 84, "SPARK"),
             (150, 104, "STAR0"), (60, 114, "SPARK"), (100, 126, "STAR0"), (138, 138, "SPARK"),
             (112, 110, "STAR0"), (88, 140, "SPARK"))] +
         [(12, 124, L_STILL, "EARTH_T", PW_EARTH), (12, 140, L_STILL, "EARTH_B", PW_EARTH),
          (0, 144, L_STILL, "HORIZON", PW_HORIZON), (80, 144, L_STILL, "HORIZON", PW_HORIZON)] +
         [(x, y, L_STILL, "REGOLITH", PW_REGOLITH) for y in (160, 176) for x in (0, 40, 80, 120)]),
]

# per show: BACKGRND, palette 5, palette 6, palette 7. The watch has 64 colours,
# so check that the ones that must differ still do through its table
# (palette7800.c): $12 and $14, say, both come out dark olive
SHOW_COLOURS = [
    # palette 6 colour 2 is black in every show: the bar's background (and the
    # drill's sky and shaft)
    (0x00, 0x22, 0x24, 0x06, 0x1A, 0x00, 0x18, 0x86, 0x0A, 0x0F),   # formation: space
    (0x12, 0x22, 0x24, 0x06, 0x20, 0x00, 0x20, 0x32, 0x24, 0x0A),   # drill: under ground (6: the shaft)
    (0xC2, 0x22, 0x24, 0x06, 0x1A, 0x00, 0x18, 0xD4, 0xF8, 0x34),   # bomber: farmland
    (0x00, 0x04, 0x08, 0x0E, 0x1A, 0x00, 0x18, 0x84, 0xC8, 0x0F),   # lander: the moon
]


def build_glyphs():
    g = [("D%d" % d, glyph_lines(rows)) for d, rows in enumerate(BIG_DIGITS)]
    g += [("COLON", glyph_lines(COLON)), ("SHIP", picture(SHIP, 4))]
    g += [("SHOT%d" % p, shot_lines(p)) for p in range(4)]
    g += [("BOOM0", boom_lines(0)), ("BOOM1", boom_lines(1))]
    g += [("ROCK", picture(ROCK)), ("DRILL0", picture(DRILL[0])), ("DRILL1", picture(DRILL[1])),
          ("PLANE", picture(PLANE)), ("BOMB", picture(BOMB, 6)), ("BOULDER", picture(BOULDER, 4)),
          ("DUNE", picture(DUNE, 5)), ("LANDER", picture(LANDER)), ("FLAME0", picture(FLAME[0])),
          ("FLAME1", picture(FLAME[1])), ("DUST", picture(DUST, 10))]
    g += [("STAR%d" % k, star_lines(k)) for k in range(3)]
    g += [("ROOTS", picture(ROOTS)), ("PEBBLES", picture(PEBBLES)), ("FOSSIL", picture(FOSSIL)),
          ("TREES", picture(TREES)), ("FIELD", picture(FIELD)), ("HOUSE", picture(HOUSE)),
          ("SPARK", picture(SPARK))]
    return g


GLYPHS = build_glyphs()
N_GLYPHS = len(GLYPHS)

# character mode (CTRL bit 4 clear): one byte, four pixels, per character, read
# from (CHARBASE + offset) << 8 | code -- the second graphics block, from its start
CH_SPACE = 0
SMALL_DIGITS = [
    ["111", "101", "101", "101", "111"], ["010", "110", "010", "010", "111"],
    ["111", "001", "111", "100", "111"], ["111", "001", "111", "001", "111"],
    ["101", "101", "111", "001", "001"], ["111", "100", "111", "001", "111"],
    ["111", "100", "111", "101", "111"], ["111", "001", "010", "010", "010"],
    ["111", "101", "111", "101", "111"], ["111", "101", "111", "001", "111"],
]
SMALL_U = ["101", "101", "101", "101", "111"]
SMALL_P = ["111", "101", "111", "100", "100"]
# the lives icons, 8 x 5, one per hour's show: each is two characters, left and right
MINI = [
    ["...11...", "..1111..", ".111111.", "11.11.11", "1......1"],   # formation: a ship
    ["...11...", "..1111..", ".111111.", "11111111", ".1.1.1.1"],   # drill: the rig
    ["...11...", "...11...", "11111111", "...11...", "..1111.."],   # bomber: a plane
    ["..1111..", ".111111.", "..1111..", ".1....1.", "11....11"],   # lander: a lander
]
# and the cruiser across the bottom, one per hour's show
CRUISER = ["SHIP", "DRILL0", "PLANE", "LANDER"]



def small_chars():
    """(name, 16 lines x 4 pixels) for every character, in code order from CH_SPACE."""
    def char(rows, colour):
        lines = [[0] * 4 for _ in range(16)]
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                if ch not in ".0":
                    lines[5 + r][c] = colour
        return lines
    out = [("SPACE", char([], 0))]
    out += [("D%d" % d, char(rows, 3)) for d, rows in enumerate(SMALL_DIGITS)]   # yellow
    out += [("U", char(SMALL_U, 2)), ("P", char(SMALL_P, 2)),                   # red
            ("R1", char(SMALL_DIGITS[1], 2)),
            ("H", char(["101", "101", "111", "101", "101"], 2)),
            ("I", char(["111", "010", "010", "010", "111"], 2))]
    for k, icon in enumerate(MINI):                                             # tinted
        out += [("LIFE%dL" % k, char([r[:4] for r in icon], 1)),
                ("LIFE%dR" % k, char([r[4:] for r in icon], 1))]
    return out


def pack160a(pixels):
    out = []
    for b in range(0, 16, 4):
        v = 0
        for p in range(4):
            v |= (pixels[b + p] & 3) << (6 - 2 * p)
        out.append(v)
    return out


ASM = """
INPTCTRL = $01
VBLANK   = $01
BACKGRND = $20
P0C1     = $21
P0C2     = $22
P0C3     = $23
P1C1     = $25
P1C2     = $26
P1C3     = $27
MSTAT    = $28
P2C1     = $29
P2C2     = $2A
P2C3     = $2B
DPPH     = $2C
P3C1     = $2D
P3C2     = $2E
P3C3     = $2F
DPPL     = $30
P4C1     = $31
P4C2     = $32
P4C3     = $33
CHARBASE = $34
P5C1     = $35
P5C2     = $36
P5C3     = $37
OFFSET   = $38
P6C1     = $39
P6C2     = $3A
P6C3     = $3B
CTRL     = $3C
P7C1     = $3D
P7C2     = $3E
P7C3     = $3F
CLK_SEC  = $0600     ; the clock registers: docs/clock-registers.md
CLK_MIN  = $0601
CLK_HOUR = $0602
CLK_FLAGS = $0607
CLK_BATTERY = $0608
CLK_STEPS_HI = $0609
CLK_STEPS_MID = $060A
CLK_STEPS_LO = $060B
CLK_CHANNEL = $060C
CLK_HI_H = $0610     ; the best day's steps, six BCD digits
CLK_HI_M = $0611
CLK_HI_L = $0612

FRAME    = $0080     ; free-running, for flicker only
LASTSEC  = $0081
SUBF     = $0082     ; frames since the seconds register last changed (0-59)
STAGE    = $0083     ; 0-2 = :57-:59, 3-5 = :00-:02, $FF = no show
LOCAL    = $0084     ; frames into the current volley
SCENE    = $0085     ; this hour's show: hour mod 4
PTR      = $0086     ; and $0087
TMP_G    = $0088
TMP_PAL  = $0089
TMP_X    = $008A
SHIPA    = $008B
SHIPB    = $008C
SHOTY    = $008D
BOOM     = $008E
FADE_T   = $008F
ZCNT     = $0090     ; entries in each zone's list, $90-$9F
LIVES    = $00A0
TMP_HI   = $00A1     ; the graphics page an entry points at
POSY     = $00A2     ; put_y: the line its top goes on, counting from zone 0
PUT_G    = $00A3
PUT_T    = $00A4
DIGM     = $00A5     ; the minute draw_min_y draws (BCD)
OBJY     = $00A6     ; a show's main actor's line, $FF = not shown
MINB     = $00A7     ; minutes, binary
TMP2     = $00A8
TMP3     = $00A9
TMP_Y    = $00AA
SCRL     = $00AB
CARRY    = $00AC
MOFF     = $00AD     ; added to the minutes' x (a shake)
FRAMEH   = $00AE     ; FRAME's high byte, for the stars' slow layers
SIDX     = $00AF
SOFF     = $00CA     ; $CA-$CE: how far each backdrop layer has moved: the stars'
                     ; three, still (always 0), and the bomber's ground
LASTSCENE = $00B3    ; the show last frame, $FF at power-on
STATIC   = $00B4     ; frames of channel-change static still to show
RND      = $00B5     ; the static's random bytes (an 8-bit LFSR, never 0)
STATIC_FRAMES = $18  ; 24 frames, 0.4 s
BUILDHI  = $00B6     ; 0 while building the first set of lists, $09 the second
NEXT_DPPL = $00B7    ; the display list list for the lists being built
NEXT_DPPH = $00B8
BDI      = $00B9     ; backdrop: the next item
BDEND    = $00BA
BDZMIN   = $00BB     ; the first zone it may use
BDZ      = $00CF     ; the zone its top is in
BDVALID  = $00BC     ; and $BD: the show whose still backdrop each set of lists
                     ; already holds ($FF: none)
BSET     = $00BE     ; the set being built: 0 or 1
BDBASE   = $1D20     ; backdrop entries at the head of each zone's list: $1D20-$1D2F
                     ; for the first set, $1D30-$1D3F for the second
HOURD    = $00D0     ; the hour as shown: 24- or 12-hour, BCD
SHADOW   = $00C0     ; $C0-$C9: BACKGRND, P5, P6, P7 for the frame being built,
                     ; written in vertical blank as its lists go up
"""

CODE_ASM = """
reset:
    SEI
    CLD
    LDX #$FF
    TXS
    LDA #$17
    STA INPTCTRL
    LDA #$00
    STA OFFSET
    STA VBLANK
    LDA #$60                  ; DMA off while the lists are built
    STA CTRL
    LDA #$00
    STA BACKGRND
    JSR digit_colours
    LDA #$0E
    STA P1C1                  ; ships, rig, bomber, lander: white
    LDA #$08
    STA P1C2                  ; grey
    LDA #$9E
    STA P1C3                  ; light blue
    LDA #$36
    STA P3C1                  ; shots, blasts, flame: red
    LDA #$1E
    STA P3C2                  ; yellow
    LDA #$0E
    STA P3C3                  ; white
    LDA #$00
    TAX
clear:
    STA DLBASE,X
    STA DLBASE+$100,X
    STA DLBASE+$200,X
    STA DLBASE+$300,X
    STA DLBASE_B,X
    STA DLBASE_B+$100,X
    STA DLBASE_B+$200,X
    STA DLBASE_B+$300,X
    INX
    BNE clear
    STA BUILDHI
    LDA #$FF
    STA BDVALID
    STA BDVALID+1
    LDA #$00
    LDX #$0E
clear_shadow:
    STA SHADOW,X              ; the first frame's colours: black (and SOFF)
    DEX
    BPL clear_shadow
    STA FRAME
    STA FRAMEH
    STA SUBF
    STA STATIC
    LDA #$FF                  ; no show yet: the first frame changes channel,
    STA LASTSCENE             ; so the set warms up through static
    LDA #$A7
    STA RND
    LDA #CH_SPACE             ; the bar's character maps start blank
    LDX #$4F
blank_maps:
    STA MAP1,X
    DEX
    BPL blank_maps
    LDX #$00                  ; zone 12's list, which never changes: a black
hud_list:                     ; band, then the lives, HI and 1UP
    LDA hud_dl_init,X
    STA HUD_DL,X
    INX
    CPX #$17
    BNE hud_list
    LDX #$07                  ; and zone 13, under the bar: the black band alone
under_bar:
    LDA hud_dl_init,X
    STA HUD_DL+DL_STRIDE_ASM,X
    DEX
    BPL under_bar
    LDA #>gfx2                ; characters come from the second graphics block
    STA CHARBASE
    LDA #$36
    STA P4C2                  ; bar: red 1UP
    LDA #$1A
    STA P4C3                  ; yellow score
    LDA CLK_SEC
    STA LASTSEC
    LDA #>dll_a
    STA NEXT_DPPH
    LDA #<dll_a
    STA NEXT_DPPL
    LDA #$40                  ; DMA on, 160A
    STA CTRL

main:
wait_vb:
    LDA MSTAT
    BPL wait_vb

    ; ---- the lists built last frame go up, with their colours; this frame's
    ; are built in the other set while they are shown ----
    LDA NEXT_DPPH
    STA DPPH
    LDA NEXT_DPPL
    STA DPPL
    LDA SHADOW
    STA BACKGRND
    LDA SHADOW+1
    STA P5C1
    LDA SHADOW+2
    STA P5C2
    LDA SHADOW+3
    STA P5C3
    LDA SHADOW+4
    STA P6C1
    LDA SHADOW+5
    STA P6C2
    LDA SHADOW+6
    STA P6C3
    LDA SHADOW+7
    STA P7C1
    LDA SHADOW+8
    STA P7C2
    LDA SHADOW+9
    STA P7C3
    LDA BUILDHI
    EOR #$09
    STA BUILDHI

    ; ---- where are we in the minute? the seconds register is the clock ----
    LDA CLK_SEC
    CMP LASTSEC
    BEQ same_second
    STA LASTSEC
    LDA #$00
    STA SUBF                  ; a new second: start counting frames again
    BEQ second_done
same_second:
    LDA SUBF
    CMP #$3B
    BCS second_done           ; never past 59
    INC SUBF
second_done:
    LDA CLK_SEC               ; the stage: :57-:59 are 0-2, :00-:02 are 3-5
    CMP #$57
    BCC not_late
    SEC
    SBC #$57
    STA STAGE
    JMP stage_done
not_late:
    CMP #$03
    BCS no_show
    CLC
    ADC #$03
    STA STAGE
    JMP stage_done
no_show:
    LDA #$FF
    STA STAGE
stage_done:
    LDA CLK_FLAGS             ; paused (the watch's Quiet Time): no shows
    AND #$08
    BEQ not_paused
    LDA #$FF
    STA STAGE
not_paused:
    LDA SUBF                  ; a volley every 30 frames
    CMP #$1E
    BCC local_ok
    SEC
    SBC #$1E
local_ok:
    STA LOCAL
    LDA CLK_HOUR              ; this hour's show, moved on by the channel the
    JSR bcd_to_bin            ; wearer has flicked to
    CLC
    ADC CLK_CHANNEL
    AND #$03
    STA SCENE

    ; ---- a new show comes in through static, which needs holey DMA off ----
    CMP LASTSCENE
    BEQ same_show
    STA LASTSCENE
    LDA CLK_FLAGS             ; (not while paused: the watch draws one frame a minute)
    AND #$08
    BNE same_show
    LDA #STATIC_FRAMES
    STA STATIC
same_show:
    LDX SCENE                 ; this show's colours
    LDY times10,X
    LDX #$00
copy_shadow:
    LDA show_colours,Y
    STA SHADOW,X
    INY
    INX
    CPX #$0A
    BNE copy_shadow
    LDX #$00                  ; which display list list: first set or second...
    LDA BUILDHI
    BEQ dll_pick
    INX
dll_pick:
    LDA STATIC
    BEQ dll_picked
    INX                       ; ... and holey DMA off for the static
    INX
    LDA #$00
    STA SHADOW                ; black behind it, palette 6 its greys (colour 2
    LDA #$06                  ; stays black, for the bar)
    STA SHADOW+4
    LDA #$0F
    STA SHADOW+6
dll_picked:
    LDA dll_lo,X
    STA NEXT_DPPL
    LDA dll_hi,X
    STA NEXT_DPPH
    LDA CLK_MIN
    JSR bcd_to_bin
    STA MINB
    LDA #$00
    STA MOFF

    LDX #$00                  ; which set: 0 or 1
    LDA BUILDHI
    BEQ bset_ok
    INX
bset_ok:
    STX BSET

    LDA STATIC
    BEQ picture_on
    LDA #$FF                  ; the static overwrites any backdrop
    STA BDVALID
    STA BDVALID+1
    JSR empty_lists
    DEC STATIC
    JSR draw_static
    JMP the_bar
picture_on:

    ; ---- the show's backdrop, behind everything else: rebuilt every frame if
    ; it moves; if it is still, kept at the head of each list once built ----
    LDX SCENE
    LDA bd_moving,X
    BNE bd_rebuild
    LDX BSET
    LDA BDVALID,X
    CMP SCENE
    BEQ bd_keep
bd_rebuild:
    JSR empty_lists
    JSR backdrop
    LDA BSET                  ; note how many entries it left in each zone
    ASL
    ASL
    ASL
    ASL
    TAY
    LDX #$01
bd_note:
    LDA ZCNT,X
    STA BDBASE+1,Y
    INY
    INX
    CPX #$0C
    BNE bd_note
    LDX SCENE
    LDA bd_moving,X
    BEQ bd_still
    LDA #$FF
    BNE bd_valid
bd_still:
    TXA
bd_valid:
    LDX BSET
    STA BDVALID,X
    JMP bd_ready
bd_keep:
    LDA BSET                  ; cut each list back to its backdrop
    ASL
    ASL
    ASL
    ASL
    STA TMP3
    LDX #$01
bd_cut:
    LDY TMP3
    LDA BDBASE+1,Y
    INC TMP3
    STA ZCNT,X
    ASL
    ASL
    TAY
    INY
    JSR set_ptr
    LDA #$00
    STA (PTR),Y
    INX
    CPX #$0C
    BNE bd_cut
bd_ready:

    ; ---- the hours and the colon, palette 0; 12-hour time if the wearer
    ; prefers it (CLK_FLAGS bit 7), without a leading zero ----
    LDA CLK_HOUR
    STA HOURD
    BIT CLK_FLAGS
    BPL hours_set
    JSR bcd_to_bin
    BNE h_not0
    LDA #$0C                  ; 0 is 12
h_not0:
    CMP #$0D
    BCC h_le12
    SBC #$0C                  ; 13-23 are 1-11 (carry is set)
h_le12:
    CMP #$0A
    BCC h_bcd
    ADC #$05                  ; 10-12 in BCD (+6, carry is set)
h_bcd:
    STA HOURD
hours_set:
    LDA #$1C                  ; palette 0, 4 bytes wide
    STA TMP_PAL
    LDX #$05
    LDA #$20
    STA TMP_X
    LDA HOURD
    LSR
    LSR
    LSR
    LSR
    BNE h_tens
    BIT CLK_FLAGS
    BMI h_units
h_tens:
    JSR add_obj
h_units:
    LDA #$34
    STA TMP_X
    LDA HOURD
    AND #$0F
    JSR add_obj
    LDA #$48
    STA TMP_X
    LDA #G_COLON
    JSR add_obj

    JSR digit_colours

    ; ---- this hour's show draws the minutes and its actors ----
    LDA SCENE
    ASL
    TAX
    LDA scene_tab+1,X
    PHA
    LDA scene_tab,X
    PHA
    RTS
scene_return:

    ; ---- the cruiser -- this hour's vehicle -- crosses the bottom once an hour,
    ; x = minutes * 2.5 ----
    LDA MINB
    LSR
    STA TMP_G
    LDA MINB
    ASL
    CLC
    ADC TMP_G
    STA TMP_X
    LDA MINB                  ; :59:57-:59:59 of the hour: it drives off the end
    CMP #59
    BNE cruise
    LDY STAGE
    CPY #$03
    BCS cruise
    LDA SUBF
    LSR
    LSR
    CLC
    ADC times15,Y
    CLC
    ADC TMP_X
    STA TMP_X
cruise:
    LDA #$3C
    STA TMP_PAL
    LDY SCENE
    LDA cruiser_g,Y
    LDX #$0B
    JSR add_obj

    JSR words                 ; STAGE CLEAR, NEW HI, GAME OVER
    BIT CLK_FLAGS             ; no phone: snow (bit 6 clear)
    BVS linked
    JSR snow
linked:

    ; ---- the bar: lives = battery in 20% steps (at least 1, at most 5) ----
the_bar:
    LDX #$00
    LDA CLK_BATTERY
count_lives:
    CMP #$01
    BCC lives_counted
    INX
    SEC
    SBC #$14
    BCS count_lives
lives_counted:
    CPX #$00
    BNE lives_min_ok
    INX
lives_min_ok:
    CPX #$06
    BCC lives_max_ok
    LDX #$05
lives_max_ok:
    LDA CLK_FLAGS             ; out of lives at 10% or less, unless charging
    AND #$20
    BNE lives_set
    LDA CLK_BATTERY
    CMP #$0B
    BCS lives_set
    LDX #$00
lives_set:
    STX LIVES
    LDA CLK_FLAGS             ; green while charging, red on the last life
    AND #$20
    BEQ not_charging
    LDA #$C8
    BNE tint_set
not_charging:
    LDA #$0E
    CPX #$02
    BCS tint_set
    LDA #$36
tint_set:
    STA P4C1
    LDA SCENE                 ; the lives are this hour's vehicle too
    ASL
    CLC
    ADC #CH_LIFE0L
    STA TMP2
    LDY #$00
    LDX #$00
fill_lives:
    CPX LIVES
    BCS no_life
    LDA TMP2
    STA MAP1,Y
    CLC
    ADC #$01
    STA MAP1+1,Y
    JMP next_life
no_life:
    LDA #CH_SPACE
    STA MAP1,Y
    STA MAP1+1,Y
next_life:
    INY
    INY
    INX
    CPX #$05
    BNE fill_lives

    ; ---- the best day's steps ----
    LDA #CH_H
    STA MAP3
    LDA #CH_I
    STA MAP3+1
    LDA #CH_SPACE
    STA MAP3+2
    LDA CLK_HI_H
    JSR high_digit
    STA MAP3+3
    LDA CLK_HI_H
    JSR low_digit
    STA MAP3+4
    LDA CLK_HI_M
    JSR high_digit
    STA MAP3+5
    LDA CLK_HI_M
    JSR low_digit
    STA MAP3+6
    LDA CLK_HI_L
    JSR high_digit
    STA MAP3+7
    LDA CLK_HI_L
    JSR low_digit
    STA MAP3+8

    ; ---- and the score: today's steps ----
    LDA #CH_R1
    STA MAP2
    LDA #CH_U
    STA MAP2+1
    LDA #CH_P
    STA MAP2+2
    LDA #CH_SPACE
    STA MAP2+3
    LDA CLK_STEPS_HI
    JSR high_digit
    STA MAP2+4
    LDA CLK_STEPS_HI
    JSR low_digit
    STA MAP2+5
    LDA CLK_STEPS_MID
    JSR high_digit
    STA MAP2+6
    LDA CLK_STEPS_MID
    JSR low_digit
    STA MAP2+7
    LDA CLK_STEPS_LO
    JSR high_digit
    STA MAP2+8
    LDA CLK_STEPS_LO
    JSR low_digit
    STA MAP2+9

    INC FRAME
    BNE frame_done
    INC FRAMEH
frame_done:
wait_novb:
    LDA MSTAT
    BMI wait_novb
    JMP main

; ======================================================================
; hour 0: formation
; ======================================================================
scene_shooter:
    LDX #$05                  ; the minutes' zone (add_obj takes the zone in X)
SHOOTER_CODE
    JMP scene_return

; ======================================================================
; hour 1: drill rig
; ======================================================================
scene_drill:
    LDA #$BC                  ; the rock under the clock: zone 6, palette 5
    STA TMP_PAL
    LDA #$10
    STA TMP_X
    LDX #$06
rock_loop:
    LDA #G_ROCK
    JSR add_obj
    LDA TMP_X
    CLC
    ADC #$10
    STA TMP_X
    CMP #$90
    BNE rock_loop
    LDA #$FF                  ; where the rig is, by stage
    STA OBJY
    LDA STAGE
    CMP #$00
    BNE dr_s1
    LDA SUBF                  ; :57 -- it rises from 152 toward the rock
    LSR
    STA TMP2
    LDA SUBF
    LSR
    LSR
    LSR
    CLC
    ADC TMP2
    STA TMP2
    LDA #$98
    SEC
    SBC TMP2
    STA OBJY
    JMP dr_minutes
dr_s1:
    CMP #$01
    BNE dr_s2
    LDA SUBF                  ; :58 -- bores through the rock
    LSR
    LSR
    STA TMP2
    LDA #$74
    SEC
    SBC TMP2
    STA OBJY
    JMP dr_minutes
dr_s2:
    CMP #$02
    BNE dr_s3
    LDA #$63                  ; :59 -- at the minutes' feet
    STA OBJY
    JMP dr_minutes
dr_s3:
    CMP #$03
    BNE dr_s4
    LDA SUBF                  ; :00 -- pushes the new minute up, 112 to 80
    LSR
    STA TMP2
    LDA SUBF
    LSR
    LSR
    LSR
    LSR
    CLC
    ADC TMP2
    STA TMP2
    LDA #$70
    SEC
    SBC TMP2
    STA POSY
    CLC
    ADC #$10
    STA OBJY
    JMP dr_minutes
dr_s4:
    CMP #$04
    BNE dr_minutes
    LDA SUBF                  ; :01 -- sinks away
    CLC
    ADC #$60
    STA OBJY
dr_minutes:
    JSR dr_hole               ; the shaft it bores, before the minutes and the rig
    LDA CLK_MIN
    STA DIGM
    LDA STAGE
    CMP #$03
    BEQ dr_rise
    CMP #$02
    BNE dr_still
    LDA SUBF
    CMP #$1E
    BCS dr_rubble
    LDA FRAME                 ; the first half of :59: the minutes shake
    LSR
    LSR
    AND #$01
    STA MOFF
dr_still:
    LDA #$50
    STA POSY
dr_rise:
    JSR draw_min_y
    JMP dr_rig
dr_rubble:
    LDA #$BC                  ; then crumble
    STA TMP_PAL
    JSR draw_blast
dr_rig:
    LDA OBJY
    CMP #$FF
    BEQ dr_done
    STA POSY
    LDA #$3C
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDA FRAME
    LSR
    LSR
    AND #$01
    CLC
    ADC #G_DRILL0
    JSR put_y
    LDA STAGE                 ; grit flies while it bores
    CMP #$01
    BEQ dr_grit
    CMP #$02
    BNE dr_done
dr_grit:
    LDA #$BC
    STA TMP_PAL
    LDA #$58
    STA TMP_X
    LDA #G_DUST
    JSR put_y
    LDA #$74
    STA TMP_X
    LDA #G_DUST
    JSR put_y
    LDA STAGE                 ; and chips of rock fall from under it
    CMP #$01
    BNE dr_done
    LDA LOCAL
    ASL
    CLC
    ADC LOCAL                 ; 3 lines a frame
    AND #$1F
    CLC
    ADC #$70                  ; from the rock's underside, 112-143
    STA POSY
    LDA #$62
    STA TMP_X
    LDA #G_SHOT0
    JSR put_y
    LDA POSY
    CLC
    ADC #$10
    AND #$1F
    CLC
    ADC #$70
    STA POSY
    LDA #$76
    STA TMP_X
    LDA #G_SHOT0
    JSR put_y
dr_done:
    JMP scene_return

; the shaft through the rock (zone 6, lines 96-111) under the rig: the rock
; glyph again in palette 6, which the drill hour makes dark, from the shaft's
; top line down -- one entry, so zone 6's bottom cuts it off at the rock's
; underside. It opens up behind the bit at :58, is clear through by :00, and
; after the rig has sunk back out at :01 the rock grows back down from the
; top in a shimmer of dust.
dr_hole:
    LDA STAGE
    CMP #$01
    BNE dh_s2
    LDA OBJY                  ; :58 -- up to the bit
    CMP #$60
    BCS dh_low
    LDA #$60
dh_low:
    CMP #$70
    BCS dh_none
    BCC dh_draw
dh_s2:
    CMP #$02
    BNE dh_s3
    LDA #$63                  ; :59 -- at the minutes' feet
    BNE dh_draw
dh_s3:
    CMP #$03
    BNE dh_s4
    LDA #$60                  ; :00 -- clear through
    BNE dh_draw
dh_s4:
    CMP #$04
    BNE dh_none
    LDA SUBF                  ; :01 -- open until the rig is out, then it closes
    CMP #$10
    BCS dh_closing
    LDA #$60
    BNE dh_draw
dh_closing:
    SEC
    SBC #$10
    LSR
    CLC
    ADC #$60
    CMP #$70
    BCS dh_none
    STA TMP3
    JSR dh_draw
    LDA FRAME                 ; the dust where the rock is growing back
    AND #$04
    BEQ dh_none
    LDA TMP3
    SEC
    SBC #$08
    STA POSY
    LDA #$BC
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDA #G_BOOM1
    JMP put_y
dh_draw:
    SEC                       ; A = the shaft's top line, 96-111
    SBC #$60
    CLC
    ADC #$E0
    STA TMP_HI
    LDA #$DC                  ; palette 6, 4 bytes
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDX #$06
    LDA #G_ROCK
    JMP add_obj_hi
dh_none:
    RTS

; ======================================================================
; hour 2: bomber run
; ======================================================================
scene_bomber:                 ; (the ground, boulders and mounds included, is the backdrop)
    LDA #$FF
    STA OBJY
    LDA STAGE
    CMP #$00
    BNE bm_s1
    LDA #$B0                  ; :57 -- climbs in from the bottom
    SEC
    SBC SUBF
    STA OBJY
    JMP bm_min
bm_s1:
    CMP #$01
    BEQ bm_hold
    CMP #$02
    BNE bm_s3
bm_hold:
    LDA #$74                  ; :58-:59 -- holds under the clock
    STA OBJY
    JMP bm_min
bm_s3:
    CMP #$03
    BNE bm_s4
    LDA SUBF                  ; :00 -- climbs away over the clock
    LSR
    LSR
    STA TMP2
    LDA #$74
    SEC
    SBC SUBF
    SEC
    SBC TMP2
    STA OBJY
    JMP bm_min
bm_s4:
    CMP #$04
    BNE bm_min
    LDA SUBF
    LSR
    STA TMP2
    LDA #$2B
    SEC
    SBC TMP2
    STA OBJY
bm_min:
    LDA STAGE
    CMP #$02
    BNE bm_digits
    LDA SUBF
    CMP #$1E
    BCC bm_bomb
    LDA #$7C                  ; the second half of :59: the blast
    STA TMP_PAL
    JSR draw_blast
    JMP bm_plane
bm_bomb:
    LDA SUBF                  ; the first half: the bomb heads for the minutes
    LSR
    STA TMP2
    LDA SUBF
    LSR
    LSR
    CLC
    ADC TMP2
    STA TMP2
    LDA #$6C
    SEC
    SBC TMP2
    STA POSY
    LDA #$7C
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDA #G_BOMB
    JSR put_y
bm_digits:
    JSR fade_minutes
    LDA CLK_MIN
    STA DIGM
    LDA #$50
    STA POSY
    JSR draw_min_y
bm_plane:
    LDA OBJY
    CMP #$FF
    BEQ bm_done
    STA POSY
    LDA #$3C
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDA #G_PLANE
    JSR put_y
bm_done:
    JMP scene_return

; ======================================================================
; hour 3: lander
; ======================================================================
scene_lander:
    LDA #$FF
    STA OBJY
    LDA #$50                  ; where the minutes sit
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$00
    STA CARRY                 ; 1: the minutes hang under the lander; 2: landed
    LDA STAGE
    CMP #$00
    BNE ld_s1
    JSR ease48                ; :57 -- comes down to hover over the clock
    CLC
    ADC #$10
    STA OBJY
    JMP ld_draw
ld_s1:
    CMP #$01
    BNE ld_s2
    LDA SUBF                  ; :58 -- takes hold, then lifts the old minute
    CMP #$0F
    BCS ld_lift
    LDA #$40
    STA OBJY
    JMP ld_draw
ld_lift:
    SEC
    SBC #$0F
    STA TMP2
    LSR
    LSR
    LSR
    STA TMP3
    LDA TMP2
    SEC
    SBC TMP3
    STA TMP2
    LDA #$40
    SEC
    SBC TMP2
    STA OBJY
    LDA #$01
    STA CARRY
    JMP ld_draw
ld_s2:
    CMP #$02
    BNE ld_s3
    LDA SUBF                  ; :59 -- carries it away out of sight
    LSR
    STA TMP2
    LDA #$19
    SEC
    SBC TMP2
    STA OBJY
    LDA #$01
    STA CARRY
    JMP ld_draw
ld_s3:
    CMP #$03
    BNE ld_s4
    LDA SUBF                  ; :00 -- brings the new minute down
    CMP #$3A
    BCS ld_landed
    JSR ease48
    CLC
    ADC #$10
    STA OBJY
    LDA #$01
    STA CARRY
    JMP ld_draw
ld_landed:
    LDA #$40                  ; and sets it down
    STA OBJY
    LDA #$02
    STA CARRY
    JMP ld_draw
ld_s4:
    CMP #$04
    BNE ld_draw
    LDA #$40                  ; :01 -- lifts off, empty
    SEC
    SBC SUBF
    STA OBJY
ld_draw:
    LDA CARRY
    CMP #$01
    BNE ld_min
    LDA OBJY
    CLC
    ADC #$10
    STA POSY
ld_min:
    JSR draw_min_y
    LDA OBJY
    CMP #$FF
    BEQ ld_done
    STA POSY
    LDA #$66
    STA TMP_X
    LDA CARRY
    CMP #$02
    BEQ ld_body
    LDA #$7C                  ; the flame flickers
    STA TMP_PAL
    LDA FRAME
    LSR
    LSR
    AND #$01
    CLC
    ADC #G_FLAME0
    JSR put_y
ld_body:
    LDA #$3C
    STA TMP_PAL
    LDA #G_LANDER
    JSR put_y
    LDA CARRY
    CMP #$02
    BNE ld_done
    LDA #$54                  ; dust as it settles
    STA POSY
    LDA #$BC
    STA TMP_PAL
    LDA #$4C
    STA TMP_X
    LDA #G_DUST
    JSR put_y
    LDA #$80
    STA TMP_X
    LDA #G_DUST
    JSR put_y
ld_done:
    JMP scene_return

; ======================================================================
; helpers
; ======================================================================

; the show's backdrop: a list of items (bd_*), each on one of five layers --
; the formation hour's stars falling at 1/8, 1/4 and 1/2 a line a frame (by the
; frame count: they are scenery, and the count never jumps), still, or moving
; with the bomber's ground. The lists are empty when this runs, so everything
; else is drawn over it.
backdrop:
    LDA FRAMEH
    STA TMP2
    LDA FRAME
    LSR TMP2
    ROR A
    STA SOFF+2                ; near stars: frames / 2
    LSR TMP2
    ROR A
    STA SOFF+1                ; middle: frames / 4
    LSR TMP2
    ROR A
    STA SOFF                  ; far: frames / 8
    LDA #$00
    STA SOFF+3                ; still
    JSR calc_scrl
    STA SOFF+4                ; the ground
    LDX SCENE
    LDA bd_zmin,X
    STA BDZMIN
    LDA bd_first,X
    STA BDI
    LDA bd_first+1,X
    STA BDEND
bd_loop:
    LDY BDI
    CPY BDEND
    BNE bd_item
    RTS
bd_item:
    INC BDI
    STY SIDX
    LDX bd_l,Y
    LDA bd_y,Y
    CLC
    ADC SOFF,X                ; the item's top line, counting from zone 0's top
    TAX
    AND #$0F
    STA PUT_T
    TXA
    LSR
    LSR
    LSR
    LSR
    STA BDZ
    ; the top part: zone BDZ points at page base + t (the item's graphics block:
    ; $E0, or $A0 for the second)
    TAX
    CPX BDZMIN
    BCC bd_lower
    CPX #$0C
    BCS bd_loop               ; in the bar or below
    LDA PUT_T
    CLC
    ADC bd_hb,Y
    STA TMP_HI
    JSR bd_add
bd_lower:
    ; the part that spills into the next zone, if any: page base - $10 + t
    ; there, and holey DMA blanks the pages outside the block (as put_y does)
    LDA PUT_T
    BEQ bd_loop
    LDY SIDX
    CLC
    ADC bd_h,Y
    CMP #$11
    BCC bd_loop               ; it ends within its zone
    LDX BDZ
    INX
    CPX BDZMIN
    BCC bd_loop
    CPX #$0C
    BCS bd_loop
    LDY SIDX
    LDA bd_hb,Y
    SEC
    SBC #$10
    CLC
    ADC PUT_T
    STA TMP_HI
    JSR bd_add
    JMP bd_loop

; one entry in zone X of the set being built: item SIDX, page TMP_HI
bd_add:
    LDA ZCNT,X
    CMP #$0F
    BCS bd_add_full
    INC ZCNT,X
    ASL
    ASL
    ORA zlo,X                 ; lists are 64-byte aligned
    STA PTR
    LDA zhi,X
    CLC
    ADC BUILDHI
    STA PTR+1
    LDY SIDX
    LDA bd_x,Y
    STA TMP_X
    LDA bd_pw,Y
    STA TMP_PAL
    LDA bd_g,Y
    LDY #$00
    STA (PTR),Y
    INY
    LDA TMP_PAL
    STA (PTR),Y
    INY
    LDA TMP_HI
    STA (PTR),Y
    INY
    LDA TMP_X
    STA (PTR),Y
    INY
    INY
    LDA #$00                  ; the end of the list
    STA (PTR),Y
bd_add_full:
    RTS

; SCRL: how far the bomber's ground has scrolled. Half a line a frame through
; each show (180 lines from :57 to :03) and still in between -- so it carries on
; from show to show rather than going back: show k (the one that starts at
; minute k's :57) runs from 180k to 180k + 180, mod 256. Through :57-:59 the
; show is this minute's; from :00, and until the next :57, the one just ended.
calc_scrl:
    LDA MINB
    LDX STAGE
    CPX #$03
    BCC scrl_k                ; :57-:59: this minute's show
    SEC
    SBC #$01                  ; after it: the show that began last minute
    BCS scrl_k
    LDA #59
scrl_k:
    STA TMP3                  ; k; TMP2 = 180k mod 256 = 128k + 32k + 16k + 4k
    ASL
    ASL
    STA TMP2
    ASL
    ASL
    CLC
    ADC TMP2
    STA TMP2
    LDA TMP3
    ASL
    ASL
    ASL
    ASL
    ASL
    CLC
    ADC TMP2
    STA TMP2
    LDA TMP3
    LSR
    LDA #$00
    ROR
    CLC
    ADC TMP2
    STA TMP2
    LDA STAGE
    CMP #$FF
    BEQ scrl_after
    TAX
    LDA times30,X             ; in the show: 30 lines a second, by the clock
    STA TMP3
    LDA SUBF
    LSR
    CLC
    ADC TMP3
    CLC
    ADC TMP2
    STA SCRL
    RTS
scrl_after:
    LDA TMP2                  ; between shows: where the last one stopped
    CLC
    ADC #180
    STA SCRL
    RTS

; the words, in zone 8 from the second graphics block: STAGE CLEAR as the
; hour runs out (minute 59, :57-:59, flashing), and while no show is on, NEW HI
; (the watch's bit 2: today beat the best day) or GAME OVER (out of lives)
words:
    LDA #>gfx2
    STA TMP_HI
    LDX #$08
    LDA STAGE
    CMP #$03
    BCS words_idle
    LDA MINB
    CMP #59
    BNE words_done
    LDA FRAME
    AND #$08
    BNE words_done
    LDA #PW_STAGE
    STA TMP_PAL
    LDA #$0E
    STA TMP_X
    LDA #WD_STAGE
    JSR add_obj_lo
    LDA #$56
    STA TMP_X
    LDA #WD_CLEAR
    JMP add_obj_lo
words_idle:
    CMP #$FF
    BNE words_done
    LDA CLK_FLAGS
    AND #$04
    BEQ words_over
    LDA #PW_NEW
    STA TMP_PAL
    LDA #$2C
    STA TMP_X
    LDA #WD_NEW
    JSR add_obj_lo
    LDA #PW_HI
    STA TMP_PAL
    LDA #$5C
    STA TMP_X
    LDA #WD_HI
    JMP add_obj_lo
words_over:
    LDA CLK_FLAGS
    AND #$20
    BNE words_done
    LDA CLK_BATTERY
    CMP #$0B
    BCS words_done
    LDA #PW_GAME
    STA TMP_PAL
    LDA #$1B
    STA TMP_X
    LDA #WD_GAME
    JSR add_obj_lo
    LDA #$57
    STA TMP_X
    LDA #WD_OVER
    JMP add_obj_lo
words_done:
    RTS

; a lost phone link: faint snow over zones 6-11 -- below the clock, above the
; bar -- from a random place in the snow tile, at a random x, new every frame
snow:
    LDA #>gfx2
    STA TMP_HI
    LDA #PW_SNOW
    STA TMP_PAL
    LDX #$06
snow_zone:
    JSR snow_entry            ; one 80-pixel window a zone, somewhere new each frame
    INX
    CPX #$0C
    BNE snow_zone
    RTS
snow_entry:
    JSR rnd
    STA TMP_X
    JSR rnd
    AND #$0F                  ; a 20-byte window into the 32-byte tile
    CMP #$0D
    BCC snow_lo
    AND #$07
snow_lo:
    CLC
    ADC #SNOW
    JMP add_obj_lo

; empty the lists of zones 1-11 in the set being built
empty_lists:
    LDX #$01
empty:
    LDA #$00
    STA ZCNT,X
    JSR set_ptr
    LDY #$01
    LDA #$00
    STA (PTR),Y
    INX
    CPX #$0C
    BNE empty
    RTS


; channel-change static: two 80-pixel entries a zone over 4K of random bytes,
; from new random places every frame
draw_static:
    LDX #$01
static_zone:
    JSR set_ptr
    LDY #$00
    JSR rnd
    STA (PTR),Y
    INY
    LDA #$CC                  ; palette 6, 20 bytes wide
    STA (PTR),Y
    INY
    LDA #>noise
    STA (PTR),Y
    INY
    LDA #$00
    STA (PTR),Y
    INY
    JSR rnd
    STA (PTR),Y
    INY
    LDA #$CC
    STA (PTR),Y
    INY
    LDA #>noise
    STA (PTR),Y
    INY
    LDA #$50
    STA (PTR),Y
    INY
    INY
    LDA #$00                  ; the end of the list
    STA (PTR),Y
    INX
    CPX #$0C
    BNE static_zone
    RTS

; the next random byte
rnd:
    LDA RND
    ASL
    BCC rnd_done
    EOR #$1D
rnd_done:
    STA RND
    RTS

; PTR = zone X's display list
set_ptr:
    LDA zlo,X
    STA PTR
    LDA zhi,X
    CLC
    ADC BUILDHI               ; the set being built
    STA PTR+1
    RTS

; one 4-byte entry in zone X: glyph A, TMP_PAL (palette | width), at TMP_X; X is kept
add_obj:
    PHA
    LDA #>gfx
    STA TMP_HI
    PLA
; ... with its graphics page in TMP_HI
add_obj_hi:
    ASL
    ASL
; ... with the entry's low byte in A rather than a glyph number
add_obj_lo:
    STA TMP_G
    LDA ZCNT,X
    CMP #$0F                  ; a list holds 15
    BCS add_full
    JSR set_ptr
    LDA ZCNT,X
    ASL
    ASL
    TAY
    LDA TMP_G
    STA (PTR),Y
    INY
    LDA TMP_PAL
    STA (PTR),Y
    INY
    LDA TMP_HI
    STA (PTR),Y
    INY
    LDA TMP_X
    STA (PTR),Y
    INY
    INY
    LDA #$00                  ; the next entry's byte 1: the end of the list
    STA (PTR),Y
    INC ZCNT,X
add_full:
    RTS

; glyph A at TMP_X with its top on line POSY (counting from zone 0's top), to the
; line: zone POSY/16 points at page $E0 + POSY%16 and the zone below at $D0 +
; POSY%16, and holey DMA blanks every page outside $E0-$EF. Zones 0 and 12 up
; are left alone (the first is never cleared, the rest are the bar).
put_y:
    STA PUT_G
    LDA POSY
    CMP #$10
    BCC put_done
    LSR
    LSR
    LSR
    LSR
    TAX
    CPX #$0C
    BCS put_done
    LDA POSY
    AND #$0F
    STA PUT_T
    CLC
    ADC #$E0
    STA TMP_HI
    LDA PUT_G
    JSR add_obj_hi
    LDA PUT_T
    BEQ put_done
    INX
    CPX #$0C
    BCS put_done
    LDA PUT_T
    CLC
    ADC #$D0
    STA TMP_HI
    LDA PUT_G
    JSR add_obj_hi
put_done:
    RTS

; the minute in DIGM at line POSY, palette 2, shaken right by MOFF
draw_min_y:
    LDA #$5C
    STA TMP_PAL
    CLC
    ADC MOFF
    STA TMP_X
    LDA DIGM
    LSR
    LSR
    LSR
    LSR
    JSR put_y
    LDA #$70
    CLC
    ADC MOFF
    STA TMP_X
    LDA DIGM
    AND #$0F
    JSR put_y
    RTS

; a blast where the minutes are, in TMP_PAL's palette
draw_blast:
    LDA FRAME
    LSR
    LSR
    AND #$01
    CLC
    ADC #G_BOOM0
    STA BOOM
    LDX #$05
    LDA #$5C
    STA TMP_X
    LDA BOOM
    JSR add_obj
    LDA #$70
    STA TMP_X
    LDA BOOM
    JSR add_obj
    RTS

; :00 -- the new minute fades in
fade_minutes:
    LDA STAGE
    CMP #$03
    BNE fade_done
    LDA SUBF
    LSR
    LSR
    STA TMP_G                 ; the luma it has reached
    LDA #$36
    JSR fade
    STA P2C1
    LDA #$28
    JSR fade
    STA P2C2
    LDA #$1E
    JSR fade
    STA P2C3
fade_done:
    RTS

; about SUBF * 0.81: 0 to 45 over a second
ease48:
    LDA SUBF
    LSR
    STA TMP3
    LSR
    STA TMP2
    LSR
    LSR
    CLC
    ADC TMP2
    CLC
    ADC TMP3
    RTS

; BCD to binary
bcd_to_bin:
    PHA
    LSR
    LSR
    LSR
    LSR
    STA TMP2
    ASL
    ASL
    CLC
    ADC TMP2
    ASL
    STA TMP2
    PLA
    AND #$0F
    CLC
    ADC TMP2
    RTS

; a BCD byte's digits as character codes
high_digit:
    LSR
    LSR
    LSR
    LSR
low_digit:
    AND #$0F
    CLC
    ADC #CH_D0
    RTS

; palettes 0 and 2: red, orange, yellow
digit_colours:
    LDA #$36
    STA P0C1
    STA P2C1
    LDA #$28
    STA P0C2
    STA P2C2
    LDA #$1E
    STA P0C3
    STA P2C3
    RTS

; A = a colour; returns it with its luma held to at most TMP_G
fade:
    PHA
    AND #$0F
    CMP TMP_G
    BCC fade_keep
    LDA TMP_G
fade_keep:
    STA FADE_T
    PLA
    AND #$F0
    ORA FADE_T
    RTS

nmi:
    RTI
"""


SHOOTER = "    ; ---- the minutes: palette 2, or from :59.5 the blast in palette 3 ----\n    LDA STAGE\n    CMP #$02\n    BNE minute_digits\n    LDA SUBF\n    CMP #$1E\n    BCC minute_digits\n    LDA #$7C                  ; palette 3\n    STA TMP_PAL\n    JSR draw_blast\n    JMP minutes_done\nminute_digits:\n    LDA #$5C                  ; palette 2 (and x = 92, as it happens)\n    STA TMP_PAL\n    STA TMP_X\n    LDA CLK_MIN\n    LSR\n    LSR\n    LSR\n    LSR\n    JSR add_obj\n    LDA #$70\n    STA TMP_X\n    LDA CLK_MIN\n    AND #$0F\n    JSR add_obj\nminutes_done:\n\n    ; ---- colours: a hit flashes the minutes white; :00 fades the new one in ----\n    LDA STAGE\n    CMP #$01\n    BEQ maybe_hit\n    CMP #$02\n    BNE no_hit\n    LDA SUBF\n    CMP #$1E\n    BCS no_hit\nmaybe_hit:\n    LDA LOCAL\n    CMP #$0C\n    BCC no_hit\n    CMP #$10\n    BCS no_hit\n    LDA #$0E\n    STA P2C1\n    STA P2C2\n    STA P2C3\nno_hit:\n    JSR fade_minutes\n\n    ; ---- the two attackers, zone 9 ----\n    LDA STAGE\n    CMP #$FF\n    BNE ships_on\n    JMP ships_done\nships_on:\n    CMP #$00\n    BNE not_arriving\n    LDA SUBF                  ; :57 -- A from the left to 92, B from the right to 112\n    CMP #$36\n    BCS a_there\n    ASL\n    SEC\n    SBC #$10\n    JMP a_set\na_there:\n    LDA #$5C\na_set:\n    STA SHIPA\n    LDA SUBF\n    CMP #$20\n    BCS b_there\n    ASL\n    STA TMP_G\n    LDA #$B0\n    SEC\n    SBC TMP_G\n    JMP b_set\nb_there:\n    LDA #$70\nb_set:\n    STA SHIPB\n    JMP place_ships\nnot_arriving:\n    CMP #$05\n    BNE on_station\n    LDA SUBF                  ; :02 -- they leave the way they came\n    ASL\n    STA TMP_G\n    LDA #$5C\n    SEC\n    SBC TMP_G\n    STA SHIPA\n    LDA #$70\n    CLC\n    ADC TMP_G\n    STA SHIPB\n    JMP place_ships\non_station:\n    LDA #$5C\n    STA SHIPA\n    LDA #$70\n    STA SHIPB\nplace_ships:\n    LDA #$3C                  ; palette 1\n    STA TMP_PAL\n    LDX #$09\n    LDA SHIPA\n    STA TMP_X\n    LDA #$0B\n    JSR add_obj\n    LDA SHIPB\n    STA TMP_X\n    LDA #$0B\n    JSR add_obj\n\n    ; ---- their shots: :58 and the first half of :59 ----\n    LDA STAGE\n    CMP #$01\n    BEQ shots_maybe\n    CMP #$02\n    BNE ships_done\n    LDA SUBF\n    CMP #$1E\n    BCS ships_done\nshots_maybe:\n    LDA LOCAL\n    CMP #$0C\n    BCS ships_done\n    ASL                       ; 4 lines a frame\n    ASL\n    STA TMP_G\n    LDA #$2C                  ; y = 44 - 4 * LOCAL, lines below the top of zone 6\n    SEC\n    SBC TMP_G\n    STA SHOTY\n    LSR\n    LSR\n    AND #$03\n    CLC\n    ADC #$0C                  ; the copy shifted to y's 4-line step\n    STA BOOM\n    LDA SHOTY\n    LSR\n    LSR\n    LSR\n    LSR\n    CLC\n    ADC #$06\n    TAX                       ; zone 6, 7 or 8\n    LDA #$7C\n    STA TMP_PAL\n    LDA SHIPA\n    STA TMP_X\n    LDA BOOM\n    JSR add_obj\n    LDA SHIPB\n    STA TMP_X\n    LDA BOOM\n    JSR add_obj\nships_done:\n"


WIDE_BASE = N_GLYPHS * 4                    # the wide tiles, after the glyphs
WIDE_LO = {}
_o = WIDE_BASE
for _n, _l, _b in WIDE:
    WIDE_LO[_n] = _o
    _o += _b
assert _o <= 256, "the glyph pages are full (%d bytes)" % _o

# the second block: the characters, then the words, then the snow
WORDS = ["GAME", "OVER", "STAGE", "CLEAR", "NEW", "HI"]
BLOCK2 = [(n, l) for n, l in small_chars()]           # one byte each
BLOCK2_WIDE = [("WD_" + w,) + word_lines(w) for w in WORDS] + [("SNOW", snow_tile(), 32),
               ("BAND", [[2] * 80 for _ in range(16)], 20)]
BLOCK2_LO = {}
_o = len(BLOCK2)
for _n, _l, _b in BLOCK2_WIDE:
    BLOCK2_LO[_n] = _o
    _o += _b
assert _o <= 256, "the second graphics block is full (%d bytes)" % _o


def source():
    L = [ln for ln in ASM.strip().splitlines()]
    a = L.append
    a("DLBASE   = $%04X" % DLBASE)
    a("DLBASE_B = $%04X" % DLBASE_B)
    a("HUD_DL   = $%04X" % (DLBASE + HUD_ZONE * DL_STRIDE))
    a("MAP1     = $%04X" % MAP1)
    a("MAP2     = $%04X" % MAP2)
    for i, (name, _) in enumerate(GLYPHS):
        a("G_%-8s= $%02X" % (name, i))
    a("MAP3     = $%04X" % MAP3)
    for i, (name, _) in enumerate(small_chars()):
        a("CH_%-6s= $%02X" % (name, i))
    for name, lines, nb in BLOCK2_WIDE:
        a("%-9s= $%02X" % (name, BLOCK2_LO[name]))
        if name == "SNOW":    # read through a 20-byte window that wanders over it
            a("PW_SNOW  = $%02X" % pw(1, 20))
        elif name == "BAND":
            pass
        else:
            a("PW_%-6s= $%02X" % (name[3:], pw(0, nb)))
    for name, _, _ in WIDE:
        a("W_%-8s= $%02X" % (name, WIDE_LO[name]))
    a("")
    a("    .org $%04X" % ROM_BASE)
    a(CODE_ASM.strip().replace("SHOOTER_CODE", SHOOTER.rstrip("\n")))
    a("code_end:")
    a("    .res $%04X-code_end,$00" % GFX2)
    a("gfx2:                 ; the second graphics block")
    for page in range(16):
        line = 15 - page
        data = []
        for _, ch in BLOCK2:
            px = ch[line]
            data.append((px[0] << 6) | (px[1] << 4) | (px[2] << 2) | px[3])
        for _, w, nb in BLOCK2_WIDE:
            for b in range(0, nb * 4, 4):
                data.append((w[line][b] << 6) | (w[line][b + 1] << 4) | (w[line][b + 2] << 2) | w[line][b + 3])
        a("    .byte " + ",".join("$%02X" % b for b in data))
        a("    .res $%X,$00" % (256 - len(data)))
    a("gfx2_end:")
    a("    .res $%04X-gfx2_end,$00" % NOISE)
    a("noise:                ; the static: 16 pages of random bytes")
    seed = 7800
    for _ in range(4096 // 32):
        row = []
        for _ in range(32):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            row.append((seed >> 16) & 0xFF)
        a("    .byte " + ",".join("$%02X" % b for b in row))
    a("gfx:")
    for page in range(16):
        line = 15 - page          # MARIA reads page (high + offset); offset counts 15..0
        data = []
        for _, g in GLYPHS:
            data += pack160a(g[line])
        for _, w, nb in WIDE:
            for b in range(0, nb * 4, 4):
                data.append((w[line][b] << 6) | (w[line][b + 1] << 4) | (w[line][b + 2] << 2) | w[line][b + 3])
        assert len(data) <= 256
        a("    .byte " + ",".join("$%02X" % b for b in data))
        a("    .res $%X,$00" % (256 - len(data)))
    a("")
    # four display list lists: zones 1-11 in the first or second set of lists,
    # each with holey DMA for 16-line zones on, or off (for the static)
    for name, second, head in (("dll_a", False, 0x4F), ("dll_b", True, 0x4F),
                               ("dlls_a", False, 0x0F), ("dlls_b", True, 0x0F)):
        a("%s:" % name)
        for z in range(DL_ZONES):
            dl = (DLBASE_B if second and 1 <= z <= 11 else DLBASE) + z * DL_STRIDE
            a("    .byte $%02X,$%02X,$%02X" % (head, dl >> 8, dl & 0xFF))
    a("dll_lo:")
    a("    .byte <dll_a,<dll_b,<dlls_a,<dlls_b")
    a("dll_hi:")
    a("    .byte >dll_a,>dll_b,>dlls_a,>dlls_b")
    a("times10:")
    a("    .byte 0,10,20,30")
    a("show_colours:          ; BACKGRND, P5C1-3, P6C1-3, P7C1-3 for each show")
    for c in SHOW_COLOURS:
        a("    .byte " + ",".join("$%02X" % v for v in c))
    items, first = [], []
    for ymin, its in BACKDROPS:
        first.append(len(items))
        items += its
    first.append(len(items))
    names = [n for n, _ in GLYPHS]
    wides = [n for n, _, _ in WIDE]
    pictures = dict(GLYPHS)
    pictures.update((n, l) for n, l, _ in WIDE)
    pictures.update((n, l) for n, l, _ in BLOCK2_WIDE)
    def height(g):        # lines down to its last drawn one
        if g == "STAR2":
            # a near star's streak is never carried into the next zone: at a zone's
            # last line only its grey tail shows, its white head drops out for a
            # moment as it crosses -- which reads as a twinkle, so it stays
            return 1
        return max(i + 1 for i, row in enumerate(pictures[g]) if any(row))
    def hb(g):
        return GFX2 >> 8 if g in BLOCK2_LO else GFX >> 8
    def lo(g):
        if g in BLOCK2_LO:
            return BLOCK2_LO[g]
        return names.index(g) * 4 if g in names else WIDE_LO[g]
    a("bd_moving:            ; 1: the backdrop moves, so it is rebuilt every frame")
    a("    .byte " + ",".join("$%02X" % int(any(t[2] != L_STILL for t in its)) for _, its in BACKDROPS))
    a("bd_zmin:")
    a("    .byte " + ",".join("$%02X" % (b[0] >> 4) for b in BACKDROPS))
    a("bd_first:")
    a("    .byte " + ",".join("$%02X" % f for f in first))
    for nm, fn in (("bd_x", lambda t: t[0]), ("bd_y", lambda t: t[1] & 0xFF), ("bd_l", lambda t: t[2]),
                   ("bd_g", lambda t: lo(t[3])), ("bd_pw", lambda t: t[4]),
                   ("bd_h", lambda t: height(t[3])), ("bd_hb", lambda t: hb(t[3]))):
        a("%s:" % nm)
        for k in range(0, len(items), 16):
            a("    .byte " + ",".join("$%02X" % fn(t) for t in items[k:k + 16]))
    a("zlo:")
    a("    .byte " + ",".join("$%02X" % ((DLBASE + z * DL_STRIDE) & 0xFF) for z in range(DL_ZONES)))
    a("zhi:")
    a("    .byte " + ",".join("$%02X" % ((DLBASE + z * DL_STRIDE) >> 8) for z in range(DL_ZONES)))
    a("DL_STRIDE_ASM = $%02X" % DL_STRIDE)
    a("hud_dl_init:          ; the black band (palette 6 colour 2), then character-mode")
    a("                      ; entries, palette 4: lives, HI, 1UP")
    a("    .byte BAND,$%02X,>gfx2,$00" % pw(6, 20))
    a("    .byte BAND,$%02X,>gfx2,$50" % pw(6, 20))
    a("    .byte $%02X,$60,$%02X,$96,$08" % (MAP1 & 0xFF, MAP1 >> 8))
    a("    .byte $%02X,$60,$%02X,$97,$3C" % (MAP3 & 0xFF, MAP3 >> 8))
    a("    .byte $%02X,$60,$%02X,$96,$70" % (MAP2 & 0xFF, MAP2 >> 8))
    a("times15:")
    a("    .byte $00,$0F,$1E")
    a("scene_tab:")
    a("    .word scene_shooter-1")
    a("    .word scene_drill-1")
    a("    .word scene_bomber-1")
    a("    .word scene_lander-1")
    a("cruiser_g:")
    a("    .byte " + ",".join("G_%s" % g for g in CRUISER))
    a("times30:")
    a("    .byte $00,$1E,$3C,$5A,$78,$96")
    a("tables_end:")
    a("    .res $FFFA-tables_end,$00")
    a("    .word nmi")
    a("    .word reset")
    a("    .word nmi")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--toolkit", required=True, help="path to the a7800-toolkit checkout")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(args.toolkit, "tools"))
    import asm
    import newgame

    src = source()
    with open(os.path.splitext(args.output)[0] + ".asm", "w") as f:
        f.write(src)
    data = asm.Assembler().assemble(src.splitlines())
    if len(data) != 0x10000 - ROM_BASE:
        raise SystemExit("assembled %d bytes, expected %d" % (len(data), 0x10000 - ROM_BASE))
    with open(args.output, "wb") as f:
        f.write(newgame.header(len(data), "PEBBLE CLOCK STUB"))
        f.write(data)
    print("wrote %s (%d bytes + 128-byte header)" % (args.output, len(data)))


if __name__ == "__main__":
    main()
