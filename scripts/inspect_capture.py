"""Inspect real replay state and perform a reversible shader replacement."""

import hashlib
import json
import math
import struct
import subprocess

import renderdoc as rd
from PIL import Image


def enum_label(namespace, value):
    for name in dir(namespace):
        if name.startswith("_"):
            continue
        member = getattr(namespace, name)
        if isinstance(member, int) and member == value:
            return name
    return str(value)


def fields(value, names):
    result = {}
    for name in names:
        member = getattr(value, name, None)
        if isinstance(member, (str, int, float, bool)) or member is None:
            result[name] = member
        elif isinstance(member, rd.ResourceFormat):
            result[name] = member.Name()
        else:
            result[name] = str(member)
    return result


def image_from_texture(controller, texture):
    raw = bytes(controller.GetTextureData(texture.resourceId, rd.Subresource(0, 0, 0)))
    fmt = texture.format
    size = (texture.width, texture.height)
    if fmt.type == rd.ResourceFormatType.BC3:
        image = Image.frombytes("RGBA", size, raw, "bcn", (3, "DXT5"))
    elif fmt.type == rd.ResourceFormatType.BC1:
        image = Image.frombytes("RGBA", size, raw, "bcn", (1, "DXT1"))
    elif fmt.compCount == 4 and fmt.compByteWidth == 1 and not fmt.Special():
        image = Image.frombytes("RGBA", size, raw, "raw", "BGRA" if fmt.BGRAOrder() else "RGBA")
    elif fmt.compCount == 1 and fmt.compByteWidth == 1:
        image = Image.frombytes("L", size, raw).convert("RGBA")
    else:
        raise ValueError(f"Unsupported texture export format: {fmt.Name()}")
    if controller.GetAPIProperties().pipelineType == rd.GraphicsAPI.OpenGL:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return image


