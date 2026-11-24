#!/usr/bin/env python3
"""Pack Tom Harte's SingleStepTests 6502 JSON into a compact binary for harte_run.

    python tests/harte_pack.py path/to/65x02/6502/v1 -o build/harte

Per test: pc(u16) s a x y p, n, n x (addr u16, value u8)   -- initial
          pc(u16) s a x y p, n, n x (addr u16, value u8)   -- final
          cycles(u8)
"""
import argparse
import json
import os
import struct


def state(s):
    out = struct.pack("<HBBBBB", s["pc"], s["s"], s["a"], s["x"], s["y"], s["p"])
    out += struct.pack("<B", len(s["ram"]))
    for addr, val in s["ram"]:
        out += struct.pack("<HB", addr, val)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for name in sorted(os.listdir(args.src)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(args.src, name)) as f:
            tests = json.load(f)
        blob = bytearray(struct.pack("<I", len(tests)))
        for t in tests:
            blob += state(t["initial"]) + state(t["final"]) + struct.pack("<B", len(t["cycles"]))
        with open(os.path.join(args.out, name.replace(".json", ".bin")), "wb") as f:
            f.write(blob)
    print("packed", args.out)


if __name__ == "__main__":
    main()
