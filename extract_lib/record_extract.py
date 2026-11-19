"""Schema-free property-record extraction.

The LLM lists every candidate polymer of a paper and one record per reported
value: sample (= a listed polymer), property (umbrella name), value, unit,
test condition, application, verbatim evidence and its location. The LLM
instructions live in property_record_instructions.md; this module builds the
prompt, then validates the answer and fills page / section / heading from the
cited passage so locations never depend on the LLM. build_polymer_view()
groups the records under their polymers for the output.
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
                 "value", "unit", "test_condition", "application", "evidence", "passage",
                 "sample_evidence", "sample_passage", "condition_evidence", "derivation"]
TEXT_FIELDS = ["sample", "material_class", "property", "property_as_written",
               "value", "unit", "test_condition", "application", "evidence", "sample_evidence"]
REQUIRED_FIELDS = ("sample", "property", "value", "evidence")
DERIVATIONS = {"stated", "positional", "cross_passage", "corrected", "inferred"}
# Values of this paper's own and control polymers are kept; records still
# tagged as other groups' work (older prompts, or the LLM ignoring §4) are dropped.
EXCLUDED_ROLES = {"prior_work"}
POLYMER_ROLES = ("this_work", "control", "prior_work")
# Other wordings of a role. Comparison / reference / commercial polymers are
# controls: they are kept as candidates and their values are extracted.
ROLE_ALIASES = {"comparison": "control", "reference": "control", "commercial": "control",
                "benchmark": "control", "baseline": "control", "standard": "control",
                "own": "this_work", "this work": "this_work", "variant": "this_work",
                "prior work": "prior_work", "literature": "prior_work", "cited": "prior_work"}
POLYMER_FIELDS = ["name", "aliases", "role", "role_as_written", "material_class", "description",
                  "application", "evidence", "passage"]
# Paper-level metadata from Phase I (cumulative_metadata.jsonl), when available.
PAPER_META_FIELDS = ["doi", "title", "year", "retrieval_source"]
CSV_COLUMNS = ["paper_id", "source_path", *PAPER_META_FIELDS, "paper_type", "polymer", "role",
               "material_class", "has_properties", "n_properties", "property",
               "property_as_written", "value", "unit", "test_condition", "application",
               "derivation", "flags",
               "evidence", "page", "section", "heading", "passage", "evidence_verified",
               "value_in_evidence", "unit_in_evidence",
               "sample_evidence", "sample_page", "sample_evidence_verified",
               "condition_evidence", "condition_evidence_verified"]
POLYMER_CSV_COLUMNS = ["paper_id", "source_path", *PAPER_META_FIELDS, "paper_type", "polymer",
                       "aliases", "role", "role_as_written", "material_class", "description",
                       "application", "has_properties", "n_properties", "properties", "source",
                       "evidence", "passage", "page", "section", "heading", "evidence_verified",
                       "flags"]

_SECTION_TITLES = {"front": "Front matter", "abstract": "Abstract",
                   "introduction": "Introduction", "experimental": "Experimental",
                   "results": "Results", "conclusion": "Conclusion"}

# Each method = its own INPUT header + the shared rules (property_record_instructions.md).
METHOD_DIR = Path(__file__).with_name("method_instructions")
METHOD_HEADERS = {
    "A": "A_fulltext.md",            # full raw text, single call
    "B": "B_sectioned.md",           # labeled passages, single call (default)
    "C2": "C2_sample_properties.md", # sample-first, step 2 (per sample)
}

def load_instructions(method: str = "B") -> str:
    header = (METHOD_DIR / METHOD_HEADERS[method]).read_text(encoding="utf-8")
    return header.strip() + "\n\n" + INSTRUCTIONS_PATH.read_text(encoding="utf-8")

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

def render_passages(paras: Sequence[Paragraph], indices: Sequence[int] = None) -> str:
    """Render passages with their GLOBAL 1-based numbers; `indices` selects a
    subset (method C step 2) without renumbering."""
    idx = list(indices) if indices is not None else range(1, len(paras) + 1)
    return "\n\n".join(f"{_label(i, paras[i - 1])} {paras[i - 1].text}" for i in idx)

def build_record_prompt(paper_id: str, paras: Sequence[Paragraph]) -> Tuple[str, str]:
    """Method B: (system, user) messages."""
    user = (f"paper_id: {paper_id}\n\n"
            f"Passages from this paper (references and acknowledgements removed):\n\n"
            f"{render_passages(paras)}")
    return load_instructions("B"), user

# Validation
_DASHES = dict.fromkeys(map(ord, "−–—‐‑"), "-")
# Glyph variants PDFs use for the same symbol (degree, multiplication, dot).
_GLYPHS = str.maketrans({"◦": "°", "∘": "°", "º": "°", "˚": "°",
                         "⋅": "·", "∙": "·", "•": "·"})

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("\xad", "").translate(_DASHES)
    return re.sub(r"\s+", " ", s).strip().lower()

def _tight(s: str) -> str:
    """For value / unit checks: _norm + unified glyphs, no spaces or dots."""
    return re.sub(r"[\s·]", "", _norm(s).translate(_GLYPHS))

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

def locate_in_pages(quote: str, pages: Sequence[str]) -> int:
    """1-based page whose text contains the quote (also tries adjacent page
    pairs for quotes that cross a page break), or 0."""
    q = _norm(quote).rstrip("…. ")
    if not q:
        return 0
    normed = [_norm(t) for t in pages]
    for i, t in enumerate(normed, 1):
        if q in t:
            return i
    for i in range(1, len(normed)):
        if q in normed[i - 1] + " " + normed[i]:
            return i
    return 0

class TextIndex:
    """Where quotes are verified: the numbered passages (B, C) and, for method
    A, also the raw page text the LLM actually saw."""
    def __init__(self, paras: Sequence[Paragraph], pages: Sequence[str] = None):
        self.paras, self.pages = paras, pages

    def find(self, quote: str, hint: int = 0) -> Dict[str, Any]:
        idx = _locate(quote, self.paras, hint)
        p = self.paras[idx - 1] if idx else None
        page = p.page if p else (locate_in_pages(quote, self.pages) if self.pages else 0)
        return {"found": bool(idx or page), "passage": idx, "page": page or "",
                "section": p.section if p else "",
                "heading": " ".join(p.heading.split()) if p and p.heading else ""}

def _as_int(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0

def _clean_record(r: Any, umbrella: Dict[str, Any],
                  force_sample: str = "") -> Dict[str, Any]:
    """Shared cleanup for every method; None if the record must be dropped."""
    if not isinstance(r, dict):
        return None
    if str(r.get("role") or "").strip().lower() in EXCLUDED_ROLES:
        return None
    rec = {k: ("" if r.get(k) is None else str(r.get(k)).strip()) for k in TEXT_FIELDS}
    if force_sample:
        rec["sample"] = force_sample
    if any(not rec[k] for k in REQUIRED_FIELDS):
        return None
    rec["property"] = re.sub(r"[^a-z0-9]+", "_", rec["property"].lower()).strip("_")
    apply_umbrella(rec, umbrella)
    rec["unit"] = unicodedata.normalize("NFKC", rec["unit"])   # "℃" -> "°C"
    der = r.get("derivation") or []
    der = [der] if isinstance(der, str) else der
    rec["derivation"] = [d.strip().lower() for d in der if isinstance(d, str) and d.strip()]
    conds = r.get("condition_evidence") or []
    conds = [conds] if isinstance(conds, (str, dict)) else conds
    rec["_conds"] = [(c.get("quote", ""), _as_int(c.get("passage"))) if isinstance(c, dict)
                     else (str(c), 0) for c in conds]
    rec["_conds"] = [(q.strip(), p) for q, p in rec["_conds"] if str(q).strip()]
    return rec

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

def _check(rec: Dict[str, Any], r: Dict[str, Any], index: TextIndex) -> None:
    """Verify every quote verbatim, check value/unit against the evidence, and
    collect human-readable flags for anything that cannot be traced."""
    flags: List[str] = []
    ev = index.find(rec["evidence"], _as_int(r.get("passage")))
    if not ev["found"]:
        flags.append("evidence_not_found")

    ev_t = _tight(rec["evidence"])
    nums = _NUM_RE.findall(rec["value"])
    value_ok = all(n in ev_t for n in nums) if nums else ""
    if value_ok is False:
        flags.append("value_not_in_evidence")
    unit_ok = (_tight(rec["unit"]) in ev_t) if rec["unit"] else ""
    if unit_ok is False:
        flags.append("unit_not_in_evidence")

    if rec["sample_evidence"]:
        se = index.find(rec["sample_evidence"], _as_int(r.get("sample_passage")))
        sample_ok = se["found"]
        if not sample_ok:
            flags.append("sample_evidence_not_found")
    else:
        se, sample_ok = {"page": ""}, False
        flags.append("sample_evidence_missing")

    cond_found = [(q, index.find(q, p)) for q, p in rec.pop("_conds")]
    if rec["test_condition"]:
        cond_ok = bool(cond_found) and all(f["found"] for _, f in cond_found)
        if not cond_found:
            flags.append("condition_evidence_missing")
        elif not cond_ok:
            flags.append("condition_evidence_not_found")
    else:
        cond_ok = ""

    bad = [d for d in rec["derivation"] if d not in DERIVATIONS]
    if not rec["derivation"]:
        flags.append("derivation_missing")
    if bad:
        flags.append("derivation_unknown:" + "/".join(bad))
    for d in ("corrected", "inferred"):
        if d in rec["derivation"]:
            flags.append(f"review:{d}")

    rec.update({
        "passage": ev["passage"], "page": ev["page"], "section": ev["section"],
        "heading": ev["heading"], "evidence_verified": ev["found"],
        "value_in_evidence": value_ok, "unit_in_evidence": unit_ok,
        "sample_page": se["page"], "sample_evidence_verified": sample_ok,
        "condition_evidence": " || ".join(f"{q} (p{f['page']})" for q, f in cond_found),
        "condition_evidence_verified": cond_ok,
        "derivation": ",".join(rec["derivation"]),
        "flags": ";".join(flags),
    })

def normalize_records(raw: Dict[str, Any], paper_id: str,
                      paras: Sequence[Paragraph],
                      umbrella: Dict[str, Any] = None,
                      force_sample: str = "",
                      pages: Sequence[str] = None) -> Dict[str, Any]:
    """Clean an LLM answer (shared cleanup), verify every quote verbatim
    (evidence, sample_evidence, condition_evidence), check value and unit
    against the evidence, attach page/section/heading, and flag anything that
    cannot be traced. `pages` = raw page text, for method A."""
    if umbrella is None:
        umbrella = load_umbrella_map()
    index = TextIndex(paras, pages)
    out: List[Dict[str, Any]] = []
    for r in (raw.get("records") or []):
        rec = _clean_record(r, umbrella, force_sample)
        if rec is None:
            continue
        _check(rec, r, index)
        out.append(rec)
    return {"paper_id": paper_id, "paper_type": str(raw.get("paper_type") or ""),
            "polymers": parse_polymers(raw), "records": out}

def normalize_records_fulltext(raw: Dict[str, Any], paper_id: str,
                               pages: Sequence[str],
                               paras: Sequence[Paragraph],
                               umbrella: Dict[str, Any] = None) -> Dict[str, Any]:
    """Method A: the LLM saw raw page text without passage numbers. Quotes are
    verified against the sectioned passages first (for section/heading, which
    the LLM never saw) and then against the raw pages."""
    return normalize_records(raw, paper_id, paras, umbrella, pages=pages)

# Polymer view: every candidate polymer, with its records as properties
def parse_polymers(raw: Dict[str, Any], key: str = "polymers") -> List[Dict[str, Any]]:
    """Clean the candidate list (`polymers`, or method C's `samples`). Entries
    with the same name are folded into one (aliases unioned)."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for s in raw.get(key) or []:
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            continue
        name = " ".join(str(s["name"]).split())
        written = str(s.get("role") or "").strip()
        role = written.lower().replace("-", "_")
        role = role if role in POLYMER_ROLES else ROLE_ALIASES.get(role.replace("_", " "), "")
        if not role:      # unknown wording: keep the candidate, guess from the word
            role = ("control" if re.search(r"control|compar|refer|commerc|bench", written, re.I)
                    else "prior_work" if re.search(r"prior|literat|cite|previous", written, re.I)
                    else "this_work")
        entry = by_name.setdefault(name, {
            "name": name, "aliases": [], "role": role, "role_as_written": written,
            **{k: str(s.get(k) or "").strip()
               for k in ("material_class", "description", "application", "evidence")},
            "passage": _as_int(s.get("passage")),
        })
        for a in s.get("aliases") or []:
            a = " ".join(str(a).split())
            if a and a != name and a not in entry["aliases"]:
                entry["aliases"].append(a)
    return list(by_name.values())

def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm(s))

