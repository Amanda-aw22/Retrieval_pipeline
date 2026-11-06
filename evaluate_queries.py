"""Evaluate previously generated polymer literature-search queries.

The rubric is a project-specific operationalization of the 2026-10-19
assignment and MemoSchema query-evaluation dimensions. It is not a
published numerical rubric. Run from the AutoSchema repository root.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from prompt_lib.llm_client import get_config, llm_chat

RUBRIC_VERSION = "query_quality_v0.1"

TOPICS = {
    "disulfide": {
        "scope": (
            "Scientific papers studying polymers containing disulfide (S-S) bonds, "
            "especially where disulfide chemistry contributes to dynamic behavior. "
            "Hydrogen bonding or other mechanisms may also contribute. "
            "Do not require every paper to be about only self-healing."
        ),
        "seed_titles": [
            "Cationic ring-opening polymerization of natural thioctic acid to chemically recyclable and self-healable poly(thioctic acid) supramolecular material",
            "Aziridine-Derived Polysulfide Elastomers for Self-Healable, Strong, and Reusable Adhesives",
            "Healable and multi driving mode soft actuator enabled by disulfide-bonded liquid crystal elastomers",
        ],
        "coverage_aspects": [
            "Polymer synthesis, preparation, and material identity",
            "Disulfide bonds and reversible/dynamic mechanisms",
            "Chemical structure and bond characterization",
            "Self-healing and mechanical behavior",
            "Recycling, reprocessing, or reversibility",
            "Demonstrated applications of such polymers",
            "Testing conditions affecting reported performance",
        ],
    },
    "batteries": {
        "scope": (
            "Scientific papers on dynamic or self-healing polymers used in battery systems, "
            "including lithium and zinc batteries. Dynamic behavior may be based on "
            "reversible covalent bonds, Diels-Alder chemistry, or reversible physical "
            "interactions. Do not require one specific battery chemistry."
        ),
        "seed_titles": [
            "Dynamically reversible cross-linked polymer electrolytes with highly ionic conductivity for dendrite-free lithium metal batteries",
            "A self-healing polymer electrolyte based on the Diels-Alder reaction in lithium-ion batteries",
            "Mussel-inspired, hydrophobic association-regulated hydrogel electrolytes with super-adhesive and self-healing properties for durable and flexible zinc-ion batteries",
        ],
        "coverage_aspects": [
            "Polymer synthesis, preparation, and material identity",
            "Dynamic bonding or reversible interaction mechanisms",
            "Chemical structure and bond characterization",
            "Self-healing and mechanical properties",
            "Ion transport and electrochemical performance",
            "Battery-level demonstrations across relevant battery systems",
            "Testing conditions affecting reported performance",
        ],
    },
}


def make_prompt(topic, queries):
    meta = TOPICS[topic]
    numbered = [{"id": f"Q{i}", **q} for i, q in enumerate(queries, start=1)]
    return f"""Evaluate a set of proposed scientific literature-search queries.

Task source: Lisa's group assignment on 2026-10-19: examine relevance,
coverage, and ambiguity; this judge is an independent review call.
The numeric rubric below is a project-specific proposal, NOT a published
rubric or a ground-truth measurement of actual retrieval success.

Topic: {topic}
Target research scope: {meta['scope']}
Seed paper titles (for context only):
{json.dumps(meta['seed_titles'], ensure_ascii=False, indent=2)}

PREDEFINED COVERAGE ASPECTS (do not invent new aspects to inflate coverage):
{json.dumps(meta['coverage_aspects'], ensure_ascii=False, indent=2)}

Rate each query on the following 1–5 scales:
- relevance: 1 = unrelated; 2 = mostly off-topic; 3 = partly relevant or overly generic;
  4 = clearly relevant but with scope problems; 5 = clearly focused on the target.
- ambiguity_risk: 1 = clear meaning and search intent; 2 = minor uncertainty;
  3 = moderate ambiguity or mixed intent; 4 = significant ambiguity;
  5 = meaning or search intent is substantially unclear.

For each query, give one concise reason tied to specific wording in that query.
Separately flag obvious Boolean query syntax concerns. Syntax concerns are
NOT verified API parsing failures. Do not run search or claim retrieval results.

Rate COVERAGE only for the SET of queries: for each predefined aspect choose
"covered", "partial", or "missing" and give Q IDs supporting the rating.
A query's mere mention of one broad word does not establish full coverage.

