"""Capture an actual SuperTuxKart race with RenderDoc, then replay and export it.

This script runs on the Linux runner, not on the student's Mac. The graphics
commands and images come from the game and the saved .rdc, not a reconstruction.
"""

import argparse
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import signal
import time

import renderdoc as rd
from PIL import Image


def action_tree(actions, structured, depth=0):
    result = []
    for action in actions:
        try:
            name = action.GetName(structured)
        except (AttributeError, TypeError):
            name = getattr(action, "customName", getattr(action, "name", ""))
        result.append({
            "event": action.eventId,
            "name": name,
            "flags": int(action.flags),
            "indices": action.numIndices,
            "instances": action.numInstances,
            "depth": depth,
        })
        result.extend(action_tree(action.children, structured, depth + 1))
    return result


def capture_game(folder, driver):
    options = rd.CaptureOptions()
    options.allowVSync = False
    command = (
        f"--render-driver={driver} --race-now --track=lighthouse --numkarts=4 "
        "--kart=tux --profile-time=180 --seed=42 --windowed --screensize=960x540 "
        "--disable-polling --shadows=1024 --enable-dynamic-lights --enable-bloom "
        "--enable-ssao --enable-ibl --enable-glow --disable-motion-blur --disable-dof"
    )
    print("GAME COMMAND:", command, flush=True)
    result = rd.ExecuteAndInject(
        "/usr/games/supertuxkart", str(folder), command, [],
        str(folder / "race"), options, False,
    )
    print("LAUNCH:", result.ident, getattr(result, "result", result), flush=True)
    if result.ident == 0:
        raise RuntimeError("RenderDoc could not launch the game")

    control = rd.CreateTargetControl("localhost", result.ident, "capture-homework", True)
    if control is None:
        raise RuntimeError("Could not connect to the launched capture target")
    pid = control.GetPID()
    if pid <= 1:
        control.Shutdown()
        raise RuntimeError("The capture target did not provide a valid owned PID")
    try:
        control.QueueCapture(80, 1)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            message = control.ReceiveMessage(None)
            if message.type == rd.TargetControlMessageType.NewCapture:
                path = Path(message.newCapture.path)
                print("CAPTURE:", path.name, path.stat().st_size, flush=True)
                return path
            if message.type == rd.TargetControlMessageType.Disconnected or not control.Connected():
                raise RuntimeError("The game exited before a capture was received")
        raise TimeoutError("No race frame was captured within five minutes")
    finally:
        control.Shutdown()
        # The PID belongs to this script's freshly launched game only.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def replay_capture(path, folder):
    capture = rd.OpenCaptureFile()
    status = capture.OpenFile(str(path), "", None)
    print("OPEN FILE:", status, flush=True)
    status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
    print("REPLAY:", status, flush=True)
    if controller is None:
        capture.Shutdown()
        raise RuntimeError(f"Cannot replay the capture: {status}")
    try:
        print("READ RESOURCES", flush=True)
        resources = controller.GetResources()
        names = {resource.resourceId: resource.name for resource in resources}
        structured = controller.GetStructuredFile()
        print("READ ACTIONS", flush=True)
        actions = action_tree(controller.GetRootActions(), structured)
        print("READ TEXTURES", flush=True)
        textures = controller.GetTextures()
        texture_data = [{
            "id": str(texture.resourceId),
            "name": names.get(texture.resourceId, ""),
            "width": texture.width,
            "height": texture.height,
            "depth": texture.depth,
            "array_size": texture.arraysize,
            "mips": texture.mips,
            "samples": texture.msSamp,
            "format": texture.format.Name(),
            "creation_flags": int(texture.creationFlags),
        } for texture in textures]
        print("REPLAY FINAL EVENT", max(action["event"] for action in actions), flush=True)
        controller.SetFrameEvent(max(action["event"] for action in actions), True)
        print("FINAL EVENT REPLAYED", flush=True)
        candidates = [texture for texture in textures
                      if texture.creationFlags & rd.TextureCategory.SwapBuffer
                      and not texture.creationFlags & rd.TextureCategory.DepthTarget]
        if not candidates:
            candidates = [texture for texture in textures
                          if "backbuffer" in names.get(texture.resourceId, "").lower()
                          and not texture.creationFlags & rd.TextureCategory.DepthTarget]
        if not candidates:
            raise RuntimeError("No final colour backbuffer was identified")
        final = max(candidates, key=lambda texture: texture.width * texture.height)
        manifest = {
            "capture": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "api": str(controller.GetAPIProperties().pipelineType),
            "frame_number": controller.GetFrameInfo().frameNumber,
            "final_image": str(final.resourceId),
            "actions": actions,
            "textures": texture_data,
            "resources": [{"id": str(resource.resourceId), "name": resource.name}
                          for resource in resources],
            "draw_count": sum(bool(action["flags"] & int(rd.ActionFlags.Drawcall))
                              for action in actions),
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with (folder / "sdk-api.txt").open("w", encoding="utf-8") as stream:
            for cls, members in [
                (rd.ReplayController, ["GetPostVSData", "GetBufferData", "GetTextureData", "DisassembleShader",
                                      "GetDisassemblyTargets", "BuildTargetShader", "ReplaceResource",
                                      "GetPipelineState", "GetGLPipelineState", "GetVulkanPipelineState"]),
                (rd.PipeState, ["GetShaderReflection", "GetShader", "GetOutputTargets",
                                "GetReadOnlyResources", "GetVertexInputs", "GetVBuffers",
                                "GetIBuffer", "GetConstantBuffer"]),
            ]:
                for member in members:
                    function = getattr(cls, member, None)
                    stream.write(f"\n{cls.__name__}.{member}\n{getattr(function, '__doc__', 'Unavailable')}\n")
        print("READ FINAL TEXTURE:", final.resourceId, final.width, final.height,
              final.format.Name(), final.msSamp, flush=True)
        pixels = bytes(controller.GetTextureData(final.resourceId, rd.Subresource(0, 0, 0)))
        print("RAW IMAGE BYTES:", len(pixels), flush=True)
        if len(pixels) != final.width * final.height * 4:
            raise RuntimeError("The final image is not a tightly packed 8-bit RGBA/BGRA image")
        order = "BGRA" if final.format.bgraOrder else "RGBA"
        image = Image.frombytes("RGBA", (final.width, final.height), pixels, "raw", order)
        if controller.GetAPIProperties().pipelineType == rd.GraphicsAPI.OpenGL:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        image.save(folder / "frame.png")
        print("VERIFIED:", manifest["api"], "draws:", manifest["draw_count"],
              "textures:", len(textures), "frame:", manifest["frame_number"], flush=True)
    finally:
        controller.Shutdown()
        capture.Shutdown()


def main():
    faulthandler.enable()
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", choices=("gl", "vulkan"), default="gl")
    args = parser.parse_args()
    folder = Path("artifacts").resolve() / args.driver
    folder.mkdir(parents=True, exist_ok=True)
    for member in ("ExecuteAndInject", "CreateTargetControl", "OpenCaptureFile"):
        print(member, getattr(rd, member).__doc__, flush=True)
    environment = rd.GlobalEnvironment()
    environment.enumerateGPUs = False
    rd.InitialiseReplay(environment, [])
    try:
        if os.environ.get("REPLAY_EXISTING"):
            paths = list(folder.glob("*.rdc"))
            if len(paths) != 1:
                raise RuntimeError("Expected exactly one downloaded capture")
            path = paths[0]
        else:
            path = capture_game(folder, args.driver)
        replay_capture(path, folder)
    finally:
        rd.ShutdownReplay()


if __name__ == "__main__":
    main()
