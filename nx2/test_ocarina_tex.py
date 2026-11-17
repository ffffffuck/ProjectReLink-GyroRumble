"""(relink) The ocarina note buttons' replacement (nx2/ocarina_prompts.py, gpu/vk/ocarina_tex.h), checked without the game
or Vulkan: makes ocarina.bin from the player's RomFS, builds gpu/vk/tests/ocarina_tex_test.cpp with the renderer's unswizzle
and CPU ASTC decoder (gpu/third_party/suyu decoders.cpp, astc.cpp) and runs it over the six OcarinaButton textures' storage
as the game has it (block linear); then compares each version as the renderer's decoder gives it with nx2/astc_decode.py's.
    python -I nx2/test_ocarina_tex.py --romfs <romfs> [--clang <clang++>] [--sheet <folder>]
--sheet: a PNG of each family's versions as the renderer decodes them (rows Switch A, B, X, Y, L, R; columns the positions).
The storage written stays in a temporary folder, deleted at the end: nothing of the game's is kept.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "gpu", "vk", "tests", "ocarina_tex_test.cpp")
SUYU = os.path.join(ROOT, "gpu", "third_party", "suyu")
COMPAT = os.path.join(ROOT, "gpu", "compat")
CLANG = os.path.join(os.environ.get("RELINK_MINGW", os.path.join(ROOT, "llvm-mingw")), "bin", "clang++.exe")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--romfs", required=True)
    ap.add_argument("--clang", default=CLANG)
    ap.add_argument("--sheet")
    a = ap.parse_args(argv)
    sys.path.insert(0, HERE)
    import astc_decode as ad  # noqa: E402
    import ocarina_prompts as op  # noqa: E402
    import wheel_assets as pa  # noqa: E402
    work = tempfile.mkdtemp(prefix="relink_ocarina_tex_")
    try:
        dictionary = pa.zs_dictionary(a.romfs)
        path = op.make(a.romfs, work, dictionary=dictionary)
        print(f"ocarina.bin: {os.path.getsize(path)} bytes", flush=True)
        bntx = pa.read_zs(a.romfs, op.BNTX, dictionary)
        tex = pa.bntx_textures(bntx)
        lines = []
        for button, name in zip(op.BUTTONS, op.TEXTURES):
            _, fmt, w, h, gobs, data = tex[name]
            bw, bh = (w + 3) // 4, (h + 3) // 4
            gobs_x = (bw * 16 + 63) // 64
            size = (bh + 8 * gobs - 1) // (8 * gobs) * gobs_x * 512 * gobs   # (the level's block-linear span)
            with open(os.path.join(work, f"storage_{button}.bin"), "wb") as f:
                f.write(bntx[data:data + size])
            lines.append(f"{button} {gobs.bit_length() - 1} storage_{button}.bin")
        del bntx
        with open(os.path.join(work, "textures.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        exe = os.path.join(work, "ocarina_tex_test.exe")
        cmd = [a.clang, "-std=c++20", "-O1", "-static", "-Wall", "-Wextra", "-Wno-unused-parameter", "-I" + SUYU,
               "-I" + COMPAT, SRC, os.path.join(SUYU, "video_core", "textures", "decoders.cpp"),
               os.path.join(SUYU, "video_core", "textures", "astc.cpp"), "-o", exe]
        r = subprocess.run(cmd, capture_output=True, text=True)
        warn = [ln for ln in (r.stdout + r.stderr).splitlines() if "ocarina_tex" in ln and ("warning" in ln or "error" in ln)]
        for ln in warn:
            print(ln)
        if r.returncode:
            print(r.stdout + r.stderr)
            print("test_ocarina_tex: the test program did not build")
            return 1
        if warn:
            print("test_ocarina_tex: warnings in ocarina_tex.h or its test")
            return 1
        rc = subprocess.run([exe, work]).returncode
        # (the renderer's decode of each version against this script's own)
        with open(path, "rb") as f:
            body = f.read()
        head = 16 + 16 * len(op.BUTTONS)
        n = op.BLOCKS * 16
        worst = 0
        for i, button in enumerate(op.BUTTONS):
            for fam in (1, 2):
                for pos in range(len(op.POSITIONS)):
                    k = (i * 2 + fam - 1) * len(op.POSITIONS) + pos
                    mine = ad.decode(body[head + k * n:head + (k + 1) * n], op.SIZE, op.SIZE)
                    with open(os.path.join(work, f"decoded_{button}_{fam}_{pos}.rgba"), "rb") as f:
                        theirs = f.read()
                    if len(theirs) != len(mine):
                        print(f"FAIL: decoded_{button}_{fam}_{pos} is {len(theirs)} bytes")
                        rc = 1
                        continue
                    worst = max(worst, max(abs(x - y) for x, y in zip(mine, theirs)))
        print(f"the renderer's ASTC decode against astc_decode.py's: largest difference {worst} of 255")
        if worst > 2:
            print("FAIL: the two decodes differ")
            rc = 1
        if a.sheet:
            from PIL import Image
            os.makedirs(a.sheet, exist_ok=True)
            for fam, fname in ((1, "xbox"), (2, "playstation")):
                sheet = Image.new("RGBA", (len(op.POSITIONS) * 100, len(op.BUTTONS) * 100), (90, 90, 90, 255))
                for i, button in enumerate(op.BUTTONS):
                    for pos in range(len(op.POSITIONS)):
                        with open(os.path.join(work, f"decoded_{button}_{fam}_{pos}.rgba"), "rb") as f:
                            im = Image.frombytes("RGBA", (op.SIZE, op.SIZE), f.read())
                        sheet.alpha_composite(im, (pos * 100 + 3, i * 100 + 3))
                out = os.path.join(a.sheet, f"ocarina_renderer_{fname}.png")
                sheet.save(out)
                print(f"sheet: {out}")
        print("test_ocarina_tex: " + ("all checks passed" if rc == 0 else "FAILED"))
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
