"""The game's time step above 60 fps for Ocarina of Time 1.1.1: C added after one instruction of the game (a64_lift.py
Lifter.after). Issues 16 and 67: "with Unlocked, everything in the game is faster".

Each frame the game's main loop (main+0x6e6c98..) reads the graphics module's nominal frame rate (an int at +0x2c8), makes
ratio = 60 / rate (30 / rate in its half-rate mode) and hands it to the engine's step (f_83b6e300: seconds = ratio / 60).
The host's real-time step (host/nn_vi.c realtime_step) writes the rate the frames really come at into that int at each
present, which is how the game follows the clock under 60 fps. Above 60 it did not: the game's own frame rate controller
(f_83b6ef.., the +0x58 of the controller at the module's +0x270) writes 60 / <its whole number of display intervals> there
every frame while the limiter is raised, never more than 60, after the host's write and before the main loop's read. Measured
with HOS_STEP_LOG=1 at the title: the step's ratio never under 1.000, the host's rate read back changed at 494 of 514
presents, and the game's clock advancing 8.85 s in 5.02 s of wall clock at 102 fps (x1.77), 6.2 s in 5.0 s at 75 fps (x1.24).

Right after the main loop turns the rate into a float (scvtf s0, s0), the rate the host measured (hos_step_fps, set only
while the frame rate is unlocked and the real-time step runs; 0 otherwise) is put in its place, so the step is the frame's
real length whatever the controller wrote. Nothing else reads the changed register: the controller, the limiter and the
half-rate flag are left as the game has them. At 60 fps and under, hos_step_fps is 0 and the game's code runs as before.

The site's original word is checked: another game version leaves it out (and says so).
"""

MAIN = 0x80020000   # (main's base in this image: nx2/eden_image.py)
SITE = 0x6e6ca8
SCVTF_S0_S0 = 0x5e21d800
TEXT = ("{ extern volatile int hos_step_fps; const int _f = hos_step_fps; if (_f > 60 && _f <= 240) { "
        "const float _v = (float)_f; uint32_t _u; memcpy(&_u, &_v, 4); a64_vset64(c->vreg[0], _u); } }"
        "   /* (nx2/oot_step_rate.py: the step above 60 fps) */")


def install(lifter, img):
    """The site into lifter.after; left out when its word differs (another game version)"""
    pc = MAIN + SITE
    try:
        have = img.word(pc)
    except Exception:
        have = None
    if have != SCVTF_S0_S0:
        print("step rate above 60 fps: main+%#x holds %s, not %08x: left out" % (
            SITE, "?" if have is None else "%08x" % have, SCVTF_S0_S0))
        return 0
    if pc in lifter.after:
        lifter.after[pc] += "\n" + TEXT
    else:
        lifter.after[pc] = TEXT
    print("step rate above 60 fps: 1 of 1 site")
    return 1
