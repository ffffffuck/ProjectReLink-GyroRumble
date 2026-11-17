"""(relink, issues 34, 63, 68, 20) The ocarina's note buttons for Xbox and PlayStation prompts: <game folder>/prompts/ocarina.bin.

The game draws the ocarina's note buttons with six textures of its Common layout archive (OcarinaButtonA_00, B, X, Y, L, R:
ASTC 4x4 sRGB, 93 x 93, one level: a white disc, or for L and R a white plate, with the Switch's letter in black), not with
the button font host/prompts.c replaces. Each build makes, for each of the six, ReLink's version for each family's label at
each pad position (the plate as the game's, its letter taken out, the label drawn in black at the letter's place and
height, nx2/button_glyphs.py's drawings), encoded in ASTC 4x4 again; and a hash of each of the game's six as the renderer
has them once unswizzled. The renderer (gpu/vk/ocarina_tex.h) uploads in place of one of the game's six the version for
the label of the pad position that Switch button is bound to.

ocarina.bin, little-endian: "RLKO", u16 version 1, u16 textures 6, u16 width 93, u16 height 93, u16 families 2, u16
positions 12; then for each of the game's textures (Switch A, B, X, Y, L, R) the button's name in 8 bytes (zero padded) and
the 64-bit FNV-1a hash of its blocks (16 bytes a 4 x 4 block, in rows, top down); then the versions, each the same number of
blocks in the same order: for each texture, for each family (xbox, playstation), for each position (south, east, west,
north, l1, r1, l2, r2, start, back, l3, r3); then the 64-bit FNV-1a hash of all the bytes before it.

Usage (a check, by hand): python -I nx2/ocarina_prompts.py <romfs> <out folder> [--png]: ocarina.bin, and with --png each
version decoded again as a PNG beside it (nx2/astc_decode.py)."""
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astc_decode as ad  # noqa: E402
import button_glyphs as bg  # noqa: E402
import wheel_assets as pa  # noqa: E402

