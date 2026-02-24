from __future__ import annotations
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from app.config import settings
from app.logger import get_logger

log = get_logger("latex")

class LatexCompileError(RuntimeError):
    pass

def compile_tex_to_pdf(tex_source: str, out_pdf_path: str, toc: bool) -> None:
    tmp_root = Path(settings.tmp_dir)
    tmp_root.mkdir(parents=True, exist_ok=True)
    workdir = tmp_root / f"tex_{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        tex_path = workdir / "main.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        engine = settings.latex_engine or "lualatex"
        max_runs = max(1, min(5, settings.latex_max_runs))
        runs = 2 if toc else 1
        runs = min(runs, max_runs)

        cmd = [
            engine,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-no-shell-escape",
            "main.tex",
        ]

        last_out = ""
        for i in range(runs):
            proc = subprocess.run(
                cmd,
                cwd=str(workdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            last_out = proc.stdout[-8000:]
            if proc.returncode != 0:
                raise LatexCompileError(last_out)

        pdf_src = workdir / "main.pdf"
        if not pdf_src.exists():
            raise LatexCompileError("PDF not produced. Output:\n" + last_out)

        out_path = Path(out_pdf_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf_src, out_path)
        log.info("Compiled PDF -> %s", out_pdf_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
