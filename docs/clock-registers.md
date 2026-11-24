# Clock registers

A small read-only block at `$0600-$060F` through which the watch tells a
cartridge the time. The watch core always answers there, whatever the
cartridge's size: it is the one block below `$4000` that nothing in the
console, a cartridge mapper or an add-on uses (going by the a7800 fork of MAME):

| range | taken by |
|---|---|
| `$0400-$047F` | POKEY at `$0450`/`$0440`, the XM's and XBoarD's registers |
| `$0480-$05FF` | RIOT RAM and its mirror |
| `$0800-$0FFF` | POKEY at `$0800` |
| `$1000-$17FF`, `$3000-$3FFF` | the High Score Cart (and 52K banksets from `$3000`) |
| `$2800-$2FFF` | a RAM mirror on some console revisions |
| `$4000-$FFFF` | cartridges, down to `$4000` for a 48K image |

The MAME side (a cart device that fills the same block from the host clock) is
still to be written; until then a cartridge run in MAME reads open bus there
and should check `$060F` first.

All values are BCD unless marked binary.

| addr | name | contents |
|---|---|---|
| `$0600` | `CLK_SEC` | seconds, `$00-$59` |
| `$0601` | `CLK_MIN` | minutes, `$00-$59` |
| `$0602` | `CLK_HOUR` | hours, `$00-$23` (24-hour) |
| `$0603` | `CLK_WDAY` | day of week, binary, 0 = Sunday |
| `$0604` | `CLK_DAY` | day of month, `$01-$31` |
| `$0605` | `CLK_MONTH` | month, `$01-$12` |
| `$0606` | `CLK_YEAR` | year, `$00-$99` |
| `$0607` | `CLK_FLAGS` | b7 the wearer prefers a 12-hour clock, b6 phone linked, b5 charging, b4 this burst came from a wrist flick (the only input a watch face gets) |
| `$0608` | `CLK_BATTERY` | battery percent, binary 0-100 |
| `$0609-$060B` | `CLK_STEPS` | steps today, six BCD digits, high byte first |
| `$060C-$060E` | | reserved, read 0 |
| `$060F` | `CLK_ID` | `$5A` when the block is present |

The watch updates the block between frames, once a minute while the face is
idle and every watch frame (four 7800 frames) during an animation burst. It
runs a burst from :57 to :03 every minute, so a ROM can stage the minute
change in that window; a wrist flick starts a six-second burst with b4 set. A ROM should read it every
frame and draw from it, so that a single frame after a long sleep already shows
the right state.

Battery and steps (and the charging bit) are taken once, as a burst starts,
and held for the burst.

Because the watch runs only short bursts of frames, a ROM should never count
frames to keep time: the seconds register is the clock.
