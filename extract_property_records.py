"""Extract property records (sample / property / value / unit / condition /
application + verbatim evidence and location) from every PDF in a folder.

Three methods share the record format, the rules
(extract_lib/property_record_instructions.md) and the post-processing:

  --method B  (default) sectioned, labeled passages; one call per paper
  --method A  full raw text, unlabeled; one call per paper (baseline)
  --method C  sample-first: --stage 1 lists samples + aliases (one call per
              paper), --stage 2 extracts per sample from the passages that
              mention it (one call per sample), then merges

LLM modes (any method / stage):
  default               call the configured LLM (see .env)
  --dump_prompts DIR    write prompt files + manifest.json, no LLM calls
  --from_responses DIR  read answers from DIR instead of calling the LLM
                        (A/B/C1: DIR/<paper_id>.json; C2: DIR/<paper_id>/<k>.json)

Outputs (--out_dir): records.jsonl, records.csv; method C also writes
samples/<paper_id>.json (step 1), samples.csv and conflicts.csv.

Examples:
  python extract_property_records.py --pdf_dir data/seeds_battery --out_dir OUT
  python extract_property_records.py --method A --pdf_dir data/seeds_battery --out_dir OUT --dump_prompts OUT/prompts
  python extract_property_records.py --method C --stage 1 --pdf_dir data/seeds_battery --out_dir OUT --dump_prompts OUT/prompts_1
  python extract_property_records.py --method C --stage 1 --pdf_dir data/seeds_battery --out_dir OUT --from_responses OUT/responses_1
  python extract_property_records.py --method C --stage 2 --pdf_dir data/seeds_battery --out_dir OUT --dump_prompts OUT/prompts_2
  python extract_property_records.py --method C --stage 2 --pdf_dir data/seeds_battery --out_dir OUT --from_responses OUT/responses_2
"""
from __future__ import annotations

import argparse, csv, json, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from extract_lib.fulltext_extract import build_fulltext_prompt, fulltext_pages
from extract_lib.llm_extract import _robust_json_parse, build_chat_client, call_extract, get_model_deployment
from extract_lib.pdf_io import extract_pdf_blocks, extract_pdf_text
from extract_lib.record_extract import (
    build_record_prompt,
    normalize_records,
    normalize_records_fulltext,
    record_passages,
    write_outputs,
)
from extract_lib.sample_first_extract import (
    build_inventory_prompt,
    build_sample_prompt,
    merge_sample_records,
    own_samples,
    parse_inventory,
    select_passages_for_sample,
)

RECORD_MAX_COMPLETION_TOKENS = 16000   # dozens of records per paper

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Property-record extraction with evidence.")
    p.add_argument("--pdf_dir", required=True, help="Folder of PDFs.")
    p.add_argument("--out_dir", required=True, help="Where to write records.jsonl / records.csv.")
    p.add_argument("--method", choices=["A", "B", "C"], default="B",
                   help="A = full text, B = sectioned passages (default), C = sample-first.")
    p.add_argument("--stage", type=int, choices=[1, 2], default=None,
                   help="Method C only: 1 = sample inventory, 2 = per-sample extraction.")
    p.add_argument("--max_papers", type=int, default=-1, help="First N PDFs (alphabetical); -1 = all.")
    p.add_argument("--paper", action="append", default=None,
                   help="Only this PDF (file stem, without .pdf); repeat for several.")
    p.add_argument("--model", default=None, help="Override chat model / deployment.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dump_prompts", default=None,
                      help="Write prompts to this folder instead of calling the LLM.")
    mode.add_argument("--from_responses", default=None,
                      help="Read answers from this folder instead of calling the LLM.")
    args = p.parse_args()
    if args.method == "C" and args.stage is None:
        p.error("--method C needs --stage 1 or --stage 2")
    return args

