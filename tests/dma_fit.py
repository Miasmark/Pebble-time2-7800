#!/usr/bin/env python3
"""Fit the toolkit's DMA cost model to the watch core, as tools/dmameasure.py --fit does to MAME.

    python tests/dma_fit.py --toolkit path/to/a7800-toolkit --run build/a78run

Builds every shape in dmameasure.fit_shapes() with probes/dma-costcart.py, runs it on the
watch core with a78run -p 90 (dma-count.lua's median over frames 120-200), and fits
PER_LINE, PER_ZONE, PER_OBJ, PER_BYTE, HOLE and DLI_COST by least squares. Prints them beside
the fork's measured constants from docs/hardware.md.

The cost carts end their display list list with zeros, so MARIA goes on fetching one-line
empty zones down to line 258 (50 of them under 16-line zones). Those cost DMA on the fork too;
the fit spreads them over the 192 drawn lines, which is part of PER_LINE.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile

import numpy as np

FORK = {"PER_LINE": 5.62, "PER_ZONE": 1.89, "PER_OBJ": 1.99, "PER_BYTE": 0.755, "DLI_COST": 17.4}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toolkit", required=True)
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(args.toolkit, "tools"))
    import dmameasure
    spec = importlib.util.spec_from_file_location(
        "cc", os.path.join(args.toolkit, "probes", "dma-costcart.py"))
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)

    work = tempfile.mkdtemp(prefix="dmafit-")
    rom = os.path.join(work, "t.a78")

    def iterations():
        out = subprocess.run([args.run, rom, "-p", "90", "-q"], stdout=subprocess.PIPE).stdout.decode()
        return int(out.split("median=")[1].split()[0])

    cc.build(rom, 1, 4, dma_on=False)
    ceiling = iterations()
    print("ceiling (DMA off): %d iterations a frame (dmameasure uses %d)" % (ceiling, dmameasure.CEILING))

    A, b, labels = [], [], []
    for nz, zl, used, nobj, width, ex in dmameasure.fit_shapes():
        labels.append("%d zones x %d lines, %d used, %d x %db %s" % (nz, zl, used, nobj, width, ",".join(ex) or ""))
        cc.build(rom, nobj, width, zones_used=used, zlines=zl, nzones=nz, **ex)
        cost = (ceiling - iterations()) * dmameasure.LOOP
        ol = used * zl * nobj
        holey = ex.get("holey", False)
        A.append([192, nz, ol, 0 if holey else ol * width, ol if holey else 0,
                  nz if ex.get("dli") else 0])
        b.append(cost)
    A, b = np.array(A, float), np.array(b, float)
    sol = np.linalg.lstsq(A, b, rcond=None)[0]
    res = np.abs(A @ sol - b) / b
    names = ("PER_LINE", "PER_ZONE", "PER_OBJ", "PER_BYTE", "HOLE", "DLI_COST")
    print("%-10s %10s %10s" % ("constant", "watch", "fork"))
    for n, v in zip(names, sol):
        print("%-10s %10.3f %10s" % (n, v, ("%.3f" % FORK[n]) if n in FORK else "-"))
    print("%d shapes, worst residual %.2f%%" % (len(b), 100 * res.max()))
    for i in np.argsort(-res)[:4]:
        print("  %5.2f%%  %s  (%.0f cycles)" % (100 * res[i], labels[i], b[i]))


if __name__ == "__main__":
    main()
