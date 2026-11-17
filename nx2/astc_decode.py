"""(relink) A small ASTC decoder for 2D LDR blocks, in pure Python: enough to read the few ASTC textures of the game's
layouts that a build changes (nx2/ocarina_prompts.py: the ocarina's note buttons). Not fast (a 93 x 93 texture in well under
a second) and not for HDR: an HDR endpoint mode or a reserved encoding decodes to magenta, as the specification's error
colour.

decode(blocks, width, height, bw=4, bh=4, srgb=True) -> bytes, RGBA8 rows top down. blocks: 16 bytes a block, in rows of
blocks top down (wheel_assets.linear_blocks). Follows the Khronos Data Format Specification's ASTC chapter (and its reference
decoder, ARM's astc-encoder, for the partition hash)."""

ERROR = (255, 0, 255, 255)

# the ranges of the integer sequence encoding: (bits, trits, quints) for each quantisation level
RANGES = [2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 192, 256]
ISE = {2: (1, 0, 0), 3: (0, 1, 0), 4: (2, 0, 0), 5: (0, 0, 1), 6: (1, 1, 0), 8: (3, 0, 0), 10: (1, 0, 1), 12: (2, 1, 0),
       16: (4, 0, 0), 20: (2, 0, 1), 24: (3, 1, 0), 32: (5, 0, 0), 40: (3, 0, 1), 48: (4, 1, 0), 64: (6, 0, 0),
       80: (4, 0, 1), 96: (5, 1, 0), 128: (7, 0, 0), 160: (5, 0, 1), 192: (6, 1, 0), 256: (8, 0, 0)}


def ise_bits(count, rng):
    b, t, q = ISE[rng]
    return b * count + (8 * count + 4) // 5 * t + (7 * count + 2) // 3 * q


def bits_of(v, start, n):
    return (v >> start) & ((1 << n) - 1)


def trit_digits(T):
    """the five trits a block's eight T bits hold"""
    def bit(x, i):
        return (x >> i) & 1
    if bits_of(T, 2, 3) == 7:
        C = (bits_of(T, 5, 3) << 2) | bits_of(T, 0, 2)
        t4 = t3 = 2
    else:
        C = bits_of(T, 0, 5)
        if bits_of(T, 5, 2) == 3:
            t4, t3 = 2, bit(T, 7)
        else:
            t4, t3 = bit(T, 7), bits_of(T, 5, 2)
    if bits_of(C, 0, 2) == 3:
        t2, t1 = 2, bit(C, 4)
        t0 = (bit(C, 3) << 1) | (bit(C, 2) & (1 - bit(C, 3)))
    elif bits_of(C, 2, 2) == 3:
        t2 = t1 = 2
        t0 = bits_of(C, 0, 2)
    else:
        t2, t1 = bit(C, 4), bits_of(C, 2, 2)
        t0 = (bit(C, 1) << 1) | (bit(C, 0) & (1 - bit(C, 1)))
    return [t0, t1, t2, t3, t4]


def quint_digits(Q):
    """the three quints a block's seven Q bits hold"""
    def bit(x, i):
        return (x >> i) & 1
    if bits_of(Q, 1, 2) == 3 and bits_of(Q, 5, 2) == 0:
        q2 = (bit(Q, 0) << 2) | ((bit(Q, 4) & (1 - bit(Q, 0))) << 1) | (bit(Q, 3) & (1 - bit(Q, 0)))
        return [4, 4, q2]
    if bits_of(Q, 1, 2) == 3:
        q2 = 4
        C = (bits_of(Q, 3, 2) << 3) | ((~bits_of(Q, 5, 2) & 3) << 1) | bit(Q, 0)
    else:
        q2 = bits_of(Q, 5, 2)
        C = bits_of(Q, 0, 5)
    if bits_of(C, 0, 3) == 5:
        q1, q0 = 4, bits_of(C, 3, 2)
    else:
        q1, q0 = bits_of(C, 3, 2), bits_of(C, 0, 3)
    return [q0, q1, q2]


def ise_decode(v, start, count, rng):
    """count values of the range rng from bit start of v: [(digit, low bits)], digit None for a range of bits only"""
    b, t, q = ISE[rng]
    v &= (1 << (start + ise_bits(count, rng))) - 1   # (a last block of trits or quints cut short: its missing bits read 0)
    out = []
    pos = start
    if t:
        while len(out) < count:
            m = []
            T = 0
            # m0, T0 T1, m1, T2 T3, m2, T4, m3, T5 T6, m4, T7
            layout = [(0, 2), (1, 2), (2, 1), (3, 2), (4, 1)]
            tpos = 0
            for i, tb in layout:
                m.append(bits_of(v, pos, b))
                pos += b
                T |= bits_of(v, pos, tb) << tpos
                pos += tb
                tpos += tb
            for d, lo in zip(trit_digits(T), m):
                out.append((d, lo))
        return out[:count]
    if q:
        while len(out) < count:
            m = []
            Q = 0
            layout = [(0, 3), (1, 2), (2, 2)]
            qpos = 0
            for i, qb in layout:
                m.append(bits_of(v, pos, b))
                pos += b
                Q |= bits_of(v, pos, qb) << qpos
                pos += qb
                qpos += qb
            for d, lo in zip(quint_digits(Q), m):
                out.append((d, lo))
        return out[:count]
    for _ in range(count):
        out.append((None, bits_of(v, pos, b)))
        pos += b
    return out


