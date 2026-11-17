// (relink) The ocarina note buttons' replacement, run-time side (gpu/vk/ocarina_tex.h), checked without the game or Vulkan
// (nx2/test_ocarina_tex.py builds and runs it). Its folder holds ocarina.bin (nx2/ocarina_prompts.py), the six
// OcarinaButton textures' storage as the game has it (block linear) and textures.txt (one line a texture: its Switch button,
// its GOB block height's log2, its storage file). The storage is unswizzled with the renderer's own function
// (Tegra::Texture::UnswizzleTexture, as vk_texture.cpp upload does), so the hashes ocarina.bin holds are checked against
// what the renderer will see; the Replacer is run for each family and position; each version is decoded with the
// renderer's own CPU ASTC decoder (Tegra::Texture::ASTC::DecompressSerial, the one an ocarina note takes) into
// decoded_<button>_<family>_<position>.rgba, which the script compares with its own decode.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <cstdint>
#include <span>

#include "../vk_api.h"
#include "../ocarina_tex.h"
#include "video_core/textures/decoders.h"
#include "video_core/textures/astc.h"   // (after decoders.h: it has no includes of its own)

namespace {

using Bytes = std::vector<uint8_t>;
constexpr uint32_t N = ocarina_tex::kBlocks;
int failures = 0;

void check(bool ok, const std::string& what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what.c_str());
        failures++;
    }
}

Bytes read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    return Bytes(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: ocarina_tex_test <folder with ocarina.bin, textures.txt and the textures' storage>\n");
        return 2;
    }
    const std::string dir = argv[1];
    const Bytes file = read_file(dir + "/ocarina.bin");
    check(file.size() == ocarina_tex::kFile, "ocarina.bin is " + std::to_string(file.size()) + " bytes");
    ocarina_tex::Table table;
    std::string why;
    check(ocarina_tex::parse(file.data(), file.size(), table, why), "ocarina.bin parses: " + why);
    if (failures) return 1;

    // the game's six, unswizzled as the renderer does
    Bytes game[ocarina_tex::kButtons];
    {
        std::ifstream list(dir + "/textures.txt");
        std::string line;
        int n = 0;
        while (std::getline(list, line)) {
            std::istringstream w(line);
            std::string button, name;
            unsigned block_height = 0;
            if (!(w >> button >> block_height >> name)) continue;
            const int i = (int)std::string("ABXYLR").find(button[0]);
            const Bytes swizzled = read_file(dir + "/" + name);
            game[i].assign(N, 0);
            constexpr uint32_t blocks_wide = (ocarina_tex::kSize + 3) / 4;
            Tegra::Texture::UnswizzleTexture(std::span<uint8_t>(game[i].data(), N), std::span<const uint8_t>(swizzled),
                                             16, blocks_wide, blocks_wide, 1, block_height, 0, 1);
            check(ocarina_tex::which(table, game[i].data()) == i,
                  std::string("OcarinaButton") + button + "_00 unswizzled by the renderer's function is recognised as Switch " + button);
            n++;
        }
        check(n == ocarina_tex::kButtons, "textures.txt names the six textures");
    }
    if (failures) return 1;

    auto loader = [&](Bytes& b, std::string& w) { w = "ocarina.bin"; b = file; return true; };
    auto quiet = [](const char*) {};

    // before the announcement: one of the six is left alone and asked again; another texture is left alone
    {
        ocarina_tex::Replacer r(loader, quiet, [](int) { return -1; });
        Bytes b = game[0];
        check(r.state() == -1 && r.apply(b.data()) == -1 && b == game[0], "before the announcement: left alone, to come again");
        Bytes other(N, 7);
        check(r.apply(other.data()) == 0, "before the announcement: another texture is not one of the six");
        r.announce(HOS_PROMPTS_TEX_VALUE(0, 1, 0, 3, 2));
        check(r.state() == 0 && r.apply(b.data()) == 0 && b == game[0], "family 0: the game's own notes");
    }
    // a file that does not hold together: the game's own notes
    {
        Bytes bad = file;
        bad[100] ^= 1;
        ocarina_tex::Replacer r([&](Bytes& b, std::string& w) { w = "ocarina.bin"; b = bad; return true; }, quiet,
                                [](int) { return -1; });
        r.announce(HOS_PROMPTS_TEX_VALUE(1, 1, 0, 3, 2));
        Bytes b = game[0];
        check(r.state() == 0 && r.apply(b.data()) == 0 && b == game[0], "a damaged ocarina.bin: the game's own notes");
    }
    // each family, each position: the version for that label, decoded by the renderer's CPU ASTC decoder
    int replaced = 0;
    for (int fam = 1; fam <= 2; fam++) {
        for (int pos = 0; pos < ocarina_tex::kPositions; pos++) {
            const int pad = ocarina_tex::kPad[pos];
            ocarina_tex::Replacer r(loader, quiet, [pad](int sw) { return sw == 6 || sw == 7 ? pad : -1; });
            r.announce(HOS_PROMPTS_TEX_VALUE(fam, pad, pad, pad, pad));
            check(r.state() == 1, "announced: replaced");
            for (int i = 0; i < ocarina_tex::kButtons; i++) {
                Bytes b = game[i];
                const int got = r.apply(b.data());
                check(got == 1 && !std::memcmp(b.data(), table.version(i, fam - 1, pos), N),
                      std::string("Switch ") + ocarina_tex::kButton[i] + " becomes " + ocarina_tex::kPositionName[fam - 1][pos]);
                replaced += got == 1;
                Bytes rgba((size_t)ocarina_tex::kSize * ocarina_tex::kSize * 4);
                Tegra::Texture::ASTC::DecompressSerial(std::span<const uint8_t>(b.data(), N), ocarina_tex::kSize,
                                                       ocarina_tex::kSize, 1, 4, 4, std::span<uint8_t>(rgba));
                const std::string out = dir + "/decoded_" + ocarina_tex::kButton[i] + "_" + std::to_string(fam) + "_" +
                                        std::to_string(pos) + ".rgba";
                std::ofstream(out, std::ios::binary).write((const char*)rgba.data(), (std::streamsize)rgba.size());
            }
            Bytes other(N, 7);
            check(r.apply(other.data()) == 0, "announced: another texture is left alone");
        }
    }
    // a button on the D-pad keeps the game's note
    {
        ocarina_tex::Replacer r(loader, quiet, [](int) { return 11; });
        r.announce(HOS_PROMPTS_TEX_VALUE(1, 11, 0, 3, 2));
        Bytes b = game[0];
        check(r.apply(b.data()) == 0 && b == game[0], "Switch A on the D-pad: the game's note");
        Bytes l = game[4];
        check(r.apply(l.data()) == 0 && l == game[4], "Switch L on the D-pad: the game's note");
        Bytes bb = game[1];
        check(r.apply(bb.data()) == 1, "Switch B on the south button: replaced");
    }
    std::printf("%d versions replaced and decoded\n", replaced);
    if (failures) std::printf("%d checks failed\n", failures);
    return failures ? 1 : 0;
}
