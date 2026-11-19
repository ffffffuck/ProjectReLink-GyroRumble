"""Stable chunk boundaries for the lifted C (lift.py --write).

a64-kit's write() closes a chunk once it reaches CHUNK_BYTES of text, so a function that grows (a jump table found,
a fix in one form) moves every boundary after it, and build.py recompiles every chunk from there on: a fix to two
functions cost a full 45-minute rebuild. Here the boundaries are guest addresses, kept in work/image/chunks.txt:
a chunk ends after the last function below the next boundary, so only the chunks holding changed functions differ.
Without the file (or with --rechunk) the boundaries come from the sizes, as before, and are saved.

write()'s test is `size >= chunk_bytes`. chunk_bytes is a Boundaries instance: an int whose reflected comparison
(__le__, which Python tries first for a subclass on the right) decides from the function just emitted.

The functions' C is made by several processes (fork, so Linux and macOS; one process elsewhere): each emits a run of
consecutive functions from the lifter as it stands, and write() takes the texts in its own order, as if it had made
them. The lifter's counters (stats, unhandled, ijump, and any passed in `counters`, such as poll.stats) get each
process's additions. The output is the one-process output, byte for byte (tests/test_parallel_emit.py).
"""
import bisect
import collections
import multiprocessing
import os
import re

import a64_lift
import lifted_c

FILE = "chunks.txt"


class Boundaries(int):
    def __new__(cls, size, starts, funcs):
        self = super().__new__(cls, size)
        self.starts = sorted(starts)
        self.recorded = []   # (without saved boundaries) where the size rule started a chunk
        self.current = None
        self.after = None    # the function emitted after `current`
        order = sorted(funcs)
        self.ends = set()
        for b in self.starts:
            i = bisect.bisect_left(order, b)
            if 0 < i < len(order):
                self.ends.add(order[i - 1])
        self.next_of = {a: b for a, b in zip(order, order[1:])}
        return self

    def __le__(self, size):   # `size >= self`
        e = self.current
        if self.starts:
            return e in self.ends
        if size >= int(self):
            nxt = self.next_of.get(e)
            if nxt is not None:
                self.recorded.append(nxt)
            return True
        return False


def load(img_dir):
    path = os.path.join(img_dir, FILE)
    if not os.path.exists(path):
        return []
    return [int(l, 16) for l in open(path) if l.strip() and not l.startswith("#")]


def save(img_dir, starts):
    with open(os.path.join(img_dir, FILE), "w") as f:
        f.write("# first guest address of each lifted C chunk after the first (nx2/chunks.py)\n")
        f.write("".join("%08x\n" % s for s in sorted(starts)))


def seed_from_gen(gen_dir):
    """The boundaries of an existing size-chunked lift: the first function defined in each code_NNN.c after the
    first"""
    starts = []
    pat = re.compile(r"^void f_([0-9a-f]+)\(Ctx \*__restrict A64_NOESC c\) \{")
    n = 1
    while os.path.exists(os.path.join(gen_dir, "code_%03d.c" % n)):
        for line in open(os.path.join(gen_dir, "code_%03d.c" % n)):
            m = pat.match(line)
            if m:
                starts.append(int(m.group(1), 16))
                break
        n += 1
    return starts


_job = None   # (the forked workers' view: the emitter, the instruction texts and the counters, inherited)


def _counts(counters):
    return [collections.Counter(c) for c in counters]


def _emit_run(es):
    emit, texts, counters = _job
    before = _counts(counters)
    out = [(e,) + tuple(emit(e, texts)) for e in es]
    return out, [collections.Counter({k: v - b.get(k, 0) for k, v in c.items() if v != b.get(k, 0)})
                 for b, c in zip(before, counters)]


