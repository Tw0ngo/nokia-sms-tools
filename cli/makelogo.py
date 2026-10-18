#!/usr/bin/env python3
"""Render text (or ASCII art) into a 72x14 1-bit BMP for a Nokia operator logo."""
import argparse, gzip, struct, sys

W, H = 72, 14
FONTS = {
    "bold": "/usr/share/consolefonts/Uni2-TerminusBold14.psf.gz",   # 8x14, 9 chars
    "normal": "/usr/share/consolefonts/Uni2-Terminus14.psf.gz",     # 8x14, 9 chars
    "small": "/usr/share/consolefonts/Uni2-Terminus12x6.psf.gz",    # 6x12, 12 chars
}

def load_psf(path):
    data = gzip.open(path).read()
    if data[:2] == b"\x36\x04":                       # PSF1
        mode, height = data[2], data[3]
        n, width, bpg, off = (512 if mode & 1 else 256), 8, height, 4
        has_table = mode & 2
    elif data[:4] == b"\x72\xb5\x4a\x86":             # PSF2
        _, off, flags, n, bpg, height, width = struct.unpack("<7I", data[4:32])
        has_table = flags & 1
    else:
        sys.exit(f"not a PSF font: {path}")
    glyphs = [data[off + i * bpg: off + (i + 1) * bpg] for i in range(n)]
    cmap = {}
    if has_table:
        pos, i = off + n * bpg, 0
        if data[:2] == b"\x36\x04":                   # PSF1 table: UCS-2 entries, 0xFFFF ends a glyph
            while i < n and pos + 1 < len(data):
                (u,) = struct.unpack("<H", data[pos:pos + 2]); pos += 2
                if u == 0xFFFF: i += 1
                elif u != 0xFFFE: cmap.setdefault(chr(u), i)
        else:                                          # PSF2 table: UTF-8, 0xFF ends a glyph
            for entry in data[pos:].split(b"\xff")[:n]:
                for ch in entry.split(b"\xfe")[0].decode("utf-8", "ignore"):
                    cmap.setdefault(ch, i)
                i += 1
    else:
        cmap = {chr(c): c for c in range(n)}
    rowbytes = (width + 7) // 8
    def glyph(ch):
        g = glyphs[cmap.get(ch, cmap.get("?", 0))]
        return [[(g[r * rowbytes + c // 8] >> (7 - c % 8)) & 1 for c in range(width)] for r in range(height)]
    return glyph, width, height

def from_text(text, font, invert):
    glyph, fw, fh = load_psf(FONTS[font])
    if len(text) * fw > W:
        sys.exit(f"text too long: max {W // fw} characters with font '{font}'")
    px = [[0] * W for _ in range(H)]
    x0, y0 = (W - len(text) * fw) // 2, (H - fh) // 2
    for k, ch in enumerate(text):
        for r, row in enumerate(glyph(ch)):
            for c, bit in enumerate(row):
                if bit: px[y0 + r][x0 + k * fw + c] = 1
    if invert:
        px = [[1 - b for b in row] for row in px]
    return px

def from_art(path):
    lines = [l.rstrip("\n") for l in open(path)]
    if len(lines) != H or any(len(l) > W for l in lines):
        sys.exit(f"art file must have exactly {H} lines of at most {W} characters")
    return [[1 if ch == "#" else 0 for ch in l.ljust(W)] for l in lines]

def write_bmp(px, path):
    stride = ((W + 31) // 32) * 4
    rows = b""
    for row in reversed(px):                           # BMP rows are bottom-up
        bits = bytearray(stride)
        for x, b in enumerate(row):
            if b: bits[x // 8] |= 0x80 >> (x % 8)
        rows += bytes(bits)
    palette = b"\xff\xff\xff\x00" + b"\x00\x00\x00\x00"   # 0 = white, 1 = black
    off = 14 + 40 + len(palette)
    hdr = b"BM" + struct.pack("<IHHI", off + len(rows), 0, 0, off)
    dib = struct.pack("<IiiHHIIiiII", 40, W, H, 1, 1, 0, len(rows), 2835, 2835, 2, 2)
    open(path, "wb").write(hdr + dib + palette + rows)

def main():
  ap = argparse.ArgumentParser()
  src = ap.add_mutually_exclusive_group(required=True)
  src.add_argument("--text"); src.add_argument("--art", help="14 lines, '#' = black")
  ap.add_argument("--font", choices=FONTS, default="bold")
  ap.add_argument("--invert", action="store_true", help="white text on black")
  ap.add_argument("-o", "--out", default="logo.bmp")
  a = ap.parse_args()
  px = from_text(a.text, a.font, a.invert) if a.text else from_art(a.art)
  write_bmp(px, a.out)
  print("+" + "-" * W + "+")
  for row in px: print("|" + "".join("#" if b else " " for b in row) + "|")
  print("+" + "-" * W + "+")
  print(f"wrote {a.out}")

if __name__ == "__main__":
    main()
