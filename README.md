# Захват кадра

Отчёт по [заданию capture](https://github.com/Mrkol/graphics-course/blob/master/tasks/capture/README.md).

Игра: SuperTuxKart 1.4. API: Vulkan. Инструмент: RenderDoc. Трасса Lighthouse, кадр 80, разрешение 960 × 540.

- [Отчёт с иллюстрациями (PDF)](output/pdf/capture_report.pdf)
- [Исходный захват RenderDoc (.rdc)](https://github.com/Dreaaammy/graphics-capture/releases/tag/report-v1)
- [Изображения, данные геометрии и байткод шейдеров](evidence/vulkan/)

В отчёте рассмотрены порядок отрисовки, геометрия сцены, текстуры, состояния пайплайна и SPIR-V. Приведён эксперимент с заменой фрагментного шейдера и сравнением результата до и после изменения.

Материалы игры: [SuperTuxKart](https://github.com/supertuxkart/stk-code/tree/1.4), [авторы](https://github.com/supertuxkart/stk-code/blob/1.4/data/CREDITS), [лицензии](https://github.com/supertuxkart/stk-code/blob/1.4/COPYING).
