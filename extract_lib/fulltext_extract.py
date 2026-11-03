"""Method A — full-text baseline: the raw PDF text, unlabeled and uncleaned,
in one LLM call. Shares the record format, rules and post-processing with
methods B and C (see record_extract.py)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from extract_lib.record_extract import load_instructions

def fulltext_pages(pdf_path: Path) -> List[str]:
    """Raw text per page, exactly as PyMuPDF returns it (pypdf fallback)."""
    try:
        import fitz  # PyMuPDF
        with fitz.open(str(pdf_path)) as doc:
            pages = [page.get_text() or "" for page in doc]
        if any(p.strip() for p in pages):
            return pages
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return [p.extract_text() or "" for p in PdfReader(str(pdf_path)).pages]
    except Exception:
        return []

def build_fulltext_prompt(paper_id: str, pages: List[str]) -> Tuple[str, str]:
    """(system, user) messages for method A."""
    user = (f"paper_id: {paper_id}\n\n"
            f"Full text of the paper:\n\n" + "\n".join(pages))
    return load_instructions("A"), user
