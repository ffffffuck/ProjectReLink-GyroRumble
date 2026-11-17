/* (relink) The Insert menu's Volume (gpu/vk/vk_menu.cpp, Gameplay tab), between the menu and the game's sound (host/nn_audio.c),
   C and C++. rx_option(HOS_OPT_VOLUME, 100) from the game side: the game's whole sound in percent, 0 to 100, read as the mix
   is made, so a change is heard at once. Here and not in vk_api.h: that header is part of the shader cache's stamp, and a
   change to it makes every player prepare the shaders again */
#pragma once
#define HOS_OPT_VOLUME 44
