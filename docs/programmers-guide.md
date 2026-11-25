# Programmer's guide: writing a cartridge for the watch

This is for anyone writing their own 7800 cartridge to run on the face: what
the watch tells a cartridge through the [clock registers](clock-registers.md),
when it tells it, and the habits that make a cartridge look right on a screen
that only runs in short bursts and shows one frame in four. The clock stub
(`tools/mkstub.py`, `tools/extra_shows.py`) follows all of it, and is the
worked example throughout.

To try a cartridge, upload it as the face's `STUB_ROM` raw resource in place
of `clockstub.a78`, or run it on this computer with `tests/a78run.c`.

## The machine

- **A silent 7800.** An NMOS 6502 (undocumented opcodes included), MARIA
  with the a7800 MAME fork's DMA timing, and the RIOT timer. TIA sound writes
  are ignored and there is no POKEY.
- **No BIOS.** The cartridge starts at its reset vector, as in the fork's
  no-BIOS mode: no signature or encryption check, so none is needed. Write
  `INPTCTRL` as usual (the stub writes `$17`).
- **Cartridges:** a linear image up to 48K, mapped to the top of memory, with
  or without an `.a78` header. With a header, bit `$0004` adds 16K of RAM at
  `$4000` and bit `$0080` adds the fork's mirror RAM (each odd page is the even
  page below it); either way the ROM is then at most 32K. Banked cartridges,
  banked RAM and extra hardware (header bits `$3F3A`) are refused.
- **No controllers.** The ports read as nothing pressed: `SWCHA` reads `$FF`,
  `INPT4`/`INPT5` read `$80`. The only input is the wrist flick, which the watch
  passes as a flag (below).
- **The clock registers,** read-only, at `$0600-$061F`. Writes there are
  ignored. MARIA's DMA can read them too.

## How the watch runs a cartridge

The watch cannot run the 7800 all the time: it runs it in **draws**. A draw
does three things, in this order:

