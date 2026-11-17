"""(relink) Button prompts for Xbox and PlayStation controllers, made from the player's own RomFS at every build
(nx2/make_game.py), into <game folder>/prompts/: the game's External font (Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs,
the font of the button glyphs U+E040..U+E092) decoded, with ReLink's own button labels added, one font per family.

    python -I nx2/button_glyphs.py --romfs <romfs> --out <game folder>/prompts

xbox.otf, playstation.otf: plain OpenType (CFF), exactly as long as the original decoded font (zero bytes after the last
table). Each holds that family's twelve labels, drawn by the code below from plain geometry: the face buttons (south, east,
west, north), the shoulders (l1, r1), the triggers (l2, r2), start and back, the stick clicks (l3, r3). The cmap gives each
of the twelve Switch-button code points (A U+E040 ... RS-push U+E057) its own 2-byte glyph id, which the host patches in
place to follow the player's bindings; as written, they hold the labels of ReLink's default bindings. Every other code
point maps to the original glyph, with its outline and advance unchanged; the game's own Switch glyphs stay in the font.
The font's empty glyphs that nothing maps to are left out to make room.

prompts.txt: what the host (host/prompts.c) needs to patch the fonts and wrap them as the game's file is wrapped, one
record a line (see write_sidecar).

keytex.bin: the HUD cross's faded letters. The game draws them from four textures of its Common layout archive
(KeyTexFontA_00, B, X, Y: BC4, 64 x 64, one level, a mask of the letter alone that the layout tints), not from the font.
The file holds a hash of each of the four as the renderer has them once unswizzled, and ReLink's own masks of each
family's four face labels (the label alone, drawn from the outlines above), which the renderer uploads in their place
(gpu/vk/prompt_tex.h; see write_keytex for the layout). The game's letters are decoded here only to be measured: their
ink box and stroke, which ours are scaled and placed to match; none of their pixels is written.
"""
import argparse
import hashlib
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wheel_assets as pa  # noqa: E402 (zs_dictionary, read_zs)

FONT = "Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs"
BFOTF_MAGIC = bytes.fromhex("d99b871a")

# The Switch buttons whose glyphs follow the bindings: name, code point, class of the glyph (its advance in the game's font)
SWITCH = (("A", 0xE040, "face"), ("B", 0xE041, "face"), ("X", 0xE042, "face"), ("Y", 0xE043, "face"),
          ("L", 0xE044, "shoulder"), ("R", 0xE045, "shoulder"), ("ZL", 0xE046, "trigger"), ("ZR", 0xE047, "trigger"),
          ("Plus", 0xE04B, "start"), ("Minus", 0xE04C, "start"), ("LS", 0xE056, "stick"), ("RS", 0xE057, "stick"))
# The pad positions a label is drawn for, in the order of the label glyphs in the fonts
POSITIONS = ("south", "east", "west", "north", "l1", "r1", "l2", "r2", "start", "back", "l3", "r3")
CLASS_OF = {"south": "face", "east": "face", "west": "face", "north": "face", "l1": "shoulder", "r1": "shoulder",
            "l2": "trigger", "r2": "trigger", "start": "start", "back": "start", "l3": "stick", "r3": "stick"}
# ReLink's default bindings: the pad position each Switch button is on (host/nn_hid.c)
DEFAULT = {"A": "east", "B": "south", "X": "north", "Y": "west", "L": "l1", "R": "r1", "ZL": "l2", "ZR": "r2",
           "Plus": "start", "Minus": "back", "LS": "l3", "RS": "r3"}
FAMILIES = ("xbox", "playstation")


class Unfit(Exception):
    """the font does not take what this script adds"""


# ---------------------------------------------------------------------------------------------------------------------
# Geometry. A contour is a closed list of segments, each ("L", p0, p1) or ("C", p0, c1, c2, p1), y up, in font units.

class Pen:
    def __init__(self):
        self.contours = []

    def move(self, x, y):
        self.contours.append([])
        self.start = self.cur = (x, y)

    def line(self, x, y):
        if math.hypot(x - self.cur[0], y - self.cur[1]) > 1e-6:
            self.contours[-1].append(("L", self.cur, (x, y)))
            self.cur = (x, y)

    def arc(self, cx, cy, a, b, t0, t1):
        """the elliptic arc of centre (cx, cy) and half-axes a, b from angle t0 to t1 (degrees, either way round), cut at
        each quarter turn so that its ends hold its extremes"""
        cuts = [t0]
        step = 1 if t1 > t0 else -1
        k = math.floor(t0 / 90) + 1 if step > 0 else math.ceil(t0 / 90) - 1
        while (k * 90 - t1) * step < -1e-9:
            cuts.append(k * 90)
            k += step
        cuts.append(t1)
        for u0, u1 in zip(cuts, cuts[1:]):
            r0, r1 = math.radians(u0), math.radians(u1)
            h = 4 / 3 * math.tan((r1 - r0) / 4)
            p0 = (cx + a * math.cos(r0), cy + b * math.sin(r0))
            p3 = (cx + a * math.cos(r1), cy + b * math.sin(r1))
            c1 = (p0[0] - h * a * math.sin(r0), p0[1] + h * b * math.cos(r0))
            c2 = (p3[0] + h * a * math.sin(r1), p3[1] - h * b * math.cos(r1))
            self.line(*p0)
            self.contours[-1].append(("C", self.cur, c1, c2, p3))
            self.cur = p3

    def close(self):
        self.line(*self.start)

    def poly(self, pts):
        self.move(*pts[0])
        for p in pts[1:]:
            self.line(*p)
        self.close()


def ell(cx, cy, a, b, t):
    """the point of angle t (degrees) on an ellipse"""
    return cx + a * math.cos(math.radians(t)), cy + b * math.sin(math.radians(t))


def root(f, lo, hi):
    """x in [lo, hi] where f changes sign (bisection)"""
    flo = f(lo)
    for _ in range(80):
        mid = (lo + hi) / 2
        if (f(mid) > 0) == (flo > 0):
            lo, flo = mid, f(mid)
        else:
            hi = mid
    return (lo + hi) / 2


def meet(p, q, r, s):
    """where the line through p and q crosses the line through r and s"""
    d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
    u = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
    return p[0] + u * (q[0] - p[0]), p[1] + u * (q[1] - p[1])


def flatten(c, steps=8):
    """a contour as a polygon"""
    pts = []
    for s in c:
        if s[0] == "L":
            pts.append(s[2])
        else:
            p0, c1, c2, p3 = s[1:]
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                pts.append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p3[0],
                            u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p3[1]))
    return pts


def area(pts):
    return sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts))) / 2