Do not use or infer any prior human ratings. Do not confuse a plausible query
with demonstrated retrieval success. Be critical and specific, including
unsupported assumptions. Return exactly one JSON object shaped as:
{{
  "topic": "{topic}",
  "query_scores": [
    {{"id": "Q1", "relevance": 1, "ambiguity_risk": 1,
      "reason": "...", "syntax_concerns": ["..."]}}
  ],
  "set_coverage": [
    {{"aspect": "exact aspect string from list above",
      "status": "covered", "supporting_queries": ["Q1"], "reason": "..."}}
  ],
  "overall_observations": ["..."],
  "limitations": ["..."]
}}
Provide one query_scores item per query, and one set_coverage item per aspect.

QUERIES TO EVALUATE:
{json.dumps(numbered, ensure_ascii=False, indent=2)}
"""


def evaluate(topic):
    input_path = Path("runs") / "polymer" / topic / "v0" / "queries.json"
    if not input_path.is_file():
        raise SystemExit(f"Missing file: {input_path}")
    source_bytes = input_path.read_bytes()
    queries = json.loads(source_bytes)
    if not isinstance(queries, list) or not queries:
        raise SystemExit(f"Expected a nonempty JSON list: {input_path}")
    prompt = make_prompt(topic, queries)
    cfg = get_config()
    out_dir = Path("runs/polymer/evaluation")
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"{topic}_v0"
    (prefix.with_suffix(".prompt.txt")).write_text(prompt, encoding="utf-8")

    print(f"Evaluating {topic}: {len(queries)} queries (one Azure LLM call)", flush=True)
    response = llm_chat([
        {"role": "system", "content": "You are a careful research query evaluator. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ], force_json=True)
    try:
        verdict = json.loads(response)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Judge did not return valid JSON: {exc}") from exc
    if not isinstance(verdict, dict):
        raise SystemExit("Judge output is not a JSON object")
    scores = verdict.get("query_scores")
    coverage = verdict.get("set_coverage")
    if not isinstance(scores, list) or len(scores) != len(queries):
        raise SystemExit("Judge response did not include one score per query")
    if not isinstance(coverage, list) or len(coverage) != len(TOPICS[topic]["coverage_aspects"]):
        raise SystemExit("Judge response did not include every coverage aspect")
    ids = {f"Q{i}" for i in range(1, len(queries) + 1)}
    if {r.get("id") for r in scores if isinstance(r, dict)} != ids:
        raise SystemExit("Judge query IDs do not match the input queries")
    expected_aspects = set(TOPICS[topic]["coverage_aspects"])
    if {r.get("aspect") for r in coverage if isinstance(r, dict)} != expected_aspects:
        raise SystemExit("Judge coverage aspects do not match predefined aspects")
    if any((not isinstance(r.get("relevance"), int) or not 1 <= r["relevance"] <= 5 or
            not isinstance(r.get("ambiguity_risk"), int) or not 1 <= r["ambiguity_risk"] <= 5)
           for r in scores):
        raise SystemExit("Judge scores do not follow the 1–5 rubric")
    if any(r.get("status") not in ("covered", "partial", "missing") for r in coverage):
        raise SystemExit("Judge coverage status is invalid")
    record = {
        "rubric_version": RUBRIC_VERSION,
        "evidence": [
            "Lisa WeChat assignment 2026-10-19 14:45",
            "MemoSchema: query-quality dimensions, evaluation section",
            "Project-specific operational definitions; not published numeric scales",
        ],
        "evaluation_type": "LLM-as-a-judge, one run; no retrieval testing",
        "judge_model": cfg.chat_deployment if cfg.provider == "azure_openai" else cfg.chat_model,
        "judge_provider": cfg.provider,
        "same_model_as_generator_possible": True,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "input_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "target_scope": TOPICS[topic]["scope"],
        "judge_output": verdict,
    }
    result_path = prefix.with_suffix(".judge.json")
    result_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    mean_rel = sum(x["relevance"] for x in scores) / len(scores)
    mean_ambiguity = sum(x["ambiguity_risk"] for x in scores) / len(scores)
    counts = {status: sum(x["status"] == status for x in coverage) for status in ("covered", "partial", "missing")}
    print(f"Saved: {result_path}")
    print(f"Mean relevance: {mean_rel:.2f}/5 | Mean ambiguity risk: {mean_ambiguity:.2f}/5")
    print(f"Set coverage: {counts['covered']} covered, {counts['partial']} partial, {counts['missing']} missing")
    print("These are subjective LLM ratings, not retrieval-accuracy measurements.")


def main():
    parser = argparse.ArgumentParser(description="Review generated polymer search queries with an LLM judge")
    parser.add_argument("--topic", choices=["disulfide", "batteries", "all"], default="all")
    args = parser.parse_args()
    for topic in (TOPICS if args.topic == "all" else [args.topic]):
        evaluate(topic)


if __name__ == "__main__":
    main()
