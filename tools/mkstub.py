#!/usr/bin/env python3
"""Build the clock cartridge: 16K of 7800 that stages every minute change by itself.

    python tools/mkstub.py --toolkit ../a7800-toolkit -o build/clockstub.a78

Everything it draws comes from the clock registers (docs/clock-registers.md),
read every frame -- never from a frame count, because the watch runs only short
bursts of frames and sleeps in between. Each hour (hour mod 4) has its own show,
from :57 to :02:

    0  formation   two attackers fly in, three volleys, the old minute explodes,
                   the new one fades in, they leave -- all through a falling
                   three-layer star field
    1  drill rig   a rig bores up through the rock under the clock, the old
                   minute shakes and crumbles, the rig pushes the new one up
                   into place and sinks away
    2  bomber run  the ground scrolls; a bomber climbs in, its bomb takes out the
                   old minute, the new one fades in as it flies off
    3  lander      a lander comes down, lifts the old minute away, and sets the
                   new one down gently in a puff of dust

Under it all: a cruiser that crosses the bottom once an hour, a step a minute,
and a lives-and-score bar in character mode -- one life per 20% of battery
(white; green while charging; red on the last one) and today's steps as the
1UP score. The cruiser and the lives are the hour's vehicle: ships, rigs,
planes or landers. The show is the hour's plus CLK_CHANNEL, which the watch
moves on at each wrist flick; whenever the show changes (a flick, the top of
the hour, power-on) the picture goes to 0.4 s of TV static first.

Within a second it counts frames, starting again whenever the seconds register
changes, so it cannot drift from the watch's clock. Real MARIA display lists
are rebuilt every frame in vertical blank, from 160A graphics in ROM laid out
line-planar for 16-line zones. Things that move up and down are placed to the
line by splitting them over two zones with shifted graphics pointers, and holey
DMA (on in every zone) blanks the pages either side of the graphics -- the way
7800 games position sprites vertically. Assembled with the a7800 toolkit's
tools/asm.py, given a header by its newgame.header().

16K, the size of the smallest commercial cartridge. No signature: the watch
starts it without a BIOS, as the a7800 fork of MAME does in its no-BIOS mode.
"""
import argparse
import math
import os
import sys

ROM_BASE = 0xC000      # code from here
NOISE = 0xD000         # 16 pages of random bytes for channel-change static
GFX = 0xE000           # 16 pages: page $E0 + (15 - line) holds line `line` of every glyph
TABLES = 0xF000        # the display list list and tables
DL_ZONES = 16          # 16 x 16 lines covers every DMA line (17-258)
DLBASE = 0x1900        # 64 bytes (15 entries) per zone's display list: $1900-$1CFF
DL_STRIDE = 64
HUD_ZONE = 12          # lines 210-225 (watch rows 168-183)
MAP1, MAP2 = 0x1D00, 0x1D10    # character maps for the bar's two entries

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
DUNE = [   # palette 6: olive, gold, sand; starts 5 lines down
    "......2222......",
    "....22222222....",
    "..222233332222..",
    ".22233311133322.",
    "2223311111113322",
    "3311111111111133",
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
        for _ in range(40):
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
    return g


GLYPHS = build_glyphs()
N_GLYPHS = len(GLYPHS)

# character mode (CTRL bit 4 clear): one byte, four pixels, per character, read
# from (CHARBASE + offset) << 8 | code -- the same pages as the glyphs, after them
CH_SPACE = N_GLYPHS * 4
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

# the bomber hour's ground: (x, phase, glyph, palette entry byte)
TERRAIN = [(12, 0, "BOULDER", 0xBC), (50, 20, "DUNE", 0xDC), (130, 8, "BOULDER", 0xBC),
           (84, 40, "DUNE", 0xDC), (30, 52, "BOULDER", 0xBC), (146, 36, "DUNE", 0xDC)]


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
            ("R1", char(SMALL_DIGITS[1], 2))]
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
SOFF     = $00AF     ; and $B0, $B1: how far each layer of stars has fallen
SIDX     = $00B2
LASTSCENE = $00B3    ; the show last frame, $FF at power-on
STATIC   = $00B4     ; frames of channel-change static still to show
RND      = $00B5     ; the static's random bytes (an 8-bit LFSR, never 0)
STATIC_FRAMES = $18  ; 24 frames, 0.4 s
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
    LDA #$22
    STA P5C1                  ; rock: dark brown
    LDA #$24
    STA P5C2                  ; tan
    LDA #$06
    STA P5C3                  ; grey
    LDA #$14
    STA P6C1                  ; dunes: olive
    LDA #$1A
    STA P6C2                  ; gold
    LDA #$18
    STA P6C3                  ; sand
    LDA #$86
    STA P7C1                  ; stars: dim blue
    LDA #$0A
    STA P7C2                  ; grey
    LDA #$0F
    STA P7C3                  ; white (the watch puts palette 7 behind its banner)
    LDA #$00
    TAX