_RECORD_ONLY = [f for f in CSV_COLUMNS if f not in
                ("paper_id", "source_path", *PAPER_META_FIELDS, "paper_type", "polymer",
                 "role", "material_class", "has_properties", "n_properties")]

def build_polymer_view(paper_id: str, paper_type: str, polymers: Sequence[Dict[str, Any]],
                       records: Sequence[Dict[str, Any]], index: "TextIndex",
                       source_path: str = "") -> Dict[str, Any]:
    """One entry per candidate polymer, each with the records whose `sample`
    is its name or an alias. Every candidate is kept, whatever its role and
    whether or not it has records (has_properties=false, properties=[]).
    A record matching no candidate adds a polymer (source "records", flagged)
    so no value is lost; only records of prior_work polymers are dropped (§4).
    Each entry carries paper_id, source_path and the evidence location."""
    out: List[Dict[str, Any]] = []
    lookup: Dict[str, Dict[str, Any]] = {}
    no_ev = {"found": False, "passage": 0, "page": "", "section": "", "heading": ""}
    for p in polymers:
        ev = index.find(p["evidence"], p["passage"]) if p["evidence"] else no_ev
        entry = {"paper_id": paper_id, "source_path": source_path,
                 **{k: p.get(k, "") for k in POLYMER_FIELDS},
                 "passage": ev["passage"] or p["passage"], "page": ev["page"],
                 "section": ev["section"], "heading": ev["heading"],
                 "evidence_verified": ev["found"], "source": "candidate_list",
                 "flags": "" if ev["found"] else
                          ("evidence_not_found" if p["evidence"] else "evidence_missing"),
                 "properties": []}
        out.append(entry)
        for n in [p["name"], *p["aliases"]]:
            lookup.setdefault(_key(n), entry)
    dropped = 0
    for r in records:
        entry = lookup.get(_key(r["sample"]))
        if entry is None:
            ev = (index.find(r["sample_evidence"]) if r.get("sample_evidence") else no_ev)
            entry = {"paper_id": paper_id, "source_path": source_path,
                     "name": r["sample"], "aliases": [], "role": "this_work", "role_as_written": "",
                     "material_class": r.get("material_class", ""), "description": "",
                     "application": r.get("application", ""),
                     "evidence": r.get("sample_evidence", ""), "passage": ev["passage"],
                     "page": ev["page"], "section": ev["section"], "heading": ev["heading"],
                     "evidence_verified": ev["found"], "source": "records",
                     "flags": "not_in_candidate_list", "properties": []}
            out.append(entry)
            lookup[_key(r["sample"])] = entry
        if entry["role"] == "prior_work":
            dropped += 1
            continue
        entry["properties"].append({k: r[k] for k in r if k not in ("sample", "material_class")})
    for e in out:
        e["has_properties"] = bool(e["properties"])
        e["n_properties"] = len(e["properties"])
        e["properties"] = e.pop("properties")          # keep the list as the last key
    return {"paper_id": paper_id, "source_path": source_path, "paper_type": paper_type,
            "n_polymers": len(out),
            "n_polymers_with_properties": sum(bool(e["properties"]) for e in out),
            "n_properties": sum(e["n_properties"] for e in out),
            "n_dropped_prior_work_records": dropped,
            "polymers": out}

