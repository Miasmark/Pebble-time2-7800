"""The clock cartridge's shows 4-10, after the four built into mkstub.py. All
seven are in the shipped cartridge; mkstub.py --shows NAME,... (or none) builds
others for trying things out.

Each is a dict that mkstub.configure() folds into the cartridge:

    label     the scene's entry point
    asm       its code (ends in JMP scene_return)
    glyphs    16 x 16 pictures in 160A for the third graphics block ($C000)
    g320      32 x 16 pictures in 320A (1 bit a pixel, colour 2 of the palette)
    wides     wide tiles: (name, 16 rows of bytes, bytes wide)
    backdrop  (first line it may use, items (x, line, layer, glyph, palette|width))
    colours   BACKGRND, palette 5, palette 6, palette 7
    mini      the lives icon, 8 x 5
    cruiser   the glyph that crosses the bottom
    needs     framework features: block3, dli (zones 8-10 in 320A), diag (a
              diagonal backdrop layer), grow (a backdrop that grows over the hour),
              cycle (palette cycling)
    tables    extra tables, as asm

Glyph names from the first two blocks (STAR0, ROCK, DUNE, BAND...) can be used too.
"""

L_STILL, L_GROUND, L_DIAG = 3, 4, 5


def pw(palette, nbytes):
    assert 1 <= nbytes <= 31, nbytes
    return (palette << 5) | ((32 - nbytes) & 0x1F)


def rows320(rows):
    """32-pixel rows of '.'/'1' as 4 bytes each, 16 lines."""
    out = []
    for r in range(16):
        row = rows[r] if r < len(rows) else "." * 32
        row = row.ljust(32, ".")
        out.append([int("".join("1" if c != "." else "0" for c in row[b * 8:b * 8 + 8]), 2) for b in range(4)])
    return out


def cycle_table(phases):
    return "    .byte " + ",".join("$%02X" % c for ph in phases for c in ph)


# ======================================================================
# tower: a stone tower in 320A -- fine bricks in the television's artifact
# colours -- with the clock on top; a climber goes up the face, punches the old
# minute off into the night, and the new one drops into place
# ======================================================================
CLIMB = [  # palette 3 in 160A: red, yellow, white
    ["......33........", "......22........", ".1...2222...1...", ".22.222222.22...",
     "..2222222222....", "......2222......", "......1111......", ".....11..11.....",
     "....11....11....", "...22......22...", "...2........2..."],
    ["......33........", "......22..1.....", ".....222222.....", "....2222222.....",
     "..122.2222......", "..2...2222......", "......1111......", ".....11..11.....",
     ".....1....11....", ".....22....22...", "............2..."],
]


def climb320(rows):
    """The 160A climber as 320A: each pixel two wide, one colour."""
    return rows320(["".join(("11" if c != "." else "..") for c in r) for r in rows])


