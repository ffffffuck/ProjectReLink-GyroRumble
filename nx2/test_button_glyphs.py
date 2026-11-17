"""(relink) Test of nx2/button_glyphs.py, run by hand (not part of a player's build), with a Python that has Pillow:

    python -I nx2/test_button_glyphs.py --romfs <romfs> --sheet <folder> [--site <site-packages that hold zstandard>]

It makes the fonts into a temporary folder, then checks them with its own readers (sfnt, cmap, CFF, Type 2 outlines; none
shared with the script): the length; every code point but the twelve maps to a glyph with the original's charstring and
advance; the slots in prompts.txt hold what the cmap gives, the defaults being ReLink's default bindings (written out below
by hand); a patched slot changes that code point only; the checksums; the labels' outlines do not touch or cross each other
(so a knocked-out label does not fill back in with any fill rule). Then it renders with FreeType (Pillow) every code point
and every label at 64 and 20 px, and writes one contact sheet of the labels per family into --sheet.

keytex.bin (the HUD cross's letters), with its own BNTX reader, GOB unswizzle (the renderer's texel copy's formula, not the
script's) and BC4 decoder: the four KeyTexFont textures are found, BC4 64 x 64 one level, and their hashes are in the
file; each of ReLink's eight masks decodes to something, differs from the others, and has an ink box and coverage close to
the game's letters' (the tolerances are in check_keytex); a BNTX missing or with textures of another shape leaves the
fonts made, keytex.bin not, and says so in one line. A contact sheet of ReLink's eight masks (none of the game's) goes
into --sheet as keytex_masks.png.
"""
import argparse
import contextlib
import hashlib
import io
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# ReLink's default bindings, by hand: Switch A is the east button, B south, X north, Y west, L l1, R r1, ZL l2, ZR r2,
# Plus start, Minus back, LS-push l3, RS-push r3. The code points are GuideKeyIcon.msbt's.
EXPECTED = {0xE040: "east", 0xE041: "south", 0xE042: "north", 0xE043: "west", 0xE044: "l1", 0xE045: "r1",
            0xE046: "l2", 0xE047: "r2", 0xE04B: "start", 0xE04C: "back", 0xE056: "l3", 0xE057: "r3"}
POSITIONS = ["south", "east", "west", "north", "l1", "r1", "l2", "r2", "start", "back", "l3", "r3"]
FAMILIES = ["xbox", "playstation"]

failures = []


def check(ok, what):
    if not ok:
        failures.append(what)
        print("FAIL", what)


# --- readers ---------------------------------------------------------------------------------------------------------

def decode_bfotf(b):
    key = bytes(x ^ y for x, y in zip(b[8:12], b"OTTO"))
    n = int.from_bytes(bytes(x ^ y for x, y in zip(b[4:8], key)), "big")
    return bytes(c ^ key[i % 4] for i, c in enumerate(b[8:8 + n]))


def directory(f):
    n = struct.unpack(">H", f[4:6])[0]
    return {f[12 + 16 * i:16 + 16 * i].decode("latin-1"): struct.unpack(">III", f[16 + 16 * i:28 + 16 * i]) + (12 + 16 * i,)
            for i in range(n)}


def u32sum(b):
    b += b"\0" * ((4 - len(b) % 4) % 4)
    s = 0
    for i in range(0, len(b), 4):
        s = (s + int.from_bytes(b[i:i + 4], "big")) & 0xFFFFFFFF
    return s


def checksums_ok(f):
    d = directory(f)
    for tag, (cs, off, ln, _) in d.items():
        b = bytearray(f[off:off + ln])
        if tag == "head":
            b[8:12] = b"\0\0\0\0"
        if u32sum(bytes(b)) != cs:
            return f"{tag} checksum"
    if u32sum(f) != 0xB1B0AFBA:
        return "file checksum (head.checkSumAdjustment)"
    return None


