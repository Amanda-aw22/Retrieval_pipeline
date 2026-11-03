"""Method C — sample-first, two steps.

Step 1 (one call per paper): list every sample with its aliases and role
        (this_work / control / prior_work). Instructions:
        method_instructions/C1_sample_inventory.md
Step 2 (one call per this_work sample): only the passages that mention the
        sample (by name or alias), plus the abstract / "Herein" anchors and
        the passage that defines it; extract records for that sample only.
        Instructions: method_instructions/C2_sample_properties.md + shared rules.
Merge:  records of all samples; the same value + evidence claimed by two
        samples is reported as a conflict.
"""
from __future__ import annotations

import re, unicodedata
from typing import Any, Dict, List, Sequence, Tuple

from extract_lib.record_extract import (
    METHOD_DIR, _norm, load_instructions, render_passages,
)
from extract_lib.retrieval import Paragraph

C1_INSTRUCTIONS = METHOD_DIR / "C1_sample_inventory.md"
SAMPLE_ROLES = {"this_work", "control", "prior_work"}
MAX_PASSAGES_PER_SAMPLE = 30
MAX_ANCHOR_PASSAGES = 3

def _as_int(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0

# Step 1: inventory
def build_inventory_prompt(paper_id: str, paras: Sequence[Paragraph]) -> Tuple[str, str]:
    user = (f"paper_id: {paper_id}\n\n"
            f"Passages from this paper (references and acknowledgements removed):\n\n"
            f"{render_passages(paras)}")
    return C1_INSTRUCTIONS.read_text(encoding="utf-8"), user

def parse_inventory(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Clean the step-1 answer. Duplicate names are folded into one entry
    (aliases unioned) so each sample has one umbrella name."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for s in raw.get("samples") or []:
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            continue
        name = " ".join(str(s["name"]).split())
        aliases = [" ".join(str(a).split()) for a in (s.get("aliases") or [])
                   if str(a).strip() and " ".join(str(a).split()) != name]
        role = str(s.get("role") or "this_work").strip().lower()
        entry = by_name.setdefault(name, {
            "name": name, "aliases": [],
            "description": str(s.get("description") or "").strip(),
            "material_class": str(s.get("material_class") or "").strip(),
            "role": role if role in SAMPLE_ROLES else "this_work",
            "application": str(s.get("application") or "").strip(),
            "evidence": str(s.get("evidence") or "").strip(),
            "passage": _as_int(s.get("passage")),
        })
        for a in aliases:
            if a not in entry["aliases"]:
                entry["aliases"].append(a)
    return {"paper_type": str(raw.get("paper_type") or ""), "samples": list(by_name.values())}

def own_samples(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in inventory["samples"] if s["role"] == "this_work"]

# Step 2: per-sample passage selection + prompt
_DASHES = dict.fromkeys(map(ord, "−–—‐‑"), "-")

def _surface(s: str) -> str:
    """NFKC + unified dashes + collapsed spaces, case kept (codes are case-sensitive)."""
    s = unicodedata.normalize("NFKC", s or "").replace("\xad", "").translate(_DASHES)
    return re.sub(r"\s+", " ", s)

def _alias_patterns(sample: Dict[str, Any]) -> List[re.Pattern]:
    pats = []
    for term in [sample["name"], *sample.get("aliases", [])]:
        t = _surface(term).strip()
        if len(t) < 2:
            continue
        # Short codes ("PTA", "CV1", "MLCE-2") match case-sensitively as whole
        # tokens; longer phrases match case-insensitively.
        flags = 0 if (len(t) <= 8 or re.search(r"\d|[A-Z]{2}", t)) else re.I
        pats.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", flags))
    return pats

def select_passages_for_sample(sample: Dict[str, Any], paras: Sequence[Paragraph],
                               max_passages: int = MAX_PASSAGES_PER_SAMPLE) -> List[int]:
    """Global 1-based passage numbers: anchors (abstract / "Herein"), the
    passage that defines the sample, and every passage or table mentioning the
    sample by name or alias. Over budget -> keep tables, results and the
    passages with the most mentions; always returned in document order."""
    pats = _alias_patterns(sample)
    hits: Dict[int, int] = {}
    for i, p in enumerate(paras, 1):
        text = _surface(p.text)
        n = sum(len(pat.findall(text)) for pat in pats)
        if n:
            hits[i] = n
    anchors = [i for i, p in enumerate(paras, 1) if p.is_anchor][:MAX_ANCHOR_PASSAGES]
    must = set(anchors)
    if 1 <= sample.get("passage", 0) <= len(paras):
        must.add(sample["passage"])
    ranked = sorted(hits, key=lambda i: (not paras[i - 1].is_table,
                                         paras[i - 1].section != "results",
                                         -hits[i], i))
    chosen = list(must)
    for i in ranked:
        if len(chosen) >= max_passages:
            break
        if i not in must:
            chosen.append(i)
    return sorted(chosen)

def build_sample_prompt(paper_id: str, sample: Dict[str, Any],
                        inventory: Dict[str, Any], paras: Sequence[Paragraph],
                        indices: Sequence[int]) -> Tuple[str, str]:
    others = [s for s in inventory["samples"] if s["name"] != sample["name"]]
    other_lines = "\n".join(
        f"  - {s['name']}" + (f" (also: {', '.join(s['aliases'][:4])})" if s["aliases"] else "")
        + f" [{s['role']}]" for s in others) or "  (none)"
    user = (f"paper_id: {paper_id}\n\n"
            f"TARGET SAMPLE: {sample['name']}\n"
            f"  aliases: {', '.join(sample['aliases']) or '(none)'}\n"
            f"  description: {sample['description']}\n"
            f"  material_class: {sample['material_class']}\n"
            f"  application stated in the paper: {sample['application'] or '(none)'}\n\n"
            f"OTHER SAMPLES in this paper (do NOT extract their values):\n{other_lines}\n\n"
            f"Passages mentioning the target (global passage numbers):\n\n"
            f"{render_passages(paras, indices)}")
    return load_instructions("C2"), user

# Merge
def merge_sample_records(per_sample: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Concatenate per-sample records; report values (same property, value and
    evidence) claimed by more than one sample as conflicts. Nothing is dropped."""
    records = [r for res in per_sample for r in res["records"]]
    claims: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for r in records:
        key = (r["property"], _norm(r["value"]), _norm(r["evidence"]))
        claims.setdefault(key, []).append(r)
    conflicts = []
    for (prop, value, _ev), rs in claims.items():
        samples = sorted({r["sample"] for r in rs})
        if len(samples) > 1:
            conflicts.append({"property": prop, "value": rs[0]["value"], "unit": rs[0]["unit"],
                              "samples": " | ".join(samples), "evidence": rs[0]["evidence"],
                              "page": rs[0].get("page", "")})
    return records, conflicts
