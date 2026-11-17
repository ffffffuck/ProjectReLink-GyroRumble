// Game-free check of the HUD cross letters' replacement (gpu/vk/prompt_tex.h): bytes in, bytes out, no Vulkan.
// nx2/test_prompt_tex.py builds it and runs it over a folder holding keytex.bin (made by nx2/button_glyphs.py from the
// player's RomFS), the four KeyTexFont textures' storage as the game has it (block linear) and textures.txt (one line a
// texture: the Switch button, the block height in GOBs as a power of two, the file). The storage is unswizzled with the
// renderer's own function (Tegra::Texture::UnswizzleTexture, as vk_texture.cpp upload does), so the hashes keytex.bin
// holds are checked against what the renderer will have.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "../vk_api.h"
#include "../prompt_tex.h"
#include "video_core/textures/decoders.h"

namespace {

int failures = 0, checks = 0;

void check(bool ok, const std::string& what) {
    checks++;
    if (ok) return;
    std::fprintf(stderr, "FAIL: %s\n", what.c_str());
    failures++;
}

std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

using Bytes = std::vector<uint8_t>;
constexpr size_t N = prompt_tex::kBlocks;

/// A Replacer over the given file bytes (nullptr: no file), its log lines and how often it read the file
struct Rig {
    std::vector<std::string> lines;
    int loads = 0;
    prompt_tex::Replacer r;
    explicit Rig(const Bytes* file)
        : r([this, file](Bytes& b, std::string& where) {
              loads++;
              where = "prompts\\keytex.bin";
              if (!file) return false;
              b = *file;
              return true;
          },
            [this](const char* line) { lines.push_back(line); }) {}
};

uint64_t le64(const uint8_t* b) { return prompt_tex::le64(b); }
void put64(uint8_t* b, uint64_t v) {
    for (int k = 0; k < 8; k++) b[k] = (uint8_t)(v >> (8 * k));
}
/// keytex.bin with its last hash made to hold again (after a change to the bytes before it)
void reseal(Bytes& k) { put64(k.data() + k.size() - 8, prompt_tex::fnv1a64(k.data(), k.size() - 8)); }

const char* kName[3][4] = {{"", "", "", ""}, {"A", "B", "X", "Y"}, {"Cross", "Circle", "Square", "Triangle"}};

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: prompt_tex_test <folder with keytex.bin, textures.txt and the textures' storage>\n");
        return 2;
    }
    const std::string dir = argv[1];
    const Bytes file = read_file(dir + "/keytex.bin");
    check(file.size() == prompt_tex::kFile, "keytex.bin is " + std::to_string(file.size()) + " bytes");
    if (file.size() != prompt_tex::kFile) return 1;
    // (the masks, by the file format's offsets: family 0 xbox, 1 playstation; position south, east, west, north)
    auto mask = [&](int fam, int pos) { return Bytes(file.begin() + 80 + (fam * 4 + pos) * N, file.begin() + 80 + (fam * 4 + pos + 1) * N); };

    // the game's four letters, unswizzled as the renderer does
    Bytes game[4];
    {
        std::ifstream list(dir + "/textures.txt");
        std::string line;
        int n = 0;
        while (std::getline(list, line)) {
            std::istringstream w(line);
            std::string button, name;
            unsigned block_height = 0;
            if (!(w >> button >> block_height >> name)) continue;
            const int i = (int)std::string("ABXY").find(button[0]);
            const Bytes swizzled = read_file(dir + "/" + name);
            game[i].assign(N, 0);
            Tegra::Texture::UnswizzleTexture(std::span<uint8_t>(game[i].data(), N), std::span<const uint8_t>(swizzled),
                                             8, prompt_tex::kSize / 4, prompt_tex::kSize / 4, 1, block_height, 0, 1);
            const uint64_t want = le64(file.data() + 16 + 16 * i + 8);
            check(prompt_tex::fnv1a64(game[i].data(), N) == want,
                  std::string("KeyTexFont") + button + "_00 unswizzled by the renderer's function has the hash keytex.bin holds");
            std::printf("KeyTexFont%s_00: %zu bytes of storage, blocks %u GOBs high, hash %016llx\n", button.c_str(),
                        swizzled.size(), 1u << block_height, (unsigned long long)want);
            n++;
        }
        check(n == 4, "textures.txt names the four textures");
        if (n != 4) return 1;
    }

    // inputs that are none of the game's letters: random bytes, each letter with one byte changed, zeros, our masks
    std::vector<Bytes> others;
    {
        uint32_t seed = 12345;
        for (int k = 0; k < 8; k++) {
            Bytes b(N);
            for (auto& c : b) c = (uint8_t)((seed = seed * 1664525u + 1013904223u) >> 24);
            others.push_back(b);
        }
        for (int i = 0; i < 4; i++)
            for (size_t at : {(size_t)0, N / 2, N - 1}) {
                Bytes b = game[i];
                b[at] ^= 1;
                others.push_back(b);
            }
        others.push_back(Bytes(N, 0));
        for (int f = 0; f < 2; f++)
            for (int p = 0; p < 4; p++) others.push_back(mask(f, p));
    }
    auto none_touched = [&](Rig& g, const std::string& what) {
        bool ok = true;
        for (const Bytes& o : others) {
            Bytes b = o;
            ok = ok && g.r.apply(b.data()) == 0 && b == o && !g.r.recognised(o.data());
        }
        check(ok, what + ": any other 2,048 bytes are left alone");
    };

    // the families, with the default bindings and the "Xbox / PC" preset's
    struct Layout { const char* name; int pad[4]; };   // (Switch A, B, X, Y)
    const Layout layouts[] = {{"default (Nintendo)", {HOS_PAD_EAST, HOS_PAD_SOUTH, HOS_PAD_NORTH, HOS_PAD_WEST}},
                              {"Xbox / PC preset", {HOS_PAD_SOUTH, HOS_PAD_EAST, HOS_PAD_WEST, HOS_PAD_NORTH}}};
    for (int fam = 1; fam <= 2; fam++)
        for (const Layout& l : layouts) {
            const std::string what = std::string(prompt_tex::kFamily[fam]) + ", " + l.name;
            Rig g(&file);
            check(g.r.state() == -1, what + ": nothing decided before the announcement");
            g.r.announce(HOS_PROMPTS_TEX_VALUE(fam, l.pad[0], l.pad[1], l.pad[2], l.pad[3]));
            check(g.r.state() == 1, what + ": on after the announcement");
            for (int round = 0; round < 2; round++)
                for (int i = 0; i < 4; i++) {
                    Bytes b = game[i];
                    check(g.r.recognised(b.data()), what + ": Switch " + "ABXY"[i] + "'s letter recognised");
                    const int rc = g.r.apply(b.data());
                    check(rc == 1 && b == mask(fam - 1, l.pad[i]), what + ": Switch " + "ABXY"[i] + "'s letter becomes the " +
                                                                       kName[fam][l.pad[i]] + " mask");
                }
            none_touched(g, what);
            std::string want = std::string("prompts: the HUD cross's letters are ") + prompt_tex::kFamily[fam] + " labels:";
            for (int i = 0; i < 4; i++) want += std::string(i ? ", " : " ") + "ABXY"[i] + " shows " + kName[fam][l.pad[i]];
            check(g.lines.size() == 1 && g.lines[0] == want, what + ": one line, \"" + want + "\"");
            check(g.loads == 1, what + ": keytex.bin read once");
            std::printf("%s: %s\n", what.c_str(), g.lines.empty() ? "(no line)" : g.lines[0].c_str());
        }

    // the game's own font (Nintendo chosen, a mod's font, a font not made): nothing replaced, the file not even read
    {
        Rig g(&file);
        g.r.announce(HOS_PROMPTS_TEX_VALUE(0, HOS_PAD_EAST, HOS_PAD_SOUTH, HOS_PAD_NORTH, HOS_PAD_WEST));
        bool ok = g.r.state() == 0;
        for (int i = 0; i < 4; i++) {
            Bytes b = game[i];
            ok = ok && g.r.apply(b.data()) == 0 && b == game[i] && !g.r.recognised(b.data());
        }
        check(ok, "nintendo: the game's letters left alone");
        none_touched(g, "nintendo");
        check(g.lines.size() == 1 && g.loads == 0, "nintendo: one line, keytex.bin not read");
        std::printf("nintendo: %s\n", g.lines.empty() ? "(no line)" : g.lines[0].c_str());
    }

    // no file, and files that do not hold together: nothing replaced, one line saying why
    {
        std::vector<std::pair<std::string, Bytes>> bad;
        bad.push_back({"one byte short", Bytes(file.begin(), file.end() - 1)});
        Bytes longer = file;
        longer.push_back(0);
        bad.push_back({"one byte longer", longer});
        Bytes b = file;
        b[4] = 2;
        reseal(b);
        bad.push_back({"another version", b});
        b = file;
        b[80 + 3 * N + 100] ^= 0x40;
        bad.push_back({"a mask's byte changed (the last hash fails)", b});
        b = file;
        b[16 + 16 * 2] = 'Z';
        reseal(b);
        bad.push_back({"a record not Switch X's", b});
        b = file;
        std::memcpy(b.data() + 16 + 16 * 1 + 8, b.data() + 16 + 8, 8);
        reseal(b);
        bad.push_back({"two textures with one hash", b});
        bad.push_back({"empty", Bytes()});
        bad.push_back({"garbage", others[0]});
        for (int k = -1; k < (int)bad.size(); k++) {
            const std::string what = k < 0 ? std::string("no keytex.bin") : "keytex.bin " + bad[k].first;
            Rig g(k < 0 ? nullptr : &bad[k].second);
            g.r.announce(HOS_PROMPTS_TEX_VALUE(1, HOS_PAD_EAST, HOS_PAD_SOUTH, HOS_PAD_NORTH, HOS_PAD_WEST));
            bool ok = g.r.state() == 0;
            for (int i = 0; i < 4; i++) {
                Bytes c = game[i];
                ok = ok && g.r.apply(c.data()) == 0 && c == game[i];
            }
            check(ok, what + ": the game's letters left alone");
            none_touched(g, what);
            check(g.lines.size() == 1 && g.lines[0].find("stay the Switch's") != std::string::npos, what + ": one line");
            std::printf("%s: %s\n", what.c_str(), g.lines.empty() ? "(no line)" : g.lines[0].c_str());
        }
    }

    // a letter uploaded before the announcement: the game's for now (-1: uploaded again later), replaced after it
    {
        Rig g(&file);
        Bytes b = game[0];
        check(g.r.apply(b.data()) == -1 && b == game[0], "before the announcement: A's letter left alone, to be uploaded again");
        Bytes c = game[3];
        check(g.r.apply(c.data()) == -1 && c == game[3], "before the announcement: Y's letter too");
        check(g.r.recognised(game[1].data()), "before the announcement: B's letter recognised (a GPU copy goes the CPU's way)");
        none_touched(g, "before the announcement");
        check(g.lines.size() == 1, "before the announcement: one line");
        g.r.announce(HOS_PROMPTS_TEX_VALUE(2, HOS_PAD_EAST, HOS_PAD_SOUTH, HOS_PAD_NORTH, HOS_PAD_WEST));
        check(g.r.state() == 1 && g.r.apply(b.data()) == 1 && b == mask(1, 1), "after it: A's letter becomes the Circle mask");
        // a second announcement does not change the family or the bindings
        g.r.announce(HOS_PROMPTS_TEX_VALUE(1, HOS_PAD_SOUTH, HOS_PAD_EAST, HOS_PAD_WEST, HOS_PAD_NORTH));
        Bytes d = game[2];
        check(g.r.apply(d.data()) == 1 && d == mask(1, 3), "a second announcement ignored: X's letter still the Triangle mask");
        check(g.lines.size() == 2, "before then after: two lines in all");
        for (auto& l : g.lines) std::printf("early: %s\n", l.c_str());
    }
    {
        Rig g(nullptr);
        Bytes b = game[0];
        check(g.r.apply(b.data()) == 0 && b == game[0] && g.lines.empty(),
              "before the announcement without keytex.bin: left alone, not to be uploaded again, no line");
    }
    // Switch A bound to a shoulder: its letter stays, the others follow
    {
        Rig g(&file);
        g.r.announce(HOS_PROMPTS_TEX_VALUE(1, HOS_PAD_LSHOULDER, HOS_PAD_SOUTH, HOS_PAD_NORTH, -1));
        Bytes a = game[0], bb = game[1], y = game[3];
        check(g.r.apply(a.data()) == 0 && a == game[0], "A on a shoulder: its letter left alone");
        check(g.r.apply(y.data()) == 0 && y == game[3], "Y bound to nothing: its letter left alone");
        check(g.r.apply(bb.data()) == 1 && bb == mask(0, 0), "B on south: the xbox A mask");
        std::printf("A on a shoulder: %s\n", g.lines.empty() ? "(no line)" : g.lines[0].c_str());
    }
    // the announcement's value round trip, -1 (none) and the last pad input included
    {
        int pad[4];
        const int fam = prompt_tex::decode(HOS_PROMPTS_TEX_VALUE(2, -1, HOS_PAD_RTRIGGER, 0, HOS_PAD_N - 1), pad);
        check(fam == 2 && pad[0] == -1 && pad[1] == HOS_PAD_RTRIGGER && pad[2] == 0 && pad[3] == HOS_PAD_N - 1,
              "HOS_PROMPTS_TEX_VALUE and decode agree");
    }

    std::printf("prompt_tex_test: %d checks, %d failed\n", checks, failures);
    return failures ? 1 : 0;
}
