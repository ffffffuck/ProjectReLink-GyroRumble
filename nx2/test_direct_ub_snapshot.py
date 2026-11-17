"""Compile and run the direct uniform-buffer snapshot regression.

Run with: python3 -I -B nx2/test_direct_ub_snapshot.py [--cxx clang++]
"""

import argparse
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    source = ROOT / "gpu" / "vk" / "tests" / "direct_ub_snapshot_test.cpp"
    with tempfile.TemporaryDirectory(prefix="direct-ub-snapshot-") as temporary:
        executable = Path(temporary) / "direct_ub_snapshot_test"
        subprocess.run(
            [args.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)],
            check=True,
        )
        subprocess.run([str(executable)], check=True)

    memory = (ROOT / "gpu" / "vk" / "vk_memory.cpp").read_text(encoding="utf-8")
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    replay = (ROOT / "gpu" / "vk" / "vk_replay.cpp").read_text(encoding="utf-8")
    internal = (ROOT / "gpu" / "vk" / "vk_internal.h").read_text(encoding="utf-8")
    assert "bool direct = false;" in internal
    assert memory.count("{p->buffer, off, size, 0, true}") == 3
    assert "should_snapshot(snapshot, b.direct)" in draw
    assert "direct_ub_at.find(u.addr, n, generation, capture_view" in draw
    assert "direct_ub_at.keep(u.addr, n, generation, capture_view" in draw
    assert "direct_ub_at.reset();" in draw
    assert "barrier (a burst's next submit start)\");\n        ub_gen++;" in replay
    assert replay.count("if (wrote) ub_gen++;") == 2
    assert "if (host->gpu_write(c0.dst, words, c0.size)) ub_gen++;" in replay
    print("direct_ub_snapshot: ok")


if __name__ == "__main__":
    main()
