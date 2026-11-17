// (relink) Low-latency frame pacing: NVIDIA Reflex (VK_NV_low_latency2) and AMD Anti-Lag 2 (VK_AMD_anti_lag).
//
// On NVIDIA GPUs with VK_NV_low_latency2, uses VkSwapchainLatencyCreateInfoNV + vkLatencySleepNV + latency markers.
// On AMD GPUs with VK_AMD_anti_lag (including Linux RADV via Mesa's VK_LAYER_MESA_anti_lag), chains
// VkPhysicalDeviceAntiLagFeaturesAMD and drives vkAntiLagUpdateAMD at INPUT and PRESENT stages while routing
// non-interpolated presents onto the main graphics queue so Mesa's layer instruments graphics timestamp queries.
#include "vk_reflex.h"

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <mutex>

#include "vk_framegen.h"
#include "vk_menu.h"

#ifndef VK_AMD_anti_lag
#define VK_AMD_anti_lag 1
#define VK_AMD_ANTI_LAG_EXTENSION_NAME "VK_AMD_anti_lag"
typedef enum VkAntiLagModeAMD {
    VK_ANTI_LAG_MODE_DRIVER_CONTROL_AMD = 0,
    VK_ANTI_LAG_MODE_ON_AMD = 1,
    VK_ANTI_LAG_MODE_OFF_AMD = 2,
    VK_ANTI_LAG_MODE_MAX_ENUM_AMD = 0x7FFFFFFF
} VkAntiLagModeAMD;
typedef enum VkAntiLagStageAMD {
    VK_ANTI_LAG_STAGE_INPUT_AMD = 0,
    VK_ANTI_LAG_STAGE_PRESENT_AMD = 1,
    VK_ANTI_LAG_STAGE_MAX_ENUM_AMD = 0x7FFFFFFF
} VkAntiLagStageAMD;
typedef struct VkPhysicalDeviceAntiLagFeaturesAMD {
    VkStructureType sType;
    void* pNext;
    VkBool32 antiLag;
} VkPhysicalDeviceAntiLagFeaturesAMD;
typedef struct VkAntiLagPresentationInfoAMD {
    VkStructureType sType;
    void* pNext;
    VkAntiLagStageAMD stage;
    uint64_t frameIndex;
} VkAntiLagPresentationInfoAMD;
typedef struct VkAntiLagDataAMD {
    VkStructureType sType;
    const void* pNext;
    VkAntiLagModeAMD mode;
    uint32_t maxFPS;
    const VkAntiLagPresentationInfoAMD* pPresentationInfo;
} VkAntiLagDataAMD;
typedef void (VKAPI_PTR* PFN_vkAntiLagUpdateAMD)(VkDevice device, const VkAntiLagDataAMD* pData);
#endif

