from __future__ import annotations

import json, re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from prompt_lib.llm_client import llm_chat
from .domain import SchemaDomainSpec

# Per-paper LLM proposal
_PER_PAPER_SYSTEM = (
    "Return ONLY valid JSON. No prose, no markdown, no commentary."
)

def _per_paper_prompt(
    spec: SchemaDomainSpec,
    last_tier_text: str,
    paper_text: str,
    max_fields: int,
) -> str:
    avoid_lines = "\n              - ".join(spec.avoid_fields)
    return f"""
        You are a {spec.extractor_role}.

        Subdomain focus: "{last_tier_text}"

        You will read ONE paper and propose REUSABLE candidate fields for a
        {spec.task_noun}. A reusable field is something that:
            * is likely reported across MANY papers about "{last_tier_text}"
              (not just this one paper),
            * has a structured value (number with units, controlled term,
              short categorical answer, named entity, etc.),
            * is grounded in a verbatim quote from the paper text below.

        Avoid fields that look like:
              - {avoid_lines}

        Return ONLY valid JSON of the form:
        {{
          "fields": [
            {{
              "name":            "<short snake_case field name>",
              "description":     "<1 sentence; what value this field captures>",
              "aliases":         ["<other names, abbreviations or symbols papers use for this quantity>"],
              "example_value":   "<a verbatim or near-verbatim example from this paper>",
              "evidence_quote":  "<<= 40-word verbatim quote from the paper>"
            }}
          ]
        }}

        Rules:
            - Propose at MOST {max_fields} fields.
            - Use snake_case names. No spaces, no punctuation in names.
            - "aliases": up to 4 surface forms as they appear in papers
              (e.g. "Tg", "glass transition temperature" for
              glass_transition_temperature). Use [] if there are none.
            - Evidence quotes MUST be copied verbatim from the paper text.
            - Do NOT propose bibliographic metadata.
            - If nothing schema-relevant is found, return {{"fields": []}}.

        Paper text:
        ```{paper_text}```
    """.strip()

