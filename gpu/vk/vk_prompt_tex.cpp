// (relink, issues 34, 63, 68, 20) The HUD cross's faded letters and the ocarina's note buttons for Xbox and PlayStation
// prompts: the renderer's side of prompt_tex.h and ocarina_tex.h. Their files are <game folder>/prompts/keytex.bin and
// ocarina.bin, read once the host announced a family other than the game's own (vk_menu.cpp menu_option_set), or at the
// first upload of one of the game's letters or notes before that.
#include <cstdio>
#include <string>
#include <vector>

#include "vk_paths.h"
#include "vk_internal.h"
#include "prompt_tex.h"
#include "ocarina_tex.h"

namespace mkwvk {
namespace {

/// <game folder>/prompts/<name> into bytes (at most max + 1 bytes: a longer file reads as longer and is refused)
bool load_prompts_file(const char* name, uint64_t max, std::vector<uint8_t>& bytes, std::string& where) {
    char exe[MAX_PATH];
    const DWORD n = game_module_path(exe, MAX_PATH);
    std::string path(exe, n < MAX_PATH ? n : 0);
    where = path.substr(0, path.find_last_of("\\/") + 1) + "prompts\\" + name;
    FILE* f = fopen(where.c_str(), "rb");
    if (!f) return false;
    bytes.assign((size_t)max + 1, 0);
    bytes.resize(fread(bytes.data(), 1, bytes.size(), f));
    fclose(f);
    return true;
}

prompt_tex::Replacer& replacer() {
    static prompt_tex::Replacer r(
        [](std::vector<uint8_t>& b, std::string& w) { return load_prompts_file("keytex.bin", prompt_tex::kFile, b, w); },
        [](const char* line) { logf("%s", line); });
    return r;
}

ocarina_tex::Replacer& ocarina() {
    static ocarina_tex::Replacer r(
        [](std::vector<uint8_t>& b, std::string& w) { return load_prompts_file("ocarina.bin", ocarina_tex::kFile, b, w); },
        [](const char* line) { logf("%s", line); },
        [](int sw) { return host && host->binding ? host->binding(HOS_BIND_PAD, sw, 0) : -1; });
    return r;
}

}  // namespace

void prompt_tex_announce(int value) {
    replacer().announce(value);
    ocarina().announce(value);
}
int prompt_tex_apply(uint8_t* blocks) { return replacer().apply(blocks); }
int prompt_tex_state() { return replacer().state(); }
bool prompt_tex_recognised(const uint8_t* blocks) { return replacer().recognised(blocks); }

int ocarina_tex_apply(uint8_t* blocks) { return ocarina().apply(blocks); }
int ocarina_tex_state() { return ocarina().state(); }

}  // namespace mkwvk
