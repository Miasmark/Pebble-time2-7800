#!/usr/bin/env python3
"""Build the emulator's two small demo cartridges.

    python tools/mkdemos.py --toolkit ../a7800-toolkit -o build

demo_artifacts.a78   320A mode: eight rows, one per hue, each with four blocks of
                     one byte repeated -- $FF (solid), $AA and $55 (one pixel on,
                     one off: a pattern a colour cycle wide, which a television
                     decodes as colour), and $F0 (four on, four off: plain
                     stripes). On the watch each pair of 320 pixels is one
                     column, shown in the television's artifact colour; a78run's
                     -L shows the old way (the left pixel).

demo_ram.a78         16K of cartridge RAM at $4000 (header bit $0004): the top
demo_mram.a78        bar says which RAM it found -- green for 16K, blue for
                     mirror RAM (header bit $0080: A8 not wired, so $4100 is
                     $4000), red for none -- and below it MARIA draws bands
                     straight out of the RAM, which the 6502 rewrites every
                     frame so they scroll. On mirror RAM each odd line is the
                     even line above it, so the bands come out in steps of two.

Both are 32K, linear, and start without a BIOS like the clock cartridge.
"""
import argparse
import os
import sys

ROM_BASE = 0x8000

COMMON = """
INPTCTRL = $01
BACKGRND = $20
P0C1     = $21
P0C2     = $22
P0C3     = $23
P1C1     = $25
P1C2     = $26
P1C3     = $27
MSTAT    = $28
P2C2     = $2A
DPPH     = $2C
P3C2     = $2E
DPPL     = $30
P4C2     = $32
P5C2     = $36
OFFSET   = $38
P6C2     = $3A
CTRL     = $3C
P7C2     = $3E
FRAME    = $0080
TMP      = $0081
"""

RESET = """
    .org $%04X
reset:
    SEI
    CLD
    LDX #$FF
    TXS
    LDA #$17
    STA INPTCTRL
    LDA #$00
    STA OFFSET
    STA BACKGRND
    STA FRAME
    LDA #$60                  ; DMA off while setting up
    STA CTRL
""" % ROM_BASE


def tail(a, name):
    a("code_end:")
    a("    .res $E000-code_end,$00")


def vectors(a):
    a("tables_end:")
    a("    .res $FFFA-tables_end,$00")
    a("    .word nmi")
    a("    .word reset")
    a("    .word nmi")


def dll(a, label, zones):
    """zones: (lines, dl label); holey DMA off, no DLIs."""
    a("%s:" % label)
    total = 0
    for lines, dl in zones:
        a("    .byte $%02X,>%s,<%s" % (lines - 1, dl, dl))
        total += lines
    assert total >= 243, total    # down past the last line MARIA draws


def artifacts_source():
    L = COMMON.strip().splitlines()
    a = L.append
    a(RESET)
    hues = [0x0E, 0x28, 0x48, 0x68, 0x88, 0xA8, 0xC8, 0xE8]
    for k, c in enumerate(hues):
        a("    LDA #$%02X" % c)
        a("    STA P%dC2" % k)
    a("""    LDA #>dll
    STA DPPH
    LDA #<dll
    STA DPPL
    LDA #$43                  ; DMA on, 320A/320C (no 5-byte headers: 320A)
    STA CTRL
idle:
    JMP idle
nmi:
    RTI""")
    tail(a, "artifacts")
    # graphics: four 8-byte blocks of one pattern byte, the same on all 16 lines
    patterns = [0xFF, 0xAA, 0x55, 0xF0]
    a("gfx:")
    for page in range(16):
        data = []
        for p in patterns:
            data += [p] * 8
        a("    .byte " + ",".join("$%02X" % b for b in data))
        a("    .res $%X,$00" % (256 - len(data)))
    a("    .org $F000")
    a("empty:")
    a("    .byte $00,$00")
    for row in range(8):
        a("row%d:" % row)
        for k in range(4):
            # 8 bytes = 64 320-mode pixels = 32 columns; palette row, width 8
            a("    .byte $%02X,$%02X,>gfx,$%02X" % (k * 8, (row << 5) | (32 - 8), 8 + k * 38))
        a("    .byte $00,$00")
    zones = [(16, "empty"), (16, "empty"), (10, "empty")]
    for row in range(8):
        zones += [(16, "row%d" % row), (6, "empty")]
    zones += [(16, "empty")] * 2
    dll(a, "dll", zones)
    vectors(a)
    return "\n".join(L) + "\n"