def propose_candidate_fields_per_paper(
    papers: List[Dict[str, str]],
    spec: SchemaDomainSpec,
    last_tier_text: str,
    max_fields_per_paper: int,
) -> List[Dict[str, Any]]:
    """papers items use 'text_for_proposal' (the larger excerpt)."""
    proposals: List[Dict[str, Any]] = []
    for paper in papers:
        text = paper.get("text_for_proposal") or paper.get("text") or ""
        prompt = _per_paper_prompt(
            spec=spec,
            last_tier_text=last_tier_text,
            paper_text=text,
            max_fields=max_fields_per_paper,
        )
        raw = llm_chat(
            [
                {"role": "system", "content": _PER_PAPER_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            force_json=True,
        )
        try:
            obj = json.loads(raw)
            fields = obj.get("fields", []) if isinstance(obj, dict) else []
        except Exception:
            fields = []

        cleaned: List[Dict[str, Any]] = []
        for f in fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("name") or "").strip().lower()
            if not name or " " in name:
                continue
            name = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")
            if not name:
                continue
            aliases = f.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            aliases = [a.strip() for a in aliases
                       if isinstance(a, str) and a.strip() and len(a.strip()) <= 60][:4]
            cleaned.append({
                "name":           name,
                "description":    (f.get("description") or "").strip(),
                "aliases":        aliases,
                "example_value":  (f.get("example_value") or "").strip(),
                "evidence_quote": (f.get("evidence_quote") or "").strip(),
            })

        proposals.append({
            "source_file": paper["source_file"],
            "fields":      cleaned,
        })
        print(f"  [paper] {paper['source_file'][:60]:<60}  -> {len(cleaned)} fields")
    return proposals

# Canonicalization + merge
_TIER_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")

# Surface-only canonicalization
_DROP_TOKENS = {
    "bet", "xrd", "tem", "sem", "tga", "dsc", "nmr", "ir",
    "measured", "estimated", "experimental",
    "value", "values",
}

def canonicalize_field_name(name: str) -> str:
    name = (name or "").strip().lower()
    toks = [t for t in name.split("_") if t and t not in _DROP_TOKENS]
    return "_".join(toks) if toks else name

def _last_tier_tokens(last_tier_text: str) -> List[str]:
    return [t.lower() for t in _TIER_TOKEN_RE.findall(last_tier_text or "")
            if len(t) >= 4]

def _matches_avoid(name: str, description: str, avoid_fields: List[str]) -> bool:
    text = f"{name} {description}".lower()
    for af in avoid_fields:
        af = af.lower()
        if "bibliographic" in af and any(
            k in text for k in ("title", "author", "authors", "doi",
                                "journal", "year_published", "publication")
        ):
            return True
    return False

def _is_generic_name(name: str, generic_names: List[str]) -> bool:
    return name.lower() in [g.lower() for g in generic_names]

def _last_tier_relevant(name: str, description: str,
                        last_tier_tokens: List[str]) -> bool:
    if not last_tier_tokens:
        return False
    text = f"{name} {description}".lower()
    return any(tok in text for tok in last_tier_tokens)

def _evidence_quality(quotes: List[str]) -> float:
    if not quotes:
        return 0.0
    score = 0.0
    for q in quotes:
        q = (q or "").strip()
        if not q:
            continue
        score += 0.5
        if re.search(r"\d", q):
            score += 0.5
    return min(score, 2.0)

MAX_ALIASES = 10

def collect_aliases(name: str, *pools: List[str]) -> List[str]:
    """Union of alias pools (order kept, case-insensitive dedupe), minus the name itself.
    snake_case entries (name variants) are rendered with spaces."""
    seen = {name.lower(), name.replace("_", " ").lower()}
    out: List[str] = []
    for pool in pools:
        for a in pool or []:
            if not isinstance(a, str):
                continue
            a = a.strip()
            if "_" in a and " " not in a:
                a = a.replace("_", " ")
            if a and a.lower() not in seen:
                seen.add(a.lower())
                out.append(a)
    return out[:MAX_ALIASES]

def merge_candidate_fields(
    per_paper: List[Dict[str, Any]],
    spec: SchemaDomainSpec,
    last_tier_text: str,
) -> List[Dict[str, Any]]:
    bucket: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "name":             "",
        "name_variants":    [],
        "aliases":          [],
        "descriptions":     [],
        "examples":         [],
        "evidence_quotes":  [],
        "papers":           [],
    })

    for paper_proposals in per_paper:
        src = paper_proposals.get("source_file", "")
        for f in paper_proposals.get("fields", []):
            raw_name = (f.get("name") or "").strip().lower()
            if not raw_name:
                continue
            name = canonicalize_field_name(raw_name)
            b = bucket[name]
            b["name"] = name
            if raw_name not in b["name_variants"]:
                b["name_variants"].append(raw_name)
            for a in f.get("aliases") or []:
                if a not in b["aliases"]:
                    b["aliases"].append(a)

            d = (f.get("description") or "").strip()
            if d and d not in b["descriptions"]:
                b["descriptions"].append(d)
            ex = (f.get("example_value") or "").strip()
            if ex and ex not in b["examples"]:
                b["examples"].append(ex)
            ev = (f.get("evidence_quote") or "").strip()
            if ev and ev not in b["evidence_quotes"]:
                b["evidence_quotes"].append(ev)
            if src and src not in b["papers"]:
                b["papers"].append(src)

    tier_tokens = _last_tier_tokens(last_tier_text)
    canonicals: List[Dict[str, Any]] = []
    for name, b in bucket.items():
        description = b["descriptions"][0] if b["descriptions"] else ""
        support_count = len(b["papers"])
        ev_quality = _evidence_quality(b["evidence_quotes"])
        relevant   = _last_tier_relevant(name, description, tier_tokens)
        generic    = _is_generic_name(name, spec.generic_field_names)
        avoided    = _matches_avoid(name, description, spec.avoid_fields)

        canonicals.append({
            "name":               name,
            "name_variants":      b["name_variants"],
            "aliases":            collect_aliases(name, b["aliases"], b["name_variants"]),
            "description":        description,
            "examples":           b["examples"][:5],
            "support_count":      support_count,
            "evidence_examples":  b["evidence_quotes"][:3],
            "_signals": {
                "evidence_quality":   ev_quality,
                "last_tier_relevant": relevant,
                "is_generic_name":    generic,
                "matches_avoid":      avoided,
                "papers":             b["papers"],
            },
        })
    return canonicals

# Synonym grouping BEFORE the support filter
# merge_candidate_fields() only unifies identical canonical names, so two papers
# naming the same quantity differently (symmetric_cell_cycling_lifetime vs
# ..._lifespan) each get support 1 and both fail min_support. One LLM call
# groups same-concept candidates so their papers are pooled first.
def _build_synonym_prompt(canonicals: List[Dict[str, Any]],
                          spec: SchemaDomainSpec, last_tier_text: str) -> str:
    lines = "\n".join(f"  - {c['name']}: {c.get('description', '')}" for c in canonicals)
    return f"""
        You are a {spec.extractor_role}. Subdomain focus: "{last_tier_text}".

        Below are candidate database fields proposed independently from
        different papers. Different papers often name the SAME quantity
        differently. Group candidates that capture exactly the same quantity or
        concept, e.g. symmetric_cell_cycling_lifetime and
        symmetric_cell_cycling_lifespan, or healing_efficiency and
        self_healing_efficiency.

        Do NOT group fields that are only related but record different things,
        e.g. healing_time vs healing_temperature, or tensile_strength vs
        elongation_at_break. When unsure, leave them separate.

        CANDIDATES:
{lines}

        Return ONLY valid JSON:
        {{"groups": [{{"keep": "<existing name>", "merge_from": ["<existing name>", "..."]}}]}}
        Use only names listed above, each name in at most one group.
        Return {{"groups": []}} if nothing should be grouped.
    """.strip()

