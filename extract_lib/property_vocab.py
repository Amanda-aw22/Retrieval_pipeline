"""Phase I schema -> Phase II property vocabulary.

final_schema.json mixes measured properties (ionic_conductivity, Mn) with
descriptive fields (synthesis_method, initiator, healing conditions). This
module splits them and turns the property fields into umbrella names:

  * field name (after the base umbrella map) = umbrella property name
  * field aliases / name_variants / merged_from  = wordings folded into it
  * descriptive fields are listed separately as hints for sample,
    material_class and test_condition

The result (property_vocab.json) is read by extract_property_records.py
--vocab: its umbrella_map replaces property_umbrella_map.json in
post-processing, and render_vocab_section() is appended to the system prompt.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Set

from extract_lib.record_extract import INSTRUCTIONS_PATH, load_umbrella_map
from extract_lib.schema_loader import load_schema

# Name tokens of fields that describe the sample or the test, not a measured value.
DESCRIPTIVE_TOKENS = {
    "method", "methods", "type", "types", "configuration", "condition", "conditions",
    "reaction", "monomer", "monomers", "initiator", "catalyst", "solvent", "precursor",
    "precursors", "application", "applications", "mechanism", "structure", "composition",
    "chemistry", "architecture", "frequency", "procedure", "route", "design", "salt",
}
_NUMBER_RE = re.compile(r"\d")

def snake(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")

def base_property_names(path: Path = INSTRUCTIONS_PATH) -> Set[str]:
    """Umbrella names listed in §3 of property_record_instructions.md."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"## 3\..*?(?=\n## 4\.)", text, re.S)
    sec = m.group(0) if m else ""
    sec = sec.split("Never encode")[0]        # stop before the counter-examples
    return set(re.findall(r"`([a-z][a-z0-9_]+)`", sec))

def _target(name: str, umbrella: Dict[str, Any]) -> str:
    t = umbrella.get(name)
    if isinstance(t, dict):
        return t.get("property") or name
    return t or name

def build_vocab(schema_path: Path, base_map: Dict[str, Any] = None) -> Dict[str, Any]:
    base_map = load_umbrella_map() if base_map is None else base_map
    known = base_property_names()
    umbrella: Dict[str, Any] = dict(base_map)
    properties: List[Dict[str, Any]] = []
    descriptive: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, str]] = []

    for f in load_schema(schema_path):
        name = snake(f.name)
        canonical = _target(name, base_map)
        tokens = set(name.split("_"))
        wordings = list(dict.fromkeys([f.name, *f.aliases, *f.member_field_names]))
        examples = " ".join(f.evidence_snippets)

        if canonical in known:
            kind = "known"
        elif tokens & DESCRIPTIVE_TOKENS:
            kind = "descriptive"
        elif _NUMBER_RE.search(examples + " " + f.description) or "value" in f.description.lower():
            kind = "new"
        else:
            kind = "descriptive"

        if kind == "descriptive":
            descriptive.append({"name": f.name, "description": f.description,
                                "aliases": f.aliases})
            continue

        mapped = []
        for w in wordings:
            key = snake(w)
            if len(key) < 3 or key == canonical:
                continue
            if key in known and key != canonical:
                conflicts.append({"wording": w, "field": f.name,
                                  "reason": f"is itself the umbrella name '{key}'"})
                continue
            if key in umbrella and _target(key, umbrella) != canonical:
                conflicts.append({"wording": w, "field": f.name,
                                  "reason": f"already maps to '{_target(key, umbrella)}'"})
                continue
            umbrella[key] = canonical
            mapped.append(w)
        properties.append({"name": canonical, "schema_field": f.name, "status": kind,
                           "description": f.description, "aliases": mapped})

    return {"schema_path": str(schema_path),
            "properties": properties,
            "descriptive_fields": descriptive,
            "umbrella_map": umbrella,
            "conflicts": conflicts}

def render_vocab_section(vocab: Dict[str, Any]) -> str:
    """Prompt text appended after the shared rules."""
    lines = ["## 7. Corpus vocabulary (from the Phase I schema of this corpus)", "",
             "Properties found across this corpus. Use the name before the colon "
             "for any of the listed wordings (in addition to §3):"]
    for p in vocab["properties"]:
        alias = f": {', '.join(p['aliases'])}" if p["aliases"] else ""
        lines.append(f"- `{p['name']}`{alias}")
    if vocab["descriptive_fields"]:
        lines += ["", "These schema fields describe the sample or the test; they are not "
                      "property names. Put such information in `sample`, `material_class` "
                      "or `test_condition` when it identifies the sample or the measurement "
                      "(properties in §3, e.g. `healing_time`, still get their own records):"]
        lines += [f"- {d['name']}" for d in vocab["descriptive_fields"]]
    return "\n".join(lines)
