"""nup.resource_names / Nup.listing: the names in a title's RomFS System/Resource, read from the package's table alone (what
make_game.py checks before the RomFS is unpacked beside the build). No game bytes.

    python -I nx2/test_nup_listing.py
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nup  # noqa: E402

NONE = 0xffffffff


def package(dirs, files, titles):
    """dirs: [(name, [children])], a child ("d", i) or ("f", i); files: [name]; titles: [(root, exefs, romfs, version)]"""
    nt, nd, nf = len(titles), len(dirs), len(files)
    strings = bytearray()

    def text(s):
        at = len(strings)
        strings.extend(s.encode())
        return at, len(s.encode())
    dir_rows, kids = [], []
    for name, children in dirs:
        no, nl = text(name)
        dir_rows.append((no, nl, len(kids), len(children)))
        kids += [i if kind == "d" else nd + i for kind, i in children]
    file_rows = [text(n) for n in files]
    dirs_at = 0x80 + 0x30 + 0x40 * nt
    files_at = dirs_at + 24 * nd
    kids_at = files_at + 80 * nf
    strings_at = kids_at + 4 * len(kids)
    blob = bytearray(strings_at + len(strings))
    blob[:4], blob[0x80:0x84] = b"NUP\x01", b"TOC\x01"
    struct.pack_into("<QQ", blob, 8, 0x80, len(blob) - 0x80)
    struct.pack_into("<IQQQQ", blob, 0x84, nt, nd, nf, strings_at - 0x80, len(strings))
    struct.pack_into("<Q", blob, 0x38, len(blob))
    struct.pack_into("<Q", blob, 0x40, len(blob))
    for i, (root, exefs, romfs, ver) in enumerate(titles):
        struct.pack_into("<QIIII", blob, 0xb0 + 0x40 * i, 0x1000 + i, root, exefs, romfs, ver)
    for i, row in enumerate(dir_rows):
        struct.pack_into("<4I", blob, dirs_at + 24 * i, *row)
    for i, (no, nl) in enumerate(file_rows):
        struct.pack_into("<IIQQQ", blob, files_at + 80 * i, no, nl, 0, 0, 0)
    for i, k in enumerate(kids):
        struct.pack_into("<I", blob, kids_at + 4 * i, k)
    blob[strings_at:] = strings
    return bytes(blob)


def main():
    table = "ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"
    files = [table, "Other.bin", "main", "Model.bfres"]
    dirs = [("", [("d", 1), ("d", 2)]),                    # 0 the title's root
            ("exefs", [("f", 2)]),                         # 1
            ("romfs", [("d", 3), ("d", 5)]),               # 2
            ("System", [("d", 4)]),                        # 3
            ("Resource", [("f", 0), ("f", 1), ("d", 6)]),  # 4
            ("Model", [("f", 3)]),                         # 5
            ("Sub", []),                                   # 6
            ("romfs", [("d", 5)])]                         # 7 a RomFS without System
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.nup")
        with open(path, "wb") as f:
            f.write(package(dirs, files, [(0, 1, 2, 0), (0, 1, 7, 65536)]))
        n = nup.Nup(path)
        try:
            assert n.listing(2, "System/Resource") == [table, "Other.bin", "Sub/"], n.listing(2, "System/Resource")
            assert n.listing(2, "") == ["System/", "Model/"]
            assert n.listing(2, "System/Nothing") is None and n.listing(7, "System/Resource") is None
        finally:
            n.f.close()
        assert nup.resource_names(path, "0") == [table, "Other.bin", "Sub/"]
        assert nup.resource_names(path) == []            # (the newest title, extract()'s default: its RomFS has no System)
        with open(path, "wb") as f:
            f.write(package(dirs, files, [(0, 1, NONE, 0)]))
        assert nup.resource_names(path) is None           # (no RomFS at all)
    print("ok: nup listing (6 cases)")


if __name__ == "__main__":
    main()
