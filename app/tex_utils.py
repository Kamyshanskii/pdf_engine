from __future__ import annotations
import re

_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

def escape_tex(s: str) -> str:
    out = []
    for ch in s:
        out.append(_SPECIALS.get(ch, ch))
    return "".join(out)

def extract_body(tex: str) -> str:
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, flags=re.S)
    if m:
        return m.group(1).strip()
    return tex.strip()

def sanitize_body(body: str) -> str:
    body = re.sub(r"\\tableofcontents\b.*?\n", "", body)

    body = re.sub(
        r"(?is)\A\s*\\section\*?\{\s*(Содержание|Contents)\s*\}.*?(\\newpage|\\clearpage)\s*",
        "",
        body,
        count=1,
    )
    body = re.sub(
        r"(?is)\A\s*(Содержание|Contents)\s*\n\s*\\begin\{(itemize|enumerate)\}.*?\\end\{\2\}\s*(\\newpage|\\clearpage)\s*",
        "",
        body,
        count=1,
    )
    return body.strip()

def make_full_tex(body: str, toc: bool) -> str:
    body = sanitize_body(body)
    toc_block = r"\tableofcontents\newpage" if toc else ""
    preamble = r'''
\documentclass[12pt]{article}
\usepackage[a4paper,margin=2.5cm]{geometry}
\usepackage{fontspec}
\usepackage{polyglossia}
\setmainlanguage{russian}
\setotherlanguage{english}
\setmainfont{DejaVu Serif}
\usepackage{microtype}
\usepackage{setspace}
\setstretch{1.12}
\usepackage{parskip}
\setlength{\parindent}{0pt}
\usepackage{hyperref}
\hypersetup{colorlinks=true, linkcolor=blue, urlcolor=blue}
\usepackage{enumitem}
\setlist{nosep}
\usepackage{bookmark}
'''.strip()
    return (
        preamble
        + "\n\n\\begin{document}\n"
        + (toc_block + "\n\n" if toc_block else "")
        + body
        + "\n\n\\end{document}\n"
    )

def text_to_tex_body(text: str) -> str:
    blocks = re.split(r"\n\s*\n+", text.strip())
    parts = []
    for b in blocks:
        lines = [escape_tex(line.rstrip()) for line in b.splitlines()]
        para = r"\\\n".join(lines)
        parts.append(para)
    return "\n\n".join(parts).strip()