def cmaps(f):
    """{(platform, encoding): {code point: glyph}} by walking each subtable code point by code point"""
    off = directory(f)["cmap"][1]
    n = struct.unpack(">H", f[off + 2:off + 4])[0]
    out = {}
    for i in range(n):
        pid, eid, so = struct.unpack(">HHI", f[off + 4 + 8 * i:off + 12 + 8 * i])
        p = off + so
        fmt = struct.unpack(">H", f[p:p + 2])[0]
        m = {}
        if fmt == 4:
            segs = struct.unpack(">H", f[p + 6:p + 8])[0] // 2
            ends_at = p + 14
            starts_at = ends_at + 2 * segs + 2
            deltas_at = starts_at + 2 * segs
            ranges_at = deltas_at + 2 * segs
            for s in range(segs):
                end = struct.unpack(">H", f[ends_at + 2 * s:ends_at + 2 * s + 2])[0]
                start = struct.unpack(">H", f[starts_at + 2 * s:starts_at + 2 * s + 2])[0]
                delta = struct.unpack(">h", f[deltas_at + 2 * s:deltas_at + 2 * s + 2])[0]
                ro = struct.unpack(">H", f[ranges_at + 2 * s:ranges_at + 2 * s + 2])[0]
                for c in range(start, end + 1):
                    if c == 0xFFFF:
                        continue
                    if ro:
                        a = ranges_at + 2 * s + ro + 2 * (c - start)
                        g = struct.unpack(">H", f[a:a + 2])[0]
                        g = (g + delta) % 65536 if g else 0
                    else:
                        g = (c + delta) % 65536
                    if g:
                        m[c] = g
        elif fmt == 6:
            first, count = struct.unpack(">HH", f[p + 6:p + 10])
            for k in range(count):
                g = struct.unpack(">H", f[p + 10 + 2 * k:p + 12 + 2 * k])[0]
                if g:
                    m[first + k] = g
        else:
            raise SystemExit(f"test: cmap format {fmt} not read by the test")
        out[(pid, eid)] = m
    return out


def index_at(b, o):
    count = struct.unpack(">H", b[o:o + 2])[0]
    if not count:
        return [], o + 2
    osz = b[o + 2]
    offs = [int.from_bytes(b[o + 3 + osz * i:o + 3 + osz * i + osz], "big") for i in range(count + 1)]
    data = o + 2 + 1 + osz * (count + 1)
    return [b[data + offs[i] - 1:data + offs[i + 1] - 1] for i in range(count)], data + offs[count] - 1


def dict_ops(b):
    ops, stack, k = {}, [], 0
    while k < len(b):
        v = b[k]
        if v < 22:
            op = v if v != 12 else 1200 + b[k + 1]
            k += 1 if v != 12 else 2
            ops[op] = stack
            stack = []
        elif v == 28:
            stack.append(int.from_bytes(b[k + 1:k + 3], "big", signed=True))
            k += 3
        elif v == 29:
            stack.append(int.from_bytes(b[k + 1:k + 5], "big", signed=True))
            k += 5
        elif v == 30:
            k += 1
            while b[k] >> 4 != 15 and b[k] & 15 != 15:
                k += 1
            k += 1
            stack.append(None)
        elif v <= 246:
            stack.append(v - 139)
            k += 1
        elif v <= 250:
            stack.append((v - 247) * 256 + b[k + 1] + 108)
            k += 2
        else:
            stack.append(-(v - 251) * 256 - b[k + 1] - 108)
            k += 2
    return ops


def cff_glyphs(f):
    """the charstrings, and (defaultWidthX, nominalWidthX)"""
    off, ln = directory(f)["CFF "][1:3]
    c = f[off:off + ln]
    _, o = index_at(c, c[2])
    tops, o = index_at(c, o)
    top = dict_ops(tops[0])
    cs, _ = index_at(c, top[17][0])
    size, poff = top[18]
    priv = dict_ops(c[poff:poff + size])
    return cs, (priv.get(20, [0])[0], priv.get(21, [0])[0])


def advances(f):
    d = directory(f)
    hhea, hmtx, maxp = d["hhea"][1], d["hmtx"][1], d["maxp"][1]
    nh = struct.unpack(">H", f[hhea + 34:hhea + 36])[0]
    ng = struct.unpack(">H", f[maxp + 4:maxp + 6])[0]
    return [struct.unpack(">H", f[hmtx + 4 * min(g, nh - 1):hmtx + 4 * min(g, nh - 1) + 2])[0] for g in range(ng)]