def bstr(pattern, m):
    """a 9- or 7-bit B term of the unquantisation tables: letters a.. are m's bits from the lowest, 0 a zero"""
    out = 0
    for ch in pattern:
        out = (out << 1) | (0 if ch == "0" else (m >> (ord(ch) - ord("a"))) & 1)
    return out


COLOR_TRIT = {1: ("000000000", 204), 2: ("b000b0bb0", 93), 3: ("cb000cbcb", 44), 4: ("dcb000dcb", 22),
              5: ("edcb000ed", 11), 6: ("fedcb000f", 5)}
COLOR_QUINT = {1: ("000000000", 113), 2: ("b0000bb00", 54), 3: ("cb0000cbc", 26), 4: ("dcb0000dc", 13),
               5: ("edcb0000e", 6)}


def unq_color(d, lo, rng):
    b, t, q = ISE[rng]
    if not t and not q:   # (bits only: replicated to eight)
        v, n = lo, b
        out = 0
        filled = 0
        while filled < 8:
            out = (out << n) | v
            filled += n
        return out >> (filled - 8)
    pat, C = (COLOR_TRIT if t else COLOR_QUINT)[b]
    A = 0x1FF if lo & 1 else 0
    B = bstr(pat, lo)
    T = d * C + B
    T ^= A
    return (A & 0x80) | (T >> 2)


WEIGHT_TRIT = {1: ("0000000", 50), 2: ("b000b0b", 23), 3: ("cb000cb", 11)}
WEIGHT_QUINT = {1: ("0000000", 28), 2: ("b0000b0", 13)}


def unq_weight(d, lo, rng):
    b, t, q = ISE[rng]
    if not t and not q:
        v, n = lo, b
        out = 0
        filled = 0
        while filled < 6:
            out = (out << n) | v
            filled += n
        r = out >> (filled - 6)
    elif b == 0:
        r = [0, 32, 63][d] if t else [0, 16, 32, 47, 63][d]
    else:
        pat, C = (WEIGHT_TRIT if t else WEIGHT_QUINT)[b]
        A = 0x7F if lo & 1 else 0
        B = bstr(pat, lo)
        T = d * C + B
        T ^= A
        r = (A & 0x20) | (T >> 2)
    return r + 1 if r > 32 else r


def block_mode(bm):
    """(weights wide, weights high, dual plane, weight range) of a 2D block mode, or None for a reserved one"""
    base = (bm >> 4) & 1
    H = (bm >> 9) & 1
    D = (bm >> 10) & 1
    A = (bm >> 5) & 3
    if bm & 3:
        base |= (bm & 3) << 1
        B = (bm >> 7) & 3
        sel = (bm >> 2) & 3
        if sel == 0:
            x, y = B + 4, A + 2
        elif sel == 1:
            x, y = B + 8, A + 2
        elif sel == 2:
            x, y = A + 2, B + 8
        else:
            B &= 1
            if bm & 0x100:
                x, y = B + 2, A + 2
            else:
                x, y = A + 2, B + 6
    else:
        base |= ((bm >> 2) & 3) << 1
        if (bm >> 2) & 3 == 0:
            return None
        B = (bm >> 9) & 3
        sel = (bm >> 7) & 3
        if sel == 0:
            x, y = 12, A + 2
        elif sel == 1:
            x, y = A + 2, 12
        elif sel == 2:
            x, y = A + 6, B + 6
            D = H = 0
        else:
            s2 = (bm >> 5) & 3
            if s2 == 0:
                x, y = 6, 10
            elif s2 == 1:
                x, y = 10, 6
            else:
                return None
    q = base - 2 + 6 * H
    if q < 0 or q > 11:
        return None
    return x, y, D, RANGES[q]


def hash52(p):
    m = 0xFFFFFFFF
    p ^= p >> 15
    p = (p * 0xEEDE0891) & m
    p ^= p >> 5
    p = (p + (p << 16)) & m
    p ^= p >> 7
    p ^= p >> 3
    p = (p ^ (p << 6)) & m
    p ^= p >> 17
    return p


