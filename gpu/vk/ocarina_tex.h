/* (relink, issues 34, 63, 68, 20) The ocarina's note buttons for Xbox and PlayStation prompts: the renderer's side of
   nx2/ocarina_prompts.py.

   The game draws the ocarina's note buttons with six textures of its Common layout archive (OcarinaButtonA_00, B, X, Y,
   L, R: ASTC 4x4 sRGB, 93 x 93, one level), not with the button font host/prompts.c replaces. Each build writes
   <game folder>/prompts/ocarina.bin: a hash of each of the six as the renderer has them once unswizzled (ASTC blocks in
   rows, top down), and ReLink's version of each for each family's label at each pad position, in ASTC again. When the
   renderer uploads a texture of that shape whose blocks have one of those hashes, it uploads instead the version for the
   label of the pad position that Switch button is bound to. Such a texture takes the CPU's ASTC decode (vk_texture.cpp
   upload), where its blocks are at hand, while the family is not the game's own.

   The family is the button font's, from the host's announcement (prompt_tex.h HOS_PROMPTS_TEX_ID, the first one kept
   for the session); the bindings are asked of the host then (its binding call, as the font was made with), so the notes,
   the HUD cross and the font show one family and one set of bindings. A family of 0, no ocarina.bin, or one that does not
   hold together: the game's own notes. A note uploaded before the announcement is the game's until then; the renderer
   uploads it again once it is known (vk_texture.cpp texture_touch, as the HUD cross's letters).

   C++ with no dependency on Vulkan or the host (bytes in, bytes out), for gpu/vk/vk_prompt_tex.cpp and the test program
   gpu/vk/tests/ocarina_tex_test.cpp. */
#pragma once

#include "prompt_tex.h"

#ifdef __cplusplus
namespace ocarina_tex {

constexpr uint32_t kSize = 93;                                    // (the textures' width and height)
constexpr uint32_t kBlocks = ((kSize + 3) / 4) * ((kSize + 3) / 4) * 16;   // (ASTC 4x4: 576 blocks of 16 bytes)
constexpr int kButtons = 6, kFamilies = 2, kPositions = 12;
constexpr uint32_t kHeader = 16, kRecords = kButtons * 16;
constexpr uint64_t kFile = kHeader + kRecords + (uint64_t)kButtons * kFamilies * kPositions * kBlocks + 8;
static const char* const kButton[kButtons] = {"A", "B", "X", "Y", "L", "R"};
// (the Switch buttons, as the host's binding call numbers them: HOS_SW_A, B, X, Y, L, R)
static const int kSwitch[kButtons] = {0, 1, 2, 3, 6, 7};
// (the pad positions in the file's order, as HOS_PAD_* numbers them: south, east, west, north, LB, RB, LT, RT, start,
// back, left stick, right stick)
static const int kPad[kPositions] = {0, 1, 2, 3, 9, 10, 15, 16, 6, 4, 7, 8};
static const char* const kPositionName[2][kPositions] = {
    {"A", "B", "X", "Y", "LB", "RB", "LT", "RT", "Menu", "View", "LS", "RS"},
    {"Cross", "Circle", "Square", "Triangle", "L1", "R1", "L2", "R2", "Options", "Create", "L3", "R3"}};

/// The file's position of a HOS_PAD_* input, -1 for one with no label (the D-pad, the guide button, none)
inline int position_of_pad(int pad) {
    for (int i = 0; i < kPositions; i++)
        if (kPad[i] == pad) return i;
    return -1;
}

/// ocarina.bin, read
struct Table {
    uint64_t hash[kButtons] = {};
    std::vector<uint8_t> versions;   // ([button][family][position] kBlocks bytes each)
    const uint8_t* version(int button, int family, int position) const {
        return versions.data() + (((uint64_t)button * kFamilies + family) * kPositions + position) * kBlocks;
    }
};

/// ocarina.bin's bytes into t: true, or false with why
inline bool parse(const uint8_t* b, size_t n, Table& t, std::string& why) {
    if (n != kFile) return why = "it is " + std::to_string(n) + " bytes, not " + std::to_string(kFile), false;
    static const uint8_t head[kHeader] = {'R', 'L', 'K', 'O', 1, 0, kButtons, 0, kSize, 0, kSize, 0, kFamilies, 0,
                                          kPositions, 0};
    if (std::memcmp(b, head, kHeader)) return why = "its header is not the one known", false;
    if (prompt_tex::fnv1a64(b, n - 8) != prompt_tex::le64(b + n - 8)) return why = "its last hash does not hold: the file is damaged", false;
    for (int i = 0; i < kButtons; i++) {
        const uint8_t* r = b + kHeader + 16 * i;
        char name[9] = {};
        std::memcpy(name, r, 8);
        if (std::strcmp(name, kButton[i])) return why = std::string("its record ") + std::to_string(i) + " is not Switch " + kButton[i] + "'s", false;
        t.hash[i] = prompt_tex::le64(r + 8);
        for (int k = 0; k < i; k++)
            if (t.hash[k] == t.hash[i]) return why = "two of its textures have the same hash", false;
    }
    t.versions.assign(b + kHeader + kRecords, b + n - 8);
    return true;
}

/// Which of the game's six (Switch A, B, X, Y, L, R: 0 to 5) blocks (kBlocks bytes, unswizzled) are, -1 none
inline int which(const Table& t, const uint8_t* blocks) {
    const uint64_t h = prompt_tex::fnv1a64(blocks, kBlocks);
    for (int i = 0; i < kButtons; i++)
        if (h == t.hash[i]) return i;
    return -1;
}

/// The renderer's state, as prompt_tex::Replacer's: the table read once, the family latched from the host's first
/// announcement and the bindings asked then, at most one line saying why the game's own notes stay and one when a
/// version is first used. Thread-safe
class Replacer {
public:
    using Load = std::function<bool(std::vector<uint8_t>& bytes, std::string& where)>;   // (false: cannot be read)
    using Log = std::function<void(const char* line)>;
    using Binding = std::function<int(int sw)>;   // (the HOS_PAD_* input a Switch button is bound to, -1 none)
    Replacer(Load load, Log log, Binding binding)
        : load_(std::move(load)), log_(std::move(log)), binding_(std::move(binding)) {}