1. It writes the clock registers.
2. It runs four 7800 frames back to back. The first three make no pixels
   (MARIA's DMA still runs, with its exact timing); only the fourth is drawn.
3. It puts the face's banner and date over the picture (see *The picture*).

A draw every 66 ms during an animation is four frames per 66 ms: real time,
about 60 frames a second, of which the watch shows 15.

Between draws the 7800 is **stopped**: no frames run, the RIOT timer does not
count, RAM keeps its contents. There are draws only:

| when | how many | notes |
|---|---|---|
| The face starts | a 6-second burst (90 draws) | the cartridge is powered on just before: RAM cleared, reset vector |
| From :57 of every minute | a 6.5-second burst (98 draws), to about :03.5 | not in Quiet Time |
| A wrist flick | a 6-second burst (90 draws) | flag b4 set; a flick during a burst lengthens it to at least 90 more draws |
| Each minute in Quiet Time | one draw (four frames), at about :00 | flag b3 set |
| The system redraws the face | one draw | e.g. coming back to the face from a menu or a notification |

So from about :03 to :57 the picture usually stands still, showing the last
frame of the last burst. Anything the cartridge leaves on screen at the end of
a burst stays there for up to a minute.

The cartridge is powered on only when the face starts (or restarts: changing
faces, an update). Everything else is the same running machine, picking up
where it stopped.

## The clock registers in practice

The full table is in [clock-registers.md](clock-registers.md). What matters for
behaviour is when each value can change:

| register | changes |
|---|---|
| `CLK_SEC`, `CLK_MIN`, `CLK_HOUR`, the date | at any draw: written from the watch's clock before each draw |
| `CLK_FLAGS` b7 (12-hour), b6 (phone linked) | at any draw |
| `CLK_FLAGS` b3 (paused) | Quiet Time, as checked at :57 and at each minute. Clear during a flick's burst, even in Quiet Time |
| `CLK_FLAGS` b4 (flick) | set from the flick until that burst's draws run out |
| `CLK_BATTERY`, `CLK_STEPS`, `CLK_HI`, `CLK_FLAGS` b5 (charging) | taken once as a burst starts, then held for the burst |
| `CLK_FLAGS` b2 (new best day) | set for the first burst in which today's steps pass the best day's (once a day), and held until the next burst starts |
| `CLK_CHANNEL` | at a flick: on by one, back to 0 after 10 (`NUM_SHOWS` in `main.c`) |
| `CLK_ID` | always `$5A` |

Because the registers are written once per draw, **nothing changes in the
middle of a draw**: all four frames see the same values, and the seconds
register moves on at most once per draw.

**Formats.** Time, date, steps and the high score are BCD; the day of the
week, battery percent and channel are binary. BCD compares work directly
(`CMP #$57` for :57), and each nibble of a BCD byte is a digit ready to use as
a character or glyph number.

**Checking for the block.** The MAME side is not written yet, so in MAME
`$0600-$061F` reads open bus. A cartridge that might run there should check
`$060F` first:

```
    LDA $060F
    CMP #$5A
    BNE no_clock              ; not on the watch: a demo mode, say
```

## Keeping time

**The seconds register is the clock.** Never keep time by counting frames
across draws: frames stop between bursts, so a frame count is only good for
how far into the current burst you are. Read the registers every frame and
draw from them.

**Within a second,** count frames, starting again whenever `CLK_SEC` changes.
The stub's counter, run once per frame:

```
    LDA CLK_SEC
    CMP LASTSEC
    BEQ same_second
    STA LASTSEC
    LDA #$00
    STA SUBF                  ; a new second: start again
    BEQ second_done
same_second:
    LDA SUBF
    CMP #59
    BCS second_done           ; never past 59
    INC SUBF
second_done:
```

Seconds change only at draw boundaries, so a second usually holds 60 or 64
frames, depending on where the draws fall (fewer if the watch is busy). Clamp the count, as above, and plan motion
so that being a draw early or late does not show.

**Draw the right picture from cold.** A single draw after a minute's sleep, or
the one Quiet Time draw, must already show the right state. Work out what to
draw from the registers each frame, and keep only cosmetic state (twinkles,
drift) from frame to frame. The stub's stage of the minute, for instance, is
worked out afresh every frame: :57-:59 are stages 0-2, :00-:02 stages 3-5,
anything else no show, and none at all while b3 is set:

```
    LDA CLK_SEC
    CMP #$57
    BCC not_late
    SEC
    SBC #$57                  ; :57-:59: 0-2
    STA STAGE
    JMP stage_done
not_late:
    CMP #$03
    BCS no_show
    CLC
    ADC #$03                  ; :00-:02: 3-5
    STA STAGE
    JMP stage_done
no_show:
    LDA #$FF
    STA STAGE
stage_done:
    LDA CLK_FLAGS             ; Quiet Time: one draw a minute, so no show
    AND #$08
    BEQ not_paused
    LDA #$FF
    STA STAGE
not_paused:
```

**The minute change** is best staged between :57 and :02, inside the watch's
burst. The new minute arrives at :00.

**Quiet Time** gives one draw a minute, at about :00, with b3 set. Draw the
finished state: the new time, no animation halfway through.

## The flick and the channel

The wrist flick is the only input. The watch does two things with it: it sets
b4 for the burst it starts, and it moves `CLK_CHANNEL` on by one. What the
channel means is up to the cartridge. The stub plays show
(hour + channel) mod 11:

```
    LDA CLK_HOUR
    JSR bcd_to_bin
    CLC
    ADC CLK_CHANNEL           ; 0-23 plus 0-10
scene_mod:
    CMP #11
    BCC scene_ok
    SBC #11
    BCS scene_mod
scene_ok:
    STA SCENE
```

A cartridge with a different number of shows should take the channel modulo
its own count. If it is going on the watch for good, set `NUM_SHOWS` in
`main.c` to match, so that every flick lands on a new show and the channel
knob has the right number of positions.

Flicks closer together than 1.5 seconds count as one.

## The picture

**What is shown.** The watch shows 192 of the 7800's lines, 42 to 233: the
safe area of Atari's software guide. MARIA's first line of DMA is line 17,
shown on line 18, so with a display list of 16-line zones from the top:

| zone | lines | on the watch |
|---|---|---|
| 0 | 18-33 | not shown |
| 1 | 34-49 | rows 0-7: only its last 8 lines |
| 2-4 | 50-97 | rows 8-55 |
| 5-11 | 98-209 | rows 56-167 |
| 12 | 210-225 | rows 168-183 |
| 13 | 226-241 | rows 184-191: only its first 8 lines |

The tube's corners are rounded off and a little glare is laid over the top
left; neither changes what the cartridge draws.

**The banner** (*ATARI 7800*, *40TH ANNIVERSARY*, the rainbow and the years)
covers rows 0-55: zones 1-4 above. It is drawn first and the cartridge draws
over it, with two rules:

- Pixels in the background colour are see-through there, so the banner shows
  behind the cartridge's picture and anything it draws passes in front.
- Pixels in any of palette 7's three colours pass *behind* the banner,
  showing only where the banner leaves the tube black. Palette 7 is for
  backdrops: stars, ground, sky. The test is on the colour, not the palette, so
  another palette's pixel in the same colour as one of palette 7's goes behind
  too.

**The date** is drawn last, over the picture, on rows 137-150: zone 10. The
stub keeps that zone clear of anything that matters.

**Colours.** The Pebble has 64 colours, so each of the 7800's 256 is shown as
the nearest of those (`palette7800.c`). Some pairs that differ on a TV come
out the same: `$12` and `$14`, for example, are both dark olive. Check colours
that must differ against the table.

**320 modes** work. Each watch column is a pair of 320 pixels, one colour
cycle, shown the way a television decodes it: a solid pair is its palette
colour, and alternating pixels (`$AA`, `$55`) come out as artifact colours
(`artifact_table.h`). The stub's tower uses that for its bricks.

**Changing modes down the screen** works with display list interrupts. Set
the DLI bit on the display list list entry of the first zone that should
change; the stub sets it on zones 8 (into 320A) and 11 (back to 160A). In this
core (as in the fork) that interrupt comes while the last line of the zone
before is still on screen, so wait for two `WSYNC`s before writing `CTRL` if
the change is to start exactly at the zone's first line. The stub's handler:

```
nmi:
    PHA
    LDA NMICTRL               ; $43 (320A) or $40 (160A)
    STA WSYNC
    STA WSYNC
    STA CTRL
    LDA #$40                  ; the next interrupt goes back to 160A
    STA NMICTRL
    PLA
    RTI
```

## Animating for a screen that shows one frame in four

The watch shows every fourth frame, so anything that changes faster than
that is seen *sampled*:

- **Flicker** (two sprites taking turns, frame by frame) does not work: the
  watch shows the same one of the pair every time.
- **Cycles** need to step at most once every four frames. A two-colour swap
  stepped every two frames looks frozen; a three-step cycle stepped every two
  frames runs *backwards*. The stub steps on `FRAME >> 2`, which the watch
  sees advance by one each draw. For a cycle whose length does not divide the
  frame counter's range (three steps, say), fold in the high byte so it does
  not jump when the counter wraps: (frames / 4) mod 3 is
  ((FRAME >> 2) + FRAMEH) mod 3, since 64 is 1 mod 3.
- **Motion** is seen four frames at a time. One pixel a frame shows as
  four-pixel steps at 15 per second, which still reads as smooth; faster
  movement starts to jump.

## Performance

A draw has 66 ms. The stub's draws take 28-37 ms on a Pebble Time 2: the
face's log reports the average every 30 draws. What costs time on the watch:

- **6502 instructions,** each emulated one by one.
- **MARIA's DMA** for every line, in all four frames.
- **The drawn frame's pixels:** colour conversion for 192 lines, more for
  lines in a 320 mode.

**Waiting for vertical blank** is free if it is written as a load and a branch
that polls `MSTAT` and nothing else: `LDA` or `BIT` of `MSTAT` (zero page or
absolute), then `BPL` or `BMI` back to that instruction. The emulator
recognises that loop and skips to where it ends instead of running it. Any
other wait loop runs instruction by instruction and costs watch time for
nothing.

```
wait_vb:
    BIT MSTAT
    BPL wait_vb
```

## Testing on this computer

`tests/a78run.c` runs a cartridge on the watch's own core and writes PPM frames
in the watch's colours (`-w` sets the first line shown; the default is 42, as
on the watch). It sets the clock registers from its options: the clock
standing (`-c HHMM`) or running (`-t HHMMSS`, a second every 60 frames), the
flick flag (`-l`), battery (`-b`), steps (`-S`), charging (`-g`), the channel
(`-C`), flicks that change it (`-k`), the high score (`-H`), a new best day
(`-N`), Quiet Time (`-Q`), no phone (`-D`) and 12-hour time (`-12`). `-n` runs
the `MSTAT` loops instruction by instruction instead of skipping them, to check
that the skip changes nothing.

a78run runs every frame and keeps the seconds moving every 60 frames, so it
does not show the watch's bursts and pauses. Before putting a cartridge on the
watch, try it from cold at different times of the minute (`-c`), and check
anything that cycles or flickers frame by frame by looking at every fourth
frame (`-e 4`).

## Checklist

- Read the clock registers every frame; never keep time by counting frames.
- Count frames within a second from the change of `CLK_SEC`, and clamp.
- Make one draw from cold show the right state.
- Stage the minute change between :57 and :02.
- Show no animation while b3 (Quiet Time) is set.
- Take the channel modulo the cartridge's own number of shows.
- Keep the clock's zone clear of the banner's rows, and zone 10 clear for the
  date; use palette 7 for backdrops that go behind the banner.
- Step cycles at most once every four frames; don't rely on flicker.
- Wait for vertical blank with a plain `MSTAT` polling loop.
- Check `$060F` for `$5A` if the cartridge might run without the watch.
