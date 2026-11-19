"""build.rss_mb counts what a compiler's child holds (Windows): llvm-mingw's clang.exe is a small wrapper that runs the compiler
(clang-<n>.exe) as its child, and the memory gate saw 4 MB for a compiler holding 1.4 GB. No compiler needed: a small parent
starts a child that holds 300 MB, as the wrapper does.

    python -I nx2/test_mem_gate.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CHILD = "b = bytearray(300 << 20)\nfor i in range(0, len(b), 4096): b[i] = 1\nimport time; time.sleep(60)\n"
PARENT = "import subprocess, sys\nsubprocess.run([sys.executable, '-c', %r])\n" % CHILD


def main():
    if os.name != "nt":
        print("skipped: Windows only (Linux reads /proc's children already, tree_rss)")
        return
    import build
    p = subprocess.Popen([sys.executable, "-c", PARENT])
    try:
        seen = 0
        for _ in range(100):   # (up to 20 s for the child to start and touch its pages)
            seen = build.rss_mb(p)
            if seen >= 300:
                break
            time.sleep(0.2)
        own = build.ProcessMemory()
        own.cb = build.ctypes.sizeof(own)
        build.ctypes.WinDLL("psapi").GetProcessMemoryInfo(build.ctypes.c_void_p(int(p._handle)), build.ctypes.byref(own), own.cb)
        assert seen >= 300, "rss_mb saw %d MB for a parent whose child holds 300 MB" % seen
        assert (own.ws >> 20) < 100, "the parent itself holds %d MB: not the wrapper's case" % (own.ws >> 20)
        print("ok: mem gate (the parent alone %d MB, with its child %d MB)" % (own.ws >> 20, seen))
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)


if __name__ == "__main__":
    main()
