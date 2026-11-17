# ARM ASTC Encoder (astcenc) 5.7.0

The core codec library of https://github.com/ARM-software/astc-encoder at tag `5.7.0` (commit `baff485`),
`Source/astcenc*.cpp` and `Source/astcenc*.h` only, unchanged. The command-line tool, tests and its third-party
image loaders are left out. Licensed under the Apache License 2.0 (`LICENSE.txt` here), which is compatible with
ReLink's GPL-3.0-or-later.

ReLink uses only its decoder (`astcenc_decompress_image`), in `gpu/vk/vk_texture.cpp`, for the ASTC textures decoded
on the CPU (AMD integrated GPUs by default, `RX_VK_ASTC_CPU=1` elsewhere): 3 to 4 times faster than suyu's decoder
on the same blocks. `RX_VK_ASTC_DECODER=suyu` goes back to suyu's.