class LLM:
    """One place for the three ways of getting an answer: real call, dump the
    prompt for another agent, or read a saved answer."""
    def __init__(self, args):
        self.args, self.client, self.model = args, None, None
        self.manifest: List[Dict[str, Any]] = []
        if not (args.dump_prompts or args.from_responses):
            self.client = build_chat_client()
            self.model = args.model or get_model_deployment()
            print(f"🤖 Using chat model/deployment: {self.model}")

    def ask(self, key: str, system: str, user: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """key = relative file stem, e.g. "paper" or "paper/3"."""
        a = self.args
        if a.dump_prompts:
            f = Path(a.dump_prompts) / f"{key}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}", encoding="utf-8")
            # Answers go next to the prompts: prompts_1/ -> responses_1/, prompts/ -> responses/
            rdir = Path(a.dump_prompts).parent / Path(a.dump_prompts).name.replace("prompts", "responses")
            self.manifest.append({**meta, "prompt": str(f), "chars": len(system) + len(user),
                                  "response": str(rdir / f"{key}.json")})
            return None
        if a.from_responses:
            f = Path(a.from_responses) / f"{key}.json"
            return _robust_json_parse(f.read_text(encoding="utf-8")) if f.exists() else None
        return call_extract(self.client, self.model, system, user, meta.get("paper_id", key),
                            max_completion_tokens=RECORD_MAX_COMPLETION_TOKENS)

    def save_manifest(self) -> None:
        if self.args.dump_prompts:
            Path(self.args.dump_prompts, "manifest.json").write_text(
                json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8")

def _passages(pdf: Path):
    blocks = extract_pdf_blocks(pdf)
    return record_passages(blocks if blocks else extract_pdf_text(pdf))

def _report(i: int, n: int, paper_id: str, rec: Dict[str, Any]) -> None:
    ok = sum(r["evidence_verified"] for r in rec["records"])
    print(f"[{i}/{n}] ✅ {paper_id}: {len(rec['records'])} records ({ok} evidence verified)")

# Methods A / B
def run_single_call(args, llm: LLM, pdfs: List[Path]) -> List[Dict[str, Any]]:
    papers = []
    for i, pdf in enumerate(pdfs, 1):
        pid = pdf.stem
        paras = _passages(pdf)
        if args.method == "A":
            pages = fulltext_pages(pdf)
            system, user = build_fulltext_prompt(pid, pages)
        else:
            system, user = build_record_prompt(pid, paras)
        raw = llm.ask(pid, system, user, {"paper_id": pid})
        if args.dump_prompts:
            print(f"[{i}/{len(pdfs)}] 📝 {pid}: {len(system) + len(user):,} chars")
            continue
        if raw is None:
            print(f"[{i}/{len(pdfs)}] ❌ no usable answer: {pid}")
            continue
        rec = (normalize_records_fulltext(raw, pid, pages, paras) if args.method == "A"
               else normalize_records(raw, pid, paras))
        papers.append(rec)
        _report(i, len(pdfs), pid, rec)
    return papers

# Method C
def run_sample_first(args, llm: LLM, pdfs: List[Path]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    out_dir = Path(args.out_dir)
    inv_dir = out_dir / "samples"
    papers, conflicts = [], []
    for i, pdf in enumerate(pdfs, 1):
        pid = pdf.stem
        paras = _passages(pdf)

        if args.stage == 1:
            system, user = build_inventory_prompt(pid, paras)
            raw = llm.ask(pid, system, user, {"paper_id": pid, "stage": 1})
            if args.dump_prompts:
                print(f"[{i}/{len(pdfs)}] 📝 {pid}: {len(system) + len(user):,} chars")
                continue
            if raw is None:
                print(f"[{i}/{len(pdfs)}] ❌ no usable answer: {pid}")
                continue
            inv = parse_inventory(raw)
            inv_dir.mkdir(parents=True, exist_ok=True)
            (inv_dir / f"{pid}.json").write_text(json.dumps(inv, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
            roles = [s["role"] for s in inv["samples"]]
            print(f"[{i}/{len(pdfs)}] 🧪 {pid}: {len(roles)} samples "
                  f"({roles.count('this_work')} this_work, {roles.count('control')} control, "
                  f"{roles.count('prior_work')} prior_work)")
            continue

        # stage 2
        inv_file = inv_dir / f"{pid}.json"
        if not inv_file.exists():
            print(f"[{i}/{len(pdfs)}] ⚠️  no step-1 inventory for {pid}; run --stage 1 first")
            continue
        inv = json.loads(inv_file.read_text(encoding="utf-8"))
        per_sample = []
        for k, sample in enumerate(own_samples(inv)):
            idx = select_passages_for_sample(sample, paras)
            system, user = build_sample_prompt(pid, sample, inv, paras, idx)
            raw = llm.ask(f"{pid}/{k}", system, user,
                          {"paper_id": pid, "stage": 2, "k": k, "sample": sample["name"],
                           "n_passages": len(idx)})
            if raw is not None:
                per_sample.append(normalize_records(raw, pid, paras, force_sample=sample["name"]))
        if args.dump_prompts:
            print(f"[{i}/{len(pdfs)}] 📝 {pid}: {len(own_samples(inv))} sample prompts")
            continue
        records, conf = merge_sample_records(per_sample)
        rec = {"paper_id": pid, "paper_type": inv.get("paper_type", ""), "records": records}
        papers.append(rec)
        conflicts += [{"paper_id": pid, **c} for c in conf]
        _report(i, len(pdfs), pid, rec)
    return papers, conflicts

def _write_c_extras(out_dir: Path, conflicts: List[Dict[str, Any]]) -> None:
    inv_rows = []
    for f in sorted((out_dir / "samples").glob("*.json")):
        inv = json.loads(f.read_text(encoding="utf-8"))
        for s in inv["samples"]:
            inv_rows.append({"paper_id": f.stem, "name": s["name"], "aliases": " | ".join(s["aliases"]),
                             "role": s["role"], "material_class": s["material_class"],
                             "description": s["description"], "application": s["application"],
                             "evidence": s["evidence"], "passage": s["passage"]})
    for name, rows in (("samples.csv", inv_rows), ("conflicts.csv", conflicts)):
        with open(out_dir / name, "w", encoding="utf-8", newline="") as fh:
            cols = list(rows[0].keys()) if rows else ["paper_id"]
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

def main() -> int:
    args = _parse_args()
    pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))
    if args.paper:
        pdfs = [p for p in pdfs if p.stem in set(args.paper)]
    if args.max_papers >= 0:
        pdfs = pdfs[: args.max_papers]
    if not pdfs:
        print(f"❌ No PDFs found in {args.pdf_dir}")
        return 1

    llm = LLM(args)
    out_dir = Path(args.out_dir)
    if args.method == "C":
        papers, conflicts = run_sample_first(args, llm, pdfs)
        if args.stage == 1 and not args.dump_prompts:
            _write_c_extras(out_dir, [])
    else:
        papers, conflicts = run_single_call(args, llm, pdfs), []
    llm.save_manifest()
    if args.dump_prompts or (args.method == "C" and args.stage == 1):
        return 0

    jsonl, csv_path = write_outputs(papers, out_dir)
    if args.method == "C":
        _write_c_extras(out_dir, conflicts)
        print(f"conflicts → {out_dir / 'conflicts.csv'} ({len(conflicts)})")
    print(f"\njsonl → {jsonl}\ncsv   → {csv_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
