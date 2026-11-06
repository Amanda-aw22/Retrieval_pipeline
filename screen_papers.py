"""Screen a retrieved corpus before property-record extraction.

No new scoring: this applies the two relevance signals the original artifact
already has, but to the corpus that Phase II will read (in the artifact they
only steer schema sampling / the optional --top_n of extract_schema_values.py):

  1. topic relevance   schema_lib.sampling.relevance_filtered_sample
                       (embedding similarity to the last-tier text, keyword
                       overlap if embeddings are unavailable); the least
                       relevant --drop_fraction of papers is dropped.
  2. schema coverage   extract_lib.retrieval.paper_relevance_signal
                       (how many schema fields have a matching passage);
                       papers covering fewer than --min_fields are dropped,
                       and --top_n keeps only the best-covered N.

Outputs (--out_dir):
  screen.csv / screen.json   one row per PDF with both scores and the decision
  corpus/                    copies of the kept PDFs (the Phase II --pdf_dir)

Example:
  python screen_papers.py --domain polymer --last_tier batteries \
      --pdf_dir runs/polymer/batteries/run_01/01_phase1/cumulative_corpus \
      --schema_path runs/polymer/batteries/run_01/01_phase1/final_schemas/final_schema.json \
      --out_dir runs/polymer/batteries/run_01/02_screen
"""
from __future__ import annotations

import argparse, csv, json, shutil, sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from extract_lib.pdf_io import extract_pdf_blocks, extract_pdf_text
from extract_lib.retrieval import paper_relevance_signal
from extract_lib.schema_loader import load_schema
from schema_lib.domain import resolve_domain_and_last_tier
from schema_lib.sampling import load_pdf_texts, relevance_filtered_sample

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Relevance screening of a PDF corpus.")
    p.add_argument("--domain", required=True)
    p.add_argument("--last_tier", default=None)
    p.add_argument("--pdf_dir", required=True)
    p.add_argument("--out_dir", required=True)
    p.add_argument("--schema_path", default=None,
                   help="Phase I final_schema.json; enables the schema-coverage check.")
    p.add_argument("--drop_fraction", type=float, default=0.25,
                   help="Least topic-relevant fraction to drop (artifact default 0.25).")
    p.add_argument("--min_fields", type=int, default=1,
                   help="Minimum schema fields covered to keep a paper.")
    p.add_argument("--top_n", type=int, default=None,
                   help="Keep at most N papers, best schema coverage first.")
    p.add_argument("--extra_keywords", default="",
                   help="Comma-separated keywords added to the last-tier text.")
    p.add_argument("--relevance_chars", type=int, default=4000)
    return p.parse_args()

def screen(args) -> List[Dict[str, Any]]:
    pdf_dir = Path(args.pdf_dir)
    _, _, last_tier_text = resolve_domain_and_last_tier(args)
    papers = load_pdf_texts(pdf_dir, relevance_chars=args.relevance_chars, proposal_chars=0)

    # 1) topic relevance (keep everything that survives the bottom-fraction drop)
    _, rel_rows = relevance_filtered_sample(
        papers=papers, last_tier_text=last_tier_text,
        extra_keywords=[k.strip() for k in args.extra_keywords.split(",") if k.strip()],
        max_papers=len(papers), drop_fraction=args.drop_fraction)
    rows = {r["source_file"]: {"paper": r["source_file"],
                               "topic_score": r["relevance_score"],
                               "topic_rank": r["rank"],
                               "topic_method": r["method"],
                               "topic_ok": not r["dropped_low_relevance"]}
            for r in rel_rows}

    # 2) schema coverage
    fields = load_schema(Path(args.schema_path)) if args.schema_path else []
    for name, row in rows.items():
        if not fields:
            row.update(fields_covered="", coverage_score="", coverage_ok=True)
            continue
        pdf = pdf_dir / name
        source = extract_pdf_blocks(pdf) or extract_pdf_text(pdf)
        rel = paper_relevance_signal(source, fields)
        row.update(fields_covered=rel.n_fields_covered,
                   coverage_score=round(rel.total_best_score, 3),
                   coverage_ok=rel.n_fields_covered >= args.min_fields)

    out = sorted(rows.values(), key=lambda r: r["topic_rank"])
    passed = [r for r in out if r["topic_ok"] and r["coverage_ok"]]
    if args.top_n and fields:
        passed.sort(key=lambda r: (-r["fields_covered"], -r["coverage_score"]))
        passed = passed[: args.top_n]
    kept = {r["paper"] for r in passed}
    for r in out:
        r["kept"] = r["paper"] in kept
        r["reason"] = ("kept" if r["kept"] else
                       "low topic relevance" if not r["topic_ok"] else
                       f"covers < {args.min_fields} schema fields" if not r["coverage_ok"] else
                       f"outside top {args.top_n}")
    return out

def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir)
    corpus = out_dir / "corpus"
    out_dir.mkdir(parents=True, exist_ok=True)
    if corpus.exists():
        shutil.rmtree(corpus)
    corpus.mkdir()

    rows = screen(args)
    for r in rows:
        if r["kept"]:
            shutil.copy(Path(args.pdf_dir) / r["paper"], corpus / r["paper"])
    (out_dir / "screen.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    with open(out_dir / "screen.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_kept = sum(r["kept"] for r in rows)
    print(f"[screen] kept {n_kept}/{len(rows)} PDFs -> {corpus}")
    for r in rows:
        if not r["kept"]:
            print(f"   ✗ {r['paper'][:90]}  ({r['reason']})")
    return 0 if n_kept else 1

if __name__ == "__main__":
    raise SystemExit(main())
