"""Build the illustrated Russian report from the checked RenderDoc evidence."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from PIL import Image as PILImage, ImageDraw
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, Preformatted,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evidence" / "vulkan"
INK = colors.HexColor("#152536")
TEAL = colors.HexColor("#087e8b")
MUTED = colors.HexColor("#526474")
PALE = colors.HexColor("#edf4f6")
WIDTH = A4[0] - 84


def read(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def mesh_preview():
    mesh = read("mesh.json")
    image = PILImage.new("RGB", (1440, 648), "#132435")
    draw = ImageDraw.Draw(image)
    points = []
    for point in mesh["positions_clip"]:
        if point is None or point[3] <= 0:
            points.append(None)
        else:
            # Positive Vulkan viewport height; the shader already flips projection Y.
            points.append(((point[0] / point[3] + 1) * 480,
                           (point[1] / point[3] + 1) * 270))
    visible = [point for point in points if point and -960 < point[0] < 1920 and -540 < point[1] < 1080]
    xmin, xmax = min(p[0] for p in visible), max(p[0] for p in visible)
    ymin, ymax = min(p[1] for p in visible), max(p[1] for p in visible)
    scale = min(1344 / max(1.0, xmax - xmin), 552 / max(1.0, ymax - ymin))
    centre_x, centre_y = (xmin + xmax) / 2, (ymin + ymax) / 2
    points = [((point[0] - centre_x) * scale + 720, (point[1] - centre_y) * scale + 324)
              if point else None for point in points]
    indices = mesh["indices"]
    for offset in range(0, len(indices) - 2, 3):
        triangle = [points[index] for index in indices[offset:offset + 3]]
        if any(point is None for point in triangle):
            continue
        if any(abs(point[0]) > 20000 or abs(point[1]) > 20000 for point in triangle):
            continue
        draw.line(triangle + [triangle[0]], fill="#58bdca", width=1)
    destination = DATA / "mesh_projection.png"
    image.save(destination)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-dir", type=Path, required=True)
    args = parser.parse_args()
    for name, filename in [("DV", "DejaVuSans.ttf"), ("DVB", "DejaVuSans-Bold.ttf"),
                           ("DVM", "DejaVuSansMono.ttf")]:
        pdfmetrics.registerFont(TTFont(name, str(args.font_dir / filename)))
    pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DV", boldItalic="DVB")
    # Canonical document date: midnight on 14 October 2026 in Europe/Moscow.
    os.environ["SOURCE_DATE_EPOCH"] = str(int(datetime(2026, 10, 13, 21, tzinfo=timezone.utc).timestamp()))
    rl_config.invariant = 1
    output = ROOT / "output" / "pdf" / "capture_report.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=42, leftMargin=42,
                                 topMargin=52, bottomMargin=42,
                                 title="Захват кадра SuperTuxKart - RenderDoc / Vulkan",
                                 author="Dreaaammy")
    styles = {
        "title": ParagraphStyle("title", fontName="DVB", fontSize=23, leading=29, textColor=INK, spaceAfter=9),
        "h": ParagraphStyle("h", fontName="DVB", fontSize=16, leading=21, textColor=INK, spaceAfter=11),
        "body": ParagraphStyle("body", fontName="DV", fontSize=9.3, leading=14, textColor=INK, spaceAfter=6),
        "small": ParagraphStyle("small", fontName="DV", fontSize=7.5, leading=11, textColor=MUTED, spaceAfter=7),
        "cell": ParagraphStyle("cell", fontName="DV", fontSize=8, leading=11, textColor=INK),
        "code": ParagraphStyle("code", fontName="DVM", fontSize=8, leading=12, textColor=INK),
    }
    story = []

    def paragraph(text, kind="body"):
        return Paragraph(text, styles[kind])

    def add(text, kind="body"):
        story.append(paragraph(text, kind))

    def image(name, width=WIDTH, height=None):
        path = DATA / name
        with PILImage.open(path) as bitmap:
            ratio = bitmap.height / bitmap.width
        return Image(str(path), width=width, height=height or width * ratio, mask="auto")

    def table(rows, widths, header=True):
        cells = [[paragraph(str(cell), "cell") for cell in row] for row in rows]
        widget = Table(cells, colWidths=widths, hAlign="LEFT")
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cddce1"))]
        if header:
            commands.append(("BACKGROUND", (0, 0), (-1, 0), PALE))
        widget.setStyle(TableStyle(commands))
        story.append(widget)
        story.append(Spacer(1, 9))

    def pair(left, right, left_caption, right_caption):
        half = (WIDTH - 12) / 2
        widget = Table([[image(left, half), image(right, half)],
                        [paragraph(left_caption, "small"), paragraph(right_caption, "small")]],
                       colWidths=[half + 6, half + 6])
        widget.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                   ("TOPPADDING", (0, 0), (-1, -1), 0),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(widget)
        story.append(Spacer(1, 7))

    manifest = read("manifest.json")
    analysis = read("analysis.json")
    experiment = read("shader_experiment.json")
    mesh = read("mesh.json")
    assert manifest["api"] == "Vulkan" and manifest["frame_number"] == 80
    assert experiment["restore_matches_original"] and experiment["changed_pixels"] > 0

    # Page 1: the captured frame and an explicit, honest methodology.
    add("Как SuperTuxKart<br/>собирает один кадр", "title")
    add("Домашнее задание «Захват кадра» • Dreaaammy • 14.10.2026", "small")
    table([["Vulkan / RenderDoc 1.24", f'{manifest["draw_count"]} draw calls',
            f'{len(manifest["textures"])} текстурных ресурсов']], [WIDTH / 3] * 3, header=False)
    story.append(image("frame.png"))
    add("Рис. 1. Настоящий кадр №80 игры SuperTuxKart 1.4: трасса Lighthouse, четыре карта, 960 × 540. "
        "Изображение выгружено из сохранённого .rdc, не реконструировано по исходникам.", "small")
    add("<b>Цель.</b> Проследить путь от вершин и текстур до готовой картинки и проверить, "
        "что изменение конкретного GPU-шейдера действительно меняет результат отрисовки.")
    add("<b>Среда.</b> Игра и RenderDoc запускались в Debian 12 на удалённом Linux-раннере GitHub Actions. "
        "Vulkan обрабатывался программным драйвером Mesa lavapipe (LLVM 15); это реальные команды Vulkan, "
        "но не измерение производительности игровой видеокарты. На Mac виртуальная машина не устанавливалась.")
    add("<b>Захват и анализ.</b> RenderDoc инъецировался в запущенный процесс игры и захватывал кадр №80. "
        "Автогонка профилировщика (--profile-time, --seed=42) делает запуск воспроизводимым; "
        "из-за этого HUD показывает 99999 кругов. Данные, доступные в окнах RenderDoc, исследованы через Replay API. "
        "Картинки в отчёте - экспорты захвата, а не скриншоты GUI RenderDoc.")
    add("<b>Главные находки:</b> один forward-render pass; alpha-test отдельно от прозрачности; "
        "позднее рисование skybox; инстансинг; упакованные вершины; BC3 и mipmaps; независимый HUD.")
    story.append(PageBreak())

    # Page 2: actual replay images before/after significant state transitions.
    add("01 / Порядок отрисовки", "h")
    add("В захвате один vkCmdBeginRenderPass (EID 6) с очисткой цвета и глубины. "
        "Все группы ниже рисуют в одну цветовую картинку ResourceId::129; это логические этапы "
        "внутри одного pass, а не пять отдельных Vulkan render passes.")
    pair("progress_e162.png", "progress_e166.png", "EID 162: непрозрачные объекты и alpha-test; небо ещё не нарисовано.",
         "EID 166: skybox заполнил фон, не перекрыв геометрию.")
    pair("progress_e198.png", "progress_e247.png", "EID 198: добавлены прозрачные эффекты, HUD отсутствует.",
         "EID 247: поверх 3D-сцены появились мини-карта, время и индикаторы.")
    table([["EID / draw calls", "Что подтверждает pipeline / shader"],
           ["14-140 / 74", "Непрозрачная геометрия: depth LESS, запись глубины, blending выключен."],
           ["143-162 / 11", "Alpha-test: тот же depth state, но fragment shader отбрасывает alpha &lt; 0.5."],
           ["166 / 1", "Skybox: 3 вершины, cubemap, depth EQUAL, запись глубины выключена."],
           ["171-198 / 15", "Прозрачность: depth LESS без записи; ONE / ONE_MINUS_SRC_ALPHA."],
           ["205-247 / 15", "HUD: depth test выключен; SRC_ALPHA / ONE_MINUS_SRC_ALPHA."],
           ["248-250", "EndRenderPass → конец command buffer → vkQueuePresentKHR."]], [118, WIDTH - 118])
    add("Небо рисуется после геометрии: EQUAL оставляет только фоновую глубину. Прозрачные объекты "
        "проверяют глубину, но не записывают её; для них используется premultiplied-alpha blending. "
        "Отдельных shadow-map, SSAO, bloom, G-buffer или compute-dispatch в этом кадре не обнаружено. "
        "Наличие флагов запуска эффектов не доказывает их реализацию в Vulkan-бэкенде 1.4.", "small")
    story.append(PageBreak())

    # Page 3: geometry and buffers, using data exported by RenderDoc.
    add("02 / Геометрия, инстансинг и буферы", "h")
    index_count = f'{len(mesh["indices"]):,}'.replace(",", " ")
    triangle_count = f'{len(mesh["indices"]) // 3:,}'.replace(",", " ")
    add(f'Разобран EID {mesh["event"]}: TriangleList, {index_count} индекса, один экземпляр - '
        f'{triangle_count} переданных треугольников. Индексы 16-битные. '
        "Это число входных примитивов, не число видимых на экране треугольников.")
    projection = mesh_preview()
    story.append(image(projection.name, WIDTH))
    add(f'Рис. 3. Увеличенная экранная проекция post-VS позиций EID {mesh["event"]}: x/w, y/w. Построена из '
        "GetPostVSData + GetBufferData, без depth test, с автоматическим масштабом; невидимые поверхности тоже могут попасть в рисунок. "
        "Это визуализация геометрии захвата, не окно Mesh Viewer.", "small")
    table([["Атрибут", "Смещение", "Формат GPU"],
           ["position", "0", "R32G32B32_FLOAT"], ["normal", "12", "R10G10B10A2_SNORM"],
           ["vertex colour", "16", "R8G8B8A8_UNORM"], ["UV / UV2", "20 / 24", "R16G16_FLOAT"],
           ["tangent", "28", "R10G10B10A2_SNORM"]], [123, 84, WIDTH - 207])
    add("<b>Упаковка.</b> Основная входная вершина занимает 32 байта: UV хранятся в half-float, "
        "нормали и тангенты - в упакованном SNORM. В post-VS выгрузке stride уже 64 байта: "
        "это выходы шейдера, а не исходный формат vertex buffer. Вершинный и индексный потоки "
        "сцены находятся в разных диапазонах одного GPU-буфера ResourceId::3765 (например, EID 49).")
    add("<b>Инстансинг.</b> У 17 draw calls instanceCount &gt; 1: например, EID 52 рисует 14 экземпляров, "
        "а EID 173 - 96 экземпляров индексированного quad (6 индексов). В VS 207 gl_InstanceIndex "
        "выбирает ObjectData из storage buffer: перенос, поворот, масштаб и параметры материала. "
        "Разные объекты можно рисовать одним вызовом без дублирования их сетки.")
    add("<b>Константы.</b> UBO u_camera (ResourceId::3850) имеет размер 384 байта: шесть матриц 4 × 4 "
        "по 64 байта, включая view, projection и обратные матрицы. Их значения выгружены и доступны "
        "в constants.json. Фрагментные и вершинные стадии используют пять разных модулей каждая.")
    story.append(PageBreak())

    # Page 4: real decoded resources and depth.
    add("03 / Текстуры и глубина", "h")
    third = (WIDTH - 18) / 3
    texture_rows = [[image("texture_3880.png", third, third), image("texture_3595.png", third, third),
                     image("texture_3969.png", third, third)],
                    [paragraph("3880: каменная поверхность<br/>1024², BC3, 11 mip-уровней", "small"),
                     paragraph("3595: alpha-test текстура<br/>256², BC3, 9 mip-уровней", "small"),
                     paragraph("3969: cubemap, грань 0<br/>1024² × 6, BC3, 11 mip-уровней", "small")]]
    thumbnails = Table(texture_rows, colWidths=[third + 6] * 3)
    thumbnails.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                    ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    story.append(thumbnails)
    add("Рис. 4. Mip 0 и slice 0 реальных изображений. BC3-декодирование выполнено после чтения "
        "сырых блоков через GetTextureData; прозрачный фон показан на белом листе.", "small")
    add("74 из 83 текстурных ресурсов имеют формат BC3. Блок 4 × 4 занимает 16 байт: для mip 0 "
        "изображения 1024² это 1 МиБ вместо 4 МиБ RGBA8 без сжатия. Mip-цепочка 1024 → 1 содержит "
        "11 уровней; она позволяет фильтровать уменьшенные изображения и уменьшает мерцание вдали. "
        "83 - число доступных ресурсов захвата, не число одновременных texture bindings.")
    story.append(image("depth.png", WIDTH - 60))
    add("Рис. 5. Depth attachment ResourceId::157, D32, 960 × 540, после EID 198. "
        "Для читаемости показана кривая pow(1 - depth, 0.2); это только отображение, не изменение depth buffer.", "small")
    add("<b>Alpha-test и blending - не одно и то же.</b> FS 200 делает Kill при "
        "textureAlpha × vertexAlpha &lt; 0.5, поэтому отверстия текстуры не записывают ни цвет, ни глубину. "
        "FS 209 оставляет непрерывную alpha и смешивается с уже нарисованной сценой. В pipeline "
        "у него depth write выключен. У HUD дополнительно выключен depth test.")
    add("<b>Цветовая картинка:</b> B8G8R8A8_UNORM, один sample; depth - D32. "
        "В данном кадре не найдены HDR color target, MSAA resolve или deferred G-buffer. "
        "Отсутствие этих ресурсов относится к этой конфигурации, а не ко всем режимам игры.")
    story.append(PageBreak())

    # Page 5: shader bytecode and a verified edit.
    add("04 / SPIR-V и редактирование шейдера", "h")
    add("Модули извлечены из reflection.rawBytes: SPIR-V 1.0, GLSL 450. spirv-dis даёт "
        "инструкции байткода, а DisassembleShader RenderDoc - более читаемую запись. Это "
        "промежуточный код для Vulkan, не машинный ассемблер конкретной видеокарты.")
    table([["Модуль / EID", "Что удалось прочитать"],
           ["FS 206 / 49", "OpImageSampleImplicitLod + OpFMul: выборка текстуры и умножение на vertex colour; изменение hue через RGB/HSV."],
           ["FS 200 / 143", "Условное сравнение alpha с 0.5 и OpKill: отбрасывание прозрачных фрагментов."],
           ["VS 207 / 49", "BuiltIn InstanceIndex, ObjectData и camera matrix: преобразование позиции в clip space."],
           ["FS 205 / 166", "Выборка cubemap по восстановленному направлению взгляда."]], [106, WIDTH - 106])
    pair("shader_before.png", "shader_after.png", "До: исходный replay кадра.",
         "После: замена FS 206; skybox, alpha-test и HUD остаются со своими шейдерами.")
    add("В RenderDoc создан SPIR-V replacement через BuildTargetShader, затем ReplaceResource. "
        "Новый fragment shader выводит постоянный цвет в location 0:")
    story.append(Preformatted("#version 450\nlayout(location=0) out vec4 colour;\nvoid main() { colour = vec4(1.0, 0.02, 0.65, 1.0); }", styles["code"]))
    story.append(Spacer(1, 8))
    percent = 100 * experiment["changed_pixels"] / experiment["total_pixels"]
    changed = f'{experiment["changed_pixels"]:,}'.replace(",", " ")
    total = f'{experiment["total_pixels"]:,}'.replace(",", " ")
    percentage = f'{percent:.2f}'.replace(".", ",")
    add(f'<b>Проверка результата.</b> Изменились {changed} из '
        f'{total} пикселей ({percentage}%). Модуль используется '
        f'в {len(experiment["affected_draw_events"])} draw calls, поэтому меняется не только EID 49. '
        "После RemoveReplacement исходный RGBA-кадр восстановился побайтово; файл .rdc не изменён.")
    table([["Окно RenderDoc", "Исследованные данные / эквивалент Replay API"],
           ["Event Browser", "Порядок и типы событий: GetRootActions, SetFrameEvent."],
           ["Pipeline State", "VS/FS, depth, blending, vertex layout, descriptors."],
           ["Texture / Resource Inspector", "Цвет, depth, mip/slice, форматы и история использования."],
           ["Mesh / Buffer Viewer", "Индексы, post-VS позиции, vertex stride и camera UBO."],
           ["Shader Viewer", "Reflection, SPIR-V, disassembly и replacement."]], [138, WIDTH - 138])
    add("<b>Итог.</b> Прослежен реальный traditional graphics pipeline и выполнено редактирование "
        "шейдера с проверкой до/после и отката. Сырые данные, SPIR-V, картинки, скрипты и исходный "
        "захват приложены в публичном репозитории; выводы можно перепроверить по EID и ResourceId.", "small")
    add('<link href="https://github.com/Mrkol/graphics-course/blob/master/tasks/capture/README.md">Задание</link> • '
        '<link href="https://github.com/supertuxkart/stk-code/tree/1.4">SuperTuxKart 1.4</link> • '
        '<link href="https://github.com/supertuxkart/stk-code/blob/1.4/data/CREDITS">Авторы игры и ассетов</link> • '
        '<link href="https://github.com/baldurk/renderdoc/tree/v1.24/docs/python_api/examples/renderdoc">RenderDoc Replay API</link> • '
        '<link href="https://github.com/Dreaaammy/graphics-capture">Материалы отчёта</link>', "small")
    add(f'SHA-256 исходного .rdc: {manifest["sha256"]}', "small")

    def decoration(canvas, doc):
        canvas.setFillColor(MUTED)
        canvas.setFont("DV", 7.5)
        canvas.drawString(42, A4[1] - 30, "GRAPHICS COURSE / CAPTURE / SUPERTUXKART")
        canvas.setStrokeColor(TEAL)
        canvas.setLineWidth(1)
        canvas.line(42, A4[1] - 36, A4[0] - 42, A4[1] - 36)
        canvas.drawString(42, 24, "Dreaaammy • RenderDoc / Vulkan • 14.10.2026")
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))

    document.build(story, onFirstPage=decoration, onLaterPages=decoration)
    print(output)


if __name__ == "__main__":
    main()
