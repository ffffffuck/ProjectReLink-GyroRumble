/* (relink) Button prompts for Xbox and PlayStation controllers (host/prompts.c). */
#pragma once
#include <stddef.h>
#include "../gpu/vk/vk_api.h"   /* (HOS_SW_N, HOS_PAD_*) */

/* The font-building core, which uses nothing else of the host. dir: the folder holding prompts.txt and <family>.otf
   (nx2/button_glyphs.py); family: "xbox" or "playstation"; binding: for each Switch button (HOS_SW_*), the HOS_PAD_*
   input it is bound to, -1 none. Writes the game's External font file (bfotf in a zstd frame) with each Switch button's
   glyph the label of its input, to out_path, and returns 1, note saying what each Switch button shows. Returns 0 with
   nothing written and note saying why when a file is missing or does not fit the others: the game's own font stays. */
int prompts_make(const char *dir, const char *family, const int binding[HOS_SW_N], const char *out_path, char *note,
                 size_t note_cap);
/* The label position (prompts.txt's south east west north l1 r1 l2 r2 start back l3 r3, 0 to 11) of a HOS_PAD_* input,
   -1 for an input without a label (the D-pad, Home, none) */
int prompts_position_of_pad(int pad);

/* nn_fs.c OpenFile: the file to read instead of guest's, 1 when there is one (the game's External font, with Xbox or
   PlayStation prompts chosen and no mod giving it) */
int hos_prompts_redirect(const char *guest, char *out, size_t cap);
/* rx_backend.c rx_start: the renderer told again what the font was given, when it was decided before the renderer started
   (gpu/vk/prompt_tex.h: the HUD cross's letters follow it) */
void hos_prompts_announce(void);
