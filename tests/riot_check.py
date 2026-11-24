#!/usr/bin/env python3
"""Compare a78run's RAM dump of the toolkit's riot.a78 with MAME's golden dump.

    a78run riot.a78 -f 40 -r riot-ram.bin -q
    python tests/riot_check.py riot-ram.bin path/to/a7800-toolkit/tests/golden/riot-mame.bin

Same ranges and pass rule as the toolkit's selftest (t_sim_riot): eight blocks
exact, under $180 differences in $1800-$1AFF.
"""
import sys

got = open(sys.argv[1], "rb").read()
gold = open(sys.argv[2], "rb").read()
ok = True
for name, lo, n in (("TIM64T", 0x000, 48), ("TIM64T flag", 0x100, 64), ("TIM1T", 0x200, 20),
                    ("TIM1T flag", 0x300, 20), ("TIM8T", 0x400, 7), ("TIM8T flag", 0x500, 20),
                    ("INTIM clears TIMINT", 0x600, 0x15), ("WSYNC", 0x700, 0x20)):
    same = got[lo:lo + n] == gold[lo:lo + n]
    ok &= same
    print("%-20s %s" % (name, "exact" if same else "DIFF"))
    if not same:
        print("  gold", gold[lo:lo + n].hex())
        print("  got ", got[lo:lo + n].hex())
off = sum(1 for i in range(0x300) if got[i] != gold[i])
print("samples differing in $1800-$1AFF: %d (the toolkit allows < 384)" % off)
ok &= off < 0x180
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
