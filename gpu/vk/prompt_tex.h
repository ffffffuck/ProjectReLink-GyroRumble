/* (relink, issues 34, 63, 68, 20) The HUD cross's faded letters for Xbox and PlayStation prompts.

   The game draws the three faded discs of the HUD's button cross with four textures of its Common layout archive
   (KeyTexFontA_00, B, X, Y: BC4, 64 x 64, one level, a mask of the Switch letter alone, tinted by the layout), not with
   the button font host/prompts.c replaces. Each build writes <game folder>/prompts/keytex.bin (nx2/button_glyphs.py
   write_keytex): a hash of each of the four as the renderer has them once unswizzled (BC4 blocks in rows, top down), and
   ReLink's own masks of each family's four face labels. When the renderer uploads a texture of exactly that shape whose
   blocks have one of those hashes, it uploads instead the mask of the label of the pad position that Switch button is
   bound to.

   The family and the bindings are the button font's: the host tells the renderer what it gave the game's font once it
   decided it (rx_option_set(HOS_PROMPTS_TEX_ID, HOS_PROMPTS_TEX_VALUE(...)), host/prompts.c; again when the renderer
   starts after that, host/rx_backend.c), and the first announcement is kept for the session, so the cross and the font
   cannot show two families or two sets of bindings. A family of 0 (the game's own font, a mod's font, a font that could
   not be made), no keytex.bin, or one that does not hold together: the game's own textures. A letter uploaded before the
   announcement is the game's until then; the renderer uploads it again once it is known (vk_texture.cpp texture_touch).

   The ids are C, for the host; the rest is C++ with no dependency on Vulkan or the host (bytes in, bytes out), for
   gpu/vk/vk_prompt_tex.cpp and the test program gpu/vk/tests/prompt_tex_test.cpp (nx2/test_prompt_tex.py). Here and not
   in vk_api.h: that header is part of the shader cache's stamp, and a change to it makes every player prepare the
   shaders again. */
#pragma once

/* rx_option_set(HOS_PROMPTS_TEX_ID, value) from the host: the button font's family (0 the game's own, 1 Xbox, 2
   PlayStation) and the HOS_PAD_* input each of Switch A, B, X and Y was bound to when the font was made (-1 none) */
#define HOS_PROMPTS_TEX_ID 4089
#define HOS_PROMPTS_TEX_VALUE(family, a, b, x, y)                                                                       \
    (((family) & 3) | ((((a) + 1) & 31) << 2) | ((((b) + 1) & 31) << 7) | ((((x) + 1) & 31) << 12) |                \
     ((((y) + 1) & 31) << 17))

#ifdef __cplusplus
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

namespace prompt_tex {

constexpr uint32_t kSize = 64;                       // (the textures' width and height)
constexpr uint32_t kBlocks = kSize * kSize / 2;      // (BC4: 8 bytes a 4 x 4 block: 2,048 bytes)
constexpr uint32_t kHeader = 16, kRecords = 4 * 16;
constexpr uint32_t kFile = kHeader + kRecords + 2 * 4 * kBlocks + 8;
static const char kButton[4] = {'A', 'B', 'X', 'Y'};
static const char* const kFamily[3] = {"nintendo", "xbox", "playstation"};
// (each family's name for the label at south, east, west, north: HOS_PAD_SOUTH .. HOS_PAD_NORTH are 0 .. 3)
static const char* const kLabel[3][4] = {{"", "", "", ""}, {"A", "B", "X", "Y"}, {"Cross", "Circle", "Square", "Triangle"}};

inline uint64_t fnv1a64(const uint8_t* b, size_t n) {
    uint64_t h = 0xCBF29CE484222325ull;
    for (size_t i = 0; i < n; i++) h = (h ^ b[i]) * 0x100000001B3ull;
    return h;
}

inline uint64_t le64(const uint8_t* b) {
    uint64_t v = 0;
    for (int k = 7; k >= 0; k--) v = v << 8 | b[k];
    return v;
}

/// keytex.bin, read
struct Table {
    uint64_t hash[4] = {};                   // (the game's textures of Switch A, B, X, Y)
    uint8_t mask[2][4][kBlocks] = {};        // (xbox, playstation; south, east, west, north)
};

/// keytex.bin's bytes into t: true, or false with why
inline bool parse(const uint8_t* b, size_t n, Table& t, std::string& why) {
    if (n != kFile) return why = "it is " + std::to_string(n) + " bytes, not " + std::to_string(kFile), false;
    static const uint8_t head[kHeader] = {'R', 'L', 'K', 'T', 1, 0, 4, 0, kSize, 0, kSize, 0, 2, 0, 4, 0};
    if (std::memcmp(b, head, kHeader)) return why = "its header is not the one known", false;
    if (fnv1a64(b, n - 8) != le64(b + n - 8)) return why = "its last hash does not hold: the file is damaged", false;
    for (int i = 0; i < 4; i++) {
        const uint8_t* r = b + kHeader + 16 * i;
        static const uint8_t zero[7] = {};
        if (r[0] != (uint8_t)kButton[i] || std::memcmp(r + 1, zero, 7))
            return why = std::string("its record ") + std::to_string(i) + " is not Switch " + kButton[i] + "'s", false;
        t.hash[i] = le64(r + 8);
        for (int k = 0; k < i; k++)
            if (t.hash[k] == t.hash[i]) return why = "two of its textures have the same hash", false;
    }
    std::memcpy(t.mask, b + kHeader + kRecords, sizeof t.mask);
    return true;
}

/// The announcement's family (0, 1, 2) and the pad input of Switch A, B, X, Y (-1 none)
inline int decode(int v, int pad[4]) {
    for (int i = 0; i < 4; i++) pad[i] = ((v >> (2 + 5 * i)) & 31) - 1;
    return v & 3;
}

/// Which of the game's four (Switch A, B, X, Y: 0 to 3) blocks (kBlocks bytes, unswizzled) are, -1 none
inline int which(const Table& t, const uint8_t* blocks) {
    const uint64_t h = fnv1a64(blocks, kBlocks);
    for (int i = 0; i < 4; i++)
        if (h == t.hash[i]) return i;
    return -1;
}

/// The renderer's state: the table read once, the family and bindings latched from the host's first announcement, at
/// most one line saying why the game's own letters stay and one when a replacement is first used. Thread-safe
class Replacer {
public:
    using Load = std::function<bool(std::vector<uint8_t>& bytes, std::string& where)>;   // (false: cannot be read)
    using Log = std::function<void(const char* line)>;
    Replacer(Load load, Log log) : load_(std::move(load)), log_(std::move(log)) {}

