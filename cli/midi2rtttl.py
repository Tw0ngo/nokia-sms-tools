#!/usr/bin/env python3
"""Convert a MIDI file into an RTTTL ringtone for send-ringtone.sh.

Ringtones are monophonic: one track/channel is picked as the melody and,
where notes overlap, the highest one wins.
"""
import argparse, math, os, struct, subprocess, sys, tempfile, wave

NOTES = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]
LENGTHS = [1, 2, 4, 8, 16, 32]          # RTTTL note lengths (whole .. 1/32)
OCT_MIN, OCT_MAX = 4, 7                  # octaves Nokia ringtones can play
GM_DRUMS = 9                             # MIDI channel 10 is percussion

# ---------------------------------------------------------------- MIDI parsing

def read_vlq(d, i):
    v = 0
    while True:
        b = d[i]; i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i

def parse_midi(path):
    """Return (ticks_per_beat, tempos, notes, names).
    notes: list of (track, channel, start_tick, end_tick, pitch)."""
    d = open(path, "rb").read()
    if d[:4] != b"MThd":
        sys.exit(f"not a MIDI file: {path}")
    hlen, fmt, ntrk, div = struct.unpack(">IHHH", d[4:14])
    if div & 0x8000:
        sys.exit("SMPTE-timed MIDI files are not supported")
    pos, tempos, notes, names = 8 + hlen, [], [], {}
    for t in range(ntrk):
        while d[pos:pos + 4] != b"MTrk":            # skip unknown chunks
            if pos + 8 > len(d): break
            pos += 8 + struct.unpack(">I", d[pos + 4:pos + 8])[0]
        if pos + 8 > len(d): break
        ln = struct.unpack(">I", d[pos + 4:pos + 8])[0]
        i, end, tick, status, on = pos + 8, pos + 8 + ln, 0, 0, {}
        pos = end
        while i < end:
            dt, i = read_vlq(d, i); tick += dt
            b = d[i]
            if b & 0x80:
                status = b; i += 1
            if status == 0xFF:                       # meta event
                typ = d[i]; ln2, i = read_vlq(d, i + 1)
                data = d[i:i + ln2]; i += ln2
                if typ == 0x51: tempos.append((tick, int.from_bytes(data, "big")))
                if typ == 0x03 and t not in names: names[t] = data.decode("latin-1").strip()
                status = 0                           # meta/sysex cancel running status
            elif status in (0xF0, 0xF7):             # sysex
                ln2, i = read_vlq(d, i); i += ln2; status = 0
            else:
                kind, ch = status & 0xF0, status & 0x0F
                nargs = 1 if kind in (0xC0, 0xD0) else 2
                args = d[i:i + nargs]; i += nargs
                if kind == 0x90 and args[1] > 0:
                    on.setdefault((ch, args[0]), []).append(tick)
                elif kind == 0x80 or (kind == 0x90 and args[1] == 0):
                    starts = on.get((ch, args[0]))
                    if starts:
                        s = starts.pop(0)
                        if tick > s: notes.append((t, ch, s, tick, args[0]))
    return div, sorted(tempos) or [(0, 500000)], notes, names

# ---------------------------------------------------------------- melody

def voices(notes):
    v = {}
    for t, ch, s, e, p in notes:
        v.setdefault((t, ch), []).append((s, e, p))
    return v

def pick_voice(v):
    """Most notes wins, but among voices with at least half as many notes as the
    busiest one prefer the highest-pitched (melodies usually sit on top)."""
    cand = {k: n for k, n in v.items() if k[1] != GM_DRUMS} or v
    most = max(len(n) for n in cand.values())
    busy = [k for k, n in cand.items() if len(n) >= most / 2]
    return max(busy, key=lambda k: sum(p for _, _, p in cand[k]) / len(cand[k]))

def skyline(ns):
    """Reduce to one note at a time: highest note at each onset, cut at the next onset."""
    by_start = {}
    for s, e, p in ns:
        if p > by_start.get(s, (0, -1))[1]: by_start[s] = (e, p)
    starts = sorted(by_start)
    out = []
    for k, s in enumerate(starts):
        e, p = by_start[s]
        if k + 1 < len(starts): e = min(e, starts[k + 1])
        out.append((s, e, p))
    return out

