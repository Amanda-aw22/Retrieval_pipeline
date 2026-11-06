"""Turn a Phase I final_schema.json into the Phase II property vocabulary.

  python build_property_vocab.py \
      --schema_path runs/polymer/battery/run_01/01_phase1/final_schemas/final_schema.json \
      --out runs/polymer/battery/run_01/03_vocab/property_vocab.json

Writes property_vocab.json (properties, descriptive fields, merged umbrella
map, alias conflicts) and property_vocab_prompt.md (the prompt section that
extract_property_records.py --vocab appends). See extract_lib/property_vocab.py.
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from extract_lib.property_vocab import build_vocab, render_vocab_section

def main() -> int:
    p = argparse.ArgumentParser(description="Phase I schema -> property vocabulary.")
    p.add_argument("--schema_path", required=True)
    p.add_argument("--out", required=True, help="Output property_vocab.json path.")
    a = p.parse_args()

    vocab = build_vocab(Path(a.schema_path))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(vocab, indent=2, ensure_ascii=False), encoding="utf-8")
    out.with_name("property_vocab_prompt.md").write_text(render_vocab_section(vocab),
                                                          encoding="utf-8")

    print(f"[vocab] {len(vocab['properties'])} property fields, "
          f"{len(vocab['descriptive_fields'])} descriptive fields, "
          f"{len(vocab['conflicts'])} alias conflicts -> {out}")
    for prop in vocab["properties"]:
        print(f"   {prop['schema_field']:<34} -> {prop['name']:<34} [{prop['status']}] "
              f"+{len(prop['aliases'])} aliases")
    for d in vocab["descriptive_fields"]:
        print(f"   {d['name']:<34}    (descriptive)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