    /// The host's announcement (HOS_PROMPTS_TEX_VALUE): the first one counts; the family and the bindings are latched
    /// then, and keytex.bin read when the family is not 0
    void announce(int v) {
        if (v < 0 || v > 0x3FFFFF) return;
        int none = -1;
        if (announced_.compare_exchange_strong(none, v)) std::call_once(latch_, [&] { latch(v); });
    }

    /// -1: no announcement yet; 0: the game's own letters for the session; 1: replaced
    int state() {
        if (announced_.load() < 0) return -1;
        std::call_once(latch_, [&] { latch(announced_.load()); });
        return on_ ? 1 : 0;
    }

    /// Before the announcement and after it: whether blocks are one of the game's four letters that are (or may be)
    /// replaced. The GPU copy into such a texture asks, to take the CPU's way instead (vk_xfer.cpp copy_b2t)
    bool recognised(const uint8_t* blocks) {
        const int s = state();
        if (s == 0 || !table_ok()) return false;
        return which(table_, blocks) >= 0;
    }

    /// blocks (kBlocks bytes, unswizzled) replaced in place by the label's mask: 1 when they were; 0 left alone (not
    /// one of the four, the game's own letters chosen, or that Switch button is not on a face position); -1 one of the
    /// four, uploaded before the announcement: left alone for now, to be uploaded again once state() is not -1
    int apply(uint8_t* blocks) {
        const int s = state();
        if (s == 0) return 0;
        if (s < 0) {
            if (!table_ok() || which(table_, blocks) < 0) return 0;
            if (!said_early_.exchange(true))
                log_("prompts: a HUD cross letter was uploaded before the button font was opened: it stays the Switch's "
                     "until then, and is uploaded again once the font's family is known");
            return -1;
        }
        const int i = which(table_, blocks);
        if (i < 0 || pad_[i] < 0 || pad_[i] > 3) return 0;
        std::memcpy(blocks, table_.mask[family_ - 1][pad_[i]], kBlocks);
        if (!used_.exchange(true)) log_(shows_.c_str());
        return 1;
    }

private:
    bool table_ok() {
        std::call_once(read_, [&] {
            std::vector<uint8_t> b;
            if (!load_(b, where_)) {
                why_ = "there is no " + where_ + " (made by the build)";
                return;
            }
            std::string w;
            if (!parse(b.data(), b.size(), table_, w)) why_ = where_ + " is not usable: " + w;
            else ok_ = true;
        });
        return ok_;
    }
    void latch(int v) {
        family_ = decode(v, pad_);
        std::string line;
        if (family_ < 1 || family_ > 2) {
            family_ = 0;
            line = "prompts: the HUD cross's letters stay the Switch's (the button font is the game's own)";
        } else if (!table_ok()) {
            line = "prompts: the HUD cross's letters stay the Switch's: " + why_;
        } else {
            on_ = true;
            shows_ = std::string("prompts: the HUD cross's letters are ") + kFamily[family_] + " labels:";
            for (int i = 0; i < 4; i++) {
                shows_ += i ? ", " : " ";
                shows_ += kButton[i];
                shows_ += pad_[i] >= 0 && pad_[i] < 4 ? std::string(" shows ") + kLabel[family_][pad_[i]]
                                                       : std::string(" keeps the Switch's (not on a face button)");
            }
        }
        if (!line.empty() && !said_.exchange(true)) log_(line.c_str());
    }

    Load load_;
    Log log_;
    std::atomic<int> announced_{-1};
    std::once_flag read_, latch_;
    Table table_;
    bool ok_ = false, on_ = false;
    int family_ = 0, pad_[4] = {-1, -1, -1, -1};
    std::string where_, why_, shows_;
    std::atomic<bool> said_{false}, said_early_{false}, used_{false};
};

}  // namespace prompt_tex

namespace mkwvk {
// (gpu/vk/vk_prompt_tex.cpp: the renderer's one Replacer, its file <game folder>/prompts/keytex.bin)
void prompt_tex_announce(int value);              // (vk_menu.cpp menu_option_set, HOS_PROMPTS_TEX_ID)
int prompt_tex_apply(uint8_t* blocks);            // (vk_texture.cpp upload, a texture of the shape: prompt_tex_shape)
int prompt_tex_state();                           // (vk_texture.cpp texture_touch: a letter uploaded before it was known)
bool prompt_tex_recognised(const uint8_t* blocks);   // (vk_xfer.cpp copy_b2t)
}  // namespace mkwvk
#endif