def _available_gb():
    """MemAvailable from /proc/meminfo in GB, Windows' available physical memory there (None where there is neither)"""
    if os.name == "nt":
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_uint32), ("load", ctypes.c_uint32)] + \
                           [(f, ctypes.c_uint64) for f in ("total", "avail", "pt", "pa", "vt", "va", "ve")]
            m = MS()
            m.len = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.avail / (1 << 30)
        except (OSError, AttributeError, ValueError):
            pass
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def emit_jobs():
    """Processes for the emit: RELINK_LIFT_JOBS, else the cores this process may use (at most 16; each holds its run's
    texts and the pages of the lift it touches), and at most one per 2 GB of available memory: a Steam Deck (16 GB) ran
    out with 8, and the system's out-of-memory handling stopped the lift ("lift.py stopped (signal 15): BrokenPipeError")
    or one worker (the update then sat at "jump tables" for good). 1 where there is no fork"""
    if os.environ.get("RELINK_LIFT_JOBS"):
        return max(1, int(os.environ["RELINK_LIFT_JOBS"]))
    if "fork" not in multiprocessing.get_all_start_methods():
        # (relink, 17/11) Windows: processes started anew (spawn_emit), each given its share of the functions: one per
        # 1.5 GB of available memory beyond 2 GB, 12 at most. A64_SPAWN_EMIT=0: one process, as before
        if not spawn_ok():
            return 1
        n = max(1, min(12, (os.cpu_count() or 1)))
        gb = _available_gb()
        if gb is not None:
            n = max(1, min(n, int((gb - 2) // 1.5)))
        return n
    try:
        n = len(os.sched_getaffinity(0))
    except AttributeError:
        n = os.cpu_count() or 1
    n = max(1, min(16, n))
    gb = _available_gb()
    if gb is not None:
        n = max(1, min(n, int(gb // 2)))
    return n


def spawn_ok():
    """(relink) Windows, where no process can be forked: the emit spread over processes started anew (spawn_emit)"""
    return os.name == "nt" and os.environ.get("A64_SPAWN_EMIT", "1") not in ("", "0")


# ---- (relink, 17/11) The emit on Windows, in processes started anew. On Linux the workers are forked and see the lifter as it
# is; here nothing can be forked, and the lift ran on one core: 294523 functions, two to three minutes of a five-minute lift
# (a mod's code patch or an update lifts again). Each worker gets, once, the lifter without its functions' bodies and its
# image (every other attribute is small: the entries, the PLT, the jump tables, the names, the sites' C), then runs of
# functions with their bodies; it makes the instruction texts of its runs itself (the same per word, a64_lift.instruction_texts)
# and sends back each function's text and calls, the counters' additions and the FPX shapes it met. The output is the one-
# process output, byte for byte (checked against a one-process lift of the whole game, 17/11).
_spawn_lifter = None
_SPAWN_SKIP = ("funcs", "img", "emit_function", "texts_lazy", "fpx_extra")


def _spawn_init(state, polls):
    global _spawn_lifter
    if polls:
        import poll
        poll.install()
    L = object.__new__(a64_lift.Lifter)
    L.__dict__.update(state)
    L.funcs = {}
    _spawn_lifter = L


def _spawn_run(items, polls):
    L = _spawn_lifter
    L.funcs = dict(items)
    counters = [L.stats, L.unhandled, L.ijump]
    if polls:
        import poll
        counters.append(poll.stats)
    before = _counts(counters)
    fpx = set()
    texts = a64_lift.instruction_texts(L.funcs, fpx)
    out = [(e,) + tuple(L.emit_function(e, texts)) for e, _ in items]
    deltas = [collections.Counter({k: v - b.get(k, 0) for k, v in c.items() if v != b.get(k, 0)})
              for b, c in zip(before, counters)]
    L.funcs = {}
    return out, deltas, fpx


def spawn_emit(L, order, counters, jobs, run):
    """(e, text, refs) for every function of order, in order, from `jobs` processes started anew. Raises before yielding
    anything if the workers cannot be started (the caller then emits here)"""
    from concurrent.futures import ProcessPoolExecutor
    polls = len(counters) > 3
    state = {k: v for k, v in L.__dict__.items() if k not in _SPAWN_SKIP}
    for k in ("stats", "unhandled", "ijump"):
        state[k] = type(getattr(L, k))()
    runs = [order[i:i + run] for i in range(0, len(order), run)]
    ctx = multiprocessing.get_context("spawn")
    fpx_all = set()
    L.fpx_extra = fpx_all   # (filled as the runs come back: the generator is closed after its last function, not run to its end)
    with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx, initializer=_spawn_init, initargs=(state, polls)) as pool:
        pending = {}
        nxt = 0

        def feed():
            nonlocal nxt
            while nxt < len(runs) and len(pending) < 2 * jobs:   # (a few runs ahead: their bodies are not all copied at once)
                es = runs[nxt]
                pending[nxt] = pool.submit(_spawn_run, [(e, L.funcs[e]) for e in es], polls)
                nxt += 1
        feed()
        for i in range(len(runs)):
            out, deltas, fpx = pending.pop(i).result()
            feed()
            for c, d in zip(counters, deltas):
                for k, v in d.items():
                    c[k] = c.get(k, 0) + v
            fpx_all |= fpx
            yield from out


def parallel_emit(emit, order, texts, counters, jobs, run=2000):
    """(e, text, refs) for every function of order, in order, emitted by `jobs` forked processes. A worker that dies
    (stopped for memory) ends the pool with an error instead of a wait forever (multiprocessing.Pool's imap never
    returns then); the runs not done yet are emitted here, one process."""
    global _job
    L = getattr(emit, "__self__", None)
    if jobs > 1 and len(order) >= 2 * run and "fork" not in multiprocessing.get_all_start_methods() and L is not None:
        done = 0
        try:
            for got in spawn_emit(L, order, counters, jobs, run):
                yield got
                done += 1
            return
        except Exception as ex:   # (a worker that could not start or stopped: the rest here, one process)
            print("emit: the worker processes stopped (%s: %s): the last %d of %d functions are emitted in this process"
                  % (type(ex).__name__, ex, len(order) - done, len(order)), flush=True)
            if texts is None:
                texts = a64_lift.instruction_texts(L.funcs, L.__dict__.setdefault("fpx_extra", set()))
                L.fpx_extra = L.fpx_extra
            for e in order[done:]:
                yield (e,) + tuple(emit(e, texts))
            return
    if texts is None and L is not None:   # (texts_lazy, with too few functions for the workers)
        texts = a64_lift.instruction_texts(L.funcs, L.__dict__.setdefault("fpx_extra", set()))
    if jobs <= 1 or len(order) < 2 * run:
        for e in order:
            yield (e,) + tuple(emit(e, texts))
        return
    import gc
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool
    runs = [order[i:i + run] for i in range(0, len(order), run)]
    done = 0
    _job = (emit, texts, counters)
    gc.collect()
    gc.freeze()   # (the lift's objects out of the collector's sight: its passes would write to every page the children share)
    try:
        ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as pool:
            for out, deltas in pool.map(_emit_run, runs):
                for c, d in zip(counters, deltas):
                    for k, v in d.items():
                        c[k] = c.get(k, 0) + v
                done += 1
                yield from out
    except BrokenProcessPool:
        print("emit: a worker process stopped (out of memory?): the last %d of %d runs are emitted in this process"
              % (len(runs) - done, len(runs)), flush=True)
    finally:
        _job = None
        gc.unfreeze()
    for es in runs[done:]:
        for e in es:
            yield (e,) + tuple(emit(e, texts))


def write(L, gen_dir, img_dir, rechunk=False, counters=(), jobs=None):
    """L.write(gen_dir) with stable boundaries; returns its files. counters: dicts the emit adds to besides the
    lifter's own (poll.stats); jobs: emit processes (emit_jobs())"""
    starts = [] if rechunk else load(img_dir)
    if not starts and not rechunk:
        starts = seed_from_gen(gen_dir)
    b = Boundaries(a64_lift.CHUNK_BYTES, starts, L.funcs)
    base_emit = L.emit_function
    jobs = emit_jobs() if jobs is None else jobs
    # (relink: on Windows the workers make their own instruction texts: none made here, unless they cannot start)
    L.texts_lazy = jobs > 1 and "fork" not in multiprocessing.get_all_start_methods()
    if L.texts_lazy:
        print("emit: %d processes" % jobs, flush=True)
    made = None

    def emit_function(e, texts):
        nonlocal made
        b.current = e
        if made is None:   # (the first call: write() has its texts now, and asks for the functions in sorted order)
            made = parallel_emit(base_emit, sorted(L.funcs), texts,
                                       [L.stats, L.unhandled, L.ijump] + list(counters), jobs)
        got = next(made, None)
        if got is None or got[0] != e:
            raise SystemExit("chunks: write() asked for f_%08x, the emit made %s" % (e, "f_%08x" % got[0] if got else "nothing"))
        return got[1], got[2]
    L.emit_function = emit_function
    try:
        files = L.write(gen_dir, chunk_bytes=b)
    finally:
        del L.emit_function
        if made is not None:
            made.close()   # (the processes go now, not when the generator is collected)
    # (the numbered chunks only: files has code_hot.c too, and counting it skipped the first stale chunk, linked twice)
    n = sum(1 for f in files if re.fullmatch(r"code_\d{3}\.c", os.path.basename(f)))
    while os.path.exists(os.path.join(gen_dir, "code_%03d.c" % n)):   # (fewer chunks than the last lift)
        os.remove(os.path.join(gen_dir, "code_%03d.c" % n))
        n += 1
    # (an earlier lift's hot pieces go now, with or without a new code_hot.c, before the patches and the page-mark groups read
    # code_*.c: split_hot.py makes this lift's after it. A lift with no code_hot.c (no hot list, A64_HOT_CHUNK unset, or none of the
    # listed functions in it): split_hot.py had renamed the old one code_hot.c.whole and left code_hot_*.c, which build.py compiled
    # again, the earlier lift's hot functions, each defined in a chunk too, linked twice. A lift with one: the old pieces, patched
    # already, and the new whole file each held the output-size places, and nx2/oot_patches.py's count of them failed the lift)
    for f in os.listdir(gen_dir):
        if f == "code_hot.c.whole" or HOT_PIECE.fullmatch(f):
            os.remove(os.path.join(gen_dir, f))
    files += split_big(gen_dir, files)
    save(img_dir, starts or b.recorded)
    print("chunks: %d, boundaries %s" % (len(files), "kept" if starts else "from sizes (saved)"))
    return files


HEAD = lifted_c.HEAD_NOTED
PART = re.compile(r"code_(\d{3})_(\d+)\.c")
HOT_PIECE = re.compile(r"code_hot_\d+\.c")   # (nx2/split_hot.py's pieces of code_hot.c)
# (a function's compile time grows with the square of its size: LLVM's passes over one function of tens of thousands of blocks. Fitted to
# a cold build of the 48 chunks at -O1 on a 24-thread desktop: seconds ~ 1.5 x MB + 4.4 x MB^2 per function, so a function of QUAD bytes
# costs twice its size. A 24 MB chunk of small functions took ~45 s; one of 20 MB holding a few 2 MB functions, 350 s)
QUAD = 350000
COSTS = "chunk_costs.txt"   # (in the gen folder: each file's estimated compile cost, for build.py's order)


def fn_cost(size):
    """A function's estimated compile cost, in bytes of plain code"""
    return size + size * size // QUAD


def text_cost(text):
    """The estimated compile cost of a C file of lifted functions"""
    marks = [m.start() for m in HEAD.finditer(text)] + [len(text)]
    return marks[0] + sum(fn_cost(b - a) for a, b in zip(marks, marks[1:]))


def write_costs(gen_dir, costs):
    """Adds or replaces files' costs in chunk_costs.txt; costs: {file name: cost}"""
    path = os.path.join(gen_dir, COSTS)
    known = {}
    if os.path.exists(path):
        for line in open(path):
            name, _, n = line.partition(" ")
            if n.strip().isdigit() and os.path.exists(os.path.join(gen_dir, name)):
                known[name] = int(n)
    known.update(costs)
    with open(path, "w", newline="\n") as f:
        f.write("".join("%s %d\n" % kv for kv in sorted(known.items())))


# (relink, 17/11) What one compiled file holds: about 8 MB of cost, not a whole 24 MB chunk. A compiler's peak memory follows its
# file's size (~59 MB per MB of C: one 24 MB chunk 1411 MB, the same chunk in three parts 434 to 527 MB each) and the CPU time does
# not (46 s whole, 47 s in three parts, objects within 0.3 % of each other's size): a 16 GB PC ran 4 to 5 compilers at once on 12
# threads, the memory as the limit. The hot pieces (nx2/split_hot.py) are not cut: their callees inline within them
PART_BYTES = 8 << 20


def split_big(gen_dir, files, budget=None):
    """Chunks whose estimated compile time (fn_cost) is well past a part's (PART_BYTES), cut at function starts into parts of about
    equal cost: code_NNN.c, code_NNN_1.c ... A chunk of the giant functions took 350 s, alone at the end of the build, where a chunk
    of ordinary functions takes 45; the boundaries kept in chunks.txt also let chunks grow (44 MB beside 25). The first part keeps the
    name, so seed_from_gen and the boundaries are as before. Writes chunk_costs.txt for every chunk; returns the new parts' paths"""
    budget = budget or PART_BYTES
    made, costs = [], {}
    for path in files:
        m = re.fullmatch(r"code_(\d{3})\.c", os.path.basename(path))
        if not m:
            continue
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        marks = [h.start() for h in HEAD.finditer(text)]
        ends = marks[1:] + [len(text)]
        cost = [fn_cost(b - a) for a, b in zip(marks, ends)]
        total = sum(cost)
        if total <= budget * 3 // 2 or len(marks) < 2:
            costs[os.path.basename(path)] = marks[0] + total if marks else len(text)
            continue
        parts = -(-total // budget)
        cuts, run, k = [0], 0, 1   # (indexes into marks where a part starts)
        for i, c in enumerate(cost):
            if i and run >= total * k // parts:
                cuts.append(i)
                k = run * parts // total + 1   # (past a giant function: the next share, not a sliver after it)
            run += c
        bounds = [marks[i] for i in cuts] + [len(text)]
        preamble = text[:marks[0]]
        for j in range(len(cuts)):
            out = path if j == 0 else os.path.join(gen_dir, "code_%s_%d.c" % (m.group(1), j))
            piece = text[:bounds[1]] if j == 0 else preamble + text[bounds[j]:bounds[j + 1]]
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                f.write(piece)
            costs[os.path.basename(out)] = len(preamble) + sum(cost[cuts[j]:cuts[j + 1] if j + 1 < len(cuts) else len(cost)])
            if j:
                made.append(out)
        print("chunks: %s (%d MB, cost %d MB) in %d parts" % (os.path.basename(path), len(text) >> 20, total >> 20, len(cuts)))
    for f in os.listdir(gen_dir):   # (parts of an earlier lift that this one did not make)
        if PART.fullmatch(f) and os.path.join(gen_dir, f) not in made:
            os.remove(os.path.join(gen_dir, f))
    write_costs(gen_dir, costs)
    return made