def outline(cs):
    """the contours (polygons, curves flattened) and the width operand of a charstring made of rmoveto, rlineto,
    hlineto, vlineto, rrcurveto, hvcurveto, vhcurveto and endchar"""
    stack, contours, x, y, width, k = [], [], 0, 0, None, 0
    first = True

    def curve(d1, d2, d3):
        nonlocal x, y
        p0 = (x, y)
        c1 = (x + d1[0], y + d1[1])
        c2 = (c1[0] + d2[0], c1[1] + d2[1])
        x, y = c2[0] + d3[0], c2[1] + d3[1]
        for j in range(1, 9):
            t = j / 8
            u = 1 - t
            contours[-1].append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * x,
                                 u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * y))
    while k < len(cs):
        v = cs[k]
        if v == 28:
            stack.append(int.from_bytes(cs[k + 1:k + 3], "big", signed=True))
            k += 3
            continue
        if v >= 32:
            if v <= 246:
                stack.append(v - 139)
                k += 1
            elif v <= 250:
                stack.append((v - 247) * 256 + cs[k + 1] + 108)
                k += 2
            elif v <= 254:
                stack.append(-(v - 251) * 256 - cs[k + 1] - 108)
                k += 2
            else:
                raise ValueError("fixed-point operand")
            continue
        k += 1
        if first and ((v == 21 and len(stack) == 3) or (v == 14 and len(stack) == 1)):
            width = stack.pop(0)
        first = False
        if v == 21:
            x, y = x + stack[0], y + stack[1]
            contours.append([(x, y)])
        elif v == 5:
            for i in range(0, len(stack), 2):
                x, y = x + stack[i], y + stack[i + 1]
                contours[-1].append((x, y))
        elif v in (6, 7):             # alternating horizontal and vertical lines, hlineto starting horizontal
            horizontal = v == 6
            for d in stack:
                x, y = (x + d, y) if horizontal else (x, y + d)
                contours[-1].append((x, y))
                horizontal = not horizontal
        elif v == 8:
            for i in range(0, len(stack), 6):
                curve(stack[i:i + 2], stack[i + 2:i + 4], stack[i + 4:i + 6])
        elif v in (30, 31):           # alternating curves, hvcurveto leaving horizontally and arriving vertically
            if len(stack) % 4:
                raise ValueError("a last curve with a fifth operand")
            horizontal = v == 31
            for i in range(0, len(stack), 4):
                a, b, c, d = stack[i:i + 4]
                if horizontal:
                    curve((a, 0), (b, c), (0, d))
                else:
                    curve((0, a), (b, c), (d, 0))
                horizontal = not horizontal
        elif v == 14:
            break
        else:
            raise ValueError(f"operator {v}")
        stack = []
    return contours, width


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = 0 if dx == dy == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5


def crosses(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def winding(p, poly):
    w = 0
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i - 1], poly[i]
        if y0 <= p[1] < y1 and (x1 - x0) * (p[1] - y0) - (p[0] - x0) * (y1 - y0) > 0:
            w += 1
        elif y1 <= p[1] < y0 and (x1 - x0) * (p[1] - y0) - (p[0] - x0) * (y1 - y0) < 0:
            w -= 1
    return w


def outline_problems(contours, gap=6):
    """contours that come within gap units of each other or cross themselves; points where the non-zero and even-odd
    fills differ"""
    out = []
    boxes = [(min(p[0] for p in c), min(p[1] for p in c), max(p[0] for p in c), max(p[1] for p in c)) for c in contours]
    for i, a in enumerate(contours):
        for j in range(i + 1, len(contours)):
            b = contours[j]
            A, B = boxes[i], boxes[j]
            if A[0] > B[2] + gap or B[0] > A[2] + gap or A[1] > B[3] + gap or B[1] > A[3] + gap:
                continue
            near = min(min(seg_dist(p, b[k - 1], b[k]) for k in range(len(b))) for p in a)
            near = min(near, min(min(seg_dist(p, a[k - 1], a[k]) for k in range(len(a))) for p in b))
            if near < gap:
                out.append(f"contours {i} and {j} {near:.1f} units apart")
        for k in range(len(a)):
            for m in range(k + 2, len(a)):
                if (k, m) != (0, len(a) - 1) and crosses(a[k - 1], a[k], a[m - 1], a[m]):
                    out.append(f"contour {i} crosses itself")
                    break
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    for gy in range(40):
        for gx in range(40):
            p = (x0 + (x1 - x0) * (gx + 0.5) / 40, y0 + (y1 - y0) * (gy + 0.31) / 40)
            ws = [winding(p, c) for c in contours]
            if (sum(ws) != 0) != (sum(abs(w) for w in ws) % 2 == 1):
                out.append(f"fill rules differ at {p[0]:.0f},{p[1]:.0f}")
                return out
    return out


# --- keytex.bin ------------------------------------------------------------------------------------------------------

