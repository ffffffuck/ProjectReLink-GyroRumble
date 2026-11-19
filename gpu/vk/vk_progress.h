// (relink) The start-up screen's numbers (vk_main.cpp, vk_menu.cpp): how far the shaders' preparation is, and about how long
// it has left. Plain C++, no Vulkan: tests/test_startup_progress.py compiles it on its own.
#pragma once

#include <cstddef>
#include <cstdio>
#include <algorithm>
#include <string>

namespace mkwvk {

/// The time left, from the rate over the last few seconds, not from the start: a start after one that was closed part-way skips
/// what is on disk already in a second or two, and that burst would have promised the rest at the same speed. Nothing is said
/// before a sixth is done (the programs differ a lot: on an RTX 3090 the first 1,500 of 32,000 took half a minute, the next 10,000
/// ten seconds, and the early estimates said 83 minutes for a 70-second job), and what is said is smoothed over about 8 s
/// (it went 1 min, 5 min, 1 min from one 10-second line to the next)
struct ProgressClock {
    static constexpr int kSamples = 64;
    static constexpr double kWindow = 15.0;    // (seconds the rate is taken over)
    static constexpr double kFirst = 3.0;      // (seconds of samples before any estimate)
    static constexpr double kMinShare = 1.0 / 6;
    static constexpr double kSmooth = 8.0;     // (seconds: the estimate's time constant)
    double t[kSamples]{};
    size_t done[kSamples]{};
    int n = 0, head = 0;   // (the samples, oldest at head)
    double last_note = -1, shown = -1;