# ---------------------------------------------------------------- RTTTL

def length_options():
    """(length in 1/32 units, rtttl length, dotted)"""
    opts = []
    for L in LENGTHS:
        u = 32 // L
        opts.append((u, L, False))
        if u >= 2: opts.append((u * 3 // 2, L, True))
    return sorted(opts, reverse=True)

OPTS = length_options()

def nearest_length(units):
    return min(OPTS, key=lambda o: (abs(o[0] - units), -o[0]))

def split_rest(units):
    out = []
    while units >= 1:
        o = next(o for o in OPTS if o[0] <= units)
        out.append(o); units -= o[0]
    return out

def to_events(melody, tpb, transpose):
    """Quantise to 1/32 notes. Returns [(pitch or None, (units, len, dotted))]."""
    unit = tpb / 8                                   # a 1/32 note in ticks
    q = lambda t: round(t / unit)
    ev, cur = [], q(melody[0][0]) if melody else 0
    for s, e, p in melody:
        qs, qe = q(s), q(e)
        if qs < cur: qs = cur                        # pushed by previous rounding
        if qe <= qs: qe = qs + 1
        for r in split_rest(qs - cur): ev.append((None, r))
        o = nearest_length(qe - qs)
        ev.append((p + transpose, o))
        cur = qs + o[0]
    return ev

def fit_octaves(ev):
    """Shift the whole tune by octaves into the playable range, then fold outliers."""
    ps = sorted(p for p, _ in ev if p is not None)
    if not ps: return ev, 0
    med = ps[len(ps) // 2]
    lo, hi = (OCT_MIN + 1) * 12, (OCT_MAX + 2) * 12 - 1   # MIDI pitch range (C4=60)
    target = (lo + hi) // 2
    shift = 12 * round((target - med) / 12)
    out, folded = [], 0
    for p, o in ev:
        if p is not None:
            p += shift
            while p < lo: p += 12; folded += 1
            while p > hi: p -= 12; folded += 1
        out.append((p, o))
    return out, folded

def rtttl(name, ev, bpm):
    def parts(p, o):
        _, L, dot = o
        if p is None: return L, "p", None, dot
        return L, NOTES[p % 12], p // 12 - 1, dot
    ps = [parts(*e) for e in ev]
    def common(vals, default):
        vals = [v for v in vals if v is not None]
        return max(set(vals), key=vals.count) if vals else default
    d = common([L for L, *_ in ps], 4)
    o = common([oc for _, _, oc, _ in ps], 5)
    toks = []
    for L, n, oc, dot in ps:
        t = ("" if L == d else str(L)) + n + ("." if dot else "")
        if oc is not None and oc != o: t += str(oc)
        toks.append(t)
    safe = "".join(c for c in name if c.isalnum() or c in " -_")[:20] or "Ringtone"
    return f"{safe}:d={d},o={o},b={bpm}:" + ",".join(toks)

# ---------------------------------------------------------------- extras

def wav_preview(ev, bpm, path, rate=22050):
    """Square-wave render, roughly how the phone will sound."""
    w = wave.open(path, "wb"); w.setnchannels(1); w.setsampwidth(1); w.setframerate(rate)
    unit_s = 60 / bpm / 8
    buf = bytearray()
    for p, (u, _, _) in ev:
        n = int(u * unit_s * rate)
        if p is None:
            buf += bytes([128]) * n
        else:
            f = 440 * 2 ** ((p - 69) / 12)
            gap = min(n // 10, int(0.01 * rate))     # tiny gap so repeated notes are audible
            buf += bytes(160 if (k * f / rate) % 1 < 0.5 else 96 for k in range(n - gap))
            buf += bytes([128]) * gap
    w.writeframes(bytes(buf)); w.close()

def smart_size(text):
    """Size in bytes of the ringtone SMS payload (Nokia OTT format), via gammu (None if unavailable)."""
    try:
        with tempfile.TemporaryDirectory() as td:
            src, dst = os.path.join(td, "t.txt"), os.path.join(td, "t.ott")   # .ott = Nokia smart messaging format
            open(src, "w").write(text)
            subprocess.run(["gammu", "copyringtone", src, dst],
                           capture_output=True, timeout=20, check=True)
            return os.path.getsize(dst)
    except Exception:
        return None

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("midi")
    ap.add_argument("-o", "--out", help="output .txt (default: <midi name>.txt)")
    ap.add_argument("--list", action="store_true", help="list tracks/channels and exit")
    ap.add_argument("--track", type=int, help="track number to use (see --list)")
    ap.add_argument("--channel", type=int, help="MIDI channel 1-16 to use (see --list)")
    ap.add_argument("--start", type=float, default=0, help="skip the first N seconds")
    ap.add_argument("--max-notes", type=int, default=60, help="cut after N notes (default 60)")
    ap.add_argument("--transpose", type=int, default=0, help="semitones up (+) or down (-)")
    ap.add_argument("--bpm", type=int, help="override tempo")
    ap.add_argument("--name", help="ringtone name shown on the phone")
    ap.add_argument("--wav", help="also write a WAV preview to listen to")
    a = ap.parse_args()

    tpb, tempos, notes, names = parse_midi(a.midi)
    v = voices(notes)
    if not v: sys.exit("no notes found")

    if a.list:
        print(f"{'track':>5} {'ch':>3} {'notes':>6}  range     name")
        for (t, ch), ns in sorted(v.items()):
            lo, hi = min(p for *_, p in ns), max(p for *_, p in ns)
            nm = lambda p: f"{NOTES[p % 12]}{p // 12 - 1}"
            drum = "  (drums)" if ch == GM_DRUMS else ""
            print(f"{t:>5} {ch + 1:>3} {len(ns):>6}  {nm(lo):>3}-{nm(hi):<4}  {names.get(t, '')}{drum}")
        return

    keys = [k for k in v if (a.track is None or k[0] == a.track) and
            (a.channel is None or k[1] == a.channel - 1)]
    if not keys: sys.exit("no notes on that track/channel (see --list)")
    sel = {k: v[k] for k in keys}
    if a.track is None and a.channel is None:
        key = pick_voice(sel); ns = sel[key]
    else:
        key = None; ns = [n for k in keys for n in sel[k]]   # merge e.g. one channel across tracks

    us_per_beat = tempos[0][1]
    bpm = a.bpm or round(60_000_000 / us_per_beat)
    start_tick = a.start * 1_000_000 / us_per_beat * tpb
    melody = [n for n in skyline(ns) if n[0] >= start_tick]
    if not melody: sys.exit("no notes after --start")

    ev = to_events(melody, tpb, a.transpose)
    ev, folded = fit_octaves(ev)
    # cut after max-notes, drop trailing rests
    out, count = [], 0
    for e in ev:
        if e[0] is not None:
            if count == a.max_notes: break
            count += 1
        out.append(e)
    while out and out[-1][0] is None: out.pop()

    name = a.name or os.path.splitext(os.path.basename(a.midi))[0]
    text = rtttl(name, out, bpm)
    dst = a.out or os.path.splitext(a.midi)[0] + ".txt"
    open(dst, "w").write(text + "\n")

    if key: print(f"melody: track {key[0]}, channel {key[1] + 1} {names.get(key[0], '')}".rstrip()
                  + "  (other choices: --list)")
    print(f"{count} of {len(melody)} notes, {bpm} bpm" + (f", {folded} notes moved by an octave to fit" if folded else ""))
    print(text)
    size = smart_size(text)
    if size is not None:
        verdict = "fits in one SMS" if size <= 128 else "probably TOO LONG for one SMS, try a smaller --max-notes"
        print(f"{size} bytes as ringtone SMS: {verdict}")
    if a.wav:
        wav_preview(out, bpm, a.wav); print(f"preview: {a.wav}")
    print(f"wrote {dst}")

if __name__ == "__main__":
    main()
