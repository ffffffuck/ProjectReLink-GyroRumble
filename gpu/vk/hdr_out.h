// (relink) HDR output: what ReLink draws itself over the game's picture (the Insert menu, the frame counter, the start-up screen,
// notices) is made in 8-bit sRGB on the CPU (vk_menu.cpp's GDI bitmaps). When the window's images are HDR10 (BT.2020 primaries,
// the ST 2084 "PQ" curve, A2B10G10R10 words), those bitmaps are turned into such words before they are copied over the picture,
// at the paper white the player chose, so the menu has the same brightness as the game's own interface. Plain arithmetic, no
// Vulkan: tests/hdr_out_test.cpp checks it.
#pragma once

#include <cmath>
#include <cstdint>

namespace mkwvk::hdr_out {

/// sRGB's transfer: a stored 0..1 value to linear light (IEC 61966-2-1)
inline double srgb_to_linear(double v) {
    return v <= 0.04045 ? v / 12.92 : std::pow((v + 0.055) / 1.055, 2.4);
}

/// ST 2084 (PQ): absolute luminance in nits (0..10000) to the stored 0..1 value
inline double pq_encode(double nits) {
    const double m1 = 2610.0 / 16384.0, m2 = 2523.0 / 4096.0 * 128.0;
    const double c1 = 3424.0 / 4096.0, c2 = 2413.0 / 4096.0 * 32.0, c3 = 2392.0 / 4096.0 * 32.0;
    const double y = std::fmin(std::fmax(nits / 10000.0, 0.0), 1.0);
    const double p = std::pow(y, m1);
    return std::pow((c1 + c2 * p) / (1.0 + c3 * p), m2);
}

/// ST 2084's inverse: the stored 0..1 value to nits
inline double pq_decode(double v) {
    const double m1 = 2610.0 / 16384.0, m2 = 2523.0 / 4096.0 * 128.0;
    const double c1 = 3424.0 / 4096.0, c2 = 2413.0 / 4096.0 * 32.0, c3 = 2392.0 / 4096.0 * 32.0;
    const double e = std::pow(std::fmin(std::fmax(v, 0.0), 1.0), 1.0 / m2);
    return 10000.0 * std::pow(std::fmax(e - c1, 0.0) / (c2 - c3 * e), 1.0 / m1);
}

/// BT.709 linear RGB to BT.2020 linear RGB (ITU-R BT.2087)
inline void bt709_to_bt2020(const double in[3], double out[3]) {
    out[0] = 0.627403896 * in[0] + 0.329283039 * in[1] + 0.043313065 * in[2];
    out[1] = 0.069097289 * in[0] + 0.919540395 * in[1] + 0.011362316 * in[2];
    out[2] = 0.016391439 * in[0] + 0.088013308 * in[1] + 0.895595253 * in[2];
}

/// One 8-bit sRGB pixel (r, g, b: 0..255) at `paper_white` nits for sRGB's 1.0, as an A2B10G10R10 word (R in the low 10 bits,
/// then G, then B, alpha 3 in the top 2)
inline uint32_t srgb8_to_hdr10(uint8_t r, uint8_t g, uint8_t b, double paper_white) {
    const double lin[3] = {srgb_to_linear(r / 255.0), srgb_to_linear(g / 255.0), srgb_to_linear(b / 255.0)};
    double wide[3];
    bt709_to_bt2020(lin, wide);
    uint32_t word = 3u << 30;
    for (int i = 0; i < 3; i++) {
        const uint32_t q = (uint32_t)std::lround(pq_encode(wide[i] * paper_white) * 1023.0);
        word |= (q > 1023 ? 1023u : q) << (10 * i);
    }
    return word;
}

/// A whole BGRA8 bitmap (GDI's order: B, G, R, A in memory) turned into A2B10G10R10 words in place, at `paper_white` nits. A black
/// pixel stays the zero word but for its alpha bits (vk_menu.cpp's keyed bitmaps skip pure black runs before this)
/// The tables bgra8_to_hdr10 works from: each 8-bit channel's linear value, and the PQ code of a linear BT.2020 value 0..1 in
/// 65536 steps at one paper white (the start-up screen is the window's size and redrawn as it moves: no pow a pixel)
struct Tables {
    float lin[256];
    uint16_t pq[65536];
    double paper_white = -1;
    void make(double pw) {
        if (pw == paper_white) return;
        for (int i = 0; i < 256; i++) lin[i] = (float)srgb_to_linear(i / 255.0);
        for (int i = 0; i < 65536; i++) {
            const long q = std::lround(pq_encode(i / 65535.0 * pw) * 1023.0);
            pq[i] = (uint16_t)(q < 0 ? 0 : q > 1023 ? 1023 : q);
        }
        paper_white = pw;
    }
};
inline uint32_t hdr10_word(const Tables& t, uint8_t r, uint8_t g, uint8_t b) {
    const double in[3] = {t.lin[r], t.lin[g], t.lin[b]};
    double wide[3];
    bt709_to_bt2020(in, wide);
    uint32_t word = 3u << 30;
    for (int c = 0; c < 3; c++) {
        const double v = wide[c] < 0 ? 0 : wide[c] > 1 ? 1 : wide[c];
        word |= (uint32_t)t.pq[(int)(v * 65535.0 + 0.5)] << (10 * c);
    }
    return word;
}
inline void bgra8_to_hdr10(uint8_t* px, size_t pixels, double paper_white) {
    static Tables* t = new Tables;   // (264 KB, once; vk_menu.cpp calls this under its bitmap lock)
    t->make(paper_white);
    for (size_t i = 0; i < pixels; i++) {
        uint8_t* p = px + i * 4;
        const uint32_t word = hdr10_word(*t, p[2], p[1], p[0]);
        p[0] = (uint8_t)word, p[1] = (uint8_t)(word >> 8), p[2] = (uint8_t)(word >> 16), p[3] = (uint8_t)(word >> 24);
    }
}

/// BT.2020 linear RGB to BT.709 linear RGB (the inverse of bt709_to_bt2020)
inline void bt2020_to_bt709(const double in[3], double out[3]) {
    out[0] = 1.660491 * in[0] - 0.587641 * in[1] - 0.072850 * in[2];
    out[1] = -0.124550 * in[0] + 1.132900 * in[1] - 0.008349 * in[2];
    out[2] = -0.018151 * in[0] - 0.100579 * in[1] + 1.118730 * in[2];
}
inline double linear_to_srgb(double v) {
    v = v < 0 ? 0 : v > 1 ? 1 : v;
    return v <= 0.0031308 ? v * 12.92 : 1.055 * std::pow(v, 1 / 2.4) - 0.055;
}

/// The F12 screenshot of an HDR picture: A2B10G10R10 words (PQ, BT.2020) turned into RGBA8 sRGB in place. Paper white is sRGB's
/// 1.0; what is brighter is brought down smoothly (extended Reinhard, white at 4x the paper white) rather than clipped
inline void hdr10_to_rgba8(uint8_t* px, size_t pixels, double paper_white) {
    static double lin[1024];
    static double made_for = -1;
    if (made_for != paper_white) {
        for (int i = 0; i < 1024; i++) lin[i] = pq_decode(i / 1023.0) / paper_white;
        made_for = paper_white;
    }
    const double w2 = 16.0;
    for (size_t i = 0; i < pixels; i++) {
        uint8_t* p = px + i * 4;
        const uint32_t word = p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
        const double wide[3] = {lin[word & 1023], lin[(word >> 10) & 1023], lin[(word >> 20) & 1023]};
        double rgb[3];
        bt2020_to_bt709(wide, rgb);
        for (int c = 0; c < 3; c++) {
            const double x = rgb[c] < 0 ? 0 : rgb[c];
            p[c] = (uint8_t)std::lround(linear_to_srgb(x * (1 + x / w2) / (1 + x)) * 255.0);
        }
        p[3] = 255;
    }
}

} // namespace mkwvk::hdr_out