def select_partition(seed, x, y, z, count, small):
    if small:
        x, y, z = x << 1, y << 1, z << 1
    seed += (count - 1) * 1024
    r = hash52(seed)
    s = [(r >> sh) & 0xF for sh in (0, 4, 8, 12, 16, 20, 24, 28, 18, 22, 26)]
    s.append(((r >> 30) | (r << 2)) & 0xF)
    s = [v * v for v in s]
    if seed & 1:
        sh1 = 4 if seed & 2 else 5
        sh2 = 6 if count == 3 else 5
    else:
        sh1 = 6 if count == 3 else 5
        sh2 = 4 if seed & 2 else 5
    sh3 = sh1 if seed & 0x10 else sh2
    shifts = [sh1, sh2, sh1, sh2, sh1, sh2, sh1, sh2, sh3, sh3, sh3, sh3]
    s = [v >> sh for v, sh in zip(s, shifts)]
    a = (s[0] * x + s[1] * y + s[10] * z + (r >> 14)) & 0x3F
    b = (s[2] * x + s[3] * y + s[11] * z + (r >> 10)) & 0x3F
    c = (s[4] * x + s[5] * y + s[8] * z + (r >> 6)) & 0x3F
    d = (s[6] * x + s[7] * y + s[9] * z + (r >> 2)) & 0x3F
    if count <= 3:
        d = 0
    if count <= 2:
        c = 0
    if count <= 1:
        b = 0
    if a >= b and a >= c and a >= d:
        return 0
    if b >= c and b >= d:
        return 1
    if c >= d:
        return 2
    return 3


def clamp(v):
    return 0 if v < 0 else 255 if v > 255 else v


def bts(a, b):
    """bit_transfer_signed: (signed offset, base)"""
    b = (b >> 1) | (a & 0x80)
    a = (a >> 1) & 0x3F
    if a & 0x20:
        a -= 0x40
    return a, b


def blue_contract(r, g, b, a):
    return ((r + b) >> 1, (g + b) >> 1, b, a)


def endpoints(cem, v):
    """an LDR endpoint mode's two RGBA endpoints from its values, or None (HDR)"""
    if cem == 0:
        return (v[0], v[0], v[0], 255), (v[1], v[1], v[1], 255)
    if cem == 1:
        l0 = (v[0] >> 2) | (v[1] & 0xC0)
        l1 = min(255, l0 + (v[1] & 0x3F))
        return (l0, l0, l0, 255), (l1, l1, l1, 255)
    if cem == 4:
        return (v[0], v[0], v[0], v[2]), (v[1], v[1], v[1], v[3])
    if cem == 5:
        o1, b0 = bts(v[1], v[0])
        o3, b2 = bts(v[3], v[2])
        return (b0, b0, b0, b2), (clamp(b0 + o1),) * 3 + (clamp(b2 + o3),)
    if cem == 6:
        return (v[0] * v[3] >> 8, v[1] * v[3] >> 8, v[2] * v[3] >> 8, 255), (v[0], v[1], v[2], 255)
    if cem in (8, 12):
        a0, a1 = (v[6], v[7]) if cem == 12 else (255, 255)
        if v[1] + v[3] + v[5] >= v[0] + v[2] + v[4]:
            return (v[0], v[2], v[4], a0), (v[1], v[3], v[5], a1)
        return blue_contract(v[1], v[3], v[5], a1), blue_contract(v[0], v[2], v[4], a0)
    if cem in (9, 13):
        o1, b0 = bts(v[1], v[0])
        o3, b2 = bts(v[3], v[2])
        o5, b4 = bts(v[5], v[4])
        if cem == 13:
            o7, b6 = bts(v[7], v[6])
        else:
            o7, b6 = 0, 255
        if o1 + o3 + o5 >= 0:
            e0 = (b0, b2, b4, b6)
            e1 = (b0 + o1, b2 + o3, b4 + o5, b6 + o7)
        else:
            e0 = blue_contract(b0 + o1, b2 + o3, b4 + o5, b6 + o7)
            e1 = blue_contract(b0, b2, b4, b6)
        return tuple(clamp(c) for c in e0), tuple(clamp(c) for c in e1)
    if cem == 10:
        return (v[0] * v[3] >> 8, v[1] * v[3] >> 8, v[2] * v[3] >> 8, v[4]), (v[0], v[1], v[2], v[5])
    return None


def reverse128(v):
    return int(format(v, "0128b")[::-1], 2)