def inside(p, pts):
    x, y = p
    n = False
    for i in range(len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            n = not n
    return n


def reverse(c):
    return [("L", s[2], s[1]) if s[0] == "L" else ("C", s[4], s[3], s[2], s[1]) for s in reversed(c)]


def orient(contours):
    """each contour counterclockwise when it is inside an even number of the others (ink), clockwise when inside an odd
    number (a hole): the glyph then fills the same with the non-zero and the even-odd rules"""
    polys = [flatten(c) for c in contours]
    out = []
    for i, c in enumerate(contours):
        depth = sum(1 for j, q in enumerate(polys) if j != i and inside(polys[i][0], q))
        if (area(polys[i]) > 0) != (depth % 2 == 0):
            c = reverse(c)
        out.append(c)
    return out


def transform(contours, f):
    return [[(s[0],) + tuple(f(p) for p in s[1:]) for s in c] for c in contours]


def bbox(contours):
    pts = [p for c in contours for s in c for p in s[1:]]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


# ---------------------------------------------------------------------------------------------------------------------
# The labels' alphabet: heavy capitals and digits with flat ends, drawn at x on the baseline with cap height H; each
# returns its width. Upright strokes are STEM times the cap height thick, horizontal ones BAR times.

STEM = 0.215
BAR = 0.18


def letter_L(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.56 * H
    p.poly([(x, 0), (x + W, 0), (x + W, bw), (x + sw, bw), (x + sw, H), (x, H)])
    return W


def letter_T(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.68 * H
    c = x + W / 2
    p.poly([(x, H), (x, H - bw), (c - sw / 2, H - bw), (c - sw / 2, 0), (c + sw / 2, 0), (c + sw / 2, H - bw),
            (x + W, H - bw), (x + W, H)])
    return W


def letter_1(p, x, H):
    sw, bw = STEM * H, BAR * H
    s = x + 0.26 * H                  # the stem's left edge; the flag goes down to the left from its top
    fy, fw = 0.24 * H, 1.45 * bw
    p.poly([(s, 0), (s + sw, 0), (s + sw, H), (s, H), (x, H - fy), (x, H - fy - fw), (s, H - fw)])
    return s + sw - x


def letter_A(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.9 * H
    top = 0.16 * W                    # the flat apex
    xa = W / 2 - top / 2
    k = xa / H                        # the legs' run per unit of height
    fw = sw * math.hypot(xa, H) / H  # a leg's horizontal width

    def il(y):
        return fw + y * k

    def ir(y):
        return W - fw - y * k
    yc = 0.2 * H
    yt = yc + bw
    p.poly([(x + px, py) for px, py in ((0, 0), (fw, 0), (il(yc), yc), (ir(yc), yc), (W - fw, 0), (W, 0), (W - xa, H),
                                         (xa, H))])
    ys = (W - 2 * fw) / (2 * k)       # where the legs' inner edges meet
    if ys > yt + 20:
        p.poly([(x + il(yt), yt), (x + ir(yt), yt), (x + W / 2, ys)])
    return W


def letter_X(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.84 * H
    fw = sw
    for _ in range(4):                # a bar's horizontal width, for its slope
        fw = sw * math.hypot(W - fw, H) / H
    l1, r1 = ((0, 0), (W - fw, H)), ((fw, 0), (W, H))
    a2, b2 = ((W - fw, 0), (0, H)), ((W, 0), (fw, H))
    pts = [(0, 0), (fw, 0), meet(*r1, *a2), (W - fw, 0), (W, 0), meet(*b2, *r1), (W, H), (W - fw, H), meet(*l1, *b2),
           (fw, H), (0, H), meet(*a2, *l1)]
    p.poly([(x + px, py) for px, py in pts])
    return W


def letter_Y(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.86 * H
    c, yj = W / 2, 0.4 * H
    dx, dy = W - (c + sw / 2), H - yj
    fw = sw * math.hypot(dx, dy) / dy
    ycr = H - (W - fw - c) * dy / dx
    pts = [(c - sw / 2, 0), (c + sw / 2, 0), (c + sw / 2, yj), (W, H), (W - fw, H), (c, ycr), (fw, H), (0, H),
           (c - sw / 2, yj)]
    p.poly([(x + px, py) for px, py in pts])
    return W


def letter_B(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.7 * H
    bu = 0.47 * (H + bw) / 2         # half-heights of the bowls: they share the middle bar
    bl = (H + bw) / 2 - bu
    al, au = 0.44 * W, 0.42 * W
    xl, xu = x + W - al, x + 0.95 * W - au
    cyl, cyu = bl, H - bu

    def outside_upper(t):
        px, py = ell(xl, cyl, al, bl, t)
        return ((px - xu) / au) ** 2 + ((py - cyu) / bu) ** 2 - 1
    tl = root(outside_upper, 0, 90)
    px, py = ell(xl, cyl, al, bl, tl)
    tu = math.degrees(math.atan2((py - cyu) / bu, (px - xu) / au))
    p.move(x, 0)
    p.line(xl, 0)
    p.arc(xl, cyl, al, bl, -90, tl)
    p.arc(xu, cyu, au, bu, tu, 90)
    p.line(x, H)
    p.close()
    p.move(x + sw, 2 * bl)          # the upper counter
    p.line(xu, 2 * bl)
    p.arc(xu, cyu, au - sw, bu - bw, -90, 90)
    p.line(x + sw, H - bw)
    p.close()
    p.move(x + sw, bw)             # the lower counter
    p.line(xl, bw)
    p.arc(xl, cyl, al - sw, bl - bw, -90, 90)
    p.line(x + sw, 2 * bl - bw)
    p.close()
    return W


def letter_R(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.76 * H
    yb = 0.37 * H                      # the bar under the bowl
    bu = (H - yb) / 2
    au = 0.45 * W
    xu, cyu = x + 0.95 * W - au, H - bu
    xj = x + sw + 0.2 * W           # the leg leaves the bar's underside here, down to the foot on the right
    lfw = sw
    for _ in range(4):                # the leg's horizontal width, for its slope
        d = (x + W - lfw - xj, -yb)
        lfw = 1.05 * sw * math.hypot(*d) / yb
    d = (x + W - lfw - xj, -yb)
    foot = (x + W, 0)

    def side(t):
        px, py = ell(xu, cyu, au, bu, t)
        return d[0] * (py - foot[1]) - d[1] * (px - foot[0])
    tk = root(side, -90, 0)
    p.move(x, 0)
    p.line(x + sw, 0)
    p.line(x + sw, yb)
    p.line(xj, yb)
    p.line(x + W - lfw, 0)
    p.line(x + W, 0)
    p.line(*ell(xu, cyu, au, bu, tk))
    p.arc(xu, cyu, au, bu, tk, 90)
    p.line(x, H)
    p.close()
    p.move(x + sw, yb + bw)
    p.line(xu, yb + bw)
    p.arc(xu, cyu, au - sw, bu - bw, -90, 90)
    p.line(x + sw, H - bw)
    p.close()
    return W


def letter_S(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.68 * H
    b = (H + bw) / 4                 # half-heights of the two rings, which share the middle stroke
    au, al = 0.47 * W, 0.5 * W
    cx, yu, yl = x + W / 2, H - b, b
    ts, te = 38, -142                 # the ends: upper right, lower left
    p.move(*ell(cx, yu, au, b, ts))
    p.arc(cx, yu, au, b, ts, 270)
    p.arc(cx, yl, al - sw, b - bw, 90, te)
    p.line(*ell(cx, yl, al, b, te))
    p.arc(cx, yl, al, b, te, 90)
    p.arc(cx, yu, au - sw, b - bw, 270, ts)
    p.close()
    return W


def letter_2(p, x, H):
    sw, bw = STEM * H, BAR * H
    W = 0.68 * H
    a, b = W / 2, 0.29 * H
    cx, cy = x + W / 2, H - b
    t0, t1 = 165, -18                 # the bowl, from its end on the left over the top to the right
    po = ell(cx, cy, a, b, t1)
    xd = sw
    for _ in range(4):                # the diagonal's outer edge meets the bottom bar xd from the left
        v = (x + xd - po[0], bw - po[1])
        xd = 1.05 * sw * math.hypot(*v) / abs(v[1])
    v = (x + xd - po[0], bw - po[1])
    base = (x, bw)

    def side(t):
        px, py = ell(cx, cy, a - sw, b - bw, t)
        return v[0] * (py - base[1]) - v[1] * (px - base[0])
    t2 = root(side, t1 - 60, t1 + 60)
    p.move(*ell(cx, cy, a, b, t0))
    p.arc(cx, cy, a, b, t0, t1)
    p.line(x + xd, bw)
    p.line(x + W, bw)
    p.line(x + W, 0)
    p.line(x, 0)
    p.line(x, bw)
    p.line(*ell(cx, cy, a - sw, b - bw, t2))
    p.arc(cx, cy, a - sw, b - bw, t2, t0)
    p.close()
    return W


def letter_3(p, x, H):
    """a flat-topped 3: the top bar, a diagonal from its right end, and the lower bowl it runs into"""
    sw, bw = STEM * H, BAR * H
    W = 0.7 * H
    a, b = W / 2, 0.3 * H             # the lower bowl
    cx, cy = x + W / 2, b
    yt = 2 * b                        # the bowl's top
    xl = x + 0.1 * W                  # the left end of the bowl's top stroke
    xa = x + 0.36 * W                 # the diagonal's inner edge meets the bowl's top here
    dx = sw
    for _ in range(4):                # the diagonal's horizontal width, for its slope
        v = (xa + dx - (x + W), yt - H)
        dx = 1.05 * sw * math.hypot(*v) / abs(v[1])
    v = (xa + dx - (x + W), yt - H)
    corner = (x + W, H)

    def side(t):
        px, py = ell(cx, cy, a, b, t)
        return v[0] * (py - corner[1]) - v[1] * (px - corner[0])
    tk = root(side, 0, 180)
    xb = xa + (H - bw - yt) * v[0] / v[1]
    te = -148
    p.move(x + 0.04 * W, H)
    p.line(x + W, H)
    p.line(*ell(cx, cy, a, b, tk))
    p.arc(cx, cy, a, b, tk, te)
    p.line(*ell(cx, cy, a - sw, b - bw, te))
    p.arc(cx, cy, a - sw, b - bw, te, 90)
    p.line(xl, yt - bw)
    p.line(xl, yt)
    p.line(xa, yt)
    p.line(xb, H - bw)
    p.line(x + 0.04 * W, H - bw)
    p.close()
    return W


LETTERS = {"L": letter_L, "T": letter_T, "1": letter_1, "A": letter_A, "X": letter_X, "Y": letter_Y, "B": letter_B,
           "R": letter_R, "S": letter_S, "2": letter_2, "3": letter_3}


def text(s, H, cx, cy):
    """the contours of the text s with cap height H, its ink centred on (cx, cy)"""
    p = Pen()
    x = 0
    for ch in s:
        x += LETTERS[ch](p, x, H) + 0.12 * H
    b = bbox(p.contours)
    dx, dy = cx - (b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2
    return transform(p.contours, lambda q: (q[0] + dx, q[1] + dy))


# ---------------------------------------------------------------------------------------------------------------------
# Plates and symbols

def disc(p, cx, cy, r):
    p.move(cx + r, cy)
    p.arc(cx, cy, r, r, 0, 360)
    p.close()


def rrect(p, x0, y0, x1, y1, rt, rb):
    """a rectangle with its top corners rounded by rt and its bottom ones by rb"""
    p.move(x0 + rb, y0)
    p.line(x1 - rb, y0)
    p.arc(x1 - rb, y0 + rb, rb, rb, -90, 0)
    p.line(x1, y1 - rt)
    p.arc(x1 - rt, y1 - rt, rt, rt, 0, 90)
    p.line(x0 + rt, y1)
    p.arc(x0 + rt, y1 - rt, rt, rt, 90, 180)
    p.line(x0, y0 + rb)
    p.arc(x0 + rb, y0 + rb, rb, rb, 180, 270)
    p.close()


def rect(p, x0, y0, x1, y1):
    p.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def ring_arc(p, cx, cy, r0, r1, t0, t1):
    """the band between radii r0 and r1 from angle t0 to t1"""
    p.move(*ell(cx, cy, r1, r1, t0))
    p.arc(cx, cy, r1, r1, t0, t1)
    p.line(*ell(cx, cy, r0, r0, t1))
    p.arc(cx, cy, r0, r0, t1, t0)
    p.close()


def bars(p, cx, cy, w, t, gap):
    """three horizontal bars"""
    for k in (-1, 0, 1):
        y = cy + k * (t + gap)
        rect(p, cx - w / 2, y - t / 2, cx + w / 2, y + t / 2)


def sym_cross(p, cx, cy):
    L, h = 158, 33
    pts = [(-h, -L), (h, -L), (h, -h), (L, -h), (L, h), (h, h), (h, L), (-h, L), (-h, h), (-L, h), (-L, -h), (-h, -h)]
    c = math.sqrt(0.5)
    p.poly([(cx + (x - y) * c, cy + (x + y) * c) for x, y in pts])


def sym_circle(p, cx, cy):
    disc(p, cx, cy, 148)
    disc(p, cx, cy, 148 - 58)


def sym_square(p, cx, cy):
    s, t = 124, 54
    rect(p, cx - s, cy - s, cx + s, cy + s)
    rect(p, cx - s + t, cy - s + t, cx + s - t, cy + s - t)


def sym_triangle(p, cx, cy):
    R, t = 178, 52                    # circumradius; the band's width
    cy -= 16                          # (the triangle looks centred a little below its centroid)
    sc = (R / 2 - t) / (R / 2)
    for r in (R, R * sc):
        p.poly([ell(cx, cy, r, r, a) for a in (90, 210, 330)])


def centre(p, q, cx, cy):
    """the contours of pen q added to pen p, their ink centred on (cx, cy)"""
    b = bbox(q.contours)
    dx, dy = cx - (b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2
    p.contours += transform(q.contours, lambda v: (v[0] + dx, v[1] + dy))


def sym_view(p, cx, cy):
    """two overlapping rectangles: the front one whole, the back one where the front one does not hide it"""
    t, g = 50, 40
    w, h, off = 190, 150, 110
    fx0, fy0 = cx - w / 2 + off / 2, cy - h / 2 - off / 2
    fx1, fy1 = fx0 + w, fy0 + h
    bx0, by0 = fx0 - off, fy0 + off
    bx1, by1 = bx0 + w, by0 + h
    rect(p, fx0, fy0, fx1, fy1)
    rect(p, fx0 + t, fy0 + t, fx1 - t, fy1 - t)
    p.poly([(bx0, by0), (fx0 - g, by0), (fx0 - g, by0 + t), (bx0 + t, by0 + t), (bx0 + t, by1 - t), (bx1 - t, by1 - t),
            (bx1 - t, fy1 + g), (bx1, fy1 + g), (bx1, by1), (bx0, by1)])


def sym_create(p, cx, cy):
    """three rays fanning out up and to the left from a point below and to the right, widening as they go"""
    q = Pen()
    d0, d1, h0, h1 = 64, 250, 13, 31  # where each ray starts and ends, its half-widths there
    for a in (90, 135, 180):
        u = (math.cos(math.radians(a)), math.sin(math.radians(a)))
        n = (-u[1], u[0])
        q.poly([(u[0] * d0 - n[0] * h0, u[1] * d0 - n[1] * h0), (u[0] * d1 - n[0] * h1, u[1] * d1 - n[1] * h1),
                (u[0] * d1 + n[0] * h1, u[1] * d1 + n[1] * h1), (u[0] * d0 + n[0] * h0, u[1] * d0 + n[1] * h0)])
    centre(p, q, cx, cy)


# The labels: a plate with the label knocked out of it, drawn for the advances the game's glyphs have (DRAWN_FOR) and
# moved to the middle of the advance the player's font gives that class of button.
DRAWN_FOR = {"face": 640, "shoulder": 720, "trigger": 720, "start": 600, "stick": 700}


def label(family, position, advances):
    """the contours of a family's label for a pad position, and its advance (advances: by class of button)"""
    cls = CLASS_OF[position]
    adv = advances[cls]
    contours = art(family == "xbox", position, cls)
    dx = (adv - DRAWN_FOR[cls]) / 2
    return orient(transform(contours, lambda q: (q[0] + dx, q[1]))), adv


def art(xbox, position, cls):
    p = Pen()
    if cls == "face":
        cx, cy = 320, 301
        disc(p, cx, cy, 282)
        if xbox:
            p.contours += text({"south": "A", "east": "B", "west": "X", "north": "Y"}[position], 292, cx, cy)
        else:
            {"south": sym_cross, "east": sym_circle, "west": sym_square, "north": sym_triangle}[position](p, cx, cy)
    elif cls == "shoulder":
        rrect(p, 55, 80, 665, 520, 128, 128)
        p.contours += text(("L" if position == "l1" else "R") + ("B" if xbox else "1"), 300, 360, 300)
    elif cls == "trigger":
        rrect(p, 82, 37, 638, 591, 236, 56)
        p.contours += text(("L" if position == "l2" else "R") + ("T" if xbox else "2"), 290, 360, 284)
    elif cls == "start":
        cx, cy = 300, 300
        if xbox:
            disc(p, cx, cy, 244)
            if position == "start":
                bars(p, cx, cy, 250, 58, 52)
            else:
                sym_view(p, cx, cy)
        else:
            rrect(p, 110, 40, 490, 560, 190, 190)
            if position == "start":
                bars(p, cx, cy, 200, 58, 52)
            else:
                sym_create(p, cx, cy)
    else:
        cx, cy, r = 350, 372, 290
        disc(p, cx, cy, r)
        p.contours += text(("L" if position == "l3" else "R") + ("S" if xbox else "3"), 272, cx, cy + 4)
        ring_arc(p, cx, cy, r + 40, r + 88, 230, 310)   # (the press: a band under the stick)
    return p.contours


# ---------------------------------------------------------------------------------------------------------------------
# CFF

def cs_num(v):
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([247 + (v >> 8), v & 255])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([251 + (v >> 8), v & 255])
    return b"\x1c" + struct.pack(">h", v)


def charstring(contours, width):
    """a Type 2 charstring of the contours, width the first operand or None. Lines along an axis go in hlineto and
    vlineto runs, curves that start along one axis and end along the other in hvcurveto and vhcurveto runs, the others
    in rlineto and rrcurveto; each contour starts with rmoveto and closes by itself"""
    out = bytearray()
    cur = (0, 0)
    first = True
    for c in contours:
        segs = [(s[0], [(round(q[0]), round(q[1])) for q in s[1:]]) for s in c]
        segs = [s for s in segs if len(set(s[1])) > 1]
        if segs and segs[-1][0] == "L" and segs[-1][1][1] == segs[0][1][0]:
            segs.pop()                # (closing the contour draws that line)
        if not segs:
            continue
        start = segs[0][1][0]
        args = ([width] if first and width is not None else []) + [start[0] - cur[0], start[1] - cur[1]]
        out += b"".join(cs_num(v) for v in args) + b"\x15"
        first = False
        cur = start
        steps = []                  # (kind, operands): kind h or v (a line along x or y), l (any line), hv or vh
        for kind, pts in segs:        # (a curve leaving along x and arriving along y, or the other way), c (any curve)
            d = [(q[0] - p[0], q[1] - p[1]) for p, q in zip([cur] + pts[1:-1], pts[1:])]
            cur = pts[-1]
            if kind == "L":
                dx, dy = d[0]
                steps.append(("h", [dx]) if dy == 0 else ("v", [dy]) if dx == 0 else ("l", [dx, dy]))
            elif d[0][1] == 0 and d[2][0] == 0:
                steps.append(("hv", [d[0][0], d[1][0], d[1][1], d[2][1]]))
            elif d[0][0] == 0 and d[2][1] == 0:
                steps.append(("vh", [d[0][1], d[1][0], d[1][1], d[2][0]]))
            else:
                steps.append(("c", [v for q in d for v in q]))
        i = 0
        while i < len(steps):         # runs of at most 48 operands
            kind = steps[i][0]
            run = []
            j = i
            if kind in ("h", "v", "hv", "vh"):
                swap = {"h": "v", "v": "h", "hv": "vh", "vh": "hv"}
                want = kind
                while j < len(steps) and steps[j][0] == want and len(run) + len(steps[j][1]) <= 48:
                    run += steps[j][1]
                    want = swap[want]
                    j += 1
                op = {"h": 6, "v": 7, "hv": 31, "vh": 30}[kind]
            else:
                while j < len(steps) and steps[j][0] == kind and len(run) + len(steps[j][1]) <= 48:
                    run += steps[j][1]
                    j += 1
                op = 5 if kind == "l" else 8
            out += b"".join(cs_num(v) for v in run) + bytes([op])
            i = j
    if first and width is not None:
        out += cs_num(width)
    return bytes(out + b"\x0e")


def read_index(b, o):
    """the items of a CFF INDEX at o, and where it ends"""
    count, = struct.unpack_from(">H", b, o)
    if count == 0:
        return [], o + 2
    osz = b[o + 2]
    offs = [int.from_bytes(b[o + 3 + osz * i:o + 3 + osz * (i + 1)], "big") for i in range(count + 1)]
    base = o + 3 + osz * (count + 1) - 1
    return [b[base + offs[i]:base + offs[i + 1]] for i in range(count)], base + offs[-1]


def write_index(items):
    if not items:
        return b"\0\0"
    end = sum(len(x) for x in items) + 1
    osz = 1 if end < 1 << 8 else 2 if end < 1 << 16 else 3 if end < 1 << 24 else 4
    offs, o = [], 1
    for x in items:
        offs.append(o)
        o += len(x)
    offs.append(o)
    return struct.pack(">HB", len(items), osz) + b"".join(v.to_bytes(osz, "big") for v in offs) + b"".join(items)


def read_dict(b):
    """a CFF DICT as [(operator, [operands])]; an operand is an int, or the bytes of a real number"""
    out, ops, k = [], [], 0
    while k < len(b):
        b0 = b[k]
        if b0 <= 21:
            op = b0
            if b0 == 12:
                k += 1
                op = 1200 + b[k]
            out.append((op, ops))
            ops = []
            k += 1
        elif b0 == 28:
            ops.append(struct.unpack_from(">h", b, k + 1)[0])
            k += 3
        elif b0 == 29:
            ops.append(struct.unpack_from(">i", b, k + 1)[0])
            k += 5
        elif b0 == 30:
            j = k + 1
            while (b[j] & 0xF) != 0xF and (b[j] >> 4) != 0xF:
                j += 1
            ops.append(bytes(b[k:j + 1]))
            k = j + 1
        elif 32 <= b0 <= 246:
            ops.append(b0 - 139)
            k += 1
        elif 247 <= b0 <= 250:
            ops.append((b0 - 247) * 256 + b[k + 1] + 108)
            k += 2
        elif 251 <= b0 <= 254:
            ops.append(-(b0 - 251) * 256 - b[k + 1] - 108)
            k += 2
        else:
            raise Unfit(f"CFF DICT byte {b0} not known")
    return out


def dict_num(v, wide=False):
    if isinstance(v, bytes):
        return v
    if wide or not -32768 <= v <= 32767:
        return b"\x1d" + struct.pack(">i", v)
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131 or -1131 <= v <= -108:
        return cs_num(v)
    return b"\x1c" + struct.pack(">h", v)


def write_dict(entries, wide=()):
    out = bytearray()
    for op, ops in entries:
        out += b"".join(dict_num(v, op in wide) for v in ops)
        out += bytes([12, op - 1200]) if op >= 1200 else bytes([op])
    return bytes(out)


def read_charset(b, o, n):
    """the SIDs of glyphs 1..n-1"""
    fmt = b[o]
    sids, k = [], o + 1
    if fmt == 0:
        sids = list(struct.unpack_from(">%dH" % (n - 1), b, k))
    elif fmt in (1, 2):
        while len(sids) < n - 1:
            first, = struct.unpack_from(">H", b, k)
            left = b[k + 2] if fmt == 1 else struct.unpack_from(">H", b, k + 2)[0]
            k += 3 if fmt == 1 else 4
            sids += range(first, first + left + 1)
    else:
        raise Unfit(f"CFF charset format {fmt} not known")
    return sids[:n - 1]


def write_charset(sids):
    """format 1 (ranges) or format 0, the shorter"""
    ranges = []
    for s in sids:
        if ranges and s == ranges[-1][0] + ranges[-1][1] + 1 and ranges[-1][1] < 255:
            ranges[-1][1] += 1
        else:
            ranges.append([s, 0])
    f1 = b"\x01" + b"".join(struct.pack(">HB", s, n) for s, n in ranges)
    f0 = b"\x00" + struct.pack(">%dH" % len(sids), *sids)
    return f1 if len(f1) <= len(f0) else f0


SID_OPS = {0, 1, 2, 3, 4, 1200, 1221, 1222}   # top DICT operators whose operands are strings


def rebuild_cff(cff, keep, extra):
    """the CFF with glyphs 0..keep-1 as they are and then the (name, charstring) of extra; returns (bytes, width info)"""
    hdr = cff[2]
    names, o = read_index(cff, hdr)
    tops, o = read_index(cff, o)
    strings, o = read_index(cff, o)
    gsubrs_at = o
    _, o = read_index(cff, o)
    gsubrs = cff[gsubrs_at:o]
    if len(tops) != 1:
        raise Unfit(f"{len(tops)} fonts in the CFF")
    top = read_dict(tops[0])
    ops = dict(top)
    for op in (1230, 1236, 1237):
        if op in ops:
            raise Unfit("a CID-keyed CFF")
    if ops.get(16, [0])[0] > 1:
        raise Unfit("a CFF with its own encoding")
    charstrings, _ = read_index(cff, ops[17][0])
    n = len(charstrings)
    sids = [0] + read_charset(cff, ops[15][0], n) if 15 in ops and ops[15][0] > 2 else None
    if sids is None:
        raise Unfit("a CFF with a predefined charset")
    psize, poff = ops[18]
    private = read_dict(cff[poff:poff + psize])
    pops = dict(private)
    region_end = poff + psize
    if 19 in pops:
        sub_at = poff + pops[19][0]
        if pops[19][0] < psize:
            raise Unfit("local subroutines before the end of the Private DICT")
        _, region_end = read_index(cff, sub_at)
    region = cff[poff:region_end]
    default_w = pops.get(20, [0])[0]
    nominal_w = pops.get(21, [0])[0]

    new_strings, sid_map = [], {}

    def keep_sid(s):
        if s < 391:
            return s
        if s not in sid_map:
            sid_map[s] = 391 + len(new_strings)
            new_strings.append(strings[s - 391])
        return sid_map[s]
    new_sids = [keep_sid(s) for s in sids[:keep]]   # (first: the kept glyphs keep their names' numbers)
    top = [(op, [keep_sid(v) for v in vs] if op in SID_OPS else vs) for op, vs in top]
    known = set(new_strings)
    for name, _ in extra:
        if name.encode() in known:
            raise Unfit(f"a glyph named {name} already")
        new_sids.append(391 + len(new_strings))
        new_strings.append(name.encode())
    new_cs = charstrings[:keep] + [cs for _, cs in extra]

    wide = (15, 17, 18)
    top = [(op, vs) for op, vs in top if op not in wide] + [(15, [0]), (17, [0]), (18, [0, 0])]
    head = cff[:hdr] + write_index(names)
    tlen = len(write_index([write_dict(top, wide)]))
    pre = head + b"\0" * tlen + write_index(new_strings) + gsubrs
    cs_at = len(pre) + len(write_charset(new_sids[1:]))
    charset_at = len(pre)
    cs_index = write_index(new_cs)
    priv_at = cs_at + len(cs_index)
    top = [(op, vs) for op, vs in top if op not in wide] + [(15, [charset_at]), (17, [cs_at]), (18, [psize, priv_at])]
    out = head + write_index([write_dict(top, wide)]) + write_index(new_strings) + gsubrs + write_charset(new_sids[1:])
    assert len(out) == cs_at
    return out + cs_index + region, default_w, nominal_w


# ---------------------------------------------------------------------------------------------------------------------
# sfnt

def unwrap(b):
    """the OpenType font inside a bfotf file: magic, then the length and the font XORed with a 4-byte key"""
    if b[:4] != BFOTF_MAGIC or len(b) < 16:
        raise Unfit("External is not a bfotf font")
    key = bytes(x ^ y for x, y in zip(b[8:12], b"OTTO"))
    size = int.from_bytes(bytes(x ^ y for x, y in zip(b[4:8], key)), "big")
    if size > len(b) - 8:
        raise Unfit("External's bfotf length is past its end")
    return bytes(c ^ key[i & 3] for i, c in enumerate(b[8:8 + size]))


def wrapper(b):
    """what the host needs to wrap a font as the game's file is wrapped: the magic, the key, the file's length and the
    bytes after the font (the file is the magic, the font's length XORed with the key, the font XORed with the key, then
    those bytes as they are)"""
    key = bytes(x ^ y for x, y in zip(b[8:12], b"OTTO"))
    size = int.from_bytes(bytes(x ^ y for x, y in zip(b[4:8], key)), "big")
    after = b[8 + size:]
    if len(after) > 4096:
        raise Unfit(f"External has {len(after)} bytes after its font")
    return {"magic": b[:4], "key": key, "length": len(b), "after": after}


def tables(font):
    """[(tag, checksum, offset, length)] in the directory's order"""
    if font[:4] != b"OTTO":
        raise Unfit("External is not a CFF OpenType font")
    n, = struct.unpack_from(">H", font, 4)
    return [(t.decode("latin-1"), s, o, ln) for t, s, o, ln in (struct.unpack_from(">4sIII", font, 12 + 16 * i)
                                                                   for i in range(n))]


def checksum(b):
    b = b + b"\0" * (-len(b) % 4)
    return sum(struct.unpack(">%dI" % (len(b) // 4), b)) & 0xFFFFFFFF


def read_cmap4(t, o):
    """{code point: glyph} of a format 4 subtable at o"""
    segx2, = struct.unpack_from(">H", t, o + 6)
    seg = segx2 // 2
    ends = struct.unpack_from(">%dH" % seg, t, o + 14)
    starts = struct.unpack_from(">%dH" % seg, t, o + 16 + segx2)
    deltas = struct.unpack_from(">%dH" % seg, t, o + 16 + 2 * segx2)
    ro_at = o + 16 + 3 * segx2
    ros = struct.unpack_from(">%dH" % seg, t, ro_at)
    m = {}
    for s in range(seg):
        for c in range(starts[s], ends[s] + 1):
            if c == 0xFFFF:
                continue
            if ros[s] == 0:
                g = (c + deltas[s]) & 0xFFFF
            else:
                g, = struct.unpack_from(">H", t, ro_at + 2 * s + ros[s] + 2 * (c - starts[s]))
                g = (g + deltas[s]) & 0xFFFF if g else 0
            if g:
                m[c] = g
    return m


def write_cmap4(m, slots):
    """a format 4 subtable of {code point: glyph} where each code point of slots has its own entry in glyphIdArray;
    returns it and the subtable offsets of those entries"""
    segs = []                         # [start, end, delta, glyphs or None]
    for c in sorted(m):
        g = m[c]
        if c in slots:
            if segs and segs[-1][1] == c - 1 and segs[-1][3] is not None:
                segs[-1][1] = c
                segs[-1][3].append(g)
            else:
                segs.append([c, c, 0, [g]])
        elif segs and segs[-1][1] == c - 1 and segs[-1][3] is None and (g - c) & 0xFFFF == segs[-1][2]:
            segs[-1][1] = c
        else:
            segs.append([c, c, (g - c) & 0xFFFF, None])
    segs.append([0xFFFF, 0xFFFF, 1, None])
    n = len(segs)
    sr = 2 * (1 << (n.bit_length() - 1))
    ros, glyphs, at = [], [], {}
    for i, (start, _, _, gl) in enumerate(segs):
        if gl is None:
            ros.append(0)
            continue
        ros.append(2 * (n - i) + 2 * len(glyphs))
        for k in range(len(gl)):
            at[start + k] = 16 + 8 * n + 2 * (len(glyphs) + k)
        glyphs += gl
    body = (struct.pack(">%dH" % n, *(s[1] for s in segs)) + b"\0\0" + struct.pack(">%dH" % n, *(s[0] for s in segs)) +
            struct.pack(">%dH" % n, *(s[2] for s in segs)) + struct.pack(">%dH" % n, *ros) +
            struct.pack(">%dH" % len(glyphs), *glyphs))
    head = struct.pack(">7H", 4, 14 + len(body), 0, 2 * n, sr, sr.bit_length() - 2, 2 * n - sr)
    return head + body, at


def rebuild_cmap(cmap, slot_glyphs):
    """the cmap with its format 4 subtables rebuilt so that each code point of slot_glyphs has its own glyph id slot,
    holding the glyph given; other subtables kept as they are. Returns it, every code point's glyph as the font had them,
    and the cmap offsets of the slots"""
    n, = struct.unpack_from(">H", cmap, 2)
    recs = [struct.unpack_from(">HHI", cmap, 4 + 8 * i) for i in range(n)]
    out = bytearray(cmap[:4 + 8 * n])
    placed, slots, before = {}, None, {}
    for so in sorted({r[2] for r in recs}):   # (the subtables in the order the font has them; records may share one)
        fmt, = struct.unpack_from(">H", cmap, so)
        if fmt == 4:
            if slots is not None:   # (the host would have two slots to patch for a code point)
                raise Unfit("two format 4 cmap subtables")
            m = read_cmap4(cmap, so)
            before.update(m)
            missing = [c for c in slot_glyphs if c not in m]
            if missing:
                raise Unfit(f"U+{missing[0]:04X} is not in External's cmap")
            m.update(slot_glyphs)
            sub, at = write_cmap4(m, set(slot_glyphs))
            out += b"\0" * (-len(out) % 2)
            slots = {c: len(out) + a for c, a in at.items()}
        else:
            ln = struct.unpack_from(">H" if fmt < 8 else ">I", cmap, so + 2 if fmt < 8 else so + 4)[0]
            sub = cmap[so:so + ln]
            out += b"\0" * (-len(out) % 2)
        placed[so] = len(out)
        out += sub
    for i, (pid, eid, so) in enumerate(recs):
        struct.pack_into(">HHI", out, 4 + 8 * i, pid, eid, placed[so])
    if slots is None:
        raise Unfit("no format 4 cmap subtable")
    return bytes(out), before, slots


def all_glyphs(cmap):
    """every glyph any cmap subtable maps to"""
    n, = struct.unpack_from(">H", cmap, 2)
    gs = set()
    for i in range(n):
        so, = struct.unpack_from(">I", cmap, 4 + 8 * i + 4)
        fmt, = struct.unpack_from(">H", cmap, so)
        if fmt == 4:
            gs |= set(read_cmap4(cmap, so).values())
        elif fmt == 6:
            first, count = struct.unpack_from(">HH", cmap, so + 6)
            gs |= set(struct.unpack_from(">%dH" % count, cmap, so + 10))
        elif fmt == 0:
            gs |= set(cmap[so + 6:so + 6 + 256])
        else:
            raise Unfit(f"cmap format {fmt} not known")
    return gs


def build(orig, family):
    """the family's font, as long as orig, and where its patchable parts are"""
    dirs = tables(orig)
    t = {tag: orig[o:o + ln] for tag, _, o, ln in dirs}
    for tag in ("GSUB", "GPOS", "kern", "morx", "COLR", "SVG ", "CFF2"):
        if tag in t:
            raise Unfit(f"External has a {tag.strip()} table: its unmapped glyphs may be in use")
    for tag in ("CFF ", "cmap", "head", "hhea", "hmtx", "maxp"):
        if tag not in t:
            raise Unfit(f"External has no {tag.strip()} table")
    if "post" in t and struct.unpack_from(">I", t["post"], 0)[0] == 0x20000:
        raise Unfit("External's post table names its glyphs")
    num_glyphs, = struct.unpack_from(">H", t["maxp"], 4)
    nh, = struct.unpack_from(">H", t["hhea"], 34)
    metrics = []
    for g in range(num_glyphs):
        aw = struct.unpack_from(">H", t["hmtx"], 4 * min(g, nh - 1))[0]
        lsb = struct.unpack_from(">h", t["hmtx"], 4 * g + 2 if g < nh else 4 * nh + 2 * (g - nh))[0]
        metrics.append((aw, lsb))
    keep = max(all_glyphs(t["cmap"])) + 1

    # the font's code points before; each Switch button's glyph, and the advance of each class of button
    _, before, _ = rebuild_cmap(t["cmap"], {cp: 0 for _, cp, _ in SWITCH})
    switch_glyph = {name: before[cp] for name, cp, _ in SWITCH}
    advances = {}
    for name, _, cls in SWITCH:
        advances.setdefault(cls, metrics[switch_glyph[name]][0])

    cff = t["CFF "]
    _, default_w, nominal_w = rebuild_cff(cff, keep, [])
    extra, ext_metrics, ext_box = [], [], []
    for pos in POSITIONS:
        contours, adv = label(family, pos, advances)
        box = bbox(contours)
        cs = charstring(contours, None if adv == default_w else adv - nominal_w)
        extra.append((f"{family[:2]}.{pos}", cs))
        ext_metrics.append((adv, round(box[0])))
        ext_box.append(box)
    label_glyph = {pos: keep + i for i, pos in enumerate(POSITIONS)}
    new_cff, _, _ = rebuild_cff(cff, keep, extra)

    slot_glyphs = {cp: label_glyph[DEFAULT[name]] for name, cp, _ in SWITCH}
    new_cmap, _, slots = rebuild_cmap(t["cmap"], slot_glyphs)

    all_metrics = metrics[:keep] + ext_metrics
    n = len(all_metrics)
    new_hmtx = b"".join(struct.pack(">Hh", aw, lsb) for aw, lsb in all_metrics)
    maxp = bytearray(t["maxp"])
    struct.pack_into(">H", maxp, 4, n)
    hhea = bytearray(t["hhea"])
    struct.pack_into(">H", hhea, 34, n)
    aw_max, min_lsb, min_rsb, x_ext = struct.unpack_from(">Hhhh", hhea, 10)
    head = bytearray(t["head"])
    xmin, ymin, xmax, ymax = struct.unpack_from(">hhhh", head, 36)
    for (adv, lsb), box in zip(ext_metrics, ext_box):
        aw_max = max(aw_max, adv)
        min_lsb = min(min_lsb, lsb)
        min_rsb = min(min_rsb, adv - round(box[2]))
        x_ext = max(x_ext, round(box[2]))
        xmin, ymin = min(xmin, math.floor(box[0])), min(ymin, math.floor(box[1]))
        xmax, ymax = max(xmax, math.ceil(box[2])), max(ymax, math.ceil(box[3]))
    struct.pack_into(">Hhhh", hhea, 10, aw_max, min_lsb, min_rsb, x_ext)
    struct.pack_into(">hhhh", head, 36, xmin, ymin, xmax, ymax)
    struct.pack_into(">I", head, 8, 0)
    new = dict(t)
    new.update({"CFF ": new_cff, "cmap": new_cmap, "hmtx": new_hmtx, "maxp": bytes(maxp), "hhea": bytes(hhea),
                "head": bytes(head)})

    # the tables in the order the original has them in the file, each on 4 bytes; the directory in its own order
    order = [tag for tag, _, _, _ in sorted(dirs, key=lambda d: d[2])]
    out = bytearray(orig[:12 + 16 * len(dirs)])
    at = {}
    for tag in order:
        out += b"\0" * (-len(out) % 4)
        at[tag] = len(out)
        out += new[tag]
    if len(out) > len(orig):
        raise Unfit(f"the {family} font needs {len(out)} bytes, more than the game's {len(orig)}")
    out += b"\0" * (len(orig) - len(out))
    dir_at = {}
    for i, (tag, _, _, _) in enumerate(dirs):
        struct.pack_into(">4sIII", out, 12 + 16 * i, tag.encode("latin-1"), checksum(new[tag]), at[tag], len(new[tag]))
        dir_at[tag] = 12 + 16 * i
    struct.pack_into(">I", out, at["head"] + 8, (0xB1B0AFBA - checksum(bytes(out))) & 0xFFFFFFFF)
    info = {"slots": {cp: at["cmap"] + o for cp, o in slots.items()}, "labels": label_glyph,
            "cmap": (at["cmap"], len(new_cmap), dir_at["cmap"] + 4), "head": at["head"] + 8,
            "switch": switch_glyph}
    return bytes(out), info


def fnv1a64(b):
    """the 64-bit FNV-1a hash of b (the host checks each font against it before patching it)"""
    h = 0xCBF29CE484222325
    for c in b:
        h = ((h ^ c) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def write_sidecar(path, orig, infos, wrap, fonts):
    """prompts.txt"""
    first = infos[FAMILIES[0]]
    for fam in FAMILIES[1:]:          # (the same font but its outlines: the same places in both files)
        i = infos[fam]
        if (i["slots"], i["cmap"], i["head"], i["switch"]) != (first["slots"], first["cmap"], first["head"],
                                                                first["switch"]):
            raise Unfit("the families' fonts differ in layout")
    lines = [
        "# Button prompt fonts made by nx2/button_glyphs.py from the game's own Font2/Scalable/External font:",
        "# xbox.otf and playstation.otf are that font, decoded, with ReLink's labels added. Numbers are decimal,",
        "# code points hex. One record a line; the same offsets hold in both files.",
        "# size <bytes>: each font's length, the original decoded font's.",
        "# original_sha256 <hex>: of the original decoded font.",
        "# slot <code point> <offset>: the file offset of that code point's glyph id (2 bytes, big-endian).",
        "# label <family> <position> <glyph id>: the glyph of that family's button at that pad position.",
        "# switch <code point> <glyph id>: the game's own glyph for that Switch button, still in both fonts.",
        "# cmap <offset> <length> <checksum offset>: the cmap table, and where the table directory holds its checksum.",
        "# head <offset>: head.checkSumAdjustment, 0xB1B0AFBA minus the file's checksum (taken with it at zero).",
        "# wrapper <magic hex> <key hex> <length> <after hex, - for none>: the game's file as it is decompressed: the",
        "# magic, the font's length (4 bytes, big-endian) XORed with the key, the font with byte i XORed with key[i & 3],",
        "# then the bytes after the font as they are; length is the whole file's.",
        "# font <family> <hex>: the 64-bit FNV-1a hash of that family's file as written here.",
        f"size {len(orig)}",
        f"original_sha256 {hashlib.sha256(orig).hexdigest()}",
    ]
    lines += [f"slot {cp:04X} {first['slots'][cp]}" for _, cp, _ in SWITCH]
    lines += [f"label {fam} {pos} {infos[fam]['labels'][pos]}" for fam in FAMILIES for pos in POSITIONS]
    lines += [f"switch {cp:04X} {first['switch'][name]}" for name, cp, _ in SWITCH]
    lines += ["cmap %d %d %d" % first["cmap"], f"head {first['head']}"]
    lines += ["wrapper %s %s %d %s" % (wrap["magic"].hex(), wrap["key"].hex(), wrap["length"],
                                       wrap["after"].hex() if wrap["after"] else "-")]
    lines += ["font %s %016x" % (fam, fnv1a64(fonts[fam])) for fam in FAMILIES]
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------------------------------------------------
# The HUD cross's letters: four BC4 textures of the Common layout archive, replaced by the renderer

BNTX = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.bntx.zs"
KEYTEX = (("A", "KeyTexFontA_00"), ("B", "KeyTexFontB_00"), ("X", "KeyTexFontX_00"), ("Y", "KeyTexFontY_00"))
FACE = ("south", "east", "west", "north")   # (the order of the masks in keytex.bin: HOS_PAD_SOUTH .. HOS_PAD_NORTH)
KEYTEX_SIZE = 64
BC4_UNORM = 0x1D01                          # (the BNTX's format number for BC4 UNORM)
FACE_CAP = 292                              # (the face labels' cap height in the fonts: the symbols are drawn to it)


def bc4_palette(a0, a1):
    """the eight values of a BC4 block with endpoints a0, a1 (rounded as a GPU's unorm interpolation)"""
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1 + 3) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1 + 2) // 5 for i in range(1, 5)] + [0, 255]


def bc4_decode(blocks, w, h):
    """linear BC4 blocks (rows of 4 x 4 blocks, top down) into w x h 8-bit values"""
    px = bytearray(w * h)
    bw = (w + 3) // 4
    for k in range(len(blocks) // 8):
        blk = blocks[8 * k:8 * k + 8]
        pal = bc4_palette(blk[0], blk[1])
        bits = int.from_bytes(blk[2:8], "little")
        bx, by = k % bw, k // bw
        for i in range(16):
            x, y = 4 * bx + i % 4, 4 * by + i // 4
            if x < w and y < h:
                px[y * w + x] = pal[(bits >> (3 * i)) & 7]
    return bytes(px)


def bc4_block(v):
    """one 4 x 4 block of 16 values as BC4: both modes tried (eight values between the endpoints; six plus 0 and 255),
    each with its endpoints moved around the block's extremes, each value given the nearest of the palette; the
    smallest squared error wins"""
    lo, hi = min(v), max(v)
    if lo == hi:
        return bytes([lo, lo]) + bytes(6)
    best = None
    inner = [x for x in v if 0 < x < 255]
    tries = [(1, lo, hi)]
    if inner:
        tries.append((0, min(inner), max(inner)))
    else:
        tries.append((0, 0, 0))
    for eight, l0, h0 in tries:
        for dh in range(-3, 4):
            for dl in range(-3, 4):
                h1, l1 = min(255, max(0, h0 + dh)), min(255, max(0, l0 + dl))
                if eight and h1 <= l1:
                    continue
                if not eight and l1 > h1:
                    continue
                a0, a1 = (h1, l1) if eight else (l1, h1)
                pal = bc4_palette(a0, a1)
                idx, err = [], 0
                for x in v:
                    k = min(range(8), key=lambda j: abs(pal[j] - x))
                    idx.append(k)
                    err += (pal[k] - x) ** 2
                if best is None or err < best[0]:
                    best = (err, a0, a1, idx)
        if best[0] == 0:
            break
    _, a0, a1, idx = best
    bits = sum(k << (3 * i) for i, k in enumerate(idx))
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def bc4_encode(px, w, h):
    """w x h 8-bit values (w, h multiples of 4) as linear BC4 blocks"""
    out = bytearray()
    for by in range(h // 4):
        for bx in range(w // 4):
            out += bc4_block([px[(4 * by + i // 4) * w + 4 * bx + i % 4] for i in range(16)])
    return bytes(out)


def bntx_linear(b, t, bpe):
    """a BNTX texture's first level as linear blocks: rows of blocks, top down (bpe bytes a block, 4 x 4 texels)"""
    _, _, w, h, gobs, data = t
    bw, bh = (w + 3) // 4, (h + 3) // 4
    pitch = (bw * bpe + 63) // 64
    return b"".join(b[data + o:data + o + bpe] for o in (pa.gob_offset(x * bpe, y, gobs, pitch)
                                                          for y in range(bh) for x in range(bw)))


def game_keytex(b):
    """{Switch button: the game's KeyTexFont texture as linear BC4 blocks} out of the decompressed Common BNTX"""
    tex = pa.bntx_textures(b)
    out = {}
    for button, name in KEYTEX:
        if name not in tex:
            raise Unfit(f"no {name} in {BNTX}")
        p, fmt, w, h, _, _ = tex[name]
        mips, = struct.unpack_from("<H", b, p + 0x16)
        layers, = struct.unpack_from("<I", b, p + 0x30)
        if (fmt, w, h, mips, layers) != (BC4_UNORM, KEYTEX_SIZE, KEYTEX_SIZE, 1, 1):
            raise Unfit(f"{name} is format {fmt:#x}, {w} x {h}, {mips} levels, {layers} layers: not the BC4 64 x 64 mask "
                        "known")
        out[button] = bntx_linear(b, tex[name], 8)
    if len({fnv1a64(v) for v in out.values()}) != len(out):   # (the renderer tells them apart by their hash alone)
        raise Unfit("two of the KeyTexFont textures have the same blocks")
    return out


def ink(px, w, h, cut=128):
    """the box of the values at cut or more (x0, y0, x1, y1: pixel edges), the coverage in pixels (the values' sum /
    255) and the stroke (the median length of the runs of values at cut or more along the rows)"""
    xs = [i % w for i, v in enumerate(px) if v >= cut]
    ys = [i // w for i, v in enumerate(px) if v >= cut]
    if not xs:
        return None, 0.0, 0
    runs = []
    for y in range(h):
        r = 0
        for x in range(w + 1):
            if x < w and px[y * w + x] >= cut:
                r += 1
            elif r:
                runs.append(r)
                r = 0
    runs.sort()
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1), sum(px) / 255, runs[len(runs) // 2]


def raster(contours, w, h, f, sub=16):
    """the contours (font units, y up) through f into w x h pixels (y down), as coverage 0..255: even-odd fill, sub rows
    a pixel, each span's ends exact along x"""
    edges = []
    for c in contours:
        pts = [f(p) for p in flatten(c, 24)]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    acc = [0.0] * (w * h)
    for sy in range(h * sub):
        y = (sy + 0.5) / sub
        xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for x0, y0, x1, y1 in edges if (y0 <= y) != (y1 <= y))
        row = (sy // sub) * w
        for a, b in zip(xs[0::2], xs[1::2]):
            a, b = max(a, 0.0), min(b, float(w))
            if b <= a:
                continue
            ia, ib = int(a), int(b)
            if ia == ib:
                acc[row + ia] += (b - a) / sub
                continue
            acc[row + ia] += (ia + 1 - a) / sub
            for i in range(ia + 1, ib):
                acc[row + i] += 1 / sub
            if ib < w:
                acc[row + ib] += (b - ib) / sub
    return bytes(min(255, round(v * 255)) for v in acc)


def keytex_target(game):
    """where and how big the game's letters are, averaged over the four: (centre x, centre y, cap height, stroke) in
    pixels"""
    boxes, strokes = [], []
    for button, _ in KEYTEX:
        box, cov, stroke = ink(bc4_decode(game[button], KEYTEX_SIZE, KEYTEX_SIZE), KEYTEX_SIZE, KEYTEX_SIZE)
        if box is None or cov < 20:
            raise Unfit(f"the game's KeyTexFont{button} holds no letter")
        boxes.append(box)
        strokes.append(stroke)
    cx = sum(b[0] + b[2] for b in boxes) / (2 * len(boxes))
    cy = sum(b[1] + b[3] for b in boxes) / (2 * len(boxes))
    cap = sum(b[3] - b[1] for b in boxes) / len(boxes)
    stroke = sorted(strokes)[len(strokes) // 2]
    if not (12 <= cap <= 56 and 16 <= cx <= 48 and 16 <= cy <= 48 and 2 <= stroke <= cap / 3):
        raise Unfit(f"the game's KeyTexFont letters are not the size known (cap height {cap:.1f}, centre {cx:.1f},"
                    f"{cy:.1f}, stroke {stroke})")
    return cx, cy, cap, stroke


def keytex_mask(family, position, target):
    """ReLink's mask of a family's face label (the label alone, no disc) as 64 x 64 coverage values: the letter drawn
    at the game's letters' cap height with their stroke, the symbol with the face labels' proportions to that height,
    its ink box centred on the game's letters'"""
    global STEM, BAR
    cx, cy, cap, stroke = target
    p = Pen()
    if family == "xbox":
        H = 1000.0
        old = STEM, BAR
        STEM = stroke / cap
        BAR = STEM * old[1] / old[0]
        try:
            contours = text({"south": "A", "east": "B", "west": "X", "north": "Y"}[position], H, 0, 0)
        finally:
            STEM, BAR = old
        s = cap / H
    else:
        {"south": sym_cross, "east": sym_circle, "west": sym_square, "north": sym_triangle}[position](p, 0, 0)
        contours = p.contours
        s = cap / FACE_CAP
    b = bbox(contours)
    mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return raster(contours, KEYTEX_SIZE, KEYTEX_SIZE, lambda q: (cx + (q[0] - mx) * s, cy - (q[1] - my) * s))


def write_keytex(path, game, masks):
    """keytex.bin, little-endian: "RLKT", u16 version 1, u16 textures 4, u16 width 64, u16 height 64, u16 families 2,
    u16 positions 4; then for each of the game's textures (Switch A, B, X, Y) the button's letter, 7 zero bytes and
    the 64-bit FNV-1a hash of its 2,048 bytes of BC4 blocks in rows, top down (as the renderer has them once
    unswizzled); then the masks, 2,048 bytes of BC4 blocks each in the same order, xbox south, east, west, north, then
    playstation's; then the 64-bit FNV-1a hash of all the bytes before it"""
    out = bytearray(b"RLKT" + struct.pack("<6H", 1, len(KEYTEX), KEYTEX_SIZE, KEYTEX_SIZE, len(FAMILIES), len(FACE)))
    for button, _ in KEYTEX:
        out += button.encode() + bytes(7) + struct.pack("<Q", fnv1a64(game[button]))
    for fam in FAMILIES:
        for pos in FACE:
            if len(masks[fam][pos]) != KEYTEX_SIZE * KEYTEX_SIZE // 2:
                raise Unfit("a mask of the wrong size")
            out += masks[fam][pos]
    out += struct.pack("<Q", fnv1a64(bytes(out)))
    with open(path, "wb") as f:
        f.write(out)


def make_keytex(romfs, dictionary, out):
    """keytex.bin into out; the path, or raises Unfit"""
    game = game_keytex(pa.read_zs(romfs, BNTX, dictionary))
    target = keytex_target(game)
    masks = {fam: {pos: bc4_encode(keytex_mask(fam, pos, target), KEYTEX_SIZE, KEYTEX_SIZE) for pos in FACE}
             for fam in FAMILIES}
    path = os.path.join(out, "keytex.bin")
    write_keytex(path, game, masks)
    return path, target


def make(romfs, out):
    dictionary = pa.zs_dictionary(romfs)
    wrapped = pa.read_zs(romfs, FONT, dictionary)
    orig = unwrap(wrapped)
    wrap = wrapper(wrapped)
    fonts, infos = {}, {}
    for fam in FAMILIES:
        fonts[fam], infos[fam] = build(orig, fam)
    os.makedirs(out, exist_ok=True)
    for fam in FAMILIES:
        path = os.path.join(out, fam + ".otf")
        with open(path, "wb") as f:
            f.write(fonts[fam])
        print(f"button prompts: {fam} labels, {len(fonts[fam])} bytes, in {path}", file=sys.stderr)
    path = os.path.join(out, "prompts.txt")
    write_sidecar(path, orig, infos, wrap, fonts)
    print(f"button prompts: the slots and labels in {path}", file=sys.stderr)
    # (the HUD cross's letters: without them the fonts still work, and the cross keeps the Switch's letters)
    path = os.path.join(out, "keytex.bin")
    try:
        path, (cx, cy, cap, stroke) = make_keytex(romfs, dictionary, out)
        print(f"button prompts: the HUD cross's letters (cap height {cap:.1f} px, stroke {stroke} px, centre {cx:.1f},"
              f"{cy:.1f}) in {path}", file=sys.stderr)
    except Exception as e:
        if os.path.exists(path):
            os.remove(path)
        print(f"button prompts: the HUD cross's letters not made ({type(e).__name__ + ': ' if not isinstance(e, Unfit) else ''}"
              f"{e}): they stay the Switch's", file=sys.stderr)
    # (relink: the ocarina's note buttons, nx2/ocarina_prompts.py; without them the notes keep the Switch's letters)
    path = os.path.join(out, "ocarina.bin")
    try:
        import ocarina_prompts
        path = ocarina_prompts.make(romfs, out, dictionary=dictionary)
        print(f"button prompts: the ocarina's note buttons ({len(ocarina_prompts.BUTTONS)} x {len(FAMILIES)} families x "
              f"{len(POSITIONS)} positions) in {path}", file=sys.stderr)
    except Exception as e:
        if os.path.exists(path):
            os.remove(path)
        print(f"button prompts: the ocarina's note buttons not made ({type(e).__name__}: {e}): they stay the Switch's",
              file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--romfs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        make(a.romfs, a.out)
    except Exception as e:   # (a font not the one known: the game shows its own prompts)
        print(f"button prompts: not made ({type(e).__name__ + ': ' if not isinstance(e, Unfit) else ''}{e})",
              file=sys.stderr)
        for name in [fam + ".otf" for fam in FAMILIES] + ["prompts.txt", "keytex.bin"]:
            if os.path.exists(os.path.join(a.out, name)):
                os.remove(os.path.join(a.out, name))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