def bntx_find(b, name):
    """(format, width, height, levels, layers, block height in GOBs, data offset) of a texture of a Switch 2 BNTX"""
    count = int.from_bytes(b[0x24:0x28], "little")
    table = int.from_bytes(b[0x28:0x30], "little")
    for i in range(count):
        brti = int.from_bytes(b[table + 8 * i:table + 8 * i + 8], "little")
        name_at = int.from_bytes(b[brti + 0x60:brti + 0x68], "little")
        n = int.from_bytes(b[name_at:name_at + 2], "little")
        if b[name_at + 2:name_at + 2 + n] != name.encode():
            continue
        u32 = lambda o: int.from_bytes(b[brti + o:brti + o + 4], "little")  # noqa: E731
        levels_at = int.from_bytes(b[brti + 0x70:brti + 0x78], "little")
        return (u32(0x1C), u32(0x24), u32(0x28), int.from_bytes(b[brti + 0x16:brti + 0x18], "little"), u32(0x30),
                1 << u32(0x34), int.from_bytes(b[levels_at:levels_at + 8], "little"))
    return None


def unswizzle_blocks(b, data, bw, bh, bpb, gobs):
    """block-linear blocks into rows: each block's place by the GOB formula of gpu/vk/vk_texture.cpp's texel copy (the
    Switch 2 GOB, 512 bytes, blocks of gobs GOBs high)"""
    th = gobs.bit_length() - 1
    gobs_x = (bw * bpb + 63) // 64
    block = 512 << th
    out = bytearray()
    for y in range(bh):
        for xb in range(0, bw * bpb, 16):
            gob = ((xb & 63) >> 5) << 8 | ((y & 7) >> 2) << 7 | ((xb & 31) >> 4) << 6 | ((y >> 1) & 1) << 5 | (y & 1) << 4
            off = (y >> (3 + th)) * gobs_x * block + (xb >> 6) * block + (((y >> 3) & ((1 << th) - 1)) << 9) + gob
            out += b[data + off:data + off + 16]
    return bytes(out)