def group_synonym_candidates(
    canonicals: List[Dict[str, Any]],
    spec: SchemaDomainSpec,
    last_tier_text: str,
    llm_chat_fn=None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pool same-concept candidates (union of papers, aliases, examples,
    evidence) and recompute their signals. On any failure returns the input."""
    debug: Dict[str, Any] = {"applied": False, "groups": []}
    if len(canonicals) < 2:
        return canonicals, debug
    fn = llm_chat_fn or llm_chat
    try:
        raw = fn([{"role": "system", "content": _PER_PAPER_SYSTEM},
                  {"role": "user", "content": _build_synonym_prompt(canonicals, spec, last_tier_text)}],
                 force_json=True)
        plan = json.loads(raw)
        if not isinstance(plan, dict):
            raise ValueError("synonym plan is not a JSON object")
        groups = plan.get("groups") or []
    except Exception as e:
        debug["reason"] = f"synonym grouping failed, kept candidates as-is: {e}"
        print(f"[schema][synonyms] WARNING: {debug['reason']}")
        return canonicals, debug

    by_name = {c["name"]: c for c in canonicals}
    used: set = set()
    absorbed: set = set()
    tier_tokens = _last_tier_tokens(last_tier_text)
    for g in groups:
        if not isinstance(g, dict):
            continue
        keep = (g.get("keep") or "").strip()
        members = [n.strip() for n in (g.get("merge_from") or []) if isinstance(n, str)]
        members = [n for n in members if n in by_name and n != keep and n not in used]
        if keep not in by_name or keep in used or not members:
            continue
        base = by_name[keep]
        papers = list(base["_signals"]["papers"])
        examples, evidence = list(base["examples"]), list(base["evidence_examples"])
        variants = list(base["name_variants"])
        aliases = list(base.get("aliases") or [])
        for n in members:
            m = by_name[n]
            papers += [p for p in m["_signals"]["papers"] if p not in papers]
            examples += [x for x in m["examples"] if x not in examples]
            evidence += [x for x in m["evidence_examples"] if x not in evidence]
            variants += [v for v in m["name_variants"] if v not in variants]
            aliases = collect_aliases(keep, aliases, [n], m.get("aliases") or [])
        base.update({
            "name_variants": variants,
            "aliases": collect_aliases(keep, aliases, variants),
            "examples": examples[:5],
            "evidence_examples": evidence[:3],
            "support_count": len(papers),
        })
        base["_signals"] = {
            "evidence_quality":   _evidence_quality(evidence),
            "last_tier_relevant": _last_tier_relevant(keep, base["description"], tier_tokens),
            "is_generic_name":    _is_generic_name(keep, spec.generic_field_names),
            "matches_avoid":      _matches_avoid(keep, base["description"], spec.avoid_fields),
            "papers":             papers,
        }
        used.update([keep, *members])
        absorbed.update(members)
        debug["groups"].append({"keep": keep, "merge_from": members,
                                "support_count": len(papers)})

    out = [c for c in canonicals if c["name"] not in absorbed]
    debug.update({"applied": True, "before": len(canonicals), "after": len(out)})
    return out, debug

# Scoring + final selection
def _score_signals(signals: Dict[str, Any], support_count: int) -> float:
    sc_support = min(support_count, 5) / 5.0          # 0..1
    sc_evid    = signals["evidence_quality"] / 2.0    # 0..1
    sc_rel     = 1.0 if signals["last_tier_relevant"] else 0.0
    penalty_g  = 0.5 if signals["is_generic_name"]    else 0.0
    penalty_a  = 1.0 if signals["matches_avoid"]      else 0.0
    return (
        1.0 * sc_support
        + 0.5 * sc_evid
        + 0.5 * sc_rel
        - penalty_g
        - penalty_a
    )

def select_final_schema(
    canonicals: List[Dict[str, Any]],
    max_final_fields: int,
    min_support: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    debug: List[Dict[str, Any]] = []
    keep_pool: List[Tuple[float, Dict[str, Any]]] = []

    for c in canonicals:
        sig = c["_signals"]
        score = _score_signals(sig, c["support_count"])
        drop_reasons: List[str] = []
        if c["support_count"] < min_support:
            drop_reasons.append(f"support_count<{min_support}")
        if sig["matches_avoid"]:
            drop_reasons.append("matches_avoid_fields")
        if sig["is_generic_name"]:
            drop_reasons.append("generic_field_name")

        debug.append({
            "name":          c["name"],
            "description":   c["description"],
            "support_count": c["support_count"],
            "score":         round(score, 3),
            "signals":       sig,
            "drop_reasons":  drop_reasons,
            "source":        "new",
        })

        if drop_reasons:
            continue
        keep_pool.append((score, c))

    keep_pool.sort(key=lambda kv: kv[0], reverse=True)
    keep = keep_pool[:max_final_fields]

    final_fields: List[Dict[str, Any]] = []
    for _score, c in keep:
        final_fields.append({
            "name":              c["name"],
            "description":       c["description"],
            "aliases":           c.get("aliases", []),
            "examples":          c["examples"],
            "support_count":     c["support_count"],
            "evidence_examples": c["evidence_examples"],
            "source":            "new",
        })
    return final_fields, debug