def ram_source():
    L = COMMON.strip().splitlines()
    a = L.append
    a("RAMGFX   = $4000           ; 16 pages: page $40 + (15 - line) is line `line`")
    a(RESET)
    a("""    ; ---- which RAM is there? ----
    LDA #$11
    STA $4000
    LDA #$22
    STA $4100
    LDA #$33
    STA $7FFF
    LDX #$36                  ; red: no RAM
    LDA $7FFF
    CMP #$33
    BNE found
    LDA $4000
    LDX #$C8                  ; green: 16K
    CMP #$11
    BEQ found
    LDX #$88                  ; blue: mirror RAM, $4100 is $4000
    CMP #$22
    BEQ found
    LDX #$36
found:
    STX P1C3
    LDA #$46
    STA P0C1                  ; the bands: pink, blue, green
    LDA #$86
    STA P0C2
    LDA #$C8
    STA P0C3
    LDA #>dll
    STA DPPH
    LDA #<dll
    STA DPPL
    LDA #$40                  ; DMA on, 160A
    STA CTRL

main:
wait_vb:
    LDA MSTAT
    BPL wait_vb
    ; ---- rewrite the 16 lines of the graphic in RAM: line y is band
    ; (y + frame / 2) mod 8, all 20 bytes across ----
    LDA FRAME
    LSR
    STA TMP
    LDY #$0F                  ; page $4F holds line 0 ... $40 line 15
fill_page:
    TYA
    EOR #$0F                  ; the line
    CLC
    ADC TMP
    AND #$07
    TAX
    LDA bands,X
    LDX #$13
    JSR store_line
    DEY
    BPL fill_page
    INC FRAME
wait_novb:
    LDA MSTAT
    BMI wait_novb
    JMP main

; A into 20 bytes of RAM page $40 + Y
store_line:
    STY TMP+1
    PHA
    TYA
    CLC
    ADC #>RAMGFX
    STA TMP+3
    LDA #$00
    STA TMP+2
    PLA
    LDY #$13
store_byte:
    STA (TMP+2),Y
    DEY
    BPL store_byte
    LDY TMP+1
    RTS

nmi:
    RTI
bands:
    .byte $00,$55,$AA,$FF,$FF,$AA,$55,$00""")
    tail(a, "ram")
    a("gfx:                       ; the bar: 20 bytes of colour 3 on every line")
    for page in range(16):
        a("    .byte " + ",".join(["$FF"] * 20))
        a("    .res $%X,$00" % (256 - 20))
    a("    .org $F000")
    a("empty:")
    a("    .byte $00,$00")
    a("bar:                        ; palette 1, 20 bytes, twice across")
    a("    .byte $00,$2C,>gfx,$00")
    a("    .byte $00,$2C,>gfx,$50")
    a("    .byte $00,$00")
    a("band_dl:                    ; palette 0, from the RAM, twice across")
    a("    .byte $00,$0C,>RAMGFX,$00")
    a("    .byte $00,$0C,>RAMGFX,$50")
    a("    .byte $00,$00")
    zones = [(16, "empty"), (16, "empty"), (16, "empty"), (16, "bar"), (16, "empty")]
    zones += [(16, "band_dl")] * 9
    zones += [(16, "empty")] * 2
    dll(a, "dll", zones)
    vectors(a)
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--toolkit", required=True, help="path to the a7800-toolkit checkout")
    ap.add_argument("-o", "--outdir", required=True)
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(args.toolkit, "tools"))
    import asm
    import newgame

    os.makedirs(args.outdir, exist_ok=True)
    for name, src, ctype in (("demo_artifacts", artifacts_source(), 0x0000),
                             ("demo_ram", ram_source(), 0x0004),
                             ("demo_mram", ram_source(), 0x0080)):
        with open(os.path.join(args.outdir, name + ".asm"), "w") as f:
            f.write(src)
        data = asm.Assembler().assemble(src.splitlines())
        if len(data) != 0x10000 - ROM_BASE:
            raise SystemExit("%s: assembled %d bytes, expected %d" % (name, len(data), 0x10000 - ROM_BASE))
        h = bytearray(newgame.header(len(data), name.upper().replace("_", " ")))
        h[53] = ctype >> 8
        h[54] = ctype & 0xFF
        path = os.path.join(args.outdir, name + ".a78")
        with open(path, "wb") as f:
            f.write(bytes(h))
            f.write(data)
        print("wrote %s (%d bytes + 128-byte header, type $%04X)" % (path, len(data), ctype))


if __name__ == "__main__":
    main()
