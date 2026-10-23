from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Set, Tuple, Union

from extract_lib.config import (
    ALIAS_HIT_WEIGHT,
    CITATION_FLOOR,
    CITATION_PENALTY,
    DROP_SECTIONS,
    EVIDENCE_HIT_WEIGHT,
    GLOBAL_PARAGRAPH_BUDGET,
    MATERIALS_SUBSECTION_WEIGHT,
    MAX_PINNED_PASSAGES,
    NAME_HIT_WEIGHT,
    NUMERIC_UNIT_BONUS,
    NUMERIC_UNIT_BONUS_CAP,
    PARAGRAPH_MAX_CHARS,
    PARAGRAPH_MIN_CHARS,
    PIN_ANCHOR_PASSAGES,
    PRIOR_WORK_CUE_WEIGHT,
    REAGENT_CUE_WEIGHT,
    SECTION_WEIGHTS,
    TABLE_WEIGHT,
    THIS_WORK_CUE_BONUS,
    TOP_K_PER_FIELD,
)
from extract_lib.pdf_io import TextBlock
from extract_lib.schema_loader import SchemaField

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")

# Context cues (see README discussion: where info lives / prior work vs. this work)
_CITATION_RE = re.compile(
    r"\[\d+(?:\s*[,–\-]\s*\d+)*\]"                  # [12], [1–3], [4,5]
    r"|\bet al\.?"                                   # Smith et al.
)
_THIS_WORK_RE = re.compile(
    r"\b(?:herein|in this (?:work|study|paper)|we (?:report|present|propose|"
    r"design(?:ed)?|develop(?:ed)?|synthesi[sz]ed|prepared|fabricated)|"
    r"denoted as|abbreviated as|(?:hereafter )?referred to as|"
    r"the as-prepared|the resulting|the obtained)\b", re.I)
_PRIOR_WORK_RE = re.compile(
    r"\b(?:has been|have been|was|were) (?:widely )?reported|previously reported|"
    r"\bpreviously\b|\bconventional\b|\bcommercially available\b", re.I)
_REAGENT_RE = re.compile(
    r"purchased from|supplied by|obtained from|used as received|"
    r"without further purification|Sigma|Aldrich|Macklin|Aladdin|Alfa Aesar|TCI\b")
_MATERIALS_HEADING_RE = re.compile(
    r"^\s*[\d.]*\s*(?:materials?|chemicals?|reagents?)(?:\s+(?:and|&)\s+"
    r"(?:reagents?|chemicals?|instruments?))?\s*$", re.I)
_NUMERIC_UNIT_RE = re.compile(
    r"[-−]?\d+(?:\.\d+)?\s*(?:°C|K\b|MPa|GPa|kPa|%|wt\s?%|mol\s?%|S\s?cm|mS\s?cm|"
    r"g\s?mol|kDa|Da\b|mAh|mA\s?cm|J\s?m|MJ\s?m|kJ|eV|V\b|h\b|min\b|nm|μm)")

# Paragraphs
@dataclass
class Paragraph:
    text: str
    section: str = "unknown"
    heading: str = ""
    is_table: bool = False
    page: int = 0            # 1-based; 0 = unknown (plain-text fallback)

    @property
    def n_citations(self) -> int:
        return len(_CITATION_RE.findall(self.text))

    @property
    def is_anchor(self) -> bool:
        """Abstract or a "Herein / In this work" paragraph: defines THIS paper's material."""
        return self.section == "abstract" or (
            self.section in ("introduction", "front", "unknown")
            and bool(_THIS_WORK_RE.search(self.text)))

def _window(text: str) -> List[str]:
    if len(text) <= PARAGRAPH_MAX_CHARS:
        return [text]
    out = []
    for i in range(0, len(text), PARAGRAPH_MAX_CHARS):
        w = text[i : i + PARAGRAPH_MAX_CHARS].strip()
        if len(w) >= PARAGRAPH_MIN_CHARS:
            out.append(w)
    return out

# Legacy plain-text splitting (fallback when layout extraction fails)
def split_paragraphs(text: str) -> List[str]:
    if not text:
        return []
    out: List[str] = []
    for p in _PARA_SPLIT_RE.split(text):
        p = p.strip()
        if len(p) >= PARAGRAPH_MIN_CHARS:
            out.extend(_window(p))
    return out