def flat_records(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Back to one dict per record (sample = polymer name), for evaluation."""
    if "polymers" not in paper or "records" in paper:      # older flat output
        return list(paper.get("records") or [])
    return [{**r, "sample": p["name"], "role": p["role"], "material_class": p["material_class"]}
            for p in paper["polymers"] for r in p["properties"]]

# Output
def write_outputs(papers: Sequence[Dict[str, Any]], out_dir: Path) -> Tuple[Path, Path]:
    """records.jsonl: one line per paper -> polymers -> properties.
    polymers.csv: one row per candidate polymer.
    records.csv: one row per property; a polymer without properties gets one
    row with empty property fields, a paper without polymers one empty row."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl, csv_path = out_dir / "records.jsonl", out_dir / "records.csv"
    with open(jsonl, "w", encoding="utf-8") as f:
        for p in papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(csv_path, "w", encoding="utf-8", newline="") as f, \
         open(out_dir / "polymers.csv", "w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        wp = csv.DictWriter(fp, fieldnames=POLYMER_CSV_COLUMNS)
        w.writeheader()
        wp.writeheader()
        for p in papers:
            base = {"paper_id": p["paper_id"], "source_path": p.get("source_path", ""),
                    "paper_type": p.get("paper_type", ""),
                    **{k: p.get(k, "") for k in PAPER_META_FIELDS}}
            if not p["polymers"]:
                w.writerow(base)
            for pol in p["polymers"]:
                head = {**base, "polymer": pol["name"], "role": pol["role"],
                        "material_class": pol["material_class"],
                        "has_properties": pol["has_properties"], "n_properties": pol["n_properties"]}
                wp.writerow({**head, "aliases": " | ".join(pol["aliases"]),
                             "properties": " | ".join(dict.fromkeys(r["property"] for r in pol["properties"])),
                             **{k: pol.get(k, "") for k in ("role_as_written", "description",
                                "application", "source", "evidence", "passage", "page", "section",
                                "heading", "evidence_verified", "flags")}})
                if not pol["properties"]:
                    w.writerow(head)
                for r in pol["properties"]:
                    w.writerow({**head, **{k: r.get(k, "") for k in _RECORD_ONLY}})
    return jsonl, csv_path