    void reset() { n = head = 0, last_note = shown = -1; }
    /// A sample: at seconds t_now, done of total (four a second at most are kept)
    void note(double t_now, size_t done_now, size_t total) {
        if (n && done_now < done[(head + n - 1) % kSamples]) reset();   // (a new stage)
        if (last_note >= 0 && t_now - last_note < 0.25) return;
        const double dt = last_note < 0 ? 0 : t_now - last_note;
        last_note = t_now;
        if (n == kSamples) head = (head + 1) % kSamples, n--;
        t[(head + n) % kSamples] = t_now, done[(head + n) % kSamples] = done_now, n++;
        while (n > 2 && t_now - t[(head + 1) % kSamples] >= kWindow) head = (head + 1) % kSamples, n--;
        const double raw = estimate(total);
        if (raw < 0) shown = -1;
        else if (shown < 0 || raw == 0) shown = raw;   // (the first one, or all done)
        else {
            shown = shown > dt ? shown - dt : 0;   // (time passing)
            shown += (raw - shown) * (dt < kSmooth ? dt / kSmooth : 1.0);
        }
    }
    /// Seconds left, or a negative number when it cannot be said yet
    double left() const { return shown; }
    /// The rate over the window alone, unsmoothed
    double estimate(size_t total) const {
        if (n < 2) return -1;
        const int last = (head + n - 1) % kSamples;
        const double span = t[last] - t[head];
        if (done[last] >= total) return 0;
        if (span < kFirst || done[last] <= done[head] || (double)done[last] < (double)total * kMinShare) return -1;
        const double rate = (double)(done[last] - done[head]) / span;
        return (double)(total - done[last]) / rate;
    }
};

/// 12345 as "12,345"
inline std::string with_commas(size_t v) {
    std::string s = std::to_string(v);
    for (int i = (int)s.size() - 3; i > 0; i -= 3) s.insert((size_t)i, ",");
    return s;
}

/// "about 40 s left": up to 5 s under a minute, whole minutes above; empty when unknown
inline std::string left_text(double secs) {
    if (secs < 0) return "";
    char b[48];
    if (secs < 10) return "a few seconds left";
    if (secs < 55) snprintf(b, sizeof b, "about %d s left", (int)((secs + 4.999) / 5) * 5);
    else snprintf(b, sizeof b, "about %d min left", secs < 90 ? 1 : (int)(secs / 60 + 0.5));
    return b;
}

/// The version line from the build's name (nx2/version.py, "v2.0.5 (source 1a2b3c4d)"): the release alone, "v2.0.5"; a build
/// between releases ("v2.0.5-2-g619c897-dirty", git describe) shows as "v2.0.5 dev"; anything else as it is
inline std::string version_text(std::string name) {
    if (const size_t at = name.find(" ("); at != std::string::npos) name.resize(at);
    size_t i = 0;
    if (name.size() > 1 && name[0] == 'v') {
        int dots = 0;
        for (i = 1; i < name.size() && ((name[i] >= '0' && name[i] <= '9') || name[i] == '.'); i++) dots += name[i] == '.';
        if (dots == 2 && i < name.size() && name[i] == '-') return name.substr(0, i) + " dev";
    }
    return name;
}

/// "32%": rounded down, and 99% until all is done
inline std::string percent_text(size_t done, size_t total) {
    if (!total) return "";
    size_t p = done >= total ? 100 : done * 100 / total;
    if (p > 99 && done < total) p = 99;
    return std::to_string(p) + "%";
}

/// What the start-up screen shows (vk_startup_paint.h draws it): the project's mark being written, one number and one line
struct StartupView {
    std::string version;            // ("v2.0.5"; empty: the build's)
    std::string label, pct;         // ("LOADING", "STARTING", "CLOSING"; "32"); an empty label: the mark alone
    std::string right;              // (after what is being done: "about 6 min left")
    std::string gpu;                // (the graphics card the pipelines are made for: "AMD Radeon RX 6600 XT")
    int stage = -1;                 // (0 the shader programs, 1 and 2 the pipelines)
    double fraction = -1;           // (how much of the staff is gold, 0 to 1; negative: nothing counted yet)
    double anim = 0;                // (seconds since the window opened: the staff opening out, the note waiting)
    double outro = -1;              // (seconds since a start that showed its staff ended: the gold finishing it, the staff
                                    // closing into the mark, its pulse; negative: not ended)
    bool operator==(const StartupView&) const = default;
};

/// The state of the start: stage 0 the shader programs, 1 the pipelines the notes predict, 2 the noted pipelines; done of total
/// in it (total 0: not counted yet); left the seconds left of that stage (negative: unknown); prepared: this start prepares
/// programs (a first start, or the first after an update: the three stages, else the last alone); secs since the window opened
struct StartupState {
    int stage = 0;
    size_t done = 0, total = 0;
    double left = -1, secs = 0;
    double since_finished = -1;   // (seconds since the work ended, a long start's last moment: the mark closing)
    bool prepared = false, finished = false, stopping = false;
    bool staff_shown = false;     // (the screen showed the staff open: its ending is played in full, kStartupOutro)
};

/// How far the whole start is, 0 to 1. A long start's three stages by about the share of its time each took on the development
/// PC (538 s, 93 s and 360 s): the programs to 0.55, the predicted pipelines to 0.65, the noted ones to the end. A start that
/// prepares nothing new has the last stage alone
inline double startup_overall(const StartupState& s) {
    const double f = s.total ? (double)std::min(s.done, s.total) / (double)s.total : 0;
    if (!s.prepared) return s.stage > 0 ? f : 0;
    return s.stage == 0 ? 0.55 * f : s.stage == 1 ? 0.55 + 0.10 * f : 0.65 + 0.35 * f;
}

/// The time left of the whole start: the stage's own estimate, and for the stages after it the time their share would take at
/// the speed of the start so far. Negative: cannot be said yet
inline double startup_left(const StartupState& s) {
    if (s.left < 0) return -1;
    if (!s.prepared || s.stage >= 2) return s.left;
    const double overall = startup_overall(s), after = s.stage == 0 ? 0.45 : 0.35;
    if (overall < 0.03 || s.secs < 5) return -1;
    return s.left + after * s.secs / overall;
}

/// The screen for a state. The first second shows the mark alone: a start with next to nothing to prepare is over by then. A
/// start that showed its staff ends in full whenever the work ended (outro: the painter keeps drawing for kStartupOutro
/// seconds before the game starts): the gold finishes the staff (0.7 s), the staff closes into the mark (1 s), the mark
/// pulses (0.4 s) and stays a moment
constexpr double kStartupOutro = 2.3;
inline StartupView startup_view(const StartupState& s) {
    StartupView v;
    v.anim = s.secs;
    v.stage = s.stage;
    if (s.stopping) {
        v.label = "CLOSING";
        return v;
    }
    if (s.finished) {
        if (s.secs >= 1.0) v.label = "STARTING", v.fraction = 1, v.pct = "100";
        if (s.staff_shown && s.since_finished >= 0 && s.secs >= 1.0) v.outro = s.since_finished;
        return v;
    }
    if (s.secs < 1.0) return v;
    v.label = "LOADING";
    if (!s.total && !(s.prepared && s.stage > 0)) return v;   // (nothing counted yet: the note waits at the start)
    const double overall = startup_overall(s);
    v.fraction = overall;
    size_t p = (size_t)(overall * 100);
    if (p > 99) p = 99;
    v.pct = std::to_string(p);
    v.right = left_text(startup_left(s));
    return v;
}

} // namespace mkwvk