Source = Union[str, Sequence[TextBlock]]

def build_paragraphs(source: Source) -> List[Paragraph]:
    """Plain text -> unlabeled paragraphs; TextBlocks -> section-labeled paragraphs."""
    if isinstance(source, str):
        return [Paragraph(p) for p in split_paragraphs(source)]
    out: List[Paragraph] = []
    for b in source:
        if b.section in DROP_SECTIONS:
            continue
        text = b.text.strip()
        if b.is_table:
            out.append(Paragraph(text[: PARAGRAPH_MAX_CHARS * 2], b.section,
                                 b.heading, is_table=True, page=b.page))
            continue
        if len(text) < PARAGRAPH_MIN_CHARS:
            continue
        for w in _window(text):
            out.append(Paragraph(w, b.section, b.heading, page=b.page))
    return out

# Scoring
def _tokenize(text: str) -> Set[str]:
    return set(t.lower() for t in _TOKEN_RE.findall(text or ""))

def _count_phrase_hits(haystack_lower: str, phrase: str) -> int:
    if not phrase:
        return 0
    if " " in phrase or "-" in phrase:
        return haystack_lower.count(phrase)
    return len(re.findall(rf"\b{re.escape(phrase)}\b", haystack_lower))

def score_paragraph_for_field(paragraph: Union[str, Paragraph], field: SchemaField) -> float:
    """Keyword relevance of one paragraph to one field (name / alias / evidence hits)."""
    text = paragraph.text if isinstance(paragraph, Paragraph) else paragraph
    if not text:
        return 0.0
    p_lower = text.lower()
    score = 0.0

    # Canonical name hit gets the biggest boost
    for variant in {field.name.lower(), field.name.replace("_", " ").lower()}:
        score += NAME_HIT_WEIGHT * _count_phrase_hits(p_lower, variant)

    # Aliases / member field names
    for alias in field.aliases + field.member_field_names:
        for variant in {alias.lower(), alias.replace("_", " ").lower()}:
            score += ALIAS_HIT_WEIGHT * _count_phrase_hits(p_lower, variant)

    # Evidence-snippet token overlap (mild)
    if field.evidence_snippets and score > 0:
        para_tokens = _tokenize(text)
        for snippet in field.evidence_snippets[:8]:
            overlap = len(para_tokens & _tokenize(snippet))
            if overlap:
                score += EVIDENCE_HIT_WEIGHT * overlap

    return score

def context_adjust(para: Paragraph, keyword_score: float) -> float:
    """Re-weight a relevant paragraph by where it sits and what cues it carries.

    Boosts: abstract/results/tables, "Herein / in this work" cues, numbers with units.
    Penalties: introduction, Materials (reagent) subsections, citation markers,
    prior-work and reagent-supplier phrasing. Unmatched paragraphs stay at 0.
    """
    if keyword_score <= 0:
        return 0.0
    text = para.text
    this_work = bool(_THIS_WORK_RE.search(text))

    bonus = THIS_WORK_CUE_BONUS if this_work else 0.0
    bonus += min(NUMERIC_UNIT_BONUS_CAP,
                 NUMERIC_UNIT_BONUS * len(_NUMERIC_UNIT_RE.findall(text)))

    mult = SECTION_WEIGHTS.get(para.section, 1.0)
    if this_work:
        mult = max(mult, 1.0)           # the "Herein" paragraph is not background
    if para.is_table:
        mult *= TABLE_WEIGHT
    if _MATERIALS_HEADING_RE.match(para.heading or ""):
        mult *= MATERIALS_SUBSECTION_WEIGHT
    mult *= max(CITATION_FLOOR, 1.0 - CITATION_PENALTY * para.n_citations)
    if _PRIOR_WORK_RE.search(text):
        mult *= PRIOR_WORK_CUE_WEIGHT
    if _REAGENT_RE.search(text):
        mult *= REAGENT_CUE_WEIGHT

    return (keyword_score + bonus) * mult

def score_in_context(para: Paragraph, field: SchemaField) -> float:
    return context_adjust(para, score_paragraph_for_field(para, field))