def tower_tile():
    """20 bytes of 320A brick: courses four lines high under solid mortar, each
    brick 16 pixels wide, every other course offset by half a brick. A brick's
    face is alternating pixels -- $AA or $55, which the television shows as two
    different artifact colours -- so the bricks come out in two tones."""
    rows = []
    for line in range(16):
        course, k = divmod(line, 4)
        if k == 0:
            rows.append([0xFF] * 20)
            continue
        row = []
        for b in range(20):
            pos = b + (2 if course % 2 else 0)
            face = 0xAA if (pos // 4) % 2 == 0 else 0x55
            row.append((face | 0xC0) if pos % 4 == 0 else face)
        row[0] |= 0xC0               # the tower's edges
        row[19] |= 0x03
        rows.append(row)
    return rows


CAP = [   # palette 5: the battlements, rows 8-15
    "................"] * 8 + [
    "1111......111111",
    "1221......122222",
    "1221......122222",
    "1111111111111111",
    "2222222222222222",
    "3333333333333333",
    ".22222222222222.",
    "..111111111111..",
]

TOWER = dict(
    label="scene_tower",
    needs={"block3", "dli"},
    glyphs=[("CLIMB0", CLIMB[0]), ("CLIMB1", CLIMB[1]), ("CAP", CAP)],
    g320=[("CLIMBW0", climb320(CLIMB[0])), ("CLIMBW1", climb320(CLIMB[1]))],
    wides=[("TOWER", tower_tile(), 20)],
    backdrop=(16, [(x, y, L_STILL, "STAR0", pw(7, 1)) for x, y in (
                      (10, 24), (40, 42), (150, 20), (124, 58), (70, 30), (20, 104),
                      (148, 112), (30, 70))] +
                  [(x, 112, L_STILL, "CAP", pw(5, 4)) for x in (64, 80, 96, 112, 128)] +
                  [(64, y, L_STILL, "TOWER", pw(7, 20)) for y in (128, 144, 160)] +
                  [(x, 176, L_STILL, "REGOLITH", pw(5, 10)) for x in (0, 40, 80, 120)]),
    colours=(0x80, 0x04, 0x08, 0x0C, 0x1A, 0x00, 0x18, 0x0A, 0x26, 0x08),
    mini=["...11...", ".1.11.1.", "..1111..", "...11...", "..1..1.."],
    cruiser="CLIMB0",
    asm="""
scene_tower:
    LDA #$43                  ; zones 8-10 in 320A, by display list interrupt
    STA NEXTCTRL
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$FF
    STA OBJY
    LDA SUBF                  ; S_T: 0-36 over a second
    LSR
    STA TMP2
    LSR
    LSR
    CLC
    ADC TMP2
    STA S_T
    LDX STAGE
    CPX #$06
    BCS tw_draw
    LDA tw_base,X             ; the climber: up the face, over the top, and down
    CPX #$02
    BEQ tw_top
    CPX #$03
    BEQ tw_set
    BCS tw_down
    SEC
    SBC S_T
    JMP tw_set
tw_down:
    CLC
    ADC S_T
    JMP tw_set
tw_top:
    LDA SUBF                  ; :59 -- a punch, and the old minute flies up
    CMP #$1E
    BCS tw_punch
    LSR
    LSR
    STA TMP2
    LDA #$68
    SEC
    SBC TMP2
    JMP tw_set
tw_punch:
    SBC #$1E
    STA TMP2
    ASL
    ADC TMP2
    STA TMP2
    LDA #$50
    SEC
    SBC TMP2
    STA POSY
    LDA #$62
tw_set:
    STA OBJY
tw_draw:
    LDA STAGE                 ; :00 -- the new one drops in
    CMP #$03
    BNE tw_min
    JSR ease48
    CLC
    ADC #$21
    STA POSY
tw_min:
    JSR fade_minutes
    JSR draw_min_y
    LDA OBJY
    CMP #$FF
    BEQ tw_done
    STA POSY
    LDA #$66
    STA TMP_X
    LDA #$7C                  ; palette 3
    STA TMP_PAL
    LDA FRAME
    LSR
    LSR
    LSR
    AND #$01
    TAY
    CLC
    ADC #G3_CLIMBW0           ; its 320A twin for the 320A zones
    STA PUTG2
    TYA
    CLC
    ADC #G3_CLIMB0
    JSR put_ym
tw_done:
    JMP scene_return
""",
    tables="""tw_base:                ; the climber's line at the start of each stage
    .byte $B0,$8C,$68,$68,$68,$8C
""",
)


# ======================================================================
# lava: a cave over a lake of fire (palette cycling); a rider on a winged
# mount charges the old minute into the lava, and the new one rises from it
# ======================================================================
RIDER = [  # palette 1: white, grey, light blue
    ["....33..........", "....22.......1..", "...222.....11...", "...2.2...111....",
     "...222.1111.....", "....21111111....", "..11111111111...", ".111111111.1.1..",
     "11.....111......", ".......1.1......", "......1...1....."],
    ["....33..........", "....22..........", "...222..........", "...2.2..........",
     "...222.1111.....", "....21111111....", "..11111111111...", ".11111111111.1..",
     "11....1111......", ".....111.1......", "....11....1....."],
]
STALAC = [  # palette 5
    "1111111111111111", "2212222122221222", ".22.222.2222.22.", ".2...22..22..2..",
    ".2...2...2...2..", ".....2...2......", ".........2......",
]


def lava_tile(surface=True):
    """20 bytes of 160A lava: a rolling surface (or none, below it) over bands of
    its three colours, which cycle."""
    import math
    px = []
    for line in range(16):
        row = []
        for x in range(80):
            top = 3 + int(round(2.5 * math.sin(x * 2 * math.pi / 26) + 1.2 * math.sin(x * 2 * math.pi / 11)))
            if surface and line < top:
                row.append(0)
            else:
                row.append(1 + ((x // 3 + line // 2 + (x * 7 % 5)) % 3))
        px.append(row)
    return pack_rows(px, 20)


def pack_rows(px, nb):
    out = []
    for row in px:
        out.append([(row[4 * b] << 6) | (row[4 * b + 1] << 4) | (row[4 * b + 2] << 2) | row[4 * b + 3]
                    for b in range(nb)])
    return out


LAVA = dict(
    label="scene_lava",
    needs={"block3", "cycle"},
    glyphs=[("RIDER0", RIDER[0]), ("RIDER1", RIDER[1]), ("STALAC", STALAC)],
    wides=[("LAVA", lava_tile(), 20), ("LAVAF", lava_tile(False), 20)],
    backdrop=(16, [(x, 25, L_STILL, "STALAC", pw(5, 4)) for x in (6, 38, 104, 140)] +
                  [(x, 144, L_STILL, "ROCK", pw(5, 4)) for x in (0, 16, 128, 144)] +
                  [(x, 160, L_STILL, "LAVA", pw(7, 20)) for x in (0, 80)] +
                  [(x, 176, L_STILL, "LAVAF", pw(7, 20)) for x in (0, 80)]),
    colours=(0x00, 0x22, 0x24, 0x06, 0x1A, 0x00, 0x18, 0x34, 0x38, 0x1C),
    mini=["11......", ".1111..1", "1111111.", "..1111..", ".1...1.."],
    cruiser="RIDER0",
    asm="""
scene_lava:
    LDA FRAME                 ; the lava's colours turn over every 8 frames
    LSR
    LSR
    LSR
    AND #$03
    STA TMP2
    ASL
    ADC TMP2
    ADC #CYC_LAVA
    TAY
    LDX #$07
    JSR cycle
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$FF
    STA OBJY
    LDA #$70
    STA S_Y
    LDA STAGE
    BNE lv_s1
    LDA SUBF                  ; :57 -- flies in from the left
    STA S_X
    JMP lv_rider
lv_s1:
    CMP #$01
    BNE lv_s2
    LDA #$3C                  ; :58 -- hovers, rising and falling
    STA S_X
    LDA FRAME
    LSR
    LSR
    AND #$07
    CLC
    ADC #$68
    STA S_Y
    JMP lv_rider
lv_s2:
    CMP #$02
    BNE lv_s3
    LDA SUBF                  ; :59 -- the charge, level with the clock
    CLC
    ADC #$3C
    STA S_X
    LDA #$4A
    STA S_Y
    LDA SUBF
    SEC
    SBC #$1E
    BCC lv_rider
    STA TMP2                  ; the second half: the old minute falls
    ASL
    ADC TMP2
    ADC #$50
    STA POSY
    CMP #$9C
    BCC lv_rider
    LDA #$FF                  ; and is gone into the lava
    STA POSY
    JMP lv_rider
lv_s3:
    CMP #$03
    BNE lv_min
    LDA SUBF                  ; :00 -- the new one rises out of it...
    LSR
    LSR
    STA TMP2
    LSR
    CLC
    ADC TMP2
    ADC SUBF
    STA TMP2
    LDA #$A0
    SEC
    SBC TMP2
    STA POSY
    LDA SUBF                  ; ... as the rider flies off
    LSR
    CLC
    ADC #$78
    STA S_X
    LDA #$48
    STA S_Y
lv_rider:
    LDA S_Y
    STA OBJY
lv_min:
    JSR fade_minutes
    LDA POSY
    CMP #$FF
    BEQ lv_nomin
    JSR draw_min_y
lv_nomin:
    LDA OBJY
    CMP #$FF
    BEQ lv_done
    STA POSY
    LDA S_X
    STA TMP_X
    LDA #$3C
    STA TMP_PAL
    LDA FRAME
    LSR
    LSR
    AND #$01
    CLC
    ADC #G3_RIDER0
    JSR put_y3
lv_done:
    JMP scene_return
""",
    cycle=[(0x34, 0x38, 0x1C), (0x38, 0x1C, 0x2A), (0x1C, 0x2A, 0x34), (0x2A, 0x34, 0x38)],
    tables="",
)


# ======================================================================
# desert: flying over the sand on a diagonal; a falcon carries the old minute
# off and brings the new one in
# ======================================================================
FALCON = [  # palette 5
    ["1..............1", "11............11", ".11..........11.", ".111...33...111.",
     "..1111.22.1111..", "...1111221111...", "....11122111....", "......1221......",
     ".......22.......", "......1111......", ".....1.11.1....."],
    ["................", "................", "................", ".......33.......",
     "....1111221111..", "..11111122111111", ".111....22....11", "11.....1221....1",
     ".......22.......", "......1111......", ".....1.11.1....."],
]
PYR = [  # palette 5: shade (and the shadow it casts), sunlit side
    ".......2........", "......222.......", ".....22221......", "....222211......",
    "...22222111.....", "..222222111.....", ".22222221111....", "2222222211111...",
    "22222222211111..", "..........11111.",
]
PALM = [  # palette 7: trunk (1), fronds (2), their shadow (3)
    "....2....2......", ".22.22..22..22..", "..2222222222....", ".222.2222.222...",
    "22...2112...22..", "......11.3......", ".....11..33.....", ".....11...33....",
    "....11.....33...", "....11......3...",
]

DESERT = dict(
    label="scene_desert",
    needs={"block3", "diag"},
    glyphs=[("FALCON0", FALCON[0]), ("FALCON1", FALCON[1]), ("PYRL", [r[:16] for r in PYR]),
            ("PALM", PALM)],
    backdrop=(16, [(x, y, L_DIAG, g, p) for x, y, g, p in (
                      (40, 30, "PYRL", pw(5, 4)), (170, 150, "PYRL", pw(5, 4)), (60, 52, "PYRL", pw(5, 4)),
                      (100, 64, "PALM", pw(7, 4)), (220, 100, "PALM", pw(7, 4)), (12, 196, "PALM", pw(7, 4)),
                      (140, 230, "PALM", pw(7, 4)), (110, 90, "PALM", pw(7, 4)),
                      (60, 120, "DUNE", pw(6, 4)), (200, 20, "DUNE", pw(6, 4)),
                      (120, 180, "DUNE", pw(6, 4)), (30, 240, "DUNE", pw(6, 4)), (240, 170, "DUNE", pw(6, 4)))] +
                  [(0, 80, L_STILL, "BAND", pw(6, 20)), (80, 80, L_STILL, "BAND", pw(6, 20))]),
    colours=(0x2C, 0x22, 0x1E, 0x0E, 0x1A, 0x00, 0x18, 0xC4, 0xC8, 0x28),
    mini=["1......1", "11.11.11", ".111111.", "...11...", "..1..1.."],
    cruiser="FALCON0",
    asm="""
scene_desert:
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$FF
    STA OBJY
    LDA #$00
    STA CARRY                 ; 1: the minutes hang under the falcon
    LDA #$3B
    SEC
    SBC SUBF
    STA S_T                   ; 59 down to 0 over the second
    LDA STAGE
    BEQ ds_in                 ; :57 -- swoops in from the top right
    CMP #$01
    BNE ds_s2
    LDA #$00                  ; :58 -- hovers over the clock
    JMP ds_in_at
ds_s2:
    CMP #$02
    BNE ds_s3
    LDA SUBF                  ; :59 -- off up to the left, with the old minute
    EOR #$FF
    CLC
    ADC #$01
    STA MOFF
    STA TMP2
    LDA #$66
    CLC
    ADC TMP2
    STA S_X
    LDA #$40
    SEC
    SBC SUBF
    STA OBJY
    INC CARRY
    JMP ds_draw
ds_s3:
    CMP #$03
    BNE ds_s4
    LDA S_T                   ; :00 -- back from the top right with the new one
    STA MOFF
    INC CARRY
    JMP ds_in
ds_s4:
    CMP #$04
    BNE ds_draw
    LDA SUBF                  ; :01 -- away up to the right
    STA TMP2
    LDA #$66
    CLC
    ADC TMP2
    STA S_X
    LDA #$40
    SEC
    SBC SUBF
    STA OBJY
    JMP ds_draw
ds_in:
    LDA S_T
ds_in_at:
    STA TMP2
    LDA #$66
    CLC
    ADC TMP2
    STA S_X
    LDA #$40
    SEC
    SBC TMP2
    STA OBJY
ds_draw:
    LDA CARRY
    BEQ ds_min
    LDA OBJY
    CLC
    ADC #$10
    STA POSY
ds_min:
    JSR draw_min_y
    LDA OBJY
    CMP #$FF
    BEQ ds_done
    STA POSY
    LDA S_X
    STA TMP_X
    LDA #$BC                  ; palette 5
    STA TMP_PAL
    LDA FRAME
    LSR
    LSR
    AND #$01
    CLC
    ADC #G3_FALCON0
    JSR put_y3
ds_done:
    JMP scene_return
""",
    tables="",
)


# ======================================================================
# night race: a road into the dark, its kerbs' stripes cycling so it rushes
# by; the old minute comes down the road and past, the new one rises off the
# horizon
# ======================================================================
def kerb(side):
    rows = []
    for r in range(16):
        c = 1 if (r // 4) % 2 == 0 else 2
        row = ["."] * 16
        x0 = 15 - r if side == "L" else r
        for dx in range(3):
            x = x0 + (dx if side == "L" else -dx)
            if 0 <= x < 16:
                row[x] = str(c)
        edge = x0 + 3 if side == "L" else x0 - 3
        if 0 <= edge < 16:
            row[edge] = "3"
        rows.append("".join(row))
    return rows


def car(shift):
    """32 x 14 from behind, palette 3: red body, yellow tail lights, white
    window, plate and bumper. `shift`: the body leans that many pixels at the
    roof as it turns (+ right, - left); the wheels stay put."""
    body = ["111111111111", "1133333333333311", "113333333333333311", "11111111111111111111",
            "111111111111111111111111", "11111111111111111111111111", "1222211111111111111111122221",
            "1222211111111111111111122221", "1111111111113333111111111111", "1111111111111111111111111111",
            "3333333333333333333333333333"]
    rows = []
    for r, b in enumerate(body):
        d = int(round(shift * (10 - r) / 10))
        pad = (32 - len(b)) // 2 + d
        rows.append(("." * pad + b).ljust(32, ".")[:32])
    rows += ["..1111....................1111..", "..1111....................1111..", "..1111....................1111.."]
    return [""] * 2 + rows


def centre_column(zone, width):
    """One byte (4 pixels) of the centre line for a zone: bands that shorten
    toward the horizon, coloured 1, 2, 3 in turn by distance. Only one of the
    three colours is lit at a time (palette 5, cycled), so the dashes run
    toward the car."""
    band, y, j = {}, 175, 0
    left = 7.0
    while y >= 96:
        band[y] = j
        left -= 1
        if left <= 0:
            j += 1
            left += 2 + 5 * (y - 96) / 79
        y -= 1
    cols = {1: [1], 2: [1, 2], 4: [0, 1, 2, 3]}[width]
    rows = []
    for line in range(16):
        c = band[16 * zone + line] % 3 + 1
        px = [c if k in cols else 0 for k in range(4)]
        rows.append([(px[0] << 6) | (px[1] << 4) | (px[2] << 2) | px[3]])
    return rows


RACE = dict(
    label="scene_race",
    needs={"block3", "cycle"},
    glyphs=[("KERBL", kerb("L")), ("KERBR", kerb("R"))] +
           [(n + h, [r[:16] if h == "L" else r[16:] for r in car(sh)])
            for n, sh in (("CAR", 0), ("CARR", 5), ("CARL", -5)) for h in "LR"],
    wides=[("CLINE%d" % z, centre_column(z, w), 1) for z, w in ((7, 1), (8, 2), (9, 2), (10, 4))],
    backdrop=(16, [(x, y, L_STILL, "STAR0", pw(6, 1)) for x, y in (
                      (12, 30), (44, 18), (90, 40), (130, 26), (150, 60), (70, 66), (24, 76), (118, 82))] +
                  # the kerbs, 16 pixels apart at the horizon
                  [((56 - 16 * k) & 0xFF, 96 + 16 * k, L_STILL, "KERBL", pw(7, 4)) for k in range(5)] +
                  [(88 + 16 * k, 96 + 16 * k, L_STILL, "KERBR", pw(7, 4)) for k in range(5)] +
                  [(78, 96, L_STILL, "CLINE7", pw(5, 1))] +
                  [(78, 16 * z, L_STILL, "CLINE%d" % z, pw(5, 1)) for z in (7, 8, 9, 10)]),
    colours=(0x00, 0x00, 0x00, 0x0E, 0x1A, 0x00, 0x18, 0x36, 0x0E, 0x06),
    mini=["..1111..", ".111111.", "11111111", "1.1111.1", "11....11"],
    cruiser="CARL",
    cruiser_bytes=8,          # the whole car: CARL and CARR sit side by side
    # the kerbs (palette 7): red and white trade places; the centre line
    # (palette 5): white in colour 3, 2, then 1 -- nearer each time
    cycle=[(0x36, 0x0E, 0x06), (0x0E, 0x36, 0x06),
           (0x00, 0x00, 0x0E), (0x00, 0x0E, 0x00), (0x0E, 0x00, 0x00)],
    asm="""
scene_race:
    LDA FRAME                 ; the kerbs: red and white change places every
    LSR                       ; 4 frames -- once each time the watch draws
    LSR
    AND #$01
    STA TMP2
    ASL
    ADC TMP2
    ADC #CYC_RACE
    TAY
    LDX #$07
    JSR cycle
    LDA FRAME                 ; the centre line: a step nearer every 4 frames,
    LSR                       ; (frames / 4) mod 3 -- frames counted in 16 bits,
    LSR                       ; and 256 / 4 = 64 is 1 mod 3, so FRAMEH just adds
    CLC
    ADC FRAMEH
    BCC rc_mod3
    ADC #$00                  ; (carry set: + 1, as 256 is 1 mod 3)
rc_mod3:
    CMP #$03
    BCC rc_phase
    SBC #$03
    BCS rc_mod3
rc_phase:
    STA TMP2
    ASL
    ADC TMP2
    ADC #CYC_RACE+6
    TAY
    LDX #$01
    JSR cycle
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$40                  ; the car: its left half's x, and straight on
    STA S_X
    LDA #G3_CARL
    STA S_G
    LDA STAGE
    CMP #$02
    BNE rc_s3
    LDA SUBF                  ; :59 -- the old minute comes down the road...
    CMP #$1A
    BCC rc_come
    LDA #$1A
rc_come:
    ASL
    ADC #$50                  ; ... to line 132, just ahead of the car
    STA POSY
    LDA SUBF                  ; the car turns into it
    SEC
    SBC #$14
    BCC rc_min
    CMP #$14
    BCC rc_turning
    LDA #$14
rc_turning:
    ADC #$40
    STA S_X
    LDA #G3_CARRL
    STA S_G
    LDA SUBF                  ; and shoves it off the road
    SEC
    SBC #$1C
    BCC rc_min
    STA TMP2
    ASL
    ADC TMP2
    STA MOFF
    JMP rc_min
rc_s3:
    CMP #$03
    BNE rc_min
    LDA SUBF                  ; :00 -- the car turns back to the middle...
    LSR
    CMP #$14
    BCS rc_new
    STA TMP2
    LDA #$54
    SEC
    SBC TMP2
    STA S_X
    LDA #G3_CARLL
    STA S_G
rc_new:
    LDA SUBF                  ; ... and the new minute rises off the horizon
    CLC
    ADC #$04
    LSR
    LSR
    STA TMP2
    LDA #$5F
    SEC
    SBC TMP2
    STA POSY
rc_min:
    JSR fade_minutes
    JSR draw_min_y
    LDA S_G                   ; going straight, it weaves a little
    CMP #G3_CARL
    BNE rc_car
    LDA FRAME
    LSR
    LSR
    LSR
    LSR
    AND #$03
    CMP #$03
    BNE rc_sway
    LDA #$01
rc_sway:
    CLC
    ADC S_X
    STA S_X
rc_car:
    LDA #$98                  ; 32 x 16, two glyphs side by side, palette 3
    STA POSY
    LDA #$7C
    STA TMP_PAL
    LDA S_X
    STA TMP_X
    LDA S_G
    JSR put_y3
    LDA S_X
    CLC
    ADC #$10
    STA TMP_X
    LDA S_G
    CLC
    ADC #$01
    JSR put_y3
    JMP scene_return
""",
    tables="",
)


# ======================================================================
# dojo: a sunset in bands below the clock -- four zones, four palettes' colour
# 2, one band tile -- with black silhouettes; two fighters spar, and a flying
# kick knocks the old minute away
# ======================================================================
FIGHT = [  # palette 6, colour 2: black
    "......22........", "......22........", ".......2........", ".....2222.......",
    "....22222222....", "...2.2222...2...", "......222.......", "......2222......",
    ".....22..22.....", ".....2....2.....", "....22....22....", "...22......22...",
]
KICK = [
    "..22............", "..22............", "...2............", "..2222..........",
    ".222222.........", "2..22222........", "....222222222222", "....222.........",
    "...22...........", "..22............", ".22.............",
]


def mirror(rows):
    return [r.ljust(16, ".")[::-1] for r in rows]


SUN = [  # palette 7 colour 1: the upper half of the sun, 32 wide
    "...........1111111111...........", "........1111111111111111........",
    "......11111111111111111111......", ".....1111111111111111111111.....",
    "....111111111111111111111111....", "...11111111111111111111111111...",
    "..1111111111111111111111111111..", "..1111111111111111111111111111..",
    ".111111111111111111111111111111.", ".111111111111111111111111111111.",
    "11111111111111111111111111111111", "11111111111111111111111111111111",
    "11111111111111111111111111111111", "11111111111111111111111111111111",
    "11111111111111111111111111111111", "11111111111111111111111111111111",
]
PAGODA = [  # palette 6 colour 2, 32 wide
    "...............22...............", "..............2222..............",
    "..2.........22222222.........2..", "...222222222222222222222222222...",
    ".....222222222222222222222222...", "........2..............2........",
    "........2..2222222222..2........", ".2......22222222222222222.....2.",
    "..222222222222222222222222222222", "....222222222222222222222222....",
    "......2................2........", "......2..222222222222..2........",
    "......2..2..2..2..2..2.2........", "......2..2..2..2..2..2.2........",
    "..222222222222222222222222222...", "22222222222222222222222222222222",
]
PINE = [
    ".......2........", "......222.......", ".....22222......", "......222.......",
    "....2222222.....", "...222222222....", ".....22222......", "...222222222....",
    "..22222222222...", ".2222222222222..", ".......2........", ".......2........",
    "......222.......", "2222222222222222", "2222222222222222", "2222222222222222",
]

DOJO = dict(
    label="scene_dojo",
    needs={"block3"},
    glyphs=[("FIGHTL", FIGHT), ("FIGHTR", mirror(FIGHT)), ("KICK", KICK),
            ("SUNL", [r[:16] for r in SUN]), ("SUNR", [r[16:] for r in SUN]),
            ("PAGL", [r[:16] for r in PAGODA]), ("PAGR", [r[16:] for r in PAGODA]), ("PINE", PINE)],
    backdrop=(96, [(x, 96 + 16 * k, L_STILL, "BAND", pw(p, 20)) for k, p in enumerate((5, 4, 0, 7))
                   for x in (0, 80)] +
                  [(64, 128, L_STILL, "SUNL", pw(7, 4)), (80, 128, L_STILL, "SUNR", pw(7, 4)),
                   (8, 144, L_STILL, "PAGL", pw(6, 4)), (24, 144, L_STILL, "PAGR", pw(6, 4)),
                   (126, 144, L_STILL, "PINE", pw(6, 4)), (142, 144, L_STILL, "PINE", pw(6, 4))]),
    colours=(0x00, 0x52, 0x54, 0x56, 0x1A, 0x00, 0x18, 0x1E, 0x1C, 0x0E),
    mini=["..11....", "..1.....", "111111..", "..11..11", ".1..1..."],
    cruiser="FIGHTL",
    asm="""
scene_dojo:
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$FF
    STA OBJY                  ; the left fighter's line ($FF: no fighters)
    LDA #$90
    STA S_Y
    LDA #G3_FIGHTL
    STA S_G
    LDA SUBF                  ; S_T: 0-36 over a second
    LSR
    STA TMP2
    LSR
    LSR
    CLC
    ADC TMP2
    STA S_T
    LDA STAGE
    BNE dj_s1
    LDA S_T                   ; :57 -- they walk in from either side
    STA S_X
    JMP dj_pair
dj_s1:
    CMP #$01
    BNE dj_s2
    LDA FRAME                 ; :58 -- they spar
    LSR
    LSR
    AND #$03
    CLC
    ADC #$24
    STA S_X
    JMP dj_pair
dj_s2:
    CMP #$02
    BNE dj_s3
    LDA #G3_KICK              ; :59 -- a flying kick
    STA S_G
    LDA SUBF
    CMP #$1E
    BCS dj_hit
    STA TMP2                  ; up and across to the clock
    LSR
    ADC TMP2
    STA TMP2
    ADC #$26
    STA S_X
    LDA #$90
    SEC
    SBC TMP2
    STA S_Y
    JMP dj_pair
dj_hit:
    SBC #$1E                  ; the old minute flies off to the right...
    STA TMP2
    ASL
    ADC TMP2
    STA MOFF
    LDA #$53                  ; ... and the kicker comes down
    STA S_X
    LDA TMP2
    LSR
    ADC TMP2
    ADC #$63
    STA S_Y
    JMP dj_pair
dj_s3:
    CMP #$03
    BNE dj_s4
    LDA #$53                  ; :00 -- landed; the new minute fades in
    STA S_X
    JMP dj_pair
dj_s4:
    CMP #$04
    BNE dj_min
    LDA #$53                  ; :01 -- they walk off
    SEC
    SBC S_T
    SEC
    SBC S_T
    STA S_X
dj_pair:
    LDA S_Y
    STA OBJY
dj_min:
    JSR fade_minutes
    JSR draw_min_y
    LDA OBJY
    CMP #$FF
    BEQ dj_done
    LDA #$DC                  ; palette 6: black
    STA TMP_PAL
    LDA #$90                  ; the right one
    STA POSY
    LDA #$90
    SEC
    SBC S_T
    LDX STAGE
    BEQ dj_r
    LDA #$68
    CPX #$04
    BNE dj_r
    CLC
    ADC S_T
dj_r:
    STA TMP_X
    LDA #G3_FIGHTR
    JSR put_y3
    LDA OBJY                  ; the left one
    STA POSY
    LDA S_X
    STA TMP_X
    LDA S_G
    JSR put_y3
dj_done:
    JMP scene_return
""",
    tables="",
)


# ======================================================================
# dungeon: a brick wall, two torches flickering (palette cycling) and a door;
# a knight arrives, the door opens, the old minute is struck away, and the new
# one comes up out of the doorway
# ======================================================================
def brick_tile():
    px = []
    for line in range(16):
        course, k = divmod(line, 8)
        row = []
        for x in range(80):
            if k == 7:
                row.append(0)
            elif (x + (8 if course else 0)) % 16 == 15:
                row.append(0)
            elif k == 0 or (x + (8 if course else 0)) % 16 == 0:
                row.append(2)
            else:
                row.append(1)
        px.append(row)
    return pack_rows(px, 20)


TORCH = [  # palette 5, cycled: the flame's three colours
    "......1.........", ".....121........", "....12321.......", "....12321.......",
    ".....121........", "......1.........", "....1111111.....", ".....11111......",
    "......111.......", "......111.......", "......111.......", ".......1........",
]
DOOR = [  # palette 6: gold planks, black between them; 32 x 32
    "..........111111111111..........", ".......1111333333333311111......",
    ".....1133332132132132133331.....", "....113332213213213213223311....",
    "...11332213213213213213223311...", "...13322132132132132132132231...",
    "..1332213213213213213213213231..", "..1322132132132132132132132231..",
] + ["..13221321321321321321321322" + "31.."] * 8
DOOR_B = ["..1322132132132132132132132231.."] * 5 + ["..1322133332132132132132132231..",
          "..1322131132132132132132132231..", "..1322133332132132132132132231.."] + \
         ["..1322132132132132132132132231.."] * 7 + ["11111111111111111111111111111111"]
OPEN = [  # the doorway: black inside the frame
    "..........111111111111..........", ".......1111222222222211111......",
    ".....1122222222222222222221.....", "....112222222222222222222211....",
    "...11222222222222222222222211...", "...12222222222222222222222221...",
    "..1222222222222222222222222221..", "..1222222222222222222222222221..",
] + ["..1222222222222222222222222221.."] * 8
OPEN_B = ["..1222222222222222222222222221.."] * 15 + ["11111111111111111111111111111111"]
KNIGHT = [  # palette 1
    ["......333.......", ".....33333......", ".....3.3.3......", "......333....2..",
     "....1111111..2..", "...111111111.2..", "...1.11111.1.2..", "...1.11111.112..",
     ".....11111......", ".....11.11......", ".....11.11......", "....111.111....."],
    ["......333.......", ".....33333......", ".....3.3.3......", "......333....2..",
     "....1111111..2..", "...111111111.2..", "...1.11111.1.2..", "...1.11111.112..",
     ".....11111......", "....11...11.....", "...11.....11....", "...1.......11..."],
]


def halves(rows):
    return [r[:16] for r in rows], [r[16:] for r in rows]


DUNGEON = dict(
    label="scene_dungeon",
    needs={"block3", "cycle"},
    glyphs=[("DOORTL", halves(DOOR)[0]), ("DOORTR", halves(DOOR)[1]),
            ("DOORBL", halves(DOOR_B)[0]), ("DOORBR", halves(DOOR_B)[1]),
            ("OPENTL", halves(OPEN)[0]), ("OPENTR", halves(OPEN)[1]),
            ("OPENBL", halves(OPEN_B)[0]), ("OPENBR", halves(OPEN_B)[1]),
            ("TORCH", TORCH), ("KNIGHT0", KNIGHT[0]), ("KNIGHT1", KNIGHT[1])],
    wides=[("BRICK", brick_tile(), 20)],
    backdrop=(96, [(x, y, L_STILL, "BRICK", pw(7, 20)) for y in (96, 112, 128, 144, 160) for x in (0, 80)] +
                  [(20, 104, L_STILL, "TORCH", pw(5, 4)), (124, 104, L_STILL, "TORCH", pw(5, 4))]),
    colours=(0x00, 0x36, 0x1E, 0x0E, 0x1A, 0x00, 0x18, 0x04, 0x08, 0x0C),
    mini=["..111...", "..1.1..1", ".11111.1", "1.111.11", "..1.1..."],
    cruiser="KNIGHT0",
    asm="""
scene_dungeon:
    LDA FRAME                 ; the torches flicker
    LSR
    LSR
    AND #$03
    STA TMP2
    ASL
    ADC TMP2
    ADC #CYC_DUNGEON
    TAY
    LDX #$01
    JSR cycle
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA #$FF
    STA OBJY                  ; the knight's line ($FF: not here)
    LDA #G3_DOORTL
    STA S_G                   ; the door: shut, or open from :58 to :00
    LDA SUBF
    LSR
    STA TMP2
    LSR
    LSR
    CLC
    ADC TMP2
    STA S_T                   ; 0-36 over a second
    LDA STAGE
    BNE dg_s1
    LDA S_T                   ; :57 -- walks in from the left
    ASL
    STA S_X
    JMP dg_here
dg_s1:
    CMP #$04
    BCS dg_s4
    LDX #G3_OPENTL
    STX S_G
    LDX #$40
    STX S_X
    CMP #$02
    BNE dg_s3
    LDA SUBF                  ; :59 -- the old minute shakes, then is struck away
    CMP #$1E
    BCS dg_blast
    AND #$02
    STA MOFF
    JMP dg_here
dg_blast:
    LDA #$7C
    STA TMP_PAL
    JSR draw_blast
    LDA #$FF
    STA POSY
    JMP dg_here
dg_s3:
    CMP #$03
    BNE dg_here
    LDA SUBF                  ; :00 -- the new one comes up out of the doorway
    LSR
    LSR
    LSR
    LSR
    STA TMP2
    LDA #$88
    SEC
    SBC SUBF
    CLC
    ADC TMP2
    STA POSY
    JMP dg_here
dg_s4:
    CMP #$06
    BCS dg_door
    LDA S_T                   ; :01 -- the door shuts and he walks off
    ASL
    ADC #$40
    LDX STAGE
    CPX #$05
    BNE dg_x4
    LDA #$FF
    STA OBJY
    BNE dg_door
dg_x4:
    STA S_X
dg_here:
    LDA #$90
    STA OBJY
dg_door:
    LDA #$DC                  ; palette 6
    STA TMP_PAL
    LDX #$08
    LDA #$5E
    STA TMP_X
    LDA S_G
    JSR add_obj3
    LDA #$6E
    STA TMP_X
    LDA S_G
    CLC
    ADC #$01
    JSR add_obj3
    INX
    LDA #$5E
    STA TMP_X
    LDA S_G
    CLC
    ADC #$02
    JSR add_obj3
    LDA #$6E
    STA TMP_X
    LDA S_G
    CLC
    ADC #$03
    JSR add_obj3
    JSR fade_minutes
    LDA POSY
    CMP #$FF
    BEQ dg_nomin
    JSR draw_min_y
dg_nomin:
    LDA OBJY
    CMP #$FF
    BEQ dg_done
    STA POSY
    LDA S_X
    STA TMP_X
    LDA #$3C
    STA TMP_PAL
    LDA FRAME
    LSR
    LSR
    LSR
    AND #$01
    CLC
    ADC #G3_KNIGHT0
    JSR put_y3
dg_done:
    JMP scene_return
""",
    cycle=[(0x36, 0x1E, 0x0E), (0x38, 0x1C, 0x0E), (0x34, 0x1E, 0x0C), (0x36, 0x1A, 0x0E)],
    tables="",
)


# ======================================================================
# mushroom garden: the field fills up over the hour (six mushrooms at :00,
# 23 by :59); a centipede winds down through it, and the old minute is shot
# ======================================================================
MUSH = [  # palette 7: cap, stem, spots
    "..1111..", ".131131.", "11111111", "...22...", "...22...", "..2222..",
]
HEAD = ["..2..2..", "...22...", ".333333.", "33133133", "33333333", ".333333.", "..3..3.."]
BODY = ["........", "........", ".111111.", "11211211", "11111111", ".111111.", "..1..1.."]
WAND = [  # palette 1
    ".......3........", ".......3........", "......323.......", "......222.......",
    ".....11111......", "....1111111.....", "....11.1.11.....",
]


def mush_positions():
    seed, out = 1986, []
    for i in range(24):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        row = i % 5
        x = 4 + (i * 37 + (seed >> 12) % 23) % 148
        out.append((x, 96 + 16 * row + (seed >> 20) % 9))
    return out


MUSHROOM = dict(
    label="scene_mush",
    needs={"block3", "grow"},
    glyphs=[("MUSH", MUSH), ("HEAD", HEAD), ("BODY", BODY), ("WAND", WAND)],
    backdrop=(96, [(x, y, L_STILL, "MUSH", pw(7, 2)) for x, y in mush_positions()]),
    colours=(0x00, 0xC6, 0x0E, 0x1C, 0x1A, 0x00, 0x18, 0x36, 0x0E, 0x4C),
    mini=["..1111..", ".1.11.1.", "11111111", "...11...", "..1111.."],
    cruiser="HEAD",
    grow=True,
    asm="""
scene_mush:
    LDA #$50
    STA POSY
    LDA CLK_MIN
    STA DIGM
    LDA STAGE
    CMP #$03
    BCC mu_on
    JMP mu_min
mu_on:
    TAX                       ; :57-:59 -- the centipede: T = 0-179
    LDA times60,X
    CLC
    ADC SUBF
    STA S_T
    LDA #$BC                  ; palette 5
    STA TMP_PAL
    LDA #$05
    STA S_I                   ; segments 5 (the tail) to 0 (the head)
mu_seg:
    LDA S_I
    ASL
    ADC S_I
    ASL                       ; 6 steps between segments
    STA TMP2
    LDA S_T
    SEC
    SBC TMP2
    BCC mu_next               ; not in yet
    STA S_P
    AND #$3F
    ASL                       ; along its row, two pixels a step...
    STA TMP_X
    LDA S_P
    AND #$40
    BEQ mu_ltr
    LDA #$86                  ; ... right to left on odd rows
    SEC
    SBC TMP_X
    JMP mu_x
mu_ltr:
    LDA TMP_X
    CLC
    ADC #$08
mu_x:
    STA TMP_X
    LDA S_P                   ; 14 lines a row
    LSR
    LSR
    LSR
    LSR
    LSR
    LSR
    TAX
    LDA mu_rows,X
    STA POSY
    LDA #G3_BODY
    LDX S_I
    BNE mu_put
    LDA #G3_HEAD
mu_put:
    JSR put_y3
mu_next:
    DEC S_I
    BPL mu_seg
    LDA #$3C                  ; the wand, under the clock
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDA #$A0
    STA POSY
    LDA #G3_WAND
    JSR put_y3
    LDA #$50
    STA POSY
    LDA STAGE
    CMP #$02
    BNE mu_min
    LDA SUBF                  ; :59 -- a shot, then the blast
    SEC
    SBC #$1E
    BCC mu_min
    CMP #$0F
    BCS mu_blast
    STA TMP2
    ASL
    ASL
    ADC TMP2
    STA TMP2
    LDA #$98
    SEC
    SBC TMP2
    STA POSY
    LDA #$7C
    STA TMP_PAL
    LDA #$66
    STA TMP_X
    LDA #G_SHOT0
    JSR put_y
    LDA #$50
    STA POSY
    JMP mu_min
mu_blast:
    LDA #$7C
    STA TMP_PAL
    JSR draw_blast
    JMP scene_return
mu_min:
    JSR fade_minutes
    JSR draw_min_y
    JMP scene_return
""",
    tables="""mu_rows:
    .byte $62,$70,$7E,$8C
times60:
    .byte $00,$3C,$78
""",
)

SHOWS = {"tower": TOWER, "lava": LAVA, "desert": DESERT, "race": RACE, "dojo": DOJO,
         "dungeon": DUNGEON, "mushroom": MUSHROOM}
ORDER = ["tower", "lava", "desert", "race", "dojo", "dungeon", "mushroom"]
