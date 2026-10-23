import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Canonical section labels attached to every TextBlock.
#   front        title / authors / affiliations / article info before the abstract
#   abstract     the abstract
#   introduction background + prior work (+ the "Herein" paragraph at its end)
#   experimental materials, synthesis, characterization methods
#   results      results and discussion
#   conclusion   conclusions / summary / outlook
#   back_matter  references, acknowledgements, declarations, CRediT, appendix
#   unknown      no recognizable heading seen yet (e.g. unusual layouts)
SECTION_LABELS = ("front", "abstract", "introduction", "experimental",
                  "results", "conclusion", "back_matter", "unknown")

@dataclass
class TextBlock:
    text: str
    page: int            # 1-based
    section: str         # one of SECTION_LABELS
    heading: str         # nearest (sub)heading text, e.g. "2.1. Materials"
    is_table: bool = False

def extract_pdf_text(pdf_path: Path) -> str:
    """Return the full text of a PDF as one string. Empty string on failure.

    Tries PyMuPDF (fitz) first because it matches Phase 1's convention and
    handles columns/figures better; falls back to pypdf so the script still
    runs in environments where fitz is unavailable.
    """
    text = _extract_with_fitz(pdf_path)
    if text.strip():
        return text
    return _extract_with_pypdf(pdf_path)

