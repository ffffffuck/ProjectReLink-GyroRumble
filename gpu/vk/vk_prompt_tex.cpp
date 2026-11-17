// (relink, issues 34, 63, 68, 20) The HUD cross's faded letters for Xbox and PlayStation prompts: the renderer's side of
// prompt_tex.h. Its file is <game folder>/prompts/keytex.bin, read once the host announced a family other than the game's
// own (vk_menu.cpp menu_option_set), or at the first upload of one of the game's letters before that.
#include <cstdio>
#include <string>
#include <vector>

#include "vk_paths.h"
#include "vk_internal.h"
#include "prompt_tex.h"

namespace mkwvk {
namespace {

bool load_keytex(std::vector<uint8_t>& bytes, std::string& where) {
    char exe[MAX_PATH];
    const DWORD n = game_module_path(exe, MAX_PATH);
    std::string path(exe, n < MAX_PATH ? n : 0);
    where = path.substr(0, path.find_last_of("\\/") + 1) + "prompts\\keytex.bin";
    FILE* f = fopen(where.c_str(), "rb");
    if (!f) return false;
    bytes.assign(prompt_tex::kFile + 1, 0);   // (one byte more: a longer file reads as longer and is refused)
    bytes.resize(fread(bytes.data(), 1, bytes.size(), f));
    fclose(f);
    return true;
}

prompt_tex::Replacer& replacer() {
    static prompt_tex::Replacer r(load_keytex, [](const char* line) { logf("%s", line); });
    return r;
}

}  // namespace

void prompt_tex_announce(int value) { replacer().announce(value); }
int prompt_tex_apply(uint8_t* blocks) { return replacer().apply(blocks); }
int prompt_tex_state() { return replacer().state(); }
bool prompt_tex_recognised(const uint8_t* blocks) { return replacer().recognised(blocks); }

}  // namespace mkwvk
