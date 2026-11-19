// gpu/vk/hdr_out.h against values published for the PQ curve (ITU-R BT.2100, BT.2408: HDR reference white 203 cd/m2 is 58 % of
// the PQ signal) and the BT.709 -> BT.2020 matrix's own properties (white stays white, each row sums to 1).
//   clang++ -std=c++20 -O1 -I.. hdr_out_test.cpp -o hdr_out_test && ./hdr_out_test
#include "../hdr_out.h"

#include <cstdio>
#include <cstdlib>

using namespace mkwvk::hdr_out;

static int failed = 0;
static void near(const char* what, double got, double want, double tol) {
    const bool ok = std::fabs(got - want) <= tol;
    if (!ok) failed++;
    std::printf("%-44s %.5f (want %.5f +- %.5f) %s\n", what, got, want, tol, ok ? "ok" : "FAILED");
}

int main() {
    near("PQ of 0 nits", pq_encode(0), 0.0, 1e-6);
    near("PQ of 100 nits", pq_encode(100), 0.5081, 0.0005);
    near("PQ of 203 nits (BT.2408: 58 %)", pq_encode(203), 0.58, 0.001);
    near("PQ of 1000 nits", pq_encode(1000), 0.7518, 0.0005);
    near("PQ of 10000 nits", pq_encode(10000), 1.0, 1e-9);
    near("PQ round trip at 350 nits", pq_decode(pq_encode(350)), 350, 0.01);
    double w[3];
    const double white[3] = {1, 1, 1};
    bt709_to_bt2020(white, w);
    near("BT.2020 white R", w[0], 1, 1e-6);
    near("BT.2020 white G", w[1], 1, 1e-6);
    near("BT.2020 white B", w[2], 1, 1e-6);
    const double red[3] = {1, 0, 0};
    bt709_to_bt2020(red, w);
    near("BT.709 red in BT.2020, R", w[0], 0.6274, 0.0001);
    // (the word: white at 203 nits is 0.58 * 1023 in each channel, alpha 3)
    const uint32_t word = srgb8_to_hdr10(255, 255, 255, 203);
    const int code = (int)std::lround(pq_encode(203) * 1023);
    near("white word R", word & 1023, code, 0);
    near("white word G", (word >> 10) & 1023, code, 0);
    near("white word B", (word >> 20) & 1023, code, 0);
    near("white word alpha", word >> 30, 3, 0);
    near("black word colour", srgb8_to_hdr10(0, 0, 0, 203) & 0x3fffffff, 0, 0);
    // (the table version against the exact one, over every grey and a few colours: at most one code apart)
    int worst = 0;
    for (int v = 0; v < 256; v += 1)
        for (int kind = 0; kind < 3; kind++) {
            const uint8_t u = (uint8_t)v;
            const uint8_t c[3] = {kind == 2 ? (uint8_t)255 : u, kind == 0 ? u : kind == 1 ? (uint8_t)40 : u,
                                  kind == 0 ? u : kind == 1 ? (uint8_t)200 : (uint8_t)0};
            uint8_t px[4] = {c[2], c[1], c[0], 255};
            bgra8_to_hdr10(px, 1, 203);
            const uint32_t t = px[0] | px[1] << 8 | px[2] << 16 | (uint32_t)px[3] << 24;
            const uint32_t e = srgb8_to_hdr10(c[0], c[1], c[2], 203);
            for (int k = 0; k < 3; k++) {
                const int d = std::abs((int)((t >> (10 * k)) & 1023) - (int)((e >> (10 * k)) & 1023));
                if (d > worst) worst = d;
            }
        }
    near("table against exact, worst code difference", worst, 0, 1);
    std::printf(failed ? "%d FAILED\n" : "all ok\n", failed);
    return failed != 0;
}
