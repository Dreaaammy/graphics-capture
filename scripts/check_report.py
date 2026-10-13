"""Cross-check the submission against its exported, machine-readable evidence."""

import hashlib
import json
from pathlib import Path
import struct

from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evidence" / "vulkan"


def read(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main():
    manifest, analysis, experiment = read("manifest.json"), read("analysis.json"), read("shader_experiment.json")
    assert manifest["api"] == "Vulkan" and manifest["frame_number"] == 80
    assert manifest["draw_count"] == len(analysis["draws"]) == 116
    assert len(manifest["textures"]) == 83
    assert len(analysis["shaders"]) == 10
    assert len([draw for draw in analysis["draws"] if draw["instances"] > 1]) == 17
    assert len([action for action in manifest["actions"] if "vkCmdBeginRenderPass" in action["name"]]) == 1
    sky = next(draw for draw in analysis["draws"] if draw["event"] == 166)
    assert sky["indices"] == 3 and sky["depth_function"] == "Equal" and not sky["depth_write"]
    assert not next(draw for draw in analysis["draws"] if draw["event"] == 205)["depth_test"]
    assert sum(texture["format"] == "BC3_UNORM" for texture in manifest["textures"]) == 74
    for shader in analysis["shaders"]:
        binary = (DATA / (shader["file"] + ".spv")).read_bytes()
        assert struct.unpack_from("<I", binary)[0] == 0x07230203
        assert "OpEntryPoint" in (DATA / (shader["file"] + ".spvasm")).read_text(encoding="utf-8")
    assert "OpKill" in (DATA / "shader_200_fragment.spvasm").read_text(encoding="utf-8")
    before = Image.open(DATA / "shader_before.png").convert("RGBA").tobytes()
    after = Image.open(DATA / "shader_after.png").convert("RGBA").tobytes()
    assert hashlib.sha256(before).hexdigest() == experiment["before_sha256"]
    assert hashlib.sha256(after).hexdigest() == experiment["after_sha256"]
    count = sum(before[index:index + 3] != after[index:index + 3] for index in range(0, len(before), 4))
    assert count == experiment["changed_pixels"] == 207889
    assert experiment["restore_matches_original"]
    assert Image.open(DATA / "shader_restored.png").convert("RGBA").tobytes() == before
    assert manifest["capture_unchanged_after_replay"]
    mesh = read("mesh.json")
    positions = [point for point in mesh["positions_clip"] if point and point[3] > 0]
    xs, ys = [point[0] / point[3] for point in positions], [point[1] / point[3] for point in positions]
    area = (min(1, max(xs)) - max(-1, min(xs))) * (min(1, max(ys)) - max(-1, min(ys)))
    assert area > 0.05, "Choose an informative visible mesh, not sub-pixel geometry"
    pdf = PdfReader(ROOT / "output" / "pdf" / "capture_report.pdf")
    assert len(pdf.pages) == 5
    texts = [page.extract_text() for page in pdf.pages]
    assert all(len(text) > 500 for text in texts)
    assert sum(len(page.images) for page in pdf.pages) >= 10
    text = "\n".join(texts)
    assert all(term in text for term in ["Vulkan", "SPIR-V", "14.10.2026", "ReplaceResource", "207"])
    assert "40.10%" in text or "40,10%" in text
    assert manifest["sha256"] in text
    print("PASS: evidence, shader bytecode, pixel experiment, restored frame, mesh and five-page PDF")


if __name__ == "__main__":
    main()