# Top-K selection
@dataclass
class RetrievedContext:
    # Mapping field_name -> ordered list of paragraph indices (most relevant first)
    per_field_indices: Dict[str, List[int]]
    # The full list of paragraphs (so callers can pull text by index)
    paragraphs: List[Paragraph]
    # The de-duplicated, globally-budgeted set of paragraph indices to send to
    # the LLM, in document order
    selected_indices: List[int]

def retrieve_per_field_contexts(
    source: Source,
    fields: List[SchemaField],
    top_k_per_field: int = TOP_K_PER_FIELD,
    global_budget: int = GLOBAL_PARAGRAPH_BUDGET,
) -> RetrievedContext:
    paragraphs = build_paragraphs(source)

    per_field: Dict[str, List[int]] = {}
    selected: List[int] = []
    seen: Set[int] = set()

    # Anchor passages first: abstract + "Herein" paragraphs tell the LLM which
    # materials belong to THIS paper.
    if PIN_ANCHOR_PASSAGES:
        for idx, para in enumerate(paragraphs):
            if para.is_anchor and len(selected) < MAX_PINNED_PASSAGES:
                seen.add(idx)
                selected.append(idx)

    # Per-field top-K
    for f in fields:
        scored: List[Tuple[int, float]] = []
        for idx, para in enumerate(paragraphs):
            s = score_in_context(para, f)
            if s > 0:
                scored.append((idx, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        top_idx = [idx for idx, _ in scored[:top_k_per_field]]
        per_field[f.name] = top_idx
        for idx in top_idx:
            if idx not in seen:
                seen.add(idx)
                selected.append(idx)

    # Fallback: if nothing scored (rare), keep first few paragraphs so that the LLM always has some context
    if not selected and paragraphs:
        selected = list(range(min(len(paragraphs), 10)))

    # Apply global budget (anchors + higher-priority picks come first), then
    # present passages in document order so the LLM reads the paper's flow.
    selected = sorted(selected[:global_budget])

    return RetrievedContext(
        per_field_indices=per_field,
        paragraphs=paragraphs,
        selected_indices=selected,
    )

_SECTION_TITLES = {
    "front": "Front matter", "abstract": "Abstract", "introduction": "Introduction",
    "experimental": "Experimental", "results": "Results", "conclusion": "Conclusion",
}

def _passage_label(i: int, para: Paragraph) -> str:
    parts = [f"Passage {i}"]
    if para.section in _SECTION_TITLES:
        parts.append(_SECTION_TITLES[para.section])
    if para.heading and para.section not in ("abstract", "front"):
        parts.append(" ".join(para.heading.split())[:80])
    if para.is_table:
        parts.append("TABLE")
    if para.n_citations:
        parts.append("cites prior work")
    return "[" + " | ".join(parts) + "]"

def render_context_pack(ctx: RetrievedContext) -> str:
    """Render the selected paragraphs as a single string for the LLM prompt."""
    lines: List[str] = []
    for i, idx in enumerate(ctx.selected_indices, 1):
        para = ctx.paragraphs[idx]
        lines.append(f"{_passage_label(i, para)} {para.text}")
    return "\n\n".join(lines)

# Paper-level relevance signal (used for --top-n ranking)
@dataclass
class PaperRelevance:
    """How relevant a paper looks BEFORE we spend any LLM tokens on it."""
    n_fields_covered: int    # how many schema fields had at least one matching paragraph
    total_best_score: float  # sum of the single best paragraph score per field
    n_paragraphs: int        # total paragraphs in the paper (after split)

def paper_relevance_signal(
    source: Source,
    fields: List[SchemaField],
) -> PaperRelevance:
    paragraphs = build_paragraphs(source)
    if not paragraphs:
        return PaperRelevance(n_fields_covered=0, total_best_score=0.0, n_paragraphs=0)

    total = 0.0
    n_covered = 0
    for f in fields:
        best = 0.0
        for p in paragraphs:
            s = score_in_context(p, f)
            if s > best:
                best = s
        if best > 0:
            n_covered += 1
            total += best
    return PaperRelevance(
        n_fields_covered=n_covered,
        total_best_score=total,
        n_paragraphs=len(paragraphs),
    )
