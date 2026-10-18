#!/usr/bin/env python3
"""Compose a 72x14 operator logo from pieces: art files (14 lines, '#' = black) and text.
Usage: ./compose.py -o out.bmp art:hammer-sichel.art gap:4 text:CCCP gap:4 art:stern.art
Pieces are laid out left to right and the whole row is centred."""
import argparse, makelogo

def art(path):
    rows = [l.rstrip("\n") for l in open(path) if l.strip()]
    rows = [r.replace(".", " ") for r in rows]
    w = max(len(r) for r in rows)
    top = (14 - len(rows)) // 2
    grid = [[0] * w for _ in range(14)]
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch == "#": grid[top + y][x] = 1
    return grid

def text(t, font, spacing=1):
    """Render text proportionally: each glyph trimmed to its ink, `spacing` px between glyphs."""
    glyph, fw, fh = makelogo.load_psf(makelogo.FONTS[font])
    top = (14 - fh) // 2
    grid = [[] for _ in range(14)]
    for k, ch in enumerate(t):
        g = glyph(ch)
        cols = [x for x in range(fw) if any(r[x] for r in g)] or [0, 1, 2]   # space -> 3 px
        if k:
            for y in range(14): grid[y] += [0] * spacing
        for y in range(14):
            src = g[y - top] if 0 <= y - top < fh else [0] * fw
            grid[y] += src[cols[0]:cols[-1] + 1]
    return grid

ap = argparse.ArgumentParser()
ap.add_argument("pieces", nargs="+")
ap.add_argument("--font", choices=makelogo.FONTS, default="bold")
ap.add_argument("--invert", action="store_true")
ap.add_argument("--spacing", type=int, default=1, help="px between letters")
ap.add_argument("-o", "--out", default="logo.bmp")
a = ap.parse_args()

row = [[] for _ in range(14)]
for p in a.pieces:
    kind, _, val = p.partition(":")
    g = art(val) if kind == "art" else text(val, a.font, a.spacing) if kind == "text" else [[0] * int(val)] * 14
    for y in range(14): row[y] += g[y]
w = len(row[0])
if w > 72: raise SystemExit(f"too wide: {w} px (max 72)")
pad = (72 - w) // 2
px = [[0] * pad + r + [0] * (72 - w - pad) for r in row]
if a.invert: px = [[1 - b for b in r] for r in px]
makelogo.write_bmp(px, a.out)
print("+" + "-" * 72 + "+")
for r in px: print("|" + "".join("#" if b else " " for b in r) + "|")
print("+" + "-" * 72 + "+")
print(f"{w} of 72 px used, wrote {a.out}")