def inspect(controller, actions, textures, final, folder):
    if controller.GetAPIProperties().pipelineType != rd.GraphicsAPI.Vulkan:
        raise ValueError("This inspection path expects the Vulkan game renderer")
    drawings = [action for action in actions if action["flags"] & int(rd.ActionFlags.Drawcall)]
    state_rows = []
    saved_shaders = {}
    for action in drawings:
        event = action["event"]
        controller.SetFrameEvent(event, True)
        pipe = controller.GetPipelineState()
        vk = controller.GetVulkanPipelineState()
        shader_ids = {}
        for stage, label in [(rd.ShaderStage.Vertex, "vertex"), (rd.ShaderStage.Pixel, "fragment")]:
            reflection = pipe.GetShaderReflection(stage)
            if reflection is None:
                continue
            shader_ids[label] = str(reflection.resourceId)
            if reflection.resourceId not in saved_shaders:
                stem = f"shader_{int(reflection.resourceId)}_{label}"
                (folder / f"{stem}.spv").write_bytes(bytes(reflection.rawBytes))
                disassembly = controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, "")
                (folder / f"{stem}.txt").write_text(disassembly, encoding="utf-8")
                saved_shaders[reflection.resourceId] = {
                    "id": str(reflection.resourceId), "stage": label, "event": event,
                    "encoding": enum_label(rd.ShaderEncoding, reflection.encoding),
                    "entry": reflection.entryPoint,
                    "input": [fields(item, ["varName", "regIndex", "compCount", "systemValue"])
                              for item in reflection.inputSignature],
                    "output": [fields(item, ["varName", "regIndex", "compCount", "systemValue"])
                               for item in reflection.outputSignature],
                    "resources": [fields(item, ["name", "bindPoint", "isTexture", "isReadOnly"])
                                  for item in reflection.readOnlyResources],
                    "constant_blocks": [fields(item, ["name", "bindPoint", "byteSize", "bufferBacked"])
                                        for item in reflection.constantBlocks],
                    "file": stem,
                }
        bound_resources = []
        for array in pipe.GetReadOnlyResources(rd.ShaderStage.Pixel, True):
            bound_resources.append({
                "binding": fields(array.bindPoint, ["bindset", "bind", "arraySize", "used"]),
                "resources": [fields(item, ["resourceId", "firstMip", "firstSlice"])
                              for item in array.resources],
            })
        row = dict(action)
        row.update({
            "pipeline": str(pipe.GetGraphicsPipelineObject()),
            "shaders": shader_ids,
            "topology": enum_label(rd.Topology, pipe.GetPrimitiveTopology()),
            "outputs": [str(item.resourceId) for item in pipe.GetOutputTargets()],
            "depth_target": str(pipe.GetDepthTarget().resourceId),
            "depth_test": vk.depthStencil.depthTestEnable,
            "depth_write": vk.depthStencil.depthWriteEnable,
            "depth_function": enum_label(rd.CompareFunction, vk.depthStencil.depthFunction),
            "blend": [{**fields(item, ["enabled", "writeMask", "logicOperationEnabled"]),
                       "colour_equation": {
                           "source": enum_label(rd.BlendMultiplier, item.colorBlend.source),
                           "destination": enum_label(rd.BlendMultiplier, item.colorBlend.destination),
                           "operation": enum_label(rd.BlendOperation, item.colorBlend.operation)}}
                      for item in pipe.GetColorBlends()],
            "vertex_attributes": [fields(item, ["name", "vertexBuffer", "byteOffset", "format",
                                                 "perInstance", "instanceRate", "used"])
                                  for item in pipe.GetVertexInputs()],
            "vertex_buffers": [fields(item, ["resourceId", "byteOffset", "byteStride", "byteSize"])
                               for item in pipe.GetVBuffers()],
            "index_buffer": fields(pipe.GetIBuffer(), ["resourceId", "byteOffset", "byteStride", "byteSize"]),
            "sampled_images": bound_resources,
            "viewport": [fields(item.vp, ["x", "y", "width", "height", "minDepth", "maxDepth"])
                         for item in vk.viewportScissor.viewportScissors],
        })
        state_rows.append(row)
    analysis = {"draws": state_rows, "shaders": list(saved_shaders.values())}
    (folder / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    print("PIPELINE INSPECTION:", len(state_rows), "draws,", len(saved_shaders), "shaders", flush=True)

    biggest = max((row for row in state_rows if row["depth_test"]), key=lambda row: row["indices"])
    controller.SetFrameEvent(biggest["event"], True)
    export_mesh(controller, biggest, folder)
    export_constants(controller, folder)
    texture_by_id = {str(texture.resourceId): texture for texture in textures}
    used_images = [item["resourceId"] for array in biggest["sampled_images"] for item in array["resources"]]
    exports = []
    for resource in dict.fromkeys(used_images):
        texture = texture_by_id.get(resource)
        if texture is None:
            continue
        filename = f"texture_{int(texture.resourceId)}.png"
        image_from_texture(controller, texture).save(folder / filename)
        exports.append({"id": resource, "event": biggest["event"], "file": filename})
    (folder / "texture_exports.json").write_text(json.dumps(exports, indent=2), encoding="utf-8")

    checkpoints = sorted({state_rows[len(state_rows) // 4]["event"],
                          state_rows[len(state_rows) // 2]["event"],
                          state_rows[3 * len(state_rows) // 4]["event"], state_rows[-1]["event"]})
    for event in checkpoints:
        controller.SetFrameEvent(event, True)
        image_from_texture(controller, final).save(folder / f"progress_e{event}.png")
    controller.SetFrameEvent(max(row["event"] for row in state_rows if row["depth_test"]), True)
    depth = next(texture for texture in textures if str(texture.resourceId) == biggest["depth_target"])
    raw_depth = bytes(controller.GetTextureData(depth.resourceId, rd.Subresource(0, 0, 0)))
    values = struct.unpack(f"<{depth.width * depth.height}f", raw_depth)
    reversed_depth = biggest["depth_function"] in ("Greater", "GreaterEqual")
    visible = values if reversed_depth else [1.0 - value for value in values]
    # A display-only curve makes distant geometry legible; original data is not changed.
    preview = bytes(max(0, min(255, int(math.pow(max(0.0, value), 0.2) * 255))) for value in visible)
    Image.frombytes("L", (depth.width, depth.height), preview).save(folder / "depth.png")
    (folder / "depth_preview.json").write_text(json.dumps({
        "resource": str(depth.resourceId), "event": max(row["event"] for row in state_rows if row["depth_test"]),
        "minimum": min(values), "maximum": max(values), "reversed_depth": reversed_depth,
        "display_curve": "pow(depth,0.2)" if reversed_depth else "pow(1-depth,0.2)"}, indent=2), encoding="utf-8")
    usage = {str(texture.resourceId): [{"event": item.eventId,
                                       "usage": enum_label(rd.ResourceUsage, item.usage)}
                                      for item in controller.GetUsage(texture.resourceId)]
             for texture in [final, depth]}
    (folder / "resource_usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
    replace_shader(controller, biggest, state_rows, final, folder)


def export_mesh(controller, draw, folder):
    mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
    print("POST VS:", mesh.numIndices, mesh.vertexByteStride, flush=True)
    if mesh.vertexResourceId == rd.ResourceId.Null() or mesh.vertexByteStride < 16:
        raise RuntimeError("No float4 post-VS position data")
    if mesh.indexResourceId != rd.ResourceId.Null():
        data = bytes(controller.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset,
                                             mesh.numIndices * mesh.indexByteStride))
        indices = list(struct.unpack(f"<{mesh.numIndices}{'H' if mesh.indexByteStride == 2 else 'I'}", data))
        indices = [index + mesh.baseVertex for index in indices]
    else:
        indices = list(range(mesh.numIndices))
    maximum = max(indices)
    raw = bytes(controller.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset,
                                        (maximum + 1) * mesh.vertexByteStride))
    positions = []
    for index in range(maximum + 1):
        point = struct.unpack_from("<4f", raw, index * mesh.vertexByteStride)
        positions.append(list(point) if all(math.isfinite(item) for item in point) else None)
    result = {"event": draw["event"], "topology": draw["topology"],
              "post_vs": fields(mesh, ["numIndices", "vertexByteStride", "indexByteStride", "baseVertex"]),
              "positions_clip": positions, "indices": indices}
    (folder / "mesh.json").write_text(json.dumps(result), encoding="utf-8")


def export_constants(controller, folder):
    pipe = controller.GetPipelineState()
    blocks = []
    def variable(item):
        result = fields(item, ["name", "rows", "columns", "type"])
        result["members"] = [variable(member) for member in item.members]
        if not item.members:
            count = item.rows * item.columns
            result["type"] = enum_label(rd.VarType, item.type)
            if item.type == rd.VarType.UInt:
                result["values"] = list(item.value.u32v)[:count]
            elif item.type == rd.VarType.SInt:
                result["values"] = list(item.value.s32v)[:count]
            else:
                values = list(item.value.f32v)[:count]
                result["values"] = [value if math.isfinite(value) else None for value in values]
        return result
    for stage, label in [(rd.ShaderStage.Vertex, "vertex"), (rd.ShaderStage.Pixel, "fragment")]:
        reflection = pipe.GetShaderReflection(stage)
        for slot, block in enumerate(reflection.constantBlocks):
            bound = pipe.GetConstantBuffer(stage, slot, 0)
            values = controller.GetCBufferVariableContents(
                pipe.GetGraphicsPipelineObject(), reflection.resourceId, stage,
                pipe.GetShaderEntryPoint(stage), slot, bound.resourceId, bound.byteOffset, bound.byteSize)
            blocks.append({"stage": label, "name": block.name, "slot": slot,
                           "binding": fields(bound, ["resourceId", "byteOffset", "byteSize"]),
                           "values": [variable(item) for item in values]})
    (folder / "constants.json").write_text(json.dumps(blocks, indent=2), encoding="utf-8")


def replace_shader(controller, chosen, draws, final, folder):
    controller.SetFrameEvent(chosen["event"], True)
    pipe = controller.GetPipelineState()
    original = pipe.GetShader(rd.ShaderStage.Pixel)
    entry = pipe.GetShaderEntryPoint(rd.ShaderStage.Pixel)
    source_path = folder / "highlight.frag"
    source_path.write_text("#version 450\nlayout(location=0) out vec4 colour;\n"
                           "void main() { colour=vec4(1.0,0.02,0.65,1.0); }\n", encoding="utf-8")
    binary_path = folder / "highlight.spv"
    subprocess.run(["glslangValidator", "-V", "-S", "frag", str(source_path), "-o", str(binary_path)], check=True)
    replacement, diagnostics = controller.BuildTargetShader(
        entry, rd.ShaderEncoding.SPIRV, binary_path.read_bytes(), rd.ShaderCompileFlags(), rd.ShaderStage.Pixel)
    if replacement == rd.ResourceId.Null():
        raise RuntimeError(f"Replacement shader failed to compile: {diagnostics}")
    last_event = max(action["event"] for action in draws)
    controller.SetFrameEvent(last_event, True)
    before = image_from_texture(controller, final)
    before.save(folder / "shader_before.png")
    try:
        controller.ReplaceResource(original, replacement)
        controller.SetFrameEvent(last_event, True)
        after = image_from_texture(controller, final)
        after.save(folder / "shader_after.png")
    finally:
        controller.RemoveReplacement(original)
        controller.SetFrameEvent(last_event, True)
        restored = image_from_texture(controller, final)
        controller.FreeTargetResource(replacement)
    before_bytes, after_bytes = before.tobytes(), after.tobytes()
    changed = sum(before_bytes[index:index + 3] != after_bytes[index:index + 3]
                  for index in range(0, len(before_bytes), 4))
    restored_equal = restored.tobytes() == before_bytes
    result = {"selected_event": chosen["event"], "shader": str(original), "entry": entry,
              "affected_draw_events": [draw["event"] for draw in draws
                                       if draw["shaders"].get("fragment") == str(original)],
              "changed_pixels": changed, "total_pixels": final.width * final.height,
              "restore_matches_original": restored_equal, "diagnostics": diagnostics,
              "before_sha256": hashlib.sha256(before_bytes).hexdigest(),
              "after_sha256": hashlib.sha256(after_bytes).hexdigest()}
    (folder / "shader_experiment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if changed == 0 or not restored_equal:
        raise RuntimeError("Shader replacement or restoration failed its pixel verification")
    print("SHADER EXPERIMENT:", changed, "pixels changed; original restored:", restored_equal, flush=True)
