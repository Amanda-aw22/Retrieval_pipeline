"""Schema-free property-record extraction.

One record per reported value: sample, property (umbrella name), value, unit,
test condition, application, verbatim evidence and its location. The LLM
instructions live in property_record_instructions.md; this module builds the
prompt, then validates the answer and fills page / section / heading from the
cited passage so locations never depend on the LLM.
"""
from __future__ import annotations

import csv, json, re, unicodedata
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from extract_lib.retrieval import Paragraph, Source, build_paragraphs

INSTRUCTIONS_PATH = Path(__file__).with_name("property_record_instructions.md")
# Cross-paper umbrella names: papers are extracted independently, so the same
# quantity can surface under different names; this map folds them together.
UMBRELLA_MAP_PATH = Path(__file__).with_name("property_umbrella_map.json")

def load_umbrella_map(path: Path = UMBRELLA_MAP_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}

def apply_umbrella(rec: Dict[str, Any], umbrella: Dict[str, Any]) -> None:
    """Rename rec["property"] per the map; a dict entry may also append a
    qualifier to test_condition. Unmapped names are left as they are."""
    target = umbrella.get(rec["property"])
    if not target:
        return
    if isinstance(target, str):
        rec["property"] = target
        return
    rec["property"] = target.get("property") or rec["property"]
    extra = (target.get("add_condition") or "").strip()
    if extra and extra.lower() not in rec["test_condition"].lower():
        rec["test_condition"] = f"{rec['test_condition']}; {extra}" if rec["test_condition"] else extra

RECORD_FIELDS = ["sample", "material_class", "property", "property_as_written",
                 "value", "unit", "test_condition", "application", "evidence", "passage"]
REQUIRED_FIELDS = ("sample", "property", "value", "evidence")
# Only this paper's own materials are kept. Answers that still tag records with
# a non-this-work role (older prompt versions, or the LLM ignoring §4) are dropped.
EXCLUDED_ROLES = {"control", "comparison", "reference", "prior_work"}
CSV_COLUMNS = ["paper_id", "paper_type", "sample", "material_class", "property",
               "property_as_written", "value", "unit", "test_condition", "application",
               "evidence", "page", "section", "heading", "passage", "evidence_verified"]

_SECTION_TITLES = {"front": "Front matter", "abstract": "Abstract",
                   "introduction": "Introduction", "experimental": "Experimental",
                   "results": "Results", "conclusion": "Conclusion"}

def load_instructions() -> str:
    return INSTRUCTIONS_PATH.read_text(encoding="utf-8")

def record_passages(source: Source) -> List[Paragraph]:
    """All passages except back matter (references, acknowledgements, ...)."""
    return build_paragraphs(source)

def _label(i: int, p: Paragraph) -> str:
    parts = [f"Passage {i}"]
    if p.page:
        parts.append(f"page {p.page}")
    if p.section in _SECTION_TITLES:
        parts.append(_SECTION_TITLES[p.section])
    if p.heading and p.section not in ("abstract", "front"):
        parts.append(" ".join(p.heading.split())[:80])
    if p.is_table:
        parts.append("TABLE")
    if p.n_citations:
        parts.append("cites prior work")
    return "[" + " | ".join(parts) + "]"

def render_passages(paras: Sequence[Paragraph]) -> str:
    return "\n\n".join(f"{_label(i, p)} {p.text}" for i, p in enumerate(paras, 1))

def build_record_prompt(paper_id: str, paras: Sequence[Paragraph]) -> Tuple[str, str]:
    """(system, user) messages."""
    user = (f"paper_id: {paper_id}\n\n"
            f"Passages from this paper (references and acknowledgements removed):\n\n"
            f"{render_passages(paras)}")
    return load_instructions(), user

# Validation
_DASHES = dict.fromkeys(map(ord, "−–—‐‑"), "-")

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("\xad", "").translate(_DASHES)
    return re.sub(r"\s+", " ", s).strip().lower()

def _locate(quote: str, paras: Sequence[Paragraph], hint: int) -> int:
    """1-based passage containing the quote (prefer the cited one), or 0."""
    q = _norm(quote).rstrip("…. ")
    if not q:
        return 0
    if 1 <= hint <= len(paras) and q in _norm(paras[hint - 1].text):
        return hint
    for i, p in enumerate(paras, 1):
        if q in _norm(p.text):
            return i
    return 0

def normalize_records(raw: Dict[str, Any], paper_id: str,
                      paras: Sequence[Paragraph],
                      umbrella: Dict[str, Any] = None) -> Dict[str, Any]:
    """Clean an LLM answer: string fields, umbrella property names, normalized
    units, control/comparison records dropped, required fields present,
    evidence verified against the
    passages, page/section/heading attached."""
    if umbrella is None:
        umbrella = load_umbrella_map()
    out: List[Dict[str, Any]] = []
    for r in (raw.get("records") or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("role") or "").strip().lower() in EXCLUDED_ROLES:
            continue
        rec = {k: ("" if r.get(k) is None else str(r.get(k)).strip())
               for k in RECORD_FIELDS if k != "passage"}
        if any(not rec[k] for k in REQUIRED_FIELDS):
            continue
        rec["property"] = re.sub(r"[^a-z0-9]+", "_", rec["property"].lower()).strip("_")
        apply_umbrella(rec, umbrella)
        rec["unit"] = unicodedata.normalize("NFKC", rec["unit"])   # "℃" -> "°C"
        try:
            hint = int(r.get("passage") or 0)
        except (TypeError, ValueError):
            hint = 0
        idx = _locate(rec["evidence"], paras, hint)
        p = paras[idx - 1] if idx else (paras[hint - 1] if 1 <= hint <= len(paras) else None)
        rec.update({
            "passage": idx or hint,
            "page": p.page if p else "",
            "section": p.section if p else "",
            "heading": " ".join(p.heading.split()) if p and p.heading else "",
            "evidence_verified": bool(idx),
        })
        out.append(rec)
    return {"paper_id": paper_id, "paper_type": str(raw.get("paper_type") or ""),
            "records": out}

# Output
def write_outputs(papers: Sequence[Dict[str, Any]], out_dir: Path) -> Tuple[Path, Path]:
    """records.jsonl (one line per paper) + records.csv (one row per record;
    papers without records get one row with empty record fields)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl, csv_path = out_dir / "records.jsonl", out_dir / "records.csv"
    with open(jsonl, "w", encoding="utf-8") as f:
        for p in papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for p in papers:
            base = {"paper_id": p["paper_id"], "paper_type": p.get("paper_type", "")}
            if not p["records"]:
                w.writerow(base)
            for r in p["records"]:
                w.writerow({**base, **{k: r.get(k, "") for k in CSV_COLUMNS if k in r}})
    return jsonl, csv_path