clear:
    STA DLBASE,X
    STA DLBASE+$100,X
    STA DLBASE+$200,X
    STA DLBASE+$300,X
    INX
    BNE clear
    STA FRAME
    STA FRAMEH
    STA SUBF
    STA STATIC
    LDA #$FF                  ; no show yet: the first frame changes channel,
    STA LASTSCENE             ; so the set warms up through static
    LDA #$A7
    STA RND
    LDA #CH_SPACE             ; the bar's character maps start blank
    LDX #$1F
blank_maps:
    STA MAP1,X
    DEX
    BPL blank_maps
    LDX #$00                  ; zone 12's list, which never changes
hud_list:
    LDA hud_dl_init,X
    STA HUD_DL,X
    INX
    CPX #$0A
    BNE hud_list
    LDA #$E0                  ; characters come from the glyph pages
    STA CHARBASE
    LDA #$36
    STA P4C2                  ; bar: red 1UP
    LDA #$1A
    STA P4C3                  ; yellow score
    LDA CLK_SEC
    STA LASTSEC
    LDA #>dll
    STA DPPH
    LDA #<dll
    STA DPPL
    LDA #$40                  ; DMA on, 160A
    STA CTRL

main:
wait_vb:
    LDA MSTAT
    BPL wait_vb               ; everything below happens in vertical blank

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
    LDA #STATIC_FRAMES
    STA STATIC
same_show:
    LDA STATIC
    BEQ picture_dll
    LDA #>dll_static
    STA DPPH
    LDA #<dll_static
    STA DPPL
    LDA #$06                  ; palette 6 turns to the static's greys
    STA P6C1
    LDA #$0A
    STA P6C2
    LDA #$0F
    STA P6C3
    JMP dll_set
picture_dll:
    LDA #>dll
    STA DPPH
    LDA #<dll
    STA DPPL
    LDA #$14
    STA P6C1                  ; dunes: olive
    LDA #$1A
    STA P6C2                  ; gold
    LDA #$18
    STA P6C3                  ; sand
dll_set:
    LDA CLK_MIN
    JSR bcd_to_bin
    STA MINB
    LDA #$00
    STA MOFF

    ; ---- empty the lists of zones 1-11 ----
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

    LDA STATIC
    BEQ picture_on
    DEC STATIC
    JSR draw_static
    JMP the_bar
picture_on:

    ; ---- the formation hour flies through stars, behind everything else ----
    LDA SCENE
    BNE no_stars
    JSR stars
no_stars:

    ; ---- the hours and the colon, palette 0 ----
    LDA #$1C                  ; palette 0, 4 bytes wide
    STA TMP_PAL
    LDX #$05
    LDA #$20
    STA TMP_X
    LDA CLK_HOUR
    LSR
    LSR
    LSR
    LSR
    JSR add_obj
    LDA #$34
    STA TMP_X
    LDA CLK_HOUR
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
    LDA #$3C
    STA TMP_PAL
    LDY SCENE
    LDA cruiser_g,Y
    LDX #$0B
    JSR add_obj

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
dr_done:
    JMP scene_return

; ======================================================================
; hour 2: bomber run
; ======================================================================
scene_bomber:
    LDA STAGE                 ; the ground scrolls half a line a frame during the show
    CMP #$FF
    BEQ bm_still
    TAX
    LDA times30,X
    STA TMP2
    LDA SUBF
    LSR
    CLC
    ADC TMP2
    JMP bm_scroll
bm_still:
    LDA #$00
bm_scroll:
    STA SCRL
    LDY #$00
bm_ground:
    LDA terr_phase,Y
    CLC
    ADC SCRL
    AND #$3F
    CLC
    ADC #$60                  ; lines 96-159
    STA POSY
    LDA terr_x,Y
    STA TMP_X
    LDA terr_pal,Y
    STA TMP_PAL
    STY TMP_Y
    LDA terr_g,Y
    JSR put_y
    LDY TMP_Y
    INY
    CPY #$06
    BNE bm_ground
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

; the star field: three layers falling at 1/8, 1/4 and 1/2 a line a frame (the
; frame count, not the clock: they are scenery, and the count never jumps). The
; lists are empty when this runs, so the stars go first and everything else is
; drawn over them. Inline rather than add_obj_hi: 24 stars every frame add up.
stars:
    LDA FRAMEH
    STA TMP2
    LDA FRAME
    LSR TMP2
    ROR A
    STA SOFF+2                ; near: frames / 2
    LSR TMP2
    ROR A
    STA SOFF+1                ; middle: frames / 4
    LSR TMP2
    ROR A
    STA SOFF                  ; far: frames / 8
    LDY #N_STARS-1