    /// The host's announcement (prompt_tex.h HOS_PROMPTS_TEX_VALUE): the first one counts
    void announce(int v) {
        if (v < 0) return;
        int none = -1;
        if (announced_.compare_exchange_strong(none, v)) std::call_once(latch_, [&] { latch(v); });
    }

    /// -1: no announcement yet; 0: the game's own notes for the session; 1: replaced
    int state() {
        if (announced_.load() < 0) return -1;
        std::call_once(latch_, [&] { latch(announced_.load()); });
        return on_ ? 1 : 0;
    }

    /// blocks (kBlocks bytes, unswizzled) replaced in place by ReLink's version: 1 when they were; 0 left alone (not one
    /// of the six, the game's own notes chosen, or that Switch button is not on a position with a label); -1 one of the
    /// six, uploaded before the announcement: left alone for now, to be uploaded again once state() is not -1
    int apply(uint8_t* blocks) {
        const int s = state();
        if (s == 0) return 0;
        if (s < 0) {
            if (!table_ok() || which(table_, blocks) < 0) return 0;
            if (!said_early_.exchange(true))
                log_("prompts: an ocarina note button was uploaded before the button font was opened: it stays the "
                     "Switch's until then, and is uploaded again once the font's family is known");
            return -1;
        }
        const int i = which(table_, blocks);
        if (i < 0 || pos_[i] < 0) return 0;
        std::memcpy(blocks, table_.version(i, family_ - 1, pos_[i]), kBlocks);
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
        int pad[4];
        family_ = prompt_tex::decode(v, pad);
        std::string line;
        if (family_ < 1 || family_ > 2) {
            family_ = 0;
            line = "prompts: the ocarina's note buttons stay the Switch's (the button font is the game's own)";
        } else if (!table_ok()) {
            line = "prompts: the ocarina's note buttons stay the Switch's: " + why_;
        } else {
            on_ = true;
            shows_ = std::string("prompts: the ocarina's note buttons are ") + prompt_tex::kFamily[family_] + " labels:";
            for (int i = 0; i < kButtons; i++) {
                // (A, B, X and Y as the announcement has them, the font's; L and R asked now)
                pos_[i] = position_of_pad(i < 4 ? pad[i] : binding_ ? binding_(kSwitch[i]) : -1);
                shows_ += i ? ", " : " ";
                shows_ += kButton[i];
                shows_ += pos_[i] >= 0 ? std::string(" shows ") + kPositionName[family_ - 1][pos_[i]]
                                       : std::string(" keeps the Switch's (not on a button with a label)");
            }
        }
        if (!line.empty() && !said_.exchange(true)) log_(line.c_str());
    }

    Load load_;
    Log log_;
    Binding binding_;
    std::atomic<int> announced_{-1};
    std::once_flag read_, latch_;
    Table table_;
    bool ok_ = false, on_ = false;
    int family_ = 0, pos_[kButtons] = {-1, -1, -1, -1, -1, -1};
    std::string where_, why_, shows_;
    std::atomic<bool> said_{false}, said_early_{false}, used_{false};
};

}  // namespace ocarina_tex

namespace mkwvk {
// (gpu/vk/vk_prompt_tex.cpp: the renderer's one ocarina Replacer, its file <game folder>/prompts/ocarina.bin)
int ocarina_tex_apply(uint8_t* blocks);   // (vk_texture.cpp upload, a texture of the shape: ocarina_tex_shape)
int ocarina_tex_state();                  // (vk_texture.cpp: the CPU's way while not 0; texture_touch)
}  // namespace mkwvk
#endif
