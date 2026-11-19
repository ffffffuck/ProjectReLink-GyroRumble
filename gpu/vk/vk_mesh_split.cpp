// (relink, issue 640) The no-mesh fallback's batches of an indirect mesh draw, on the GPU (vk_mesh_split.h). A draw runs its task
// workgroups (or, without a task, its mesh workgroups) a batch at a time, as many as the records' slots hold; an indirect draw's count
// is written by the GPU's culling, so the batches' dispatch and draw arguments are made by this small compute program from it, one
// invocation a batch. Made here with sirit (the translator's SPIR-V writer), not by the translator: nothing of it is in the shader cache.
#include "vk_mesh_split.h"

#include <mutex>
#include <vector>

#include <sirit/sirit.h>

#include "vk_internal.h"

namespace mkwvk {
namespace {

std::vector<uint32_t> split_spirv() {
    using Sirit::Id;
    Sirit::Module m{0x00010300};
    m.AddCapability(spv::Capability::Shader);
    m.AddCapability(spv::Capability::Int64);
    m.AddCapability(spv::Capability::PhysicalStorageBufferAddresses);
    m.AddExtension("SPV_KHR_physical_storage_buffer");
    m.SetMemoryModel(spv::AddressingModel::PhysicalStorageBuffer64, spv::MemoryModel::GLSL450);
    const Id void_t{m.TypeVoid()}, bool_t{m.TypeBool()};
    const Id u32{m.TypeInt(32, false)}, u64{m.TypeInt(64, false)}, u32x3{m.TypeVector(u32, 3)};
    // (the push block: MeshSplitPush, vk_mesh_split.h)
    const Id push_t{m.TypeStruct(u64, u32, u32, u32, u32)};
    m.Decorate(push_t, spv::Decoration::Block);
    const uint32_t offsets[5] = {0, 8, 12, 16, 20};
    for (uint32_t i = 0; i < 5; i++) m.MemberDecorate(push_t, i, spv::Decoration::Offset, offsets[i]);
    const Id push{m.AddGlobalVariable(m.TypePointer(spv::StorageClass::PushConstant, push_t), spv::StorageClass::PushConstant)};
    const Id invocation{m.AddGlobalVariable(m.TypePointer(spv::StorageClass::Input, u32x3), spv::StorageClass::Input)};
    m.Decorate(invocation, spv::Decoration::BuiltIn, spv::BuiltIn::GlobalInvocationId);
    const Id word_block{m.TypeStruct(u32)};
    m.Decorate(word_block, spv::Decoration::Block);
    m.MemberDecorate(word_block, 0, spv::Decoration::Offset, 0U);
    const Id word_block_ptr{m.TypePointer(spv::StorageClass::PhysicalStorageBuffer, word_block)};
    const Id word_ptr{m.TypePointer(spv::StorageClass::PhysicalStorageBuffer, u32)};
    const auto c = [&](uint32_t v) { return m.Constant(u32, v); };

    const Id main{m.OpFunction(void_t, spv::FunctionControlMask::MaskNone, m.TypeFunction(void_t))};
    m.AddLabel();
    const auto member = [&](uint32_t i, Id type) {
        return m.OpLoad(type, m.OpAccessChain(m.TypePointer(spv::StorageClass::PushConstant, type), push, c(i)));
    };
    const Id args{member(0, u64)}, per_batch{member(1, u32)}, threads{member(2, u32)}, vertices{member(3, u32)},
        batches{member(4, u32)};
    const Id batch{m.OpCompositeExtract(u32, m.OpLoad(u32x3, invocation), 0U)};
    const Id then_label{m.OpLabel()}, merge_label{m.OpLabel()};
    const Id in_range{m.OpULessThan(bool_t, batch, batches)};   // (before the merge: it must come right before its branch)
    m.OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);
    m.OpBranchConditional(in_range, then_label, merge_label);
    m.AddLabel(then_label);
    const auto word = [&](Id index) {
        const Id address{m.OpIAdd(u64, args, m.OpUConvert(u64, m.OpIMul(u32, index, c(4))))};
        return m.OpAccessChain(word_ptr, m.OpConvertUToPtr(word_block_ptr, address), c(0));
    };
    const Id count{m.OpLoad(u32, word(c(0)), spv::MemoryAccessMask::Aligned, 4U)};
    const Id first{m.OpIMul(u32, batch, per_batch)};
    // (the batch's workgroups: what is left of the count past its first, at most a batch's)
    const Id n{m.OpSelect(u32, m.OpUGreaterThan(bool_t, count, first), m.OpUMin(u32, m.OpISub(u32, count, first), per_batch), c(0))};
    const Id slots{m.OpIMul(u32, n, threads)};
    const Id at{m.OpIAdd(u32, c(kMeshSplitHeadWords), m.OpIMul(u32, batch, c(kMeshSplitBatchWords)))};
    const Id values[10] = {n, c(1), c(1), slots, c(1), c(1), m.OpIMul(u32, slots, vertices), c(1), c(0), c(0)};
    for (uint32_t i = 0; i < 10; i++) m.OpStore(word(m.OpIAdd(u32, at, c(i))), values[i], spv::MemoryAccessMask::Aligned, 4U);
    m.OpBranch(merge_label);
    m.AddLabel(merge_label);
    m.OpReturn();
    m.OpFunctionEnd();
    m.AddEntryPoint(spv::ExecutionModel::GLCompute, main, "main", invocation);   // (SPIR-V 1.3: the inputs and outputs only)
    m.AddExecutionMode(main, spv::ExecutionMode::LocalSize, 64U, 1U, 1U);
    return m.Assemble();
}

VkPipeline pipeline = VK_NULL_HANDLE;
VkPipelineLayout layout = VK_NULL_HANDLE;

} // namespace

bool mesh_split_pipeline(VkPipeline* out_pipeline, VkPipelineLayout* out_layout) {
    static std::once_flag once;
    std::call_once(once, [] {
        const std::vector<uint32_t> code = split_spirv();
        VkShaderModuleCreateInfo mi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        mi.codeSize = code.size() * 4, mi.pCode = code.data();
        VkShaderModule module = VK_NULL_HANDLE;
        if (vkCreateShaderModule(dev.device, &mi, nullptr, &module) != VK_SUCCESS || !module) {
            logf("vk: fallback: the batches' program could not be made: indirect mesh draws run their first batch only");
            return;
        }
        const VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, (uint32_t)sizeof(MeshSplitPush)};
        VkPipelineLayoutCreateInfo li{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        li.pushConstantRangeCount = 1, li.pPushConstantRanges = &range;
        vkCreatePipelineLayout(dev.device, &li, nullptr, &layout);
        VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, module, "main", nullptr};
        ci.layout = layout;
        if (!layout || vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &ci, nullptr, &pipeline) != VK_SUCCESS) {
            pipeline = VK_NULL_HANDLE;
            logf("vk: fallback: the batches' pipeline could not be made: indirect mesh draws run their first batch only");
        }
        vkDestroyShaderModule(dev.device, module, nullptr);
    });
    *out_pipeline = pipeline, *out_layout = layout;
    return pipeline != VK_NULL_HANDLE;
}

} // namespace mkwvk
