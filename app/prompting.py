from __future__ import annotations

def build_requirements(toc_indexes: bool, structure: bool, spelling: bool) -> str:
    lines = []
    lines.append(f"{'Требуется' if spelling else 'Запрещено'}: Проверка и исправление орфографии/пунктуации")
    lines.append(f"{'Требуется' if structure else 'Запрещено'}: Улучшение структуры: пробелы/переносы строк/табуляция")
    lines.append(f"{'Требуется' if toc_indexes else 'Запрещено'}: Оглавление")
    return "\n".join(lines)

def build_user_prompt(
    input_text_or_tex: str,
    is_tex: bool,
    toc_indexes: bool,
    structure: bool,
    spelling: bool,
    extra: str,
) -> str:
    req = build_requirements(toc_indexes, structure, spelling)
    extra_line = f'Требуется, если какие-то утверждения тут противоречат утверждениям сверху, делай так, как написано тут: "{extra}"'
    fmt = (
        "\n\nФОРМАТ ВЫВОДА:\n"
        "Верни только LaTeX (.tex) документ. Без пояснений, без code fences.\n"
        "Документ должен компилироваться LuaLaTeX/XeLaTeX.\n"
        "Используй секции \\section / \\subsection там, где это уместно.\n"
        "Если требуется 'Оглавление': НЕ вставляй \\tableofcontents и НЕ создавай оглавление вручную (не делай отдельный раздел Содержание и не пиши список ссылок). Просто размечай заголовки \\section/\\subsection — система добавит оглавление автоматически.\n"
        "Если 'Оглавление' запрещено: НЕ вставляй \\tableofcontents и НЕ добавляй ручной раздел 'Содержание'.\n"
    )
    header = "ВХОДНЫЕ ДАННЫЕ (LaTeX)" if is_tex else "ВХОДНЫЕ ДАННЫЕ (ТЕКСТ)"
    return (
        "ТРЕБОВАНИЯ К РЕДАКТИРОВАНИЮ:\n"
        + req
        + "\n\nEXTRA:\n"
        + extra_line
        + fmt
        + f"\n\n{header}:\n<<<\n{input_text_or_tex}\n>>>\n"
    )
