# Pebble Time 2 7800

A watch face for the Pebble Time 2 that celebrates the Atari 7800's 40th
anniversary by running a real 7800 cartridge: a silent 6502 and MARIA, written
for the watch, play a 16K cartridge that draws the time and stages every
minute change by itself, with a different show each hour. The face puts it in an old TV in 7800 dress, with the
anniversary banner and the date on the glass.

![The four hourly shows, :57 to :03](docs/four-shows.gif)

| hour mod 4 | show, :57 to :02 |
|---|---|
| 0 | **formation** -- two attackers fly in, three volleys, the old minute explodes, the new one fades in; all of it over a three-layer star field falling behind ([closer look](docs/starfield.gif)) |
| 1 | **drill rig** -- a rig bores up through the rock, the old minute shakes and crumbles, the rig pushes the new one up into place |
| 2 | **bomber run** -- the ground scrolls, a bomber climbs in and its bomb takes out the old minute; the new one fades in as it flies off |
| 3 | **lander** -- a lander comes down, lifts the old minute away and sets the new one down in a puff of dust |

- `watchface/` — the Pebble project. The 7800 is `watchface/src/c/emu`; the
  cartridge it runs is `watchface/resources/data/clockstub.a78`.
- `tools/`, `tests/`, `docs/` — what builds the cartridge and the core's
  tables, and the checks that the core behaves like the 7800 (and the a7800
  fork of MAME).

The build scripts lean on the **a7800-toolkit** repo, checked out beside this
one: its assembler (`tools/asm.py`), `newgame.header()`, the opcode table the
core's tables are generated from (`tools/m6502.py`), the RIOT test cart with
MAME's golden dump of it, and the DMA cost cartridges.

## The watch face

```
cd watchface
pebble build
pebble install --emulator emery      # or --phone <ip>
```

`USE_EMULATOR` at the top of `watchface/src/c/main.c` picks the cartridge (1)
or the earlier hand-drawn hourly scenes (0). `EMU_FRAMES_PER_DRAW` sets how
many 7800 frames run per watch frame (4 is real time); the face logs the
milliseconds a draw takes every 30 draws.

The screen changes only during animation bursts: one from :57 to :03 every
minute, and six seconds after a wrist flick. Battery, steps and the phone link
are recorded when they change but drawn from the next burst; battery and steps
are read once as a burst starts, so the bar and the score move at most once a
burst. A lost phone link still vibrates at once.

The cartridge draws in front of the banner (its background colour is
see-through there), except palette 7: that is the backdrop, and it passes behind
the banner, showing only where the banner leaves the tube black. The formation
hour's stars are drawn in palette 7 for that reason.

## What is here

| | |
|---|---|
| `tools/gen_m6502_tables.py` | Generates the core's opcode tables (`m6502_tables.h`) from the toolkit's `m6502.py`: one opcode table for both projects. |
| `tools/mkstub.py` | Builds `clockstub.a78`, a 16K cartridge that plays the minute change by itself from the clock registers, with one of four shows each hour; a cruiser crosses the bottom once an hour, a step a minute, over a lives-and-score bar in character mode (a life per 20% of battery, today's steps as the 1UP score); the cruiser and the lives are the hour's vehicle -- ship, rig, plane or lander. Moving actors are placed to the line across zone boundaries with holey DMA, as 7800 games do. No input recordings: a watch face gets none to replay. |
| `docs/clock-registers.md` | The read-only block at `$0600-$060F` through which the watch tells a ROM the time (clear of every mapper and add-on, so any cartridge size keeps it). |
| `tests/harte_pack.py`, `tests/harte_run.c` | Tom Harte's SingleStepTests for the 6502: 10,000 cases for each of the 256 opcodes, checking registers, memory and cycle counts. |
| `tests/klaus_run.c` | Klaus Dormann's 6502 functional test. |
| `tests/a78run.c` | Runs a cartridge on the watch core on the host: frames to PPM, RAM dumps, per-frame CPU/DMA/WSYNC time, the clock registers standing (`-c HHMM`) or running (`-t HHMMSS`), the wrist-flick flag (`-l`), battery (`-b`), steps (`-S`), charging (`-g`). |
| `tests/vbprobe.c` | Reports how far into the frame a cartridge's main loop runs before it is done (the line its PC first reaches a given address), so the display lists are known to be ready before MARIA reads them. |
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
    -o watchface/src/c/emu/m6502_tables.h

# the stub, then into the watch face's resources
python tools/mkstub.py --toolkit ../a7800-toolkit -o build/clockstub.a78
cp build/clockstub.a78 watchface/resources/data/

# the host runner
W=watchface/src/c/emu
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
  `$0600-$060F` from the host clock, so a stub runs the same in MAME.
- Speed on the watch itself. On this host the clock stub costs about 0.7 million
  x86 instructions per drawn frame (callgrind, `-Os`); the watch face logs
  milliseconds per draw, which is the number that matters.

Input clips (`.inp` recordings replayed on the watch) were dropped: a watch face
receives no buttons or touch, so a cartridge plays its own animation, keyed to
the clock registers. `a78run -i` still takes a one-byte-per-frame stream for
testing.
