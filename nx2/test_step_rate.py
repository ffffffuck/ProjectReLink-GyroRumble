"""nx2/oot_step_rate.py: the site is taken only when the game's own instruction is there, and its text joins what another
change already put after the same instruction.

    python -I nx2/test_step_rate.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oot_step_rate  # noqa: E402


class Image:
    def __init__(self, words):
        self.words = words

    def word(self, pc):
        return self.words[pc]   # (KeyError outside the image, as a real image raises)


class Lifter:
    def __init__(self):
        self.after = {}


def quiet(fn, *a):
    with redirect_stdout(io.StringIO()) as out:
        r = fn(*a)
    return r, out.getvalue()


def main():
    pc = oot_step_rate.MAIN + oot_step_rate.SITE
    assert pc == 0x80706ca8, hex(pc)

    lifter = Lifter()
    r, said = quiet(oot_step_rate.install, lifter, Image({pc: oot_step_rate.SCVTF_S0_S0}))
    assert r == 1 and "1 of 1 site" in said, said
    text = lifter.after[pc]
    # (the host's rate only above 60 and within what the host writes; the register the game's fdiv divides by)
    assert "hos_step_fps" in text and "_f > 60 && _f <= 240" in text and "c->vreg[0]" in text, text

    lifter = Lifter()
    lifter.after[pc] = "/* earlier */"
    quiet(oot_step_rate.install, lifter, Image({pc: oot_step_rate.SCVTF_S0_S0}))
    assert lifter.after[pc].startswith("/* earlier */\n") and "hos_step_fps" in lifter.after[pc]

    # another game version: another word there, or no such address
    lifter = Lifter()
    r, said = quiet(oot_step_rate.install, lifter, Image({pc: 0xd503201f}))
    assert r == 0 and not lifter.after and "left out" in said, said
    r, said = quiet(oot_step_rate.install, lifter, Image({}))
    assert r == 0 and not lifter.after and "left out" in said, said
    print("ok: oot_step_rate (4 cases)")


if __name__ == "__main__":
    main()
