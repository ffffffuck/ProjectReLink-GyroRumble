/* (relink) The sticks' dead zone, for every controller path (host/pad_sdl.c, host/nn_hid.c's XInput).
   It was a square, per-axis cut at XInput's 7849 (24%): a stick that drifts past it walks Link on its own, a diagonal
   push lost one axis near the centre, and the value jumped from 0 to 24% as it left the zone.
   Here the dead zone is a circle of the Controls tab's Left / Right stick dead zone (percent of the stick's travel;
   24 by default, the old cut), and what is past it is scaled so that it starts just past the game's own dead zone
   (HOS_STICK_FLOOR, 20% by default: the mouse look's measured floor, nn_hid.c) and still reaches the edge at full tilt.
   HOS_STICK_DEADZONE_L / _R=<percent> override the menu's rows. */
#ifndef HOS_STICK_H
#define HOS_STICK_H
#include <math.h>
#include <stdint.h>
#include <stdlib.h>

static inline double hos_stick_floor(void) {
    static double floor_v = -1;
    if (floor_v < 0) {
        const char *e = getenv("HOS_STICK_FLOOR");
        int p = e && *e ? atoi(e) : 20;
        p = p < 0 ? 0 : p > 60 ? 60 : p;
        floor_v = p / 100.0;
    }
    return floor_v;
}

/* the dead zone in percent: the environment's when set, else the menu's row (rx_option), else 24 */
static inline int hos_stick_deadzone_percent(int right) {
    static int env[2] = {-2, -2};
    if (env[right] == -2) {
        const char *e = getenv(right ? "HOS_STICK_DEADZONE_R" : "HOS_STICK_DEADZONE_L");
        env[right] = e && *e ? atoi(e) : -1;
    }
    int rx_option(int id, int otherwise);
    int p = env[right] >= 0 ? env[right] : rx_option(right ? 11 : 10, 24);   /* (HOS_OPT_DEADZONE_L / _R: gpu/vk/vk_menu.h) */
    return p < 0 ? 0 : p > 90 ? 90 : p;
}

/* x, y: the stick as the controller reports it (-32768..32767); out: the same direction, 0 inside the dead zone */
static inline void hos_stick_apply(int x, int y, int percent, int32_t out[2]) {
    const double fx = (x < -32767 ? -32767 : x) / 32767.0, fy = (y < -32767 ? -32767 : y) / 32767.0;
    const double mag = sqrt(fx * fx + fy * fy), dz = percent / 100.0;
    if (mag <= dz || mag <= 0) { out[0] = out[1] = 0; return; }
    const double fl = dz > 0 ? hos_stick_floor() : 0;   /* (no dead zone: the stick as it is, no floor either) */
    const double m = mag > 1 ? 1 : mag;   /* (a square gate's corners reach past 1) */
    const double k = (fl + (m - dz) / (1 - dz) * (1 - fl)) / mag;
    double ox = fx * k * 32767, oy = fy * k * 32767;
    ox = ox > 32767 ? 32767 : ox < -32767 ? -32767 : ox;
    oy = oy > 32767 ? 32767 : oy < -32767 ? -32767 : oy;
    out[0] = (int32_t)lrint(ox), out[1] = (int32_t)lrint(oy);
}
#endif