def bc4(blocks, w):
    """BC4 blocks in rows into w x w values (interpolation in floating point, rounded)"""
    px = [0] * (w * w)
    per = w // 4
    for k in range(len(blocks) // 8):
        a, c = blocks[8 * k], blocks[8 * k + 1]
        if a > c:
            pal = [a, c] + [round(((7 - i) * a + i * c) / 7) for i in range(1, 7)]
        else:
            pal = [a, c] + [round(((5 - i) * a + i * c) / 5) for i in range(1, 5)] + [0, 255]
        bits = int.from_bytes(blocks[8 * k + 2:8 * k + 8], "little")
        for i in range(16):
            px[(4 * (k // per) + i // 4) * w + 4 * (k % per) + i % 4] = pal[(bits >> (3 * i)) & 7]
    return px


def measure(px, w):
    """the ink box (pixels at 128 or more: x0, y0, x1, y1 as pixel edges), the coverage (sum / 255) and the median run of
    pixels at 128 or more along the rows"""
    on = [(i % w, i // w) for i, v in enumerate(px) if v >= 128]
    if not on:
        return None, 0.0, 0
    runs = []
    for y in range(w):
        r = 0
        for x in range(w + 1):
            if x < w and px[y * w + x] >= 128:
                r += 1
            elif r:
                runs.append(r)
                r = 0
    runs.sort()
    return ((min(p[0] for p in on), min(p[1] for p in on), max(p[0] for p in on) + 1, max(p[1] for p in on) + 1),
            sum(px) / 255, runs[len(runs) // 2])


def fnv64(b):
    h = 0xCBF29CE484222325
    for c in b:
        h = ((h ^ c) * 0x100000001B3) % (1 << 64)
    return h


def check_keytex(path, bntx, sheet_dir, Image, ImageDraw, ImageFont):
    """keytex.bin against the game's textures. Tolerances: each mask's ink box centre within 2 px of the game's letters'
    mean centre in x and y, its height within 4 px of their mean height and its width within 6 px of their mean width,
    its coverage within 40 % of their mean coverage; each Xbox letter against the game's same letter: each side of the
    box within 3 px, coverage within 25 %, median run within 1 px"""
    with open(path, "rb") as fh:
        k = fh.read()
    check(len(k) == 16 + 4 * 16 + 8 * 2048 + 8, f"keytex.bin is {len(k)} bytes")
    check(k[:4] == b"RLKT" and struct.unpack("<6H", k[4:16]) == (1, 4, 64, 64, 2, 4), "keytex.bin's header")
    check(struct.unpack("<Q", k[-8:])[0] == fnv64(k[:-8]), "keytex.bin's last hash")
    game = {}
    for i, sw in enumerate("ABXY"):
        t = bntx_find(bntx, f"KeyTexFont{sw}_00")
        check(t is not None, f"KeyTexFont{sw}_00 found")
        if t is None:
            return
        fmt, w, h, levels, layers, gobs, data = t
        print(f"(KeyTexFont{sw}_00: format {fmt:#x}, {w} x {h}, {levels} level, {layers} layer, blocks {gobs} GOBs high)")
        check((fmt, w, h, levels, layers) == (0x1D01, 64, 64, 1, 1), f"KeyTexFont{sw}_00 is BC4 64 x 64, one level")
        blocks = unswizzle_blocks(bntx, data, 16, 16, 8, gobs)
        rec = k[16 + 16 * i:32 + 16 * i]
        check(rec[:1] == sw.encode() and rec[1:8] == bytes(7), f"keytex.bin's record {i} is Switch {sw}'s")
        check(struct.unpack("<Q", rec[8:])[0] == fnv64(blocks), f"keytex.bin holds KeyTexFont{sw}_00's hash")
        game[sw] = measure(bc4(blocks, 64), 64)
        print(f"(game {sw}: ink box {game[sw][0]}, coverage {game[sw][1]:.0f} px, median run {game[sw][2]} px)")
    hashes = {k[24 + 16 * i:32 + 16 * i] for i in range(4)}
    check(len(hashes) == 4, "keytex.bin's four hashes differ (the renderer tells the textures apart by them)")
    boxes = [g[0] for g in game.values()]
    mean_cx = sum(b[0] + b[2] for b in boxes) / 8
    mean_cy = sum(b[1] + b[3] for b in boxes) / 8
    mean_w = sum(b[2] - b[0] for b in boxes) / 4
    mean_h = sum(b[3] - b[1] for b in boxes) / 4
    mean_cov = sum(g[1] for g in game.values()) / 4
    masks, seen = {}, {}
    for f, fam in enumerate(FAMILIES):
        for p, pos in enumerate(["south", "east", "west", "north"]):
            at = 80 + (4 * f + p) * 2048
            blocks = k[at:at + 2048]
            px = bc4(blocks, 64)
            masks[(fam, pos)] = px
            box, cov, run = measure(px, 64)
            print(f"({fam} {pos}: ink box {box}, coverage {cov:.0f} px, median run {run} px)")
            check(box is not None and cov > 50, f"{fam} {pos}: the mask is not empty")
            if box is None:
                continue
            check(blocks not in seen, f"{fam} {pos} and {seen.get(blocks)} are the same mask")
            seen[blocks] = (fam, pos)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            check(abs(cx - mean_cx) <= 2 and abs(cy - mean_cy) <= 2,
                  f"{fam} {pos}: centre {cx},{cy} not within 2 px of the game's {mean_cx},{mean_cy}")
            check(abs((box[3] - box[1]) - mean_h) <= 4 and abs((box[2] - box[0]) - mean_w) <= 6,
                  f"{fam} {pos}: {box[2] - box[0]} x {box[3] - box[1]} against the game's {mean_w} x {mean_h}")
            check(abs(cov - mean_cov) <= 0.4 * mean_cov, f"{fam} {pos}: coverage {cov:.0f} against the game's {mean_cov:.0f}")
            if fam == "xbox":
                sw = {"south": "A", "east": "B", "west": "X", "north": "Y"}[pos]
                gbox, gcov, grun = game[sw]
                check(all(abs(a - b) <= 3 for a, b in zip(box, gbox)) and abs(cov - gcov) <= 0.25 * gcov and
                      abs(run - grun) <= 1, f"xbox {pos} ({sw}) against the game's {sw}: {box} {cov:.0f} {run}, "
                      f"{gbox} {gcov:.0f} {grun}")
    # the contact sheet: ReLink's masks only, each at 64 x 64 and enlarged 4 times, white on dark as the layout tints them
    cell = 4 * 64 + 12
    sheet = Image.new("L", (cell * 4, 2 * (cell + 64 + 24)), 40)
    dr = ImageDraw.Draw(sheet)
    small = ImageFont.load_default()
    for (fam, pos), px in masks.items():
        f, p = FAMILIES.index(fam), ["south", "east", "west", "north"].index(pos)
        im = Image.new("L", (64, 64))
        im.putdata(px)
        x, y = p * cell + 6, f * (cell + 64 + 24) + 6
        sheet.paste(im.resize((256, 256), Image.NEAREST), (x, y))
        sheet.paste(im, (x, y + 262))
        dr.text((x + 72, y + 290), f"{fam} {pos}", font=small, fill=255)
    os.makedirs(sheet_dir, exist_ok=True)
    out = os.path.join(sheet_dir, "keytex_masks.png")
    sheet.save(out)
    print(f"keytex: contact sheet {out}")


# --- the test --------------------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--romfs", required=True)
    ap.add_argument("--sheet", required=True, help="folder for the contact sheets")
    ap.add_argument("--site", help="a site-packages folder that holds zstandard, for a Python without it")
    a = ap.parse_args()
    if a.site:
        sys.path.insert(0, a.site)
    sys.path.insert(0, HERE)
    import button_glyphs  # noqa: E402
    import wheel_assets  # noqa: E402
    from PIL import Image, ImageDraw, ImageFont

    # a RomFS without the font, and labels too big for the font: one line, exit code 1, no files left
    def failing_run(romfs, why):
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = button_glyphs.main(["--romfs", romfs or d, "--out", os.path.join(d, "p")])
            left = os.listdir(os.path.join(d, "p")) if os.path.isdir(os.path.join(d, "p")) else []
            lines = err.getvalue().splitlines()
            print(f"({why}: exit {rc}: {' / '.join(lines)})")
            check(rc == 1 and len(lines) == 1 and not left, f"{why}: exit 1, one line, no files")
            return lines[0] if lines else ""
    failing_run(None, "a RomFS without the font")
    real_label = button_glyphs.label

    def huge_label(family, position, advances):
        p = button_glyphs.Pen()
        for k in range(200):
            button_glyphs.rect(p, 10 * k % 600, 3 * k, 10 * k % 600 + 5, 3 * k + 2)
        return p.contours, 640
    button_glyphs.label = huge_label
    try:
        check("more than the game's" in failing_run(a.romfs, "labels that do not fit"), "the does-not-fit message")
    finally:
        button_glyphs.label = real_label

    tmp = tempfile.mkdtemp(prefix="relink_prompts_")
    check(button_glyphs.main(["--romfs", a.romfs, "--out", tmp]) == 0, "the script exits 0")
    wrapped = wheel_assets.read_zs(a.romfs, "Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs",
                                   wheel_assets.zs_dictionary(a.romfs))
    orig = decode_bfotf(wrapped)
    side = {"slot": {}, "label": {}, "switch": {}, "font": {}}
    with open(os.path.join(tmp, "prompts.txt"), "rb") as fh:
        raw = fh.read()
    check(b"\r" not in raw, "prompts.txt has LF line ends")
    for line in raw.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        w = line.split()
        if w[0] == "slot":
            side["slot"][int(w[1], 16)] = int(w[2])
        elif w[0] == "label":
            side["label"][(w[1], w[2])] = int(w[3])
        elif w[0] == "switch":
            side["switch"][int(w[1], 16)] = int(w[2])
        elif w[0] == "font":
            side["font"][w[1]] = int(w[2], 16)
        else:
            side[w[0]] = [int(v) if v.isdigit() else v for v in w[1:]]
    check(side["size"] == [len(orig)], "size is the original's length")
    check(side["original_sha256"] == [hashlib.sha256(orig).hexdigest()], "original_sha256")
    check(sorted(side["slot"]) == sorted(EXPECTED), "twelve slots")
    check(len(side["label"]) == 24, "24 labels")
    # the wrapper record: the game's own file made again from it and the original font, byte for byte
    w = side.get("wrapper", [])
    check(len(w) == 4, "a wrapper record of four fields")
    if len(w) == 4:
        key = bytes.fromhex(w[1])
        after = b"" if w[3] == "-" else bytes.fromhex(w[3])
        again = (bytes.fromhex(w[0]) + bytes(x ^ y for x, y in zip(len(orig).to_bytes(4, "big"), key)) +
                 bytes(c ^ key[i & 3] for i, c in enumerate(orig)) + after)
        print(f"(wrapper: magic {w[0]}, key {w[1]}, {w[2]} bytes, {len(after)} after the font)")
        check(w[2] == len(wrapped) and again == wrapped, "the wrapper record makes the game's file again")

    o_maps, o_cs, o_adv = cmaps(orig), cff_glyphs(orig)[0], advances(orig)
    for fam in FAMILIES:
        with open(os.path.join(tmp, fam + ".otf"), "rb") as fh:
            f = fh.read()
        print(f"{fam}.otf: {len(f)} bytes, {len(f) - len(f.rstrip(bytes(1)))} of them zero padding at the end")
        check(len(f) == len(orig), f"{fam}: length")
        h = 0xCBF29CE484222325        # (FNV-1a, 64 bits, written out here again)
        for c in f:
            h = ((h ^ c) * 0x100000001B3) % (1 << 64)
        check(side["font"].get(fam) == h, f"{fam}: the font record's hash")
        check(checksums_ok(f) is None, f"{fam}: {checksums_ok(f)}")
        d = directory(f)
        check(side["cmap"] == [d["cmap"][1], d["cmap"][2], d["cmap"][3] + 4], f"{fam}: the cmap record")
        check(side["head"] == [d["head"][1] + 8], f"{fam}: the head record")
        od = directory(orig)
        check(sorted(d) == sorted(od), f"{fam}: the same tables")
        changed = {"head": (8, 12), "hhea": (34, 36), "maxp": (4, 6)}   # (only checkSumAdjustment, numberOfHMetrics,
        for tag in od:                                                  # numGlyphs; CFF, cmap and hmtx are checked below)
            if tag in ("CFF ", "cmap", "hmtx"):
                continue
            new = bytearray(f[d[tag][1]:d[tag][1] + d[tag][2]])
            old = bytearray(orig[od[tag][1]:od[tag][1] + od[tag][2]])
            if tag in changed:
                lo, hi = changed[tag]
                new[lo:hi] = old[lo:hi] = b"\0" * (hi - lo)
            check(new == old, f"{fam}: {tag} unchanged")
        maps, (cs, (dflt, nominal)), adv = cmaps(f), cff_glyphs(f), advances(f)
        check(sorted(maps) == sorted(o_maps), f"{fam}: cmap subtables")
        labels = {pos: side["label"][(fam, pos)] for pos in POSITIONS}
        others = set()
        for key, om in o_maps.items():
            m = maps[key]
            check(sorted(m) == sorted(om), f"{fam}: cmap {key} code points")
            for cp, g in om.items():
                if cp in EXPECTED:
                    continue
                others.add(m[cp])
                check(cs[m[cp]] == o_cs[g] and adv[m[cp]] == o_adv[g], f"{fam}: U+{cp:04X} glyph or advance")
            for cp, pos in EXPECTED.items():
                if cp not in om:      # (the Macintosh subtable stops at U+00FF)
                    check(key != (3, 1), f"{fam}: U+{cp:04X} in the Windows Unicode cmap")
                    continue
                check(m.get(cp) == labels[pos], f"{fam}: {key} U+{cp:04X} is the {pos} label")
                check(struct.unpack(">H", f[side["slot"][cp]:side["slot"][cp] + 2])[0] == m.get(cp),
                      f"{fam}: slot U+{cp:04X} holds the cmap's glyph")
                check(adv[m[cp]] == o_adv[om[cp]], f"{fam}: U+{cp:04X} keeps its advance")
                check(side["switch"][cp] == om[cp] and cs[om[cp]] == o_cs[om[cp]], f"{fam}: U+{cp:04X} Switch glyph")
        check(len(set(labels.values())) == 12 and not set(labels.values()) & (others | set(side["switch"].values())),
              f"{fam}: twelve glyphs of their own")
        want_adv = {"south": 640, "east": 640, "west": 640, "north": 640, "l1": 720, "r1": 720, "l2": 720, "r2": 720,
                    "start": 600, "back": 600, "l3": o_adv[o_maps[(3, 1)][0xE056]], "r3": o_adv[o_maps[(3, 1)][0xE057]]}
        for pos, g in labels.items():
            check(adv[g] == want_adv[pos], f"{fam}: {pos} advance {adv[g]}")
            contours, width = outline(cs[g])
            check((width is None) == (adv[g] == dflt) and (width is None or width + nominal == adv[g]),
                  f"{fam}: {pos} charstring width")
            for problem in outline_problems(contours):
                check(False, f"{fam}: {pos}: {problem}")
        # patch every slot with every other label: only that code point changes; the host's checksum fix holds
        for cp, at in side["slot"].items():
            for pos, g in labels.items():
                if g == maps[(3, 1)][cp]:
                    continue
                p = bytearray(f)
                p[at:at + 2] = struct.pack(">H", g)
                coff, clen, cfield = side["cmap"]
                p[cfield:cfield + 4] = struct.pack(">I", u32sum(bytes(p[coff:coff + clen])))
                h, = side["head"]
                p[h:h + 4] = b"\0\0\0\0"
                p[h:h + 4] = struct.pack(">I", (0xB1B0AFBA - u32sum(bytes(p))) & 0xFFFFFFFF)
                pm = cmaps(bytes(p))
                for key in maps:
                    diff = {c for c in maps[key] if pm[key].get(c) != maps[key][c]}
                    check(sorted(pm[key]) == sorted(maps[key]), f"{fam}: patching U+{cp:04X} keeps the code points")
                    if cp in maps[key]:
                        check(diff == {cp} and pm[key][cp] == g, f"{fam}: patching U+{cp:04X} with {pos}")
                    else:
                        check(not diff, f"{fam}: patching U+{cp:04X} leaves {key} alone")
                check(checksums_ok(bytes(p)) is None, f"{fam}: checksums after patching U+{cp:04X}")

        # FreeType: every other code point renders as in the original; the twelve and every label render, all different
        def render(data, cp, size):
            font = ImageFont.truetype(io.BytesIO(data), size)
            im = Image.new("L", (size * 2, size * 2), 0)
            ImageDraw.Draw(im).text((size // 2, size * 3 // 2), chr(cp), font=font, fill=255, anchor="ls")
            return im
        for cp in o_maps[(3, 1)]:
            if cp not in EXPECTED and cp >= 0x20:
                check(render(f, cp, 64).tobytes() == render(orig, cp, 64).tobytes(), f"{fam}: U+{cp:04X} renders as before")
        for cp in EXPECTED:
            for size in (64, 20):
                check(render(f, cp, size).getbbox() is not None, f"{fam}: U+{cp:04X} at {size} px")
        shots = {}
        for pos, g in labels.items():
            p = bytearray(f)
            p[side["slot"][0xE040]:side["slot"][0xE040] + 2] = struct.pack(">H", g)
            shots[pos] = {size: render(bytes(p), 0xE040, size) for size in (64, 20)}
            for size in (64, 20):
                check(shots[pos][size].getbbox() is not None, f"{fam}: {pos} at {size} px is empty")
        for size in (64, 20):
            seen = {}
            for pos in POSITIONS:
                k = shots[pos][size].tobytes()
                check(k not in seen, f"{fam}: {pos} and {seen.get(k)} look the same at {size} px")
                seen[k] = pos

        # the contact sheet: each label at 64 px, at 20 px, and the 20 px one enlarged 4 times; only ReLink's glyphs
        cell, zoom = 132, 4
        sheet = Image.new("L", (cell * 12, 330), 255)
        dr = ImageDraw.Draw(sheet)
        small = ImageFont.load_default()
        for i, pos in enumerate(POSITIONS):
            x = i * cell
            for img, y in ((shots[pos][64], 6), (shots[pos][20], 140)):
                sheet.paste(0, (x + (cell - img.width) // 2, y), img)
            big = shots[pos][20].resize((40 * zoom, 40 * zoom), Image.NEAREST).crop((20, 20, 140, 140))
            sheet.paste(0, (x + 6, 184), big)
            dr.text((x + 6, 314), pos, font=small, fill=0)
        os.makedirs(a.sheet, exist_ok=True)
        path = os.path.join(a.sheet, f"prompts_{fam}.png")
        sheet.save(path)
        print(f"{fam}: contact sheet {path}")

    # keytex.bin: the HUD cross's letters
    bntx_rel = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.bntx.zs"
    kpath = os.path.join(tmp, "keytex.bin")
    check(os.path.exists(kpath), "keytex.bin made")
    if os.path.exists(kpath):
        bntx = wheel_assets.read_zs(a.romfs, bntx_rel, wheel_assets.zs_dictionary(a.romfs))
        check_keytex(kpath, bntx, a.sheet, Image, ImageDraw, ImageFont)
        del bntx

    # a BNTX missing, textures of another shape: the fonts made, keytex.bin not, one line about it, exit 0
    def keytex_failing(why, attr, value):
        old = getattr(button_glyphs, attr)
        setattr(button_glyphs, attr, value)
        try:
            with tempfile.TemporaryDirectory() as d:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = button_glyphs.main(["--romfs", a.romfs, "--out", d])
                left = sorted(os.listdir(d))
                lines = [ln for ln in err.getvalue().splitlines() if "cross" in ln]
                print(f"({why}: exit {rc}: {' / '.join(lines)}; files {left})")
                check(rc == 0 and len(lines) == 1 and "not made" in lines[0] and
                      left == ["playstation.otf", "prompts.txt", "xbox.otf"], f"{why}: fonts made, keytex.bin not, one line")
        finally:
            setattr(button_glyphs, attr, old)
    keytex_failing("no BNTX", "BNTX", "UI/Layout2Archive/Missing.bntx.zs")
    keytex_failing("textures of another shape", "KEYTEX", (("A", "DoCommandCrossBase_A_00"), ("B", "KeyTexFontB_00"),
                                                           ("X", "KeyTexFontX_00"), ("Y", "KeyTexFontY_00")))

    for name in os.listdir(tmp):
        os.remove(os.path.join(tmp, name))
    os.rmdir(tmp)
    print("test_button_glyphs:", "FAILED, %d problems" % len(failures) if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