namespace mkwvk {

namespace {

constexpr VkStructureType kStAntiLagFeaturesAMD = (VkStructureType)1000476000;
constexpr VkStructureType kStAntiLagDataAMD = (VkStructureType)1000476001;
constexpr VkStructureType kStAntiLagPresInfoAMD = (VkStructureType)1000476002;

struct Reflex {
    bool have_ext = false, ready = false;
    bool have_amd_ext = false, amd_ready = false, amd_active = false;
    bool latency_next = false;           // (the swapchain being made has latency mode on: reflex_swapchain_info)
    bool latency = false;                // (the current one has)
    VkPhysicalDevicePresentIdFeaturesKHR present_id{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_FEATURES_KHR};
    VkPhysicalDeviceAntiLagFeaturesAMD amd_features{kStAntiLagFeaturesAMD, nullptr, VK_FALSE};
    VkSwapchainLatencyCreateInfoNV latency_info{VK_STRUCTURE_TYPE_SWAPCHAIN_LATENCY_CREATE_INFO_NV};
    PFN_vkSetLatencySleepModeNV set_mode = nullptr;
    PFN_vkLatencySleepNV sleep = nullptr;
    PFN_vkSetLatencyMarkerNV marker = nullptr;
    PFN_vkGetLatencyTimingsNV timings = nullptr;
    PFN_vkAntiLagUpdateAMD amd_update = nullptr;
    VkSwapchainKHR swapchain = VK_NULL_HANDLE;
    VkSemaphore wake = VK_NULL_HANDLE;   // (timeline: the sleep's end)
    uint64_t wake_value = 0;
    uint64_t id = 1;                     // (the frame's present id / frameIndex)
    int mode = -1;                       // (as set on the swapchain)
    bool frame_marked = false;           // (this frame's start markers given: its other markers count)
    VkPresentIdKHR pid{VK_STRUCTURE_TYPE_PRESENT_ID_KHR};
    std::mutex mu;
};
Reflex& rx() {
    static Reflex r;
    return r;
}

int wanted_mode() {
    static const char* e = getenv("RX_VK_REFLEX");
    return std::clamp(e && *e ? atoi(e) : opts.reflex, 0, 2);
}

/// Usable now (NVIDIA Reflex): the row on, the extension and its feature, a swapchain made with latency mode, and no framegen
bool on_nv() {
    Reflex& r = rx();
    return r.ready && r.swapchain && r.latency && wanted_mode() > 0 && !framegen_on();
}

bool on_amd() {
    Reflex& r = rx();
    return r.amd_ready && wanted_mode() > 0 && !framegen_on();
}

void mark(VkLatencyMarkerNV m) {
    Reflex& r = rx();
    VkSetLatencyMarkerInfoNV i{VK_STRUCTURE_TYPE_SET_LATENCY_MARKER_INFO_NV};
    i.presentID = r.id, i.marker = m;
    r.marker(dev.device, r.swapchain, &i);
}

void amd_mark(VkAntiLagStageAMD stage, uint64_t frame_idx) {
    Reflex& r = rx();
    VkAntiLagPresentationInfoAMD pi{kStAntiLagPresInfoAMD, nullptr, stage, frame_idx};
    VkAntiLagDataAMD d{kStAntiLagDataAMD, nullptr, VK_ANTI_LAG_MODE_ON_AMD, 0, &pi};
    r.amd_update(dev.device, &d);
}

void apply_mode() {
    Reflex& r = rx();
    const int want = wanted_mode();
    if (want == r.mode) return;
    VkLatencySleepModeInfoNV m{VK_STRUCTURE_TYPE_LATENCY_SLEEP_MODE_INFO_NV};
    m.lowLatencyMode = want >= 1, m.lowLatencyBoost = want == 2, m.minimumIntervalUs = 0;
    const VkResult res = r.set_mode(dev.device, r.swapchain, &m);
    logf("vk: NVIDIA Reflex: %s%s", want == 0 ? "off" : want == 1 ? "on" : "on + boost",
         res == VK_SUCCESS ? "" : " (the driver refused the sleep mode)");
    r.mode = want;
}

void log_timings() {
    static const bool wanted = getenv("RX_VK_REFLEX_LOG") && atoi(getenv("RX_VK_REFLEX_LOG")) > 0;
    Reflex& r = rx();
    if (!wanted || !r.timings || r.id % 300) return;
    VkGetLatencyMarkerInfoNV g{VK_STRUCTURE_TYPE_GET_LATENCY_MARKER_INFO_NV};
    r.timings(dev.device, r.swapchain, &g);
    if (!g.timingCount) return;
    std::vector<VkLatencyTimingsFrameReportNV> t(g.timingCount, {VK_STRUCTURE_TYPE_LATENCY_TIMINGS_FRAME_REPORT_NV});
    g.pTimings = t.data();
    r.timings(dev.device, r.swapchain, &g);
    double sim_present = 0, gpu_end = 0, sim = 0;
    int n = 0;
    for (uint32_t i = 0; i < g.timingCount; i++) {
        const auto& f = t[i];
        if (!f.simStartTimeUs || !f.presentEndTimeUs || f.presentEndTimeUs < f.simStartTimeUs) continue;
        sim_present += (double)(f.presentEndTimeUs - f.simStartTimeUs);
        sim += f.simEndTimeUs > f.simStartTimeUs ? (double)(f.simEndTimeUs - f.simStartTimeUs) : 0.0;
        gpu_end += f.gpuRenderEndTimeUs > f.simStartTimeUs ? (double)(f.gpuRenderEndTimeUs - f.simStartTimeUs) : 0.0;
        n++;
    }
    if (n)
        logf("vk: NVIDIA Reflex (%s): over %d frames, simulation start to present end %.1f ms, to the GPU's end %.1f ms, "
             "simulation %.1f ms", r.mode == 0 ? "off" : r.mode == 1 ? "on" : "on + boost", n, sim_present / n / 1000.0,
             gpu_end / n / 1000.0, sim / n / 1000.0);
}

}  // namespace

void reflex_enable_implicit_layer() {
#if defined(__linux__)
    const char* e = getenv("RX_VK_REFLEX");
    // Loading Mesa's implicit layer is itself observable overhead. Keep Linux
    // Anti-Lag genuinely opt-in; the benchmark/launcher requests it with 1.
    if (!e || !*e || atoi(e) <= 0) return;
    if (!getenv("DISABLE_LAYER_MESA_ANTI_LAG")) setenv("ENABLE_LAYER_MESA_ANTI_LAG", "1", 0);
#endif
}

void reflex_device_extensions(std::vector<const char*>& want, const std::vector<VkExtensionProperties>& all) {
    static const char* e = getenv("RX_VK_REFLEX");
    if (e && !strcmp(e, "-")) return;   // (RX_VK_REFLEX=-: the extensions not even enabled)
    bool ll = false, pid = false, amd = false;
    for (const auto& x : all) {
        ll |= !strcmp(x.extensionName, VK_NV_LOW_LATENCY_2_EXTENSION_NAME);
        pid |= !strcmp(x.extensionName, VK_KHR_PRESENT_ID_EXTENSION_NAME);
        amd |= !strcmp(x.extensionName, VK_AMD_ANTI_LAG_EXTENSION_NAME);
    }
    if (ll && pid) {
        for (const char* n : {VK_NV_LOW_LATENCY_2_EXTENSION_NAME, VK_KHR_PRESENT_ID_EXTENSION_NAME}) {
            bool listed = false;
            for (const char* w : want) listed |= !strcmp(w, n);
            if (!listed) want.push_back(n);
        }
        rx().have_ext = true;
        return;
    }
    if (amd) {
        bool listed = false;
        for (const char* w : want) listed |= !strcmp(w, VK_AMD_ANTI_LAG_EXTENSION_NAME);
        if (!listed) want.push_back(VK_AMD_ANTI_LAG_EXTENSION_NAME);
        rx().have_amd_ext = true;
    }
}

void reflex_device_features(void**& tail) {
    Reflex& r = rx();
    if (r.have_ext) {
        *tail = &r.present_id, tail = &r.present_id.pNext;
    } else if (r.have_amd_ext) {
        *tail = &r.amd_features, tail = &r.amd_features.pNext;
    }
}

void reflex_device_created() {
    Reflex& r = rx();
    if (r.have_ext) {
        if (!r.present_id.presentId) {
            logf("vk: NVIDIA Reflex: the device has no present ids: off");
            return;
        }
        r.set_mode = (PFN_vkSetLatencySleepModeNV)vkGetDeviceProcAddr(dev.device, "vkSetLatencySleepModeNV");
        r.sleep = (PFN_vkLatencySleepNV)vkGetDeviceProcAddr(dev.device, "vkLatencySleepNV");
        r.marker = (PFN_vkSetLatencyMarkerNV)vkGetDeviceProcAddr(dev.device, "vkSetLatencyMarkerNV");
        r.timings = (PFN_vkGetLatencyTimingsNV)vkGetDeviceProcAddr(dev.device, "vkGetLatencyTimingsNV");
        VkSemaphoreTypeCreateInfo tc{VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO};
        tc.semaphoreType = VK_SEMAPHORE_TYPE_TIMELINE;
        VkSemaphoreCreateInfo sc{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        sc.pNext = &tc;
        r.ready = r.set_mode && r.sleep && r.marker && vkCreateSemaphore(dev.device, &sc, nullptr, &r.wake) == VK_SUCCESS;
        logf("vk: NVIDIA Reflex: %s", r.ready ? "available (VK_NV_low_latency2)" : "its functions are missing: off");
        return;
    }
    if (r.have_amd_ext) {
        if (!r.amd_features.antiLag) {
            logf("vk: AMD Anti-Lag 2: antiLag feature off on device");
            return;
        }
        r.amd_update = (PFN_vkAntiLagUpdateAMD)vkGetDeviceProcAddr(dev.device, "vkAntiLagUpdateAMD");
        r.amd_ready = r.amd_update != nullptr;
        logf("vk: AMD Anti-Lag 2: %s", r.amd_ready ? "available (VK_AMD_anti_lag)" : "vkAntiLagUpdateAMD missing: off");
    }
}

bool reflex_available() { return rx().ready || rx().amd_ready; }
bool reflex_is_amd() { return rx().amd_ready && !rx().ready; }
bool reflex_use_main_queue() { return on_amd(); }

void reflex_swapchain_info(VkSwapchainCreateInfoKHR& ci) {
    Reflex& r = rx();
    r.latency_next = r.ready && wanted_mode() > 0;
    if (!r.latency_next) return;   // (Off or AMD Anti-Lag: swapchain needs no NV latency struct)
    r.latency_info.pNext = ci.pNext;
    r.latency_info.latencyModeEnable = VK_TRUE;
    ci.pNext = &r.latency_info;
}

bool reflex_swapchain_stale() {
    Reflex& r = rx();
    return r.ready && r.swapchain && r.latency != (wanted_mode() > 0);
}

void reflex_swapchain_created(VkSwapchainKHR swapchain) {
    Reflex& r = rx();
    if (!r.ready) return;
    std::lock_guard lk(r.mu);
    const bool was = r.latency;
    r.swapchain = swapchain, r.mode = -1, r.frame_marked = false, r.latency = r.latency_next;
    if (r.latency) apply_mode();
    else if (was) logf("vk: NVIDIA Reflex: off");
}

void reflex_present_begins() {
    if (!on_nv()) return;
    Reflex& r = rx();
    std::lock_guard lk(r.mu);
    if (!r.frame_marked) return;
    mark(VK_LATENCY_MARKER_SIMULATION_END_NV);
    mark(VK_LATENCY_MARKER_RENDERSUBMIT_START_NV);
}

void reflex_submit_ends() {
    if (!on_nv()) return;
    Reflex& r = rx();
    std::lock_guard lk(r.mu);
    if (r.frame_marked) mark(VK_LATENCY_MARKER_RENDERSUBMIT_END_NV);
}

const void* reflex_present_id(const void* next) {
    Reflex& r = rx();
    if (on_amd()) {
        std::lock_guard lk(r.mu);
        if (r.frame_marked) amd_mark(VK_ANTI_LAG_STAGE_PRESENT_AMD, r.id);
        return next;
    }
    if (!on_nv()) return next;
    std::lock_guard lk(r.mu);
    if (!r.frame_marked) return next;
    mark(VK_LATENCY_MARKER_PRESENT_START_NV);
    r.pid.pNext = next, r.pid.swapchainCount = 1, r.pid.pPresentIds = &r.id;
    return &r.pid;
}

void reflex_presented() {
    if (!on_nv()) return;
    Reflex& r = rx();
    std::lock_guard lk(r.mu);
    if (r.frame_marked) mark(VK_LATENCY_MARKER_PRESENT_END_NV);
}

void reflex_sleep() {
    Reflex& r = rx();
    if (r.amd_ready && !r.ready) {
        const bool want = on_amd();
        uint64_t fid = 0;
        {
            std::lock_guard lk(r.mu);
            if (!want) {
                if (r.amd_active) {
                    VkAntiLagDataAMD d{kStAntiLagDataAMD, nullptr, VK_ANTI_LAG_MODE_OFF_AMD, 0, nullptr};
                    r.amd_update(dev.device, &d);
                    r.amd_active = false;
                    r.frame_marked = false;
                    logf("vk: AMD Anti-Lag 2: off");
                }
                return;
            }
            if (!r.amd_active) {
                r.amd_active = true;
                logf("vk: AMD Anti-Lag 2: on");
            }
            fid = ++r.id;
            r.frame_marked = true;
        }
        // Outside r.mu so Mesa's os_time_nanosleep_until never holds our mutex
        amd_mark(VK_ANTI_LAG_STAGE_INPUT_AMD, fid);
        return;
    }
    if (!on_nv()) return;
    {
        std::lock_guard lk(r.mu);
        apply_mode();   // (the menu's row may have changed)
        if (r.frame_marked) log_timings(), r.id++;
        VkLatencySleepInfoNV s{VK_STRUCTURE_TYPE_LATENCY_SLEEP_INFO_NV};
        s.signalSemaphore = r.wake, s.value = ++r.wake_value;
        if (r.sleep(dev.device, r.swapchain, &s) != VK_SUCCESS) return;
    }
    VkSemaphoreWaitInfo w{VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO};
    w.semaphoreCount = 1, w.pSemaphores = &r.wake, w.pValues = &r.wake_value;
    vkWaitSemaphores(dev.device, &w, 100000000ull);   // (100 ms at most: a driver that never wakes it does not stop the game)
    std::lock_guard lk(r.mu);
    mark(VK_LATENCY_MARKER_SIMULATION_START_NV);
    mark(VK_LATENCY_MARKER_INPUT_SAMPLE_NV);
    r.frame_marked = true;
}

} // namespace mkwvk
