"""(relink) The HUD cross letters' replacement, run-time side (gpu/vk/prompt_tex.h), checked without the game or Vulkan:
builds gpu/vk/tests/prompt_tex_test.cpp with the renderer's unswizzle (gpu/third_party/suyu decoders.cpp) and runs it over
a folder this script fills from the player's RomFS: keytex.bin made by nx2/button_glyphs.py, and the four KeyTexFont
textures' storage as the game has it (block linear), which the program unswizzles as the renderer does.
    python -I nx2/test_prompt_tex.py --romfs <romfs> [--site <site-packages that hold zstandard>] [--clang <clang++>]
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
SRC = os.path.join(ROOT, "gpu", "vk", "tests", "prompt_tex_test.cpp")
SUYU = os.path.join(ROOT, "gpu", "third_party", "suyu")
CLANG = os.path.join(os.environ.get("RELINK_MINGW", r"F:\Switch2build\llvm-mingw"), "bin", "clang++.exe")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--romfs", required=True)
    ap.add_argument("--site", help="a site-packages folder that holds zstandard, for a Python without it")
    ap.add_argument("--clang", default=CLANG)
    a = ap.parse_args(argv)
    if a.site:
        sys.path.insert(0, a.site)
    sys.path.insert(0, HERE)
    import button_glyphs  # noqa: E402
    import wheel_assets as pa  # noqa: E402

    work = tempfile.mkdtemp(prefix="relink_prompt_tex_")
    try:
        dictionary = pa.zs_dictionary(a.romfs)
        path, target = button_glyphs.make_keytex(a.romfs, dictionary, work)
        print(f"keytex.bin: {os.path.getsize(path)} bytes (the game's letters: cap height {target[2]:.1f} px, stroke "
              f"{target[3]} px)", flush=True)
        bntx = pa.read_zs(a.romfs, button_glyphs.BNTX, dictionary)
        tex = pa.bntx_textures(bntx)
        lines = []
        for button, name in button_glyphs.KEYTEX:
            _, fmt, w, h, gobs, data = tex[name]
            bw, bh = (w + 3) // 4, (h + 3) // 4
            gobs_x = (bw * 8 + 63) // 64
            size = (bh + 8 * gobs - 1) // (8 * gobs) * gobs_x * 512 * gobs   # (the level's block-linear span)
            with open(os.path.join(work, f"storage_{button}.bin"), "wb") as f:
                f.write(bntx[data:data + size])
            lines.append(f"{button} {gobs.bit_length() - 1} storage_{button}.bin")
        del bntx
        with open(os.path.join(work, "textures.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        exe = os.path.join(work, "prompt_tex_test.exe")
        cmd = [a.clang, "-std=c++20", "-O1", "-static", "-Wall", "-Wextra", "-Wno-unused-parameter", "-I" + SUYU, SRC,
               os.path.join(SUYU, "video_core", "textures", "decoders.cpp"), "-o", exe]
        r = subprocess.run(cmd, capture_output=True, text=True)
        warn = [ln for ln in (r.stdout + r.stderr).splitlines() if "prompt_tex" in ln and ("warning" in ln or "error" in ln)]
        for ln in warn:
            print(ln)
        if r.returncode:
            print(r.stdout + r.stderr)
            print("test_prompt_tex: the test program did not build")
            return 1
        if warn:
            print("test_prompt_tex: warnings in prompt_tex.h or its test")
            return 1
        rc = subprocess.run([exe, work]).returncode
        print("test_prompt_tex: " + ("all checks passed" if rc == 0 else "FAILED"))
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
