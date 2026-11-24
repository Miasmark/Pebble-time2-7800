#!/usr/bin/env python3
"""Build the clock stub: an 8K 7800 cartridge that plays the minute change by itself.

    python tools/mkstub.py --toolkit ../a7800-toolkit -o build/clockstub.a78

Everything it draws comes from the clock registers (docs/clock-registers.md),
read every frame -- never from a frame count, because the watch runs only short
bursts of frames and sleeps in between:

    :57      two ships fly in from the sides and take station under the minutes
    :58-:59  three volleys; each hit flashes the minute digits white
    :59.5    the old minute explodes
    :00      the new minute fades in
    :02      the ships leave
    other    the clock, and a cruiser crossing the bottom once a minute

Under it all, a lives-and-score bar in character mode: one ship per 20% of
battery (white; green while charging; red on the last one) and today's steps as
the 1UP score.

Within a second it counts frames, and the count starts again every time the
seconds register changes, so it can never drift from the watch's clock. With
CLK_FLAGS bit 4 set (the wearer flicked their wrist) and nothing else going on,
the top rows of the digits cycle through the hues.

Real MARIA display lists rebuilt every frame in vertical blank, 160A graphics in
ROM laid out line-planar for 16-line zones, the display list list in ROM. The
shots move 4 lines at a time between zones using four pre-shifted copies. It is
assembled with the a7800 toolkit's tools/asm.py and given a header by its
newgame.header().

8K is half the smallest commercial cartridge (16K). No signature: the watch
starts it without a BIOS, as the a7800 fork of MAME does in its no-BIOS mode.
"""
import argparse
import math
import os
import sys

ROM_BASE = 0xE000
GFX = 0xE000           # 16 pages: page $E0 + (15 - line) holds line `line` of every glyph
CODE = 0xF000          # the display list list, tables, then the code
DL_ZONES = 16          # 16 x 16 lines covers every DMA line (17-258)
DLBASE = 0x1900        # 32 bytes per zone's display list
DL_STRIDE = 32
TIME_ZONE = 5          # lines 98-113 (watch rows 56-71)
SHOT_ZONE0 = 6         # shots travel through zones 6-8
SHIP_ZONE = 9          # the attackers
CRUISER_ZONE = 11
TIME_X = [32, 52, 72, 92, 112]
SHIP_A_X, SHIP_B_X = 92, 112   # under the minute digits

G_COLON, G_SHIP, G_SHOT0, G_BOOM0 = 10, 11, 12, 16
N_GLYPHS = 18
HUD_ZONE = 12          # lines 210-225 (watch rows 168-183)
MAP1, MAP2 = 0x1B00, 0x1B10    # character maps for the bar's two entries

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
MINI_SHIP = ["...11...", "..1111..", ".111111.", "11.11.11", "1......1"]


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
    out += [("SHIPL", char([r[:4] for r in MINI_SHIP], 1)),                       # tinted
            ("SHIPR", char([r[4:] for r in MINI_SHIP], 1))]
    return out

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