def decode_block(blk, bw, bh, srgb):
    """the bw x bh texels of one block, [(r, g, b, a)] in rows"""
    v = int.from_bytes(blk, "little")
    n = bw * bh
    bm = v & 0x7FF
    if bm & 0x1FF == 0x1FC:   # (void extent: one colour)
        if bm & 0x200:
            return [ERROR] * n   # (HDR)
        c = tuple(bits_of(v, 64 + 16 * i, 16) >> 8 for i in range(4))
        return [c] * n
    mode = block_mode(bm)
    if mode is None:
        return [ERROR] * n
    gx, gy, dual, wrng = mode
    if gx > bw or gy > bh:
        return [ERROR] * n
    parts = bits_of(v, 11, 2) + 1
    nweights = gx * gy * (dual + 1)
    wbits = ise_bits(nweights, wrng)
    if nweights > 64 or wbits < 24 or wbits > 96 or (dual and parts == 4):
        return [ERROR] * n
    below = 128 - wbits
    if parts == 1:
        cems = [bits_of(v, 13, 4)]
        cstart = 17
        seed = 0
    else:
        seed = bits_of(v, 13, 10)
        enc = bits_of(v, 23, 6)
        cstart = 29
        if enc & 3 == 0:
            cems = [(enc >> 2) & 0xF] * parts
        else:
            extra = 3 * parts - 4
            below -= extra
            enc |= bits_of(v, below, extra) << 6
            base = (enc & 3) - 1
            enc >>= 2
            cbits = [(enc >> i) & 1 for i in range(parts)]
            enc >>= parts
            cems = []
            for i in range(parts):
                cems.append(((base + cbits[i]) << 2) | (enc & 3))
                enc >>= 2
    ccs = None
    if dual:
        below -= 2
        ccs = bits_of(v, below, 2)
    nvals = sum(((c >> 2) + 1) * 2 for c in cems)
    if nvals > 18:
        return [ERROR] * n
    avail = below - cstart
    crng = None
    for r in reversed(RANGES):
        if ise_bits(nvals, r) <= avail:
            crng = r
            break
    if crng is None:
        return [ERROR] * n
    vals = [unq_color(d, lo, crng) for d, lo in ise_decode(v, cstart, nvals, crng)]
    eps = []
    k = 0
    for c in cems:
        cnt = ((c >> 2) + 1) * 2
        e = endpoints(c, vals[k:k + cnt])
        k += cnt
        if e is None:
            return [ERROR] * n
        eps.append(e)
    rw = reverse128(v)
    grid = [unq_weight(d, lo, wrng) for d, lo in ise_decode(rw, 0, nweights, wrng)]
    planes = dual + 1

    def weight(s, t, p):
        if gx == bw and gy == bh:
            return grid[(t * gx + s) * planes + p]
        ds = (1024 + bw // 2) // (bw - 1)
        dt = (1024 + bh // 2) // (bh - 1)
        cs, ct = ds * s, dt * t
        gs = (cs * (gx - 1) + 32) >> 6
        gt = (ct * (gy - 1) + 32) >> 6
        js, fs = gs >> 4, gs & 0xF
        jt, ft = gt >> 4, gt & 0xF

        def at(i, j):
            i = min(i, gx - 1)
            j = min(j, gy - 1)
            return grid[(j * gx + i) * planes + p]
        w11 = (fs * ft + 8) >> 4
        w10 = ft - w11
        w01 = fs - w11
        w00 = 16 - fs - ft + w11
        return (at(js, jt) * w00 + at(js + 1, jt) * w01 + at(js, jt + 1) * w10 + at(js + 1, jt + 1) * w11 + 8) >> 4

    out = []
    small = n < 31
    for t in range(bh):
        for s in range(bw):
            part = select_partition(seed, s, t, 0, parts, small) if parts > 1 else 0
            e0, e1 = eps[part]
            px = []
            for ch in range(4):
                w = weight(s, t, 1 if dual and ch == ccs else 0)
                if srgb and ch < 3:
                    c0, c1 = (e0[ch] << 8) | 0x80, (e1[ch] << 8) | 0x80
                else:
                    c0, c1 = e0[ch] * 257, e1[ch] * 257
                c = (c0 * (64 - w) + c1 * w + 32) >> 6
                px.append(c >> 8)
            out.append(tuple(px))
    return out


def decode(blocks, width, height, bw=4, bh=4, srgb=True):
    """RGBA8 rows top down of a width x height texture whose blocks are in rows top down"""
    nbx, nby = (width + bw - 1) // bw, (height + bh - 1) // bh
    out = bytearray(width * height * 4)
    for by in range(nby):
        for bx in range(nbx):
            i = (by * nbx + bx) * 16
            texels = decode_block(blocks[i:i + 16], bw, bh, srgb)
            for t in range(bh):
                y = by * bh + t
                if y >= height:
                    break
                for s in range(bw):
                    x = bx * bw + s
                    if x >= width:
                        break
                    o = (y * width + x) * 4
                    out[o:o + 4] = bytes(texels[t * bw + s])
    return bytes(out)
