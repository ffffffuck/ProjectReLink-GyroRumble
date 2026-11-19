"""(relink) The page-mark grouping over worker processes (a64_lift.group_marks): the files come out as the one-process grouping
makes them, also when a worker dies halfway (its file is grouped in the lift's own process, once).

    python nx2/test_group_marks.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.join(HERE, "..", "third_party", "a64-kit", "lift")
sys.path[:0] = [HERE, LIFT]

FUNC = """void f_%08x(Ctx *__restrict A64_NOESC c) {
    recomp_store64(c,(c->x[1]+8ULL),c->x[2]);
    recomp_store64(c,(c->x[1]+16ULL),c->x[3]);
    recomp_store32(c,(c->x[4]+0ULL),c->x[5]);
}
"""


def make(dirpath, files=6, funcs=3000):
    paths = []
    for n in range(files):
        p = os.path.join(dirpath, "code_%03d.c" % n)
        with open(p, "w", newline="") as f:
            f.write("".join(FUNC % (0x1000 + (n * funcs + i) * 16) for i in range(funcs)))
        paths.append(p)
    return paths


def run(dirpath, env):
    code = ("import sys, glob, os; sys.path[:0] = %r; import a64_lift; "
            "print(a64_lift.group_marks(sorted(glob.glob(os.path.join(%r, 'code_*.c')))))" % ([HERE, LIFT], dirpath))
    e = dict(os.environ, **env)
    r = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def contents(dirpath):
    return {n: open(os.path.join(dirpath, n), "rb").read() for n in sorted(os.listdir(dirpath))}


def main():
    base = tempfile.mkdtemp()
    try:
        ref, par, die = (os.path.join(base, d) for d in ("one", "workers", "dead"))
        for d in (ref, par, die):
            os.mkdir(d)
            make(d)
        run(ref, {"A64_MARK_JOBS": "1"})
        out = run(par, {"A64_MARK_JOBS": "4"})
        assert out.strip().splitlines()[-1] == "6", out   # (every file has groups: the test sees the grouping)
        assert b"A64_MARK((c->x[1]" in contents(ref)["code_000.c"]
        assert contents(par) == contents(ref), "the workers' grouping differs from one process's"
        print("workers: same files as one process", out.strip().splitlines()[-1])
        out = run(die, {"A64_MARK_JOBS": "4", "A64_MARK_TEST_DIE": "code_002.c"})
        assert "worker processes stopped" in out, out
        assert contents(die) == contents(ref), "after a dead worker the files differ from one process's"
        print("a dead worker: the lift goes on, same files as one process")
        print(out.strip().splitlines()[1] if len(out.strip().splitlines()) > 1 else out.strip())
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
