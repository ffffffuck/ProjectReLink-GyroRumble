// (relink, issue 640) The no-mesh fallback's batches of an indirect mesh draw, their arguments made on the GPU (vk_mesh_split.cpp).
// The arguments buffer: word 0 the draw's count of task workgroups (or mesh workgroups without a task), copied there from the game's
// indirect record; then, from word kMeshSplitHeadWords, kMeshSplitBatchWords words a batch: the task dispatch (x, 1, 1), the mesh
// dispatch (slots, 1, 1) and the vertex draw (slots * vertices, 1, 0, 0), where a batch runs min(count - first, per_batch) workgroups
// and slots = that * threads (1 for a mesh program without a task)
#pragma once

#include <cstdint>

#include "vk_loader.h"

namespace mkwvk {

constexpr uint32_t kMeshSplitHeadWords = 4;    // (the count, and padding: the batches' records start 16-byte aligned)
constexpr uint32_t kMeshSplitBatchWords = 12;  // (3 + 3 + 4, and padding)
constexpr uint32_t kMeshSplitTaskWord = 0, kMeshSplitMeshWord = 3, kMeshSplitDrawWord = 6;

struct MeshSplitPush {
    uint64_t args;         // (the arguments buffer's device address)
    uint32_t per_batch;    // (workgroups a batch runs)
    uint32_t threads;      // (slots a workgroup takes: the task's threads, or 1)
    uint32_t vertices;     // (vertices a slot draws)
    uint32_t batches;      // (batches made: one invocation each)
};

/// The program that makes the batches' arguments (made once; false when it could not be)
bool mesh_split_pipeline(VkPipeline* pipeline, VkPipelineLayout* layout);

} // namespace mkwvk
