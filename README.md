# a7800-pebble-tools

Build scripts and tests for running small 7800 cartridges on a Pebble Time 2
watch face. The watch side (the 6502 and MARIA core, `src/c/emu`) lives in the
watch face project; this repo builds what it runs and checks that it runs it
correctly.

It leans on two other repos and expects them checked out beside it:

- **a7800-toolkit** — the assembler (`tools/asm.py`), `newgame.header()`, the
  opcode table the core's tables are generated from (`tools/m6502.py`), the
  RIOT test cart and MAME's golden dump of it, and the DMA cost cartridges.
- **the watch face** (`atari7800-40th`) — `src/c/emu`, which the tests compile.

## What is here

| | |
|---|---|
| `tools/gen_m6502_tables.py` | Generates the core's opcode tables (`m6502_tables.h`) from the toolkit's `m6502.py`: one opcode table for both projects. |
| `tools/mkstub.py` | Builds `clockstub.a78`, an 8K cartridge that plays the minute change by itself from the clock registers: ships fly in at :57, shoot the old minute out, the new one fades in at :00, they leave at :02. No input recordings: a watch face gets none to replay. |
| `docs/clock-registers.md` | The read-only block at `$4000-$400F` through which the watch tells a ROM the time. |
| `tests/harte_pack.py`, `tests/harte_run.c` | Tom Harte's SingleStepTests for the 6502: 10,000 cases for each of the 256 opcodes, checking registers, memory and cycle counts. |
| `tests/klaus_run.c` | Klaus Dormann's 6502 functional test. |
| `tests/a78run.c` | Runs a cartridge on the watch core on the host: frames to PPM, RAM dumps, per-frame CPU/DMA/WSYNC time, the clock registers standing (`-c HHMM`) or running (`-t HHMMSS`), the wrist-flick flag (`-l`). |
| `tests/riot_check.py` | The toolkit's RIOT test cart against MAME's golden RAM dump, by the toolkit selftest's own rule. |
| `tests/dma_fit.py` | Runs the toolkit's DMA cost cartridges on the core and fits the same model `dmameasure.py --fit` fits to MAME. |

## Results

| check | result |
|---|---|
| Harte SingleStepTests | all 2,440,000 cases pass for the 244 opcodes that are not JAM. The 12 JAM opcodes halt the CPU by design (the tests record the bus going on). |
| Klaus Dormann functional test | passes (traps at `$3469`) |
| RIOT timer and WSYNC vs MAME (`riot.a78`) | all eight blocks the toolkit checks are exact; the full 4K of RAM is byte-identical to the toolkit simulator's |
| DMA cost model vs the fork | ceiling 1,960 iterations (MAME: 1,960); per line 5.657 cycles (fork 5.62), per zone 1.911 (1.89), per object 1.977 (1.99), per byte 0.750 (0.755) |
| MSTAT fast-forward | identical RAM and frames to instruction-by-instruction stepping on all three carts tried |

The DLI comes out at 13.1 cycles against `docs/hardware.md`'s measured 17.4.
The fork's current `maria.cpp` no longer charges DMA time for a DLI ("the a7800
fork charged 17"), which leaves the 6502's NMI entry and `RTI`, 13 cycles. The
17.4 looks like a measurement of the earlier fork.

## Running it

```
# the opcode tables (only when the toolkit's m6502.py changes)
python tools/gen_m6502_tables.py --toolkit ../a7800-toolkit \
    -o ../atari7800-40th/src/c/emu/m6502_tables.h

# the stub, then into the watch project's resources
python tools/mkstub.py --toolkit ../a7800-toolkit -o build/clockstub.a78
cp build/clockstub.a78 ../atari7800-40th/resources/data/

# the host runner
W=../atari7800-40th/src/c/emu
cc -O2 -I$W tests/a78run.c $W/a7800.c $W/m6502.c $W/palette7800.c -o build/a78run
build/a78run build/clockstub.a78 -f 480 -t 202656 -s 0,479 -e 4   # :56 to :04, every 4th frame

# 6502 tests (fetch the vectors first: SingleStepTests/65x02 6502/v1, and
# Klaus2m5/6502_65C02_functional_tests bin_files)
python tests/harte_pack.py path/to/65x02/6502/v1 -o build/harte
cc -O2 -I$W tests/harte_run.c $W/m6502.c -o build/harte_run && build/harte_run build/harte
cc -O2 -I$W tests/klaus_run.c $W/m6502.c -o build/klaus_run
build/klaus_run path/to/6502_functional_test.bin

# 7800 checks
build/a78run ../a7800-toolkit/tests/carts/riot.a78 -f 40 -r build/riot-ram.bin -q
python tests/riot_check.py build/riot-ram.bin ../a7800-toolkit/tests/golden/riot-mame.bin
python tests/dma_fit.py --toolkit ../a7800-toolkit --run build/a78run
```

## Not done yet

- The MAME side of the clock registers: a cart device in the fork that fills
  `$4000-$400F` from the host clock, so a stub runs the same in MAME.
- Speed on the watch itself. On this host the clock stub costs about 0.7 million
  x86 instructions per drawn frame (callgrind, `-Os`); the watch face logs
  milliseconds per draw, which is the number that matters.

Input clips (`.inp` recordings replayed on the watch) were dropped: a watch face
receives no buttons or touch, so a cartridge plays its own animation, keyed to
the clock registers. `a78run -i` still takes a one-byte-per-frame stream for
testing.
