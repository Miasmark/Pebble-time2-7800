# Clock registers

A small read-only block at `$4000-$400F` through which the watch tells a stub
ROM the time. The watch core answers there whenever the cartridge's ROM starts
at `$4100` or higher, which every stub does. The MAME side (a cart device that
fills the same block from the host clock) is still to be written; until then a
stub run in MAME reads whatever the cartridge slot returns there and should
check `$400F` first.

All values are BCD unless marked binary.

| addr | name | contents |
|---|---|---|
| `$4000` | `CLK_SEC` | seconds, `$00-$59` |
| `$4001` | `CLK_MIN` | minutes, `$00-$59` |
| `$4002` | `CLK_HOUR` | hours, `$00-$23` (24-hour) |
| `$4003` | `CLK_WDAY` | day of week, binary, 0 = Sunday |
| `$4004` | `CLK_DAY` | day of month, `$01-$31` |
| `$4005` | `CLK_MONTH` | month, `$01-$12` |
| `$4006` | `CLK_YEAR` | year, `$00-$99` |
| `$4007` | `CLK_FLAGS` | b7 the wearer prefers a 12-hour clock, b6 phone linked, b5 charging, b4 this burst came from a wrist flick (the only input a watch face gets) |
| `$4008` | `CLK_BATTERY` | battery percent, binary 0-100 |
| `$4009-$400B` | `CLK_STEPS` | steps today, six BCD digits, high byte first |
| `$400C-$400E` | | reserved, read 0 |
| `$400F` | `CLK_ID` | `$5A` when the block is present |

The watch updates the block between frames, once a minute while the face is
idle and every watch frame (four 7800 frames) during an animation burst. It
runs a burst from :57 to :03 every minute, so a ROM can stage the minute
change in that window; a wrist flick starts a six-second burst with b4 set. A ROM should read it every
frame and draw from it, so that a single frame after a long sleep already shows
the right state.

Because the watch runs only short bursts of frames, a ROM should never count
frames to keep time: the seconds register is the clock.
