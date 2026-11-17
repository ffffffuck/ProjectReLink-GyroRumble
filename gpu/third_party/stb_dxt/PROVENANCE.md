# stb_dxt v1.13

`stb_dxt.h` from https://github.com/nothings/stb at commit `48fb1c9709f973041a7e6992d720ac625266cdd3`, unchanged. Public domain or MIT
(its own text, at the end of the file). ReLink uses it to keep the ASTC textures decoded on the CPU as BC3
(`gpu/vk/vk_texture.cpp`, `astc_bc3_cpu`), a quarter of their RGBA8 size.