BNTX = bg.BNTX
BUTTONS = ("A", "B", "X", "Y", "L", "R")
TEXTURES = tuple("OcarinaButton%s_00" % b for b in BUTTONS)
SIZE = 93
BLOCKS = ((SIZE + 3) // 4) ** 2           # (576 blocks of 16 bytes: 9,216 bytes)
ASTC_4x4_SRGB = 0x2D06                    # (the BNTX's format number)
POSITIONS = bg.POSITIONS                  # (south, east, west, north, l1, r1, l2, r2, start, back, l3, r3)
FAMILIES = bg.FAMILIES                    # (xbox, playstation)


class Unfit(Exception):
    """the game's note buttons are not the ones this script knows"""


def fnv1a64(b):
    h = 0xCBF29CE484222325
    for c in b:
        h = ((h ^ c) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


# ---------------------------------------------------------------------------------------------------------------------
# ASTC 4x4 encoding: one partition, endpoint mode 12 (RGBA direct), a 4 x 4 grid of 16-level weights (block mode 0x242,
# as some of the game's own blocks), the colours in the 48-level range the decoder then takes (trits and 4 bits each).
# For pictures of few colours along a line in each block (the plates and their labels: white, black and transparent
# white), which this is for.

BLOCK_MODE = 0x242
WEIGHT_RANGE = 16
COLOR_RANGE = 48
assert ad.block_mode(BLOCK_MODE) == (4, 4, 0, WEIGHT_RANGE)

# (each 8-bit value's nearest in the colour range, as (trit, low bits); each weight level's value 0..64)
_COLOR_CODES = [(d, lo) for d in range(3) for lo in range(16)]
_COLOR_VALUE = {c: ad.unq_color(c[0], c[1], COLOR_RANGE) for c in _COLOR_CODES}
NEAREST_COLOR = [min(_COLOR_CODES, key=lambda c: (abs(_COLOR_VALUE[c] - v), c)) for v in range(256)]
WEIGHT_VALUE = [ad.unq_weight(None, lo, WEIGHT_RANGE) for lo in range(16)]
# (the smallest T for each five trits: with trailing trits 0 its high bits are 0, as a block cut short needs)
TRITS_TO_T = {}
for _T in range(256):
    TRITS_TO_T.setdefault(tuple(ad.trit_digits(_T)), _T)


def ise_trits(values, bits):
    """(digit, low bits) values of a trit range packed as the integer sequence encoding: (int, bit count)"""
    out, pos = 0, 0
    total = ad.ise_bits(len(values), {4: COLOR_RANGE}[bits])
    for k in range(0, len(values), 5):
        group = list(values[k:k + 5]) + [(0, 0)] * (5 - len(values[k:k + 5]))
        T = TRITS_TO_T[tuple(d for d, _ in group)]
        layout = [(0, 2), (1, 2), (2, 1), (3, 2), (4, 1)]
        tpos = 0
        for i, tb in layout:
            out |= group[i][1] << pos
            pos += bits
            out |= ((T >> tpos) & ((1 << tb) - 1)) << pos
            pos += tb
            tpos += tb
    return out & ((1 << total) - 1), total


def encode_block(px):
    """16 RGBA texels (rows of 4) into a 16-byte ASTC 4x4 block"""
    lo = [min(p[c] for p in px) for c in range(4)]
    hi = [max(p[c] for p in px) for c in range(4)]
    e0 = [NEAREST_COLOR[v] for v in lo]
    e1 = [NEAREST_COLOR[v] for v in hi]
    q0 = [_COLOR_VALUE[c] for c in e0]
    q1 = [_COLOR_VALUE[c] for c in e1]
    axis = [b - a for a, b in zip(q0, q1)]
    n2 = sum(a * a for a in axis)
    weights = []
    for p in px:
        if n2 == 0:
            weights.append(0)
            continue
        t = sum((p[c] - q0[c]) * axis[c] for c in range(4)) / n2
        w64 = max(0.0, min(64.0, t * 64))
        weights.append(min(range(16), key=lambda k: abs(WEIGHT_VALUE[k] - w64)))
    # (endpoint mode 12's values: r0 r1 g0 g1 b0 b1 a0 a1; the decoder keeps their order when the second's r+g+b is not
    # below the first's, which min and max always give)
    vals = [e0[0], e1[0], e0[1], e1[1], e0[2], e1[2], e0[3], e1[3]]
    colors, nbits = ise_trits(vals, 4)
    v = BLOCK_MODE | (12 << 13) | (colors << 17)
    assert 17 + nbits <= 128 - 64
    stream = 0
    for i, w in enumerate(weights):
        stream |= w << (4 * i)
    v |= ad.reverse128(stream)
    return v.to_bytes(16, "little")


def encode(rgba, w, h):
    """RGBA8 rows (top down) into ASTC 4x4 blocks in rows, top down; the texels past the edge repeat the last ones"""
    out = bytearray()
    for by in range((h + 3) // 4):
        for bx in range((w + 3) // 4):
            px = []
            for t in range(4):
                y = min(by * 4 + t, h - 1)
                for s in range(4):
                    x = min(bx * 4 + s, w - 1)
                    o = (y * w + x) * 4
                    px.append(tuple(rgba[o:o + 4]))
            out += encode_block(px)
    return bytes(out)


# ---------------------------------------------------------------------------------------------------------------------
# The pictures

def game_textures(b):
    """{Switch button: (its blocks as the renderer has them once unswizzled, its RGBA8 pixels)} out of the Common BNTX"""
    tex = pa.bntx_textures(b)
    out = {}
    for button, name in zip(BUTTONS, TEXTURES):
        t = tex.get(name)
        if t is None:
            raise Unfit(f"there is no {name}")
        if (t[1], t[2], t[3]) != (ASTC_4x4_SRGB, SIZE, SIZE):
            raise Unfit(f"{name} is format {t[1]:#x}, {t[2]} x {t[3]}, not ASTC 4x4 sRGB {SIZE} x {SIZE}")
        blocks = pa.linear_blocks(b, t)
        out[button] = (blocks, ad.decode(blocks, SIZE, SIZE))
    hashes = [fnv1a64(out[x][0]) for x in BUTTONS]
    if len(set(hashes)) != len(hashes):
        raise Unfit("two of the note buttons have the same blocks")
    return out


def letter_of(rgba):
    """the game's letter in a note button: its ink (black on the plate) as coverage, and (box, stroke) in pixels"""
    cov = bytes(round((255 - rgba[i]) * rgba[i + 3] / 255) for i in range(0, len(rgba), 4))
    box, amount, stroke = bg.ink(cov, SIZE, SIZE)
    if box is None or amount < 40:
        raise Unfit("a note button holds no letter")
    return box, stroke


def label_contours(family, position, cap, stroke):
    """the contours (y up) of a family's label for a pad position, alone (no plate), and the scale that gives it the
    game's letters' height: (contours, scale)"""
    xbox = family == "xbox"
    cls = bg.CLASS_OF[position]
    p = bg.Pen()
    old = bg.STEM, bg.BAR
    bg.STEM = stroke / cap
    bg.BAR = bg.STEM * old[1] / old[0]
    try:
        H = 1000.0
        if cls == "face" and xbox:
            return bg.text({"south": "A", "east": "B", "west": "X", "north": "Y"}[position], H, 0, 0), cap / H
        if cls == "face":
            {"south": bg.sym_cross, "east": bg.sym_circle, "west": bg.sym_square, "north": bg.sym_triangle}[position](p, 0, 0)
            return p.contours, cap / bg.FACE_CAP
        if cls in ("shoulder", "trigger", "stick"):
            first = {"l1": "L", "r1": "R", "l2": "L", "r2": "R", "l3": "L", "r3": "R"}[position]
            second = {"shoulder": "B1", "trigger": "T2", "stick": "S3"}[cls][0 if xbox else 1]
            return bg.text(first + second, H, 0, 0), cap / H
        if position == "start":
            bg.bars(p, 0, 0, 250, 58, 52)
        elif xbox:
            bg.sym_view(p, 0, 0)
        else:
            bg.sym_create(p, 0, 0)
        b = bg.bbox(p.contours)
        return p.contours, cap / (b[3] - b[1])
    finally:
        bg.STEM, bg.BAR = old


def plate_width_at(rgba, y):
    """the plate's opaque run across row y, in pixels: (x0, x1)"""
    xs = [x for x in range(SIZE) if rgba[(y * SIZE + x) * 4 + 3] >= 128]
    return (xs[0], xs[-1] + 1) if xs else (0, 0)


def version(rgba, family, position, stroke):
    """the game's note button with its letter replaced by the family's label for the pad position: RGBA8 rows. stroke:
    the game's letters' (the median of the six: one weight for all the labels)"""
    (x0, y0, x1, y1), _ = letter_of(rgba)
    cap = y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    contours, s = label_contours(family, position, cap, stroke)
    b = bg.bbox(contours)
    # (a wide label held inside the plate, with a margin: its width at the letter's rows)
    p0, p1 = plate_width_at(rgba, int(cy))
    room = (p1 - p0) - 2 * max(6.0, 0.16 * (p1 - p0))
    if (b[2] - b[0]) * s > room:
        s = room / (b[2] - b[0])
    mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    ink = bg.raster(contours, SIZE, SIZE, lambda q: (cx + (q[0] - mx) * s, cy - (q[1] - my) * s))
    out = bytearray(rgba)
    for i in range(SIZE * SIZE):
        a = rgba[4 * i + 3]
        g = 255 - ink[i] if a else 255
        out[4 * i:4 * i + 3] = bytes((g, g, g))
    return bytes(out)


def make(romfs, out, png=False, dictionary=None):
    """ocarina.bin into out; returns its path, or raises Unfit"""
    dictionary = dictionary or pa.zs_dictionary(romfs)
    game = game_textures(pa.read_zs(romfs, BNTX, dictionary))
    body = bytearray(b"RLKO" + struct.pack("<6H", 1, len(BUTTONS), SIZE, SIZE, len(FAMILIES), len(POSITIONS)))
    for button in BUTTONS:
        body += button.encode().ljust(8, b"\0") + struct.pack("<Q", fnv1a64(game[button][0]))
    strokes = sorted(letter_of(game[button][1])[1] for button in BUTTONS)
    stroke = strokes[len(strokes) // 2]
    for button in BUTTONS:
        rgba = game[button][1]
        for fam in FAMILIES:
            for pos in POSITIONS:
                blocks = encode(version(rgba, fam, pos, stroke), SIZE, SIZE)
                if len(blocks) != BLOCKS * 16:
                    raise Unfit("a version of the wrong size")
                body += blocks
                if png:
                    from PIL import Image
                    Image.frombytes("RGBA", (SIZE, SIZE), ad.decode(blocks, SIZE, SIZE)).save(
                        os.path.join(out, f"ocarina_{button}_{fam}_{pos}.png"))
    body += struct.pack("<Q", fnv1a64(bytes(body)))
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "ocarina.bin")
    with open(path, "wb") as f:
        f.write(body)
    return path


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if len(a) < 2:
        print(__doc__)
        return 2
    path = make(a[0], a[1], png="--png" in a)
    print(f"ocarina prompts: {os.path.getsize(path)} bytes in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
