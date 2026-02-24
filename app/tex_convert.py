from __future__ import annotations
import re
from app.tex_utils import extract_body

def _strip_comments(s: str) -> str:
    lines = []
    for line in s.splitlines():
        out = []
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i-1] != "\\"):
                break
            out.append(line[i])
            i += 1
        lines.append("".join(out))
    return "\n".join(lines)

def _replace_simple_commands(s: str, cmd: str, wrap_left: str, wrap_right: str) -> str:
    pat = re.compile(rf"\\{cmd}\{{([^{{}}]*)\}}")
    while True:
        new = pat.sub(lambda m: wrap_left + m.group(1) + wrap_right, s)
        if new == s:
            return s
        s = new

def _replace_href_md(s: str) -> str:
    pat = re.compile(r"\\href\{([^{}]*)\}\{([^{}]*)\}")
    while True:
        new = pat.sub(lambda m: f"[{m.group(2)}]({m.group(1)})", s)
        if new == s:
            return s
        s = new

def _replace_href_plain(s: str) -> str:
    pat = re.compile(r"\\href\{([^{}]*)\}\{([^{}]*)\}")
    while True:
        new = pat.sub(lambda m: m.group(2), s)
        if new == s:
            return s
        s = new

def _remove_env(s: str, env: str) -> str:
    s = re.sub(rf"\\begin\{{{env}\}}", "", s)
    s = re.sub(rf"\\end\{{{env}\}}", "", s)
    return s

def tex_to_markdown(tex: str) -> str:
    s = extract_body(tex)
    s = _strip_comments(s)

    s = re.sub(r"\\section\{([^{}]*)\}", lambda m: f"\n\n# {m.group(1)}\n\n", s)
    s = re.sub(r"\\subsection\{([^{}]*)\}", lambda m: f"\n\n## {m.group(1)}\n\n", s)
    s = re.sub(r"\\subsubsection\{([^{}]*)\}", lambda m: f"\n\n### {m.group(1)}\n\n", s)

    s = _replace_href_md(s)
    s = _replace_simple_commands(s, "textbf", "**", "**")
    s = _replace_simple_commands(s, "textit", "*", "*")
    s = _replace_simple_commands(s, "emph", "*", "*")

    s = _remove_env(s, "itemize")
    s = _remove_env(s, "enumerate")
    s = re.sub(r"\\item\s*", "\n- ", s)

    s = s.replace(r"\\", "\n")
    s = s.replace(r"\par", "\n\n")

    s = s.replace("{", "").replace("}", "")

    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", s)

    s = s.replace(r"\&", "&").replace(r"\%", "%").replace(r"\_", "_").replace(r"\#", "#").replace(r"\$", "$")
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s

def tex_to_text(tex: str) -> str:
    s = extract_body(tex)
    s = _strip_comments(s)

    s = re.sub(r"\\section\{([^{}]*)\}", lambda m: f"\n\n{m.group(1)}\n\n", s)
    s = re.sub(r"\\subsection\{([^{}]*)\}", lambda m: f"\n\n{m.group(1)}\n\n", s)
    s = re.sub(r"\\subsubsection\{([^{}]*)\}", lambda m: f"\n\n{m.group(1)}\n\n", s)

    s = _replace_href_plain(s)
    s = _replace_simple_commands(s, "textbf", "", "")
    s = _replace_simple_commands(s, "textit", "", "")
    s = _replace_simple_commands(s, "emph", "", "")

    s = _remove_env(s, "itemize")
    s = _remove_env(s, "enumerate")
    s = re.sub(r"\\item\s*", "\n- ", s)

    s = s.replace(r"\\", "\n")
    s = s.replace(r"\par", "\n\n")

    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", s)

    s = s.replace(r"\&", "&").replace(r"\%", "%").replace(r"\_", "_").replace(r"\#", "#").replace(r"\$", "$")
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s
