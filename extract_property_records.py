"""Extract property records (sample / property / value / unit / condition /
application + verbatim evidence and location) from every PDF in a folder.

Instructions for the LLM: extract_lib/property_record_instructions.md

Modes:
  default             call the configured LLM (see .env) for each paper
  --dump_prompts DIR  write one prompt file per paper + manifest.json, no LLM
                      calls (e.g. to have another agent answer them)
  --from_responses DIR  read DIR/<paper_id>.json answers instead of calling the LLM

Outputs (in --out_dir): records.jsonl (one line per paper), records.csv (one row
per record; evidence_verified = quote found verbatim in the cited passage).

Examples:
  python extract_property_records.py --pdf_dir data/seeds_battery --out_dir tests/polymer/battery/3_property_records
  python extract_property_records.py --pdf_dir data/seeds_battery --out_dir OUT --dump_prompts OUT/prompts
  python extract_property_records.py --pdf_dir data/seeds_battery --out_dir OUT --from_responses OUT/responses
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from extract_lib.llm_extract import _robust_json_parse, build_chat_client, call_extract, get_model_deployment
from extract_lib.pdf_io import extract_pdf_blocks, extract_pdf_text
from extract_lib.record_extract import (
    build_record_prompt,
    normalize_records,
    record_passages,
    write_outputs,
)

RECORD_MAX_COMPLETION_TOKENS = 16000   # dozens of records per paper

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Property-record extraction with evidence.")
    p.add_argument("--pdf_dir", required=True, help="Folder of PDFs.")
    p.add_argument("--out_dir", required=True, help="Where to write records.jsonl / records.csv.")
    p.add_argument("--max_papers", type=int, default=-1, help="First N PDFs (alphabetical); -1 = all.")
    p.add_argument("--model", default=None, help="Override chat model / deployment.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dump_prompts", default=None,
                      help="Write prompts to this folder instead of calling the LLM.")
    mode.add_argument("--from_responses", default=None,
                      help="Read <paper_id>.json answers from this folder instead of calling the LLM.")
    return p.parse_args()

def main() -> int:
    args = _parse_args()
    pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))
    if args.max_papers >= 0:
        pdfs = pdfs[: args.max_papers]
    if not pdfs:
        print(f"❌ No PDFs found in {args.pdf_dir}")
        return 1

    client = model = None
    if not (args.dump_prompts or args.from_responses):
        client = build_chat_client()
        model = args.model or get_model_deployment()
        print(f"🤖 Using chat model/deployment: {model}")

    manifest, papers = [], []
    for i, pdf in enumerate(pdfs, 1):
        paper_id = pdf.stem
        blocks = extract_pdf_blocks(pdf)
        paras = record_passages(blocks if blocks else extract_pdf_text(pdf))
        if not paras:
            print(f"[{i}/{len(pdfs)}] ⚠️  no text: {paper_id}")
            continue
        system, user = build_record_prompt(paper_id, paras)

        if args.dump_prompts:
            d = Path(args.dump_prompts); d.mkdir(parents=True, exist_ok=True)
            f = d / f"{paper_id}.txt"
            f.write_text(f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}", encoding="utf-8")
            manifest.append({"paper_id": paper_id, "prompt": str(f), "n_passages": len(paras),
                             "chars": len(user)})
            print(f"[{i}/{len(pdfs)}] 📝 {paper_id}: {len(paras)} passages, {len(user):,} chars")
            continue

        if args.from_responses:
            resp = Path(args.from_responses) / f"{paper_id}.json"
            raw = _robust_json_parse(resp.read_text(encoding="utf-8")) if resp.exists() else None
        else:
            raw = call_extract(client, model, system, user, paper_id,
                               max_completion_tokens=RECORD_MAX_COMPLETION_TOKENS)
        if raw is None:
            print(f"[{i}/{len(pdfs)}] ❌ no usable answer: {paper_id}")
            continue
        rec = normalize_records(raw, paper_id, paras)
        papers.append(rec)
        n_ok = sum(r["evidence_verified"] for r in rec["records"])
        print(f"[{i}/{len(pdfs)}] ✅ {paper_id}: {len(rec['records'])} records "
              f"({n_ok} evidence verified)")

    if args.dump_prompts:
        Path(args.dump_prompts, "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    jsonl, csv_path = write_outputs(papers, Path(args.out_dir))
    print(f"\njsonl → {jsonl}\ncsv   → {csv_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