SHIP = [   # 16 x 9, colours 1-3 of palette 1
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


def blank():
    return [[0] * 16 for _ in range(16)]


def glyph_lines(rows):
    """A 5x7 font glyph as 16 lines of 16 pixels (colour per pixel, 0 = transparent).
    Each font pixel is 3 wide and 2 tall; rows 0-1 colour 3, 2-4 colour 2, 5-6 colour 1."""
    lines = blank()
    for r, bits in enumerate(rows):
        colour = 3 if r < 2 else (2 if r < 5 else 1)
        for c in range(5):
            if bits & (0x10 >> c):
                for dy in range(2):
                    for dx in range(3):
                        lines[1 + 2 * r + dy][c * 3 + dx] = colour
    return lines


def ship_lines():
    lines = blank()
    for r, row in enumerate(SHIP):
        for c, ch in enumerate(row):
            lines[4 + r][c] = 0 if ch == "." else int(ch)
    return lines


def shot_lines(phase):
    """A 2-pixel bolt, 4 lines tall, starting `phase` * 4 lines down the zone:
    white head, yellow tail (palette 3)."""
    lines = blank()
    for i, colour in enumerate((3, 3, 2, 2)):
        lines[phase * 4 + i][7] = lines[phase * 4 + i][8] = colour
    return lines


def boom_lines(frame):
    """Two frames of an explosion: spokes, then scattered sparks (palette 3: red, yellow, white)."""
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
OFFSET   = $38
CTRL     = $3C
CLK_SEC  = $4000
CLK_MIN  = $4001
CLK_HOUR = $4002
CLK_FLAGS = $4007
CLK_BATTERY = $4008
CLK_STEPS_HI = $4009
CLK_STEPS_MID = $400A
CLK_STEPS_LO = $400B
CHARBASE = $34
P4C1     = $31
P4C2     = $32
P4C3     = $33
LIVES    = $00A0

FRAME    = $0080     ; free-running, for flicker only
LASTSEC  = $0081
SUBF     = $0082     ; frames since the seconds register last changed (0-59)
STAGE    = $0083     ; 0-2 = :57-:59, 3-5 = :00-:02, $FF = nothing on
LOCAL    = $0084     ; frames into the current volley
SECB     = $0085     ; seconds, binary
PTR      = $0086     ; and $0087
TMP_G    = $0088
TMP_PAL  = $0089
TMP_X    = $008A
SHIPA    = $008B
SHIPB    = $008C
SHOTY    = $008D
BOOM     = $008E     ; a glyph number held across two add_obj calls
FADE_T   = $008F
ZCNT     = $0090     ; entries in each zone's list, $90-$9F
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
    STA P1C1                  ; ships: white
    LDA #$08
    STA P1C2                  ; grey
    LDA #$9E
    STA P1C3                  ; light blue
    LDA #$36
    STA P3C1                  ; shots and blasts: red
    LDA #$1E
    STA P3C2                  ; yellow
    LDA #$0E
    STA P3C3                  ; white
    LDA #$00
    TAX
clear:
    STA DLBASE,X
    STA DLBASE+$100,X
    INX
    BNE clear
    STA FRAME
    STA SUBF
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
    LDA CLK_SEC               ; seconds in binary: tens * 10 + ones
    LSR
    LSR
    LSR
    LSR
    STA TMP_G
    ASL
    ASL
    CLC
    ADC TMP_G
    ASL
    STA TMP_G
    LDA CLK_SEC
    AND #$0F
    CLC
    ADC TMP_G
    STA SECB
    LDA CLK_SEC               ; the stage: :57-:59 are 0-2, :00-:02 are 3-5
    CMP #$57
    BCC not_late
    SEC
    SBC #$57
    STA STAGE
    JMP stage_done
not_late:
    CMP #$03
    BCS nothing_on
    CLC
    ADC #$03
    STA STAGE
    JMP stage_done
nothing_on:
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

    ; ---- empty the lists of zones 5-11 ----
    LDX #$05
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
    LDA #$0A
    JSR add_obj

    ; ---- the minutes: palette 2, or from :59.5 the blast in palette 3 ----
    LDA STAGE
    CMP #$02
    BNE minute_digits
    LDA SUBF
    CMP #$1E
    BCC minute_digits
    LDA #$7C                  ; palette 3
    STA TMP_PAL
    LDA FRAME
    LSR
    LSR
    AND #$01
    CLC
    ADC #$10                  ; blast frame 16 or 17
    STA BOOM
    LDA #$5C
    STA TMP_X
    LDA BOOM
    JSR add_obj
    LDA #$70
    STA TMP_X
    LDA BOOM
    JSR add_obj
    JMP minutes_done
minute_digits:
    LDA #$5C                  ; palette 2 (and x = 92, as it happens)
    STA TMP_PAL
    STA TMP_X
    LDA CLK_MIN
    LSR
    LSR
    LSR
    LSR
    JSR add_obj
    LDA #$70
    STA TMP_X
    LDA CLK_MIN
    AND #$0F
    JSR add_obj
minutes_done:

    ; ---- colours ----
    JSR digit_colours
    LDA STAGE                 ; a hit flashes the minutes white
    CMP #$01
    BEQ maybe_hit
    CMP #$02
    BNE no_hit
    LDA SUBF
    CMP #$1E
    BCS no_hit
maybe_hit:
    LDA LOCAL
    CMP #$0C
    BCC no_hit
    CMP #$10
    BCS no_hit
    LDA #$0E
    STA P2C1
    STA P2C2
    STA P2C3
no_hit:
    LDA STAGE                 ; :00 -- the new minute fades in
    CMP #$03
    BNE no_fade
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
no_fade:
    LDA STAGE                 ; a wrist flick, with nothing else on: hues cycle
    CMP #$FF
    BNE no_look
    LDA CLK_FLAGS
    AND #$10
    BEQ no_look
    LDA FRAME
    ASL
    ASL
    AND #$F0
    ORA #$0C
    STA P0C3
    STA P2C3
no_look:

    ; ---- the two attackers, zone 9 ----
    LDA STAGE
    CMP #$FF
    BNE ships_on
    JMP ships_done
ships_on:
    CMP #$00
    BNE not_arriving
    LDA SUBF                  ; :57 -- A from the left to 92, B from the right to 112
    CMP #$36
    BCS a_there
    ASL
    SEC
    SBC #$10
    JMP a_set
a_there:
    LDA #$5C
a_set:
    STA SHIPA
    LDA SUBF
    CMP #$20
    BCS b_there
    ASL
    STA TMP_G
    LDA #$B0
    SEC
    SBC TMP_G
    JMP b_set
b_there:
    LDA #$70
b_set:
    STA SHIPB
    JMP place_ships
not_arriving:
    CMP #$05
    BNE on_station
    LDA SUBF                  ; :02 -- they leave the way they came
    ASL
    STA TMP_G
    LDA #$5C
    SEC
    SBC TMP_G
    STA SHIPA
    LDA #$70
    CLC
    ADC TMP_G
    STA SHIPB
    JMP place_ships
on_station:
    LDA #$5C
    STA SHIPA
    LDA #$70
    STA SHIPB
place_ships:
    LDA #$3C                  ; palette 1
    STA TMP_PAL
    LDX #$09
    LDA SHIPA
    STA TMP_X
    LDA #$0B
    JSR add_obj
    LDA SHIPB
    STA TMP_X
    LDA #$0B
    JSR add_obj

    ; ---- their shots: :58 and the first half of :59 ----
    LDA STAGE
    CMP #$01
    BEQ shots_maybe
    CMP #$02
    BNE ships_done
    LDA SUBF
    CMP #$1E
    BCS ships_done
shots_maybe:
    LDA LOCAL
    CMP #$0C
    BCS ships_done
    ASL                       ; 4 lines a frame
    ASL
    STA TMP_G
    LDA #$2C                  ; y = 44 - 4 * LOCAL, lines below the top of zone 6
    SEC
    SBC TMP_G
    STA SHOTY
    LSR
    LSR
    AND #$03
    CLC
    ADC #$0C                  ; the copy shifted to y's 4-line step
    STA BOOM
    LDA SHOTY
    LSR
    LSR
    LSR
    LSR
    CLC
    ADC #$06
    TAX                       ; zone 6, 7 or 8
    LDA #$7C
    STA TMP_PAL
    LDA SHIPA
    STA TMP_X
    LDA BOOM
    JSR add_obj
    LDA SHIPB
    STA TMP_X
    LDA BOOM
    JSR add_obj
ships_done:

    ; ---- the cruiser crosses the bottom once a minute: x = seconds * 2.5 ----
    LDA SECB
    LSR
    STA TMP_G
    LDA SECB
    ASL
    CLC
    ADC TMP_G
    STA TMP_X
    LDA #$3C
    STA TMP_PAL
    LDX #$0B
    LDA #$0B
    JSR add_obj

    ; ---- the bar: lives = battery in 20% steps (at least 1, at most 5) ----
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
    LDY #$00
    LDX #$00
fill_lives:
    CPX LIVES
    BCS no_life
    LDA #CH_SHIPL
    STA MAP1,Y
    LDA #CH_SHIPR
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
wait_novb:
    LDA MSTAT
    BMI wait_novb
    JMP main

; PTR = zone X's display list
set_ptr:
    LDA zlo,X
    STA PTR
    LDA zhi,X
    STA PTR+1
    RTS

; one 4-byte entry in zone X: glyph A, TMP_PAL (palette | width), at TMP_X; X is kept
add_obj:
    ASL
    ASL
    STA TMP_G
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
    LDA #>gfx
    STA (PTR),Y
    INY
    LDA TMP_X
    STA (PTR),Y
    INY
    INY
    LDA #$00                  ; the next entry's byte 1: the end of the list
    STA (PTR),Y
    INC ZCNT,X
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


def source():
    glyphs = [glyph_lines(d) for d in BIG_DIGITS] + [glyph_lines(COLON), ship_lines()]
    glyphs += [shot_lines(p) for p in range(4)] + [boom_lines(0), boom_lines(1)]
    assert len(glyphs) == N_GLYPHS
    L = [ln for ln in ASM.strip().splitlines()]
    a = L.append
    a("DLBASE   = $%04X" % DLBASE)
    a("HUD_DL   = $%04X" % (DLBASE + HUD_ZONE * DL_STRIDE))
    a("MAP1     = $%04X" % MAP1)
    a("MAP2     = $%04X" % MAP2)
    for i, (name, _) in enumerate(small_chars()):
        a("CH_%-6s= $%02X" % (name, CH_SPACE + i))
    a("")
    a("    .org $%04X" % ROM_BASE)
    a("gfx:")
    for page in range(16):
        line = 15 - page          # MARIA reads page (high + offset); offset counts 15..0
        data = []
        for g in glyphs:
            data += pack160a(g[line])
        for _, ch in small_chars():
            px = ch[line]
            data.append((px[0] << 6) | (px[1] << 4) | (px[2] << 2) | px[3])
        a("    .byte " + ",".join("$%02X" % b for b in data))
        a("    .res $%X,$00" % (256 - len(data)))
    a("")
    a("dll:")
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
    a("xpos:")
    a("    .byte " + ",".join("$%02X" % x for x in TIME_X))
    a("")
    a(CODE_ASM.strip())
    a("code_end:")
    a("    .res $FFFA-code_end,$00")
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