def _extract_with_fitz(pdf_path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""
    try:
        doc = fitz.open(str(pdf_path))
        parts: List[str] = []
        for page in doc:
            try:
                parts.append(page.get_text() or "")
            except Exception:
                continue
        doc.close()
        return "\n".join(parts)
    except Exception as e:
        print(f"   ⚠️  fitz failed on {pdf_path.name}: {e}")
        return ""

def _extract_with_pypdf(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        parts: List[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts)
    except Exception as e:
        print(f"   ⚠️  pypdf failed on {pdf_path.name}: {e}")
        return ""

# Structure-aware extraction (section-labeled paragraph blocks)
_NUM_PREFIX_RE = re.compile(r"^\s*(?:[A-Z]\.|\d+(?:\.\d+)*)\.?\s+")
_SUBHEADING_RE = re.compile(r"^\d+\.\d+(?:\.\d+)*\.?\s")
_SPACED_WORD_RE = re.compile(r"^(?:[A-Z]\s+){3,}[A-Z]$")  # "A B S T R A C T"

def _is_spaced_word(text: str) -> bool:
    return bool(_SPACED_WORD_RE.match(" ".join(text.split())))

# Standard section names. A line that is EXACTLY one of these (optionally
# numbered) is a heading even without bold styling: Nature-family PDFs set
# "Results"/"Methods" as the first line of the paragraph block, and ACS
# post-prints run "RESULTS AND DISCUSSION" into the paragraph's first line.
_HEADING_TEXT_RE = re.compile(
    r"(?:(?:\d+|[IVX]+)\.?\s*|■\s*)?"
    r"(?:abstract|introduction|background|results?(?: and discussion)?|discussion|"
    r"methods?|materials and methods|experimental(?: section| procedures?| details)?|"
    r"conclusions?(?: and outlooks?)?|summary(?: and outlooks?)?|outlook|references|"
    r"acknowledge?ments?|data availability|author contributions?|competing interests?|"
    r"conflicts? of interest|supporting information|associated content|author information)"
    r"\.?", re.I)

def _is_heading_text(text: str) -> bool:
    t = " ".join(text.split())
    first_alpha = next((c for c in t if c.isalpha()), "")
    # Real headings are capitalized ("Methods", "METHODS"); a lowercase
    # "method" is a table cell or a stray word, not a section.
    return first_alpha.isupper() and bool(_HEADING_TEXT_RE.fullmatch(t))

_NOT_ABSTRACT_RE = re.compile(
    r"Universit|Department|School of|Institute|Laboratory|College|@|Received|"
    r"Accepted|Correspondence|Copyright|©|https?://|doi\.org", re.I)

def _line_text(ln) -> str:
    return "".join(s["text"] for s in ln.get("spans", []))

def _split_on_headings(lines) -> List[Tuple[str, object]]:
    """Split a block's lines at standard section-name headings found anywhere
    in the block: a line that is exactly a section name, or leading bold spans
    forming one ("RESULTS AND DISCUSSION The ..."). Returns a sequence of
    ("heading", str) and ("lines", [line dicts or str]) segments."""
    segs: List[Tuple[str, object]] = []
    cur: List[object] = []
    for ln in lines:
        text = " ".join(_line_text(ln).split())
        if len(lines) > 1 and _is_heading_text(text):
            if cur:
                segs.append(("lines", cur))
                cur = []
            segs.append(("heading", text))
            continue
        spans = [s for s in ln.get("spans", []) if s["text"]]
        lead = []
        for s in spans:
            if not _span_is_bold(s):
                break
            lead.append(s["text"])
        lead_text = " ".join("".join(lead).split())
        if lead and len(lead) < len(spans) and _is_heading_text(lead_text):
            if cur:
                segs.append(("lines", cur))
            segs.append(("heading", lead_text))
            cur = ["".join(s["text"] for s in spans[len(lead):])]
            continue
        cur.append(ln)
    if cur:
        segs.append(("lines", cur))
    return segs

_TOP_NUMBERED_RE = re.compile(r"^\d+\.?\s+[A-Za-z]")       # "2 Results", "3. Conclusions"
_TABLE_CAPTION_RE = re.compile(r"\bTable\s*[0-9IVX]+", re.I)
_NOT_SUBHEADING_RE = re.compile(r"^(?:Fig(?:ure)?\.?|Table|Scheme|Keywords?)\b", re.I)

def _reading_order_key(bbox, page_width: float, full_tops: List[float]):
    """Two-column reading order: within each band between full-width blocks,
    left column top-to-bottom, then right column, then the full-width block."""
    x0, y0, x1, y1 = bbox
    if x1 - x0 > 0.6 * page_width:
        return (sum(1 for t in full_tops if t < y0 - 1), 2, y0, x0)
    band = sum(1 for t in full_tops if t <= y0)
    col = 0 if (x0 + x1) / 2 < page_width / 2 else 1
    return (band, col, y0, x0)

# Fractions of page height treated as running header / footer zones.
_HEADER_FRAC = 0.06
_FOOTER_FRAC = 0.94

def classify_heading(text: str) -> Optional[str]:
    """Map a top-level heading to a canonical section label, or None."""
    t = " ".join(text.split())
    if _is_spaced_word(t):
        t = t.replace(" ", "")
        t = {"ARTICLEINFO": "article info"}.get(t, t)
    t = _NUM_PREFIX_RE.sub("", t).strip().lower()
    if not t:
        return None
    if t.startswith("abstract"):
        return "abstract"
    if t.startswith("article info") or t.startswith("articleinfo"):
        return "front"
    if t.startswith("introduction") or t == "background":
        return "introduction"
    if any(k in t for k in ("reference", "acknowledg", "declaration",
                            "credit", "data availability", "appendix",
                            "supplementary", "supporting information",
                            "author contribution", "funding",
                            "conflict of interest", "competing interest")):
        return "back_matter"
    if "conclusion" in t or t.startswith("summary") or t.startswith("outlook"):
        return "conclusion"
    if "result" in t or "discussion" in t:
        return "results"
    if any(k in t for k in ("experimental", "method", "materials",
                            "procedure", "synthesis")):
        return "experimental"
    return None

def _span_is_bold(s) -> bool:
    return bool(s["flags"] & 16) or "bold" in (s.get("font") or "").lower()

def _span_is_italic(s) -> bool:
    return bool(s["flags"] & 2) or "italic" in (s.get("font") or "").lower()

def _join_lines(lines: List[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("\xad"):            # soft hyphen at line break
            out = out[:-1] + ln
        elif out.endswith("-") and not out.endswith(" -"):
            out = out + ln                   # keep hyphenated compounds intact
        else:
            out = f"{out} {ln}" if out else ln
    return out.replace("\xad", "")

def _table_markdown(tab) -> str:
    try:
        return tab.to_markdown().strip()
    except Exception:
        rows = tab.extract() or []
        return "\n".join(" | ".join(str(c or "").strip() for c in r) for r in rows)

def _inside(inner, outer, tol: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)

def extract_pdf_blocks(pdf_path: Path) -> List[TextBlock]:
    """Return paragraph-level blocks tagged with section + nearest heading.

    Uses PyMuPDF layout info: top-level headings are short all-bold blocks;
    numbered subheadings ("2.1. Materials") are italic/bold numbered prefixes;
    the abstract is introduced by "Abstract" or the spaced "A B S T R A C T".
    Running headers/footers are dropped; detected tables become one block each
    (Markdown). Returns [] on failure so callers can fall back to plain text.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        print(f"   ⚠️  fitz failed on {pdf_path.name}: {e}")
        return []

    blocks: List[TextBlock] = []
    section, heading = "front", ""

    def handle_text(text: str, spans, pno: int) -> None:
        nonlocal section, heading
        short = len(text) <= 80
        all_bold = bool(spans) and all(_span_is_bold(s) for s in spans)

        # Top-level heading: a standard section name, a spaced
        # "A B S T R A C T", or a bold heading numbered "2 ..." / "3. ...".
        if short and (_is_heading_text(text) or _is_spaced_word(text)
                      or (all_bold and _TOP_NUMBERED_RE.match(text)
                          and not _SUBHEADING_RE.match(text))):
            label = classify_heading(text)
            if label is not None:
                section, heading = label, text
                return

        # Numbered subheading ("2.1. Materials"), standalone or run-in.
        if spans and _SUBHEADING_RE.match(text) and (_span_is_italic(spans[0])
                                                     or _span_is_bold(spans[0])):
            lead = []
            for s in spans:
                if _span_is_italic(s) or _span_is_bold(s):
                    lead.append(s["text"])
                else:
                    break
            heading = " ".join("".join(lead).split()) or text[:80]
            if len(lead) == len(spans):
                return                          # standalone subheading
            flat = " ".join(text.split())
            if flat.startswith(heading):
                text = flat[len(heading):].strip() or flat
        # Unnumbered bold subheading ("Synthesis of network polymers"):
        # updates the heading only; never switches the top-level section.
        elif short and all_bold and not _NOT_SUBHEADING_RE.match(text):
            heading = text
            return

        blocks.append(TextBlock(text, pno, section, heading))

    for pno, page in enumerate(doc, start=1):
        H, W = page.rect.height, page.rect.width

        page_blocks = page.get_text("dict").get("blocks", [])

        # find_tables() runs a layout model (~1-2 s/page), so only call it on
        # pages that mention a table caption. Skip page 1: it mistakes the
        # front-matter boxes (article info / abstract) for tables.
        tables = []
        if pno > 1 and any(_TABLE_CAPTION_RE.search(_line_text(ln))
                           for b in page_blocks for ln in b.get("lines", [])):
            try:
                tables = [(t, md) for t in page.find_tables().tables
                          if (md := _table_markdown(t))]
            except Exception:
                pass
        table_boxes = [tuple(t.bbox) for t, _ in tables]

        items = [(tuple(t.bbox), "table", md) for t, md in tables]
        for b in page_blocks:
            if b.get("type", 0) != 0:
                continue
            bbox = tuple(b["bbox"])
            if bbox[3] < _HEADER_FRAC * H or bbox[1] > _FOOTER_FRAC * H:
                continue
            if any(_inside(bbox, tb) for tb in table_boxes):
                continue
            items.append((bbox, "text", b))
        full_tops = sorted(bb[1] for bb, _, _ in items if bb[2] - bb[0] > 0.6 * W)
        items.sort(key=lambda it: _reading_order_key(it[0], W, full_tops))

        for _bbox, kind, obj in items:
            if kind == "table":
                blocks.append(TextBlock(obj, pno, section, heading, is_table=True))
                continue
            lines = [ln for ln in obj.get("lines", []) if _line_text(ln).strip()]
            for kind2, seg in _split_on_headings(lines):
                if kind2 == "heading":
                    label = classify_heading(seg)
                    if label is not None:
                        section, heading = label, seg
                    continue
                seg_lines = [x for x in seg if not isinstance(x, str)]
                spans = [s for ln in seg_lines for s in ln.get("spans", [])
                         if s["text"].strip()]
                # Spans carry their own spacing; joining with "" keeps
                # subscripts attached ("T"+"g" -> "Tg") so aliases match.
                text = _join_lines([x if isinstance(x, str) else _line_text(x)
                                    for x in seg])
                if text:
                    handle_text(text, spans, pno)
    doc.close()

    # No "Abstract" heading (e.g. Nature-family articles): the first long,
    # sentence-like front-matter paragraph on the first pages is the abstract.
    if not any(b.section == "abstract" for b in blocks):
        for b in blocks:
            if b.section != "front" or b.page > 2 or b.is_table:
                continue
            if (len(b.text) >= 400 and b.text.count(". ") >= 2
                    and not _NOT_ABSTRACT_RE.search(b.text[:200])):
                b.section = "abstract"
                break

    # No "Introduction" heading (Nature-family): long front-matter paragraphs
    # after the abstract, up to the first real section, are the introduction.
    if not any(b.section == "introduction" for b in blocks):
        after_abstract = False
        for b in blocks:
            if b.section == "abstract":
                after_abstract = True
                continue
            if b.section != "front":
                if after_abstract:
                    break
                continue
            if (after_abstract and len(b.text) >= 300
                    and not _NOT_ABSTRACT_RE.search(b.text[:200])):
                b.section = "introduction"
    return blocks