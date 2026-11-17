"""Field of view for Ocarina of Time 1.1.1: C added after one instruction of the game (a64_lift.py Lifter.after).

The camera update (main+0x4c52d0..) copies the active camera's lens into the perspective projection the renderer and the culling
read: near (+0x134) and far (+0x138) planes, then the vertical angle in radians (ldr s0, [x19, #0x13c]), which it stores at
+0xa0 and from whose half it takes the sine, cosine and tangent (+0xa4, +0xa8, +0xac: the matrix update and the frustum culling
use those). Right after that load the angle is multiplied by hos_fov_scale (host/nn_vi.c: the Insert menu's Field of view in
percent, HOS_FOV=<percent> over it), so the projection, its matrix and the culling all get the wider (or narrower) angle
together, and the camera's own angle is left as the game set it: nothing compounds from frame to frame. At 1.0 (100%, the
default) nothing changes. The angle stays under 2.9 radians (166 degrees).

The site's original word is checked: another game version leaves it out (and says so).
"""

MAIN = 0x80020000   # (main's base in this image: nx2/eden_image.py)
SITE = 0x4c52f8
LDR_S0_X19_13C = 0xbd413e60
TEXT = ("{ extern volatile float hos_fov_scale; const float _k = hos_fov_scale; if (_k > 0.0f && (_k < 0.999f || _k > 1.001f)) { "
        "float _a; memcpy(&_a, c->vreg[0], 4); if (_a > 0.0f && _a < 3.1f) { _a *= _k; if (_a > 2.9f) _a = 2.9f; "
        "uint32_t _u; memcpy(&_u, &_a, 4); a64_vset64(c->vreg[0], _u); } } }   /* (nx2/oot_fov.py: field of view) */")


def install(lifter, img):
    """The site into lifter.after; left out when its word differs (another game version)"""
    pc = MAIN + SITE
    try:
        have = img.word(pc)
    except Exception:
        have = None
    if have != LDR_S0_X19_13C:
        print("field of view: main+%#x holds %s, not %08x: left out" % (SITE, "?" if have is None else "%08x" % have,
                                                                        LDR_S0_X19_13C))
        return 0
    if pc in lifter.after:
        lifter.after[pc] += "\n" + TEXT
    else:
        lifter.after[pc] = TEXT
    print("field of view: 1 of 1 site")
    return 1