star_loop:
    LDX star_l,Y
    LDA star_y,Y
    CLC
    ADC SOFF,X
    TAX                       ; the star's line, counting from zone 0's top
    LDA zone_of,X
    BMI star_next             ; not in zones 1-11
    STA TMP3
    LDA page_of,X             ; $E0 + the line within the zone
    STA TMP_HI
    LDX TMP3
    LDA ZCNT,X
    CMP #$0F
    BCS star_next
    INC ZCNT,X
    ASL
    ASL
    ORA zlo,X                 ; lists are 64-byte aligned
    STA PTR
    LDA zhi,X
    STA PTR+1
    LDA star_x,Y
    STA TMP_X
    LDA star_g,Y
    STY SIDX
    LDY #$00
    STA (PTR),Y
    INY
    LDA #$FF                  ; palette 7, one byte wide
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
    LDY SIDX
star_next:
    DEY
    BPL star_loop
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


def source():
    L = [ln for ln in ASM.strip().splitlines()]
    a = L.append
    a("DLBASE   = $%04X" % DLBASE)
    a("HUD_DL   = $%04X" % (DLBASE + HUD_ZONE * DL_STRIDE))
    a("MAP1     = $%04X" % MAP1)
    a("MAP2     = $%04X" % MAP2)
    for i, (name, _) in enumerate(GLYPHS):
        a("G_%-8s= $%02X" % (name, i))
    for i, (name, _) in enumerate(small_chars()):
        a("CH_%-6s= $%02X" % (name, CH_SPACE + i))
    a("")
    a("    .org $%04X" % ROM_BASE)
    a(CODE_ASM.strip().replace("SHOOTER_CODE", SHOOTER.rstrip("\n")))
    a("code_end:")
    a("    .res $%04X-code_end,$00" % NOISE)
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
        for _, ch in small_chars():
            px = ch[line]
            data.append((px[0] << 6) | (px[1] << 4) | (px[2] << 2) | px[3])
        assert len(data) <= 256
        a("    .byte " + ",".join("$%02X" % b for b in data))
        a("    .res $%X,$00" % (256 - len(data)))
    a("")
    a("dll:                  ; every zone 16 lines, holey DMA for 16-line zones on")
    for z in range(DL_ZONES):
        dl = DLBASE + z * DL_STRIDE
        a("    .byte $4F,$%02X,$%02X" % (dl >> 8, dl & 0xFF))
    a("dll_static:           ; the same zones with holey DMA off, for the static")
    for z in range(DL_ZONES):
        dl = DLBASE + z * DL_STRIDE
        a("    .byte $0F,$%02X,$%02X" % (dl >> 8, dl & 0xFF))
    a("zlo:")
    a("    .byte " + ",".join("$%02X" % ((DLBASE + z * DL_STRIDE) & 0xFF) for z in range(DL_ZONES)))
    a("zhi:")
    a("    .byte " + ",".join("$%02X" % ((DLBASE + z * DL_STRIDE) >> 8) for z in range(DL_ZONES)))
    a("hud_dl_init:          ; two character-mode entries, palette 4, 10 characters each")
    a("    .byte $%02X,$60,$%02X,$96,$08" % (MAP1 & 0xFF, MAP1 >> 8))
    a("    .byte $%02X,$60,$%02X,$96,$70" % (MAP2 & 0xFF, MAP2 >> 8))
    a("scene_tab:")
    a("    .word scene_shooter-1")
    a("    .word scene_drill-1")
    a("    .word scene_bomber-1")
    a("    .word scene_lander-1")
    a("cruiser_g:")
    a("    .byte " + ",".join("G_%s" % g for g in CRUISER))
    a("times30:")
    a("    .byte $00,$1E,$3C,$5A,$78,$96")
    a("terr_x:")
    a("    .byte " + ",".join("$%02X" % t[0] for t in TERRAIN))
    a("terr_phase:")
    a("    .byte " + ",".join("$%02X" % t[1] for t in TERRAIN))
    a("terr_g:")
    a("    .byte " + ",".join("G_%s" % t[2] for t in TERRAIN))
    a("terr_pal:")
    a("    .byte " + ",".join("$%02X" % t[3] for t in TERRAIN))
    a("N_STARS = %d" % len(STARS))
    a("star_x:")
    a("    .byte " + ",".join("$%02X" % t[0] for t in STARS))
    a("star_y:")
    a("    .byte " + ",".join("$%02X" % t[1] for t in STARS))
    a("star_l:")
    a("    .byte " + ",".join("$%02X" % t[2] for t in STARS))
    a("star_g:               ; glyph * 4, as a display list entry's low byte")
    star0 = [n for n, _ in GLYPHS].index("STAR0")
    a("    .byte " + ",".join("$%02X" % ((star0 + t[2]) * 4) for t in STARS))
    a("zone_of:              ; line -> zone, $FF outside zones 1-11")
    for r in range(0, 256, 32):
        a("    .byte " + ",".join("$%02X" % (y >> 4 if 16 <= y < 192 else 0xFF) for y in range(r, r + 32)))
    a("page_of:              ; line -> $E0 + its line within the zone")
    for r in range(0, 256, 32):
        a("    .byte " + ",".join("$%02X" % (0xE0 + (y & 15)) for y in range(r, r + 32)))
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
