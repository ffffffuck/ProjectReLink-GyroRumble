"""Mapped-page accounting is bounded and all three used-memory reports use it.

No game data, renderer, Vulkan device or guest-memory mapping is used.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="clang")
    args = parser.parse_args()

    fixture = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define HOS_GUEST_SPACE (1ULL << 39)
#include "host/hos_mapped_pages.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); return 1; } } while (0)

int main(void) {
    HosMappedPages pages = {0};
    const uint64_t base = 0x1000000000ULL;
    CHECK(hos_mapped_pages_set(&pages, base, 0x2000, 1) == 0x2000);
    CHECK(pages.bytes == 0x2000);
    CHECK(hos_mapped_pages_set(&pages, base, 0x2000, 1) == 0);
    CHECK(hos_mapped_pages_set(&pages, base + 0x1000, 0x2000, 1) == 0x1000);
    CHECK(pages.bytes == 0x3000);
    CHECK(hos_mapped_pages_set(&pages, base, 0x2000, 0) == 0x2000);
    CHECK(hos_mapped_pages_set(&pages, base, 0x2000, 0) == 0);
    CHECK(pages.bytes == 0x1000);
    CHECK(hos_mapped_pages_set(&pages, base + 0x2000, 0x1000, 0) == 0x1000);
    CHECK(pages.bytes == 0);

    /* FreeAddressRegion clears its span even when FreeMemoryPages was skipped. */
    const uint64_t region = 0x7f00000000ULL;
    CHECK(hos_mapped_pages_set(&pages, region + 0x1000, 0x4000, 1) == 0x4000);
    CHECK(hos_mapped_pages_set(&pages, region, 0x10000, 0) == 0x4000);
    CHECK(pages.bytes == 0);

    /* Crossing or overflowing the end of the guest window is clipped. */
    CHECK(hos_mapped_pages_set(&pages, HOS_GUEST_SPACE - 0x800, UINT64_MAX, 1) == 0x1000);
    CHECK(pages.bytes == 0x1000);
    CHECK(hos_mapped_pages_set(&pages, HOS_GUEST_SPACE, 0x1000, 1) == 0);
    CHECK(hos_mapped_pages_set(&pages, HOS_GUEST_SPACE - 0x800, UINT64_MAX, 0) == 0x1000);
    CHECK(pages.bytes == 0);
    puts("PASS: mapped-page accounting counts once and stays inside the guest window");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="mapped-memory-info-") as temp:
        source = Path(temp) / "fixture.c"
        executable = Path(temp) / "fixture"
        source.write_text(fixture, encoding="utf-8")
        subprocess.run(
            [args.cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(source), "-o", str(executable)],
            check=True,
        )
        subprocess.run([str(executable)], check=True)

    mem = (ROOT / "host/hos_mem.c").read_text(encoding="utf-8")
    svc = (ROOT / "host/hos_svc.c").read_text(encoding="utf-8")
    free = mem[mem.index("HLE(_ZN2nn2os15FreeMemoryPagesEmm)"):mem.index("HLE(_ZN2nn2os15QueryMemoryInfo")]
    region_free = mem[mem.index("void hos_region_free("):mem.index("/* ---- nn::os memory")]
    assert free.index("hos_mapped_pages_set") < free.index("if (ARG(0) + ARG(1) >")
    assert region_free.index("hos_mapped_pages_set") < region_free.index("memmove")
    assert "HOS_MEMINFO_MAPPED" in mem and "HOS_PAGES_LIMIT" not in mem
    assert "hos_reported_used_memory(hos_program_size + (16ULL << 20), 0)" in mem
    assert svc.count("hos_reported_used_memory(hos_program_size, 1)") == 2
    print("PASS: QueryMemoryInfo and both GetInfo answers share the opt-in accounting")


if __name__ == "__main__":
    main()
