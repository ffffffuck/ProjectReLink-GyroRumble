#ifndef HOS_MAPPED_PAGES_H
#define HOS_MAPPED_PAGES_H

#include <stdint.h>
#include <stdlib.h>

/* One lazily allocated 32 KiB bitmap covers a GiB of the guest window. */
#define HOS_MAPPED_LEAF_SHIFT 30
#define HOS_MAPPED_LEAF_PAGES (1ULL << (HOS_MAPPED_LEAF_SHIFT - 12))
#define HOS_MAPPED_LEAVES ((HOS_GUEST_SPACE + (1ULL << HOS_MAPPED_LEAF_SHIFT) - 1) >> HOS_MAPPED_LEAF_SHIFT)

typedef struct HosMappedPages {
    uint64_t *leaves[HOS_MAPPED_LEAVES];
    uint64_t bytes;
} HosMappedPages;

/* Called under hos_mem.c's lock. Returns the bytes whose mapped state changed.
   Ranges are clipped to the guest window before their end is formed, so a
   malformed length cannot wrap the page walk. A failed leaf allocation errs
   low, which preserves the old free-memory report for that leaf. */
static inline uint64_t hos_mapped_pages_set(HosMappedPages *map, uint64_t addr, uint64_t size, int mapped) {
    if (!size || addr >= HOS_GUEST_SPACE) return 0;
    const uint64_t end = size > HOS_GUEST_SPACE - addr ? HOS_GUEST_SPACE : addr + size;
    const uint64_t first = addr >> 12;
    const uint64_t last = (end + 0xFFF) >> 12;
    uint64_t changed = 0;

    for (uint64_t page = first; page < last; page++) {
        uint64_t **leaf = &map->leaves[page / HOS_MAPPED_LEAF_PAGES];
        if (!*leaf) {
            if (!mapped) continue;
            *leaf = (uint64_t *)calloc(HOS_MAPPED_LEAF_PAGES / 64, sizeof(uint64_t));
            if (!*leaf) continue;
        }
        const uint64_t index = page % HOS_MAPPED_LEAF_PAGES;
        const uint64_t bit = 1ULL << (index & 63);
        uint64_t *word = &(*leaf)[index >> 6];
        const int was_mapped = (*word & bit) != 0;
        if (mapped && !was_mapped) {
            *word |= bit;
            changed += 0x1000;
        } else if (!mapped && was_mapped) {
            *word &= ~bit;
            changed += 0x1000;
        }
    }
    if (mapped) map->bytes += changed;
    else map->bytes -= changed;
    return changed;
}

#endif
