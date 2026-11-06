"""Compare property-record extraction methods on the same papers.

Automatic metrics (no human labels needed):
  volume        records, records per research paper, distinct samples / properties
  grounding     evidence verified verbatim, page located
  leakage       records from review papers, from Introduction passages, with
                citation markers in the evidence, with control-like sample names
  completeness  unit / test_condition / application / material_class filled
  cost          LLM calls and prompt characters (from the prompt manifests)
  agreement     values found by several methods (key = paper + property + value,
                and a loose key = paper + value + unit), majority-vote recall,
                and whether methods attach a shared value to the same sample
                (sample names canonicalized with method C's alias inventory)

Also writes adjudication_sheet.csv: one row per unique value across methods,
with each method's sample and evidence and an empty `label` column for a human
judge (correct / wrong_value / wrong_sample / not_this_work / not_a_property).

Usage:
  python evaluate_records.py --topics disulfide battery --out_dir tests/polymer/compare/report
  python evaluate_records.py --base compare_v2 --out_dir tests/polymer/compare_v2/report
  # any output folders of extract_property_records.py (one or more methods):
  python evaluate_records.py --run B=runs/polymer/battery/run_01/04_records/B --out_dir OUT
"""
from __future__ import annotations

import argparse, csv, json, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent
TP = ROOT / "tests" / "polymer"

# Where each method's outputs live (records + prompt manifests).
METHODS = {
    "A_fulltext":     {"records": "compare/A/{t}/records.jsonl",
                       "manifests": ["compare/A/{t}/prompts/manifest.json"]},
    "B_sectioned":    {"records": "{t}/3_property_records/records.jsonl",
                       "manifests": ["{t}/3_property_records/prompts/manifest.json"]},
    "C_sample_first": {"records": "compare/C/{t}/records.jsonl",
                       "manifests": ["compare/C/{t}/prompts_1/manifest.json",
                                     "compare/C/{t}/prompts_2/manifest.json"]},
}
INVENTORY = "compare/C/{t}/samples"

def use_base(base: str) -> None:
    """All three methods under one folder, e.g. tests/polymer/compare_v2/{A,B,C}/{t}."""
    global INVENTORY
    for key, m in (("A_fulltext", "A"), ("B_sectioned", "B"), ("C_sample_first", "C")):
        METHODS[key] = {"records": f"{base}/{m}/{{t}}/records.jsonl",
                        "manifests": [f"{base}/{m}/{{t}}/prompts/manifest.json",
                                      f"{base}/{m}/{{t}}/prompts_2/manifest.json"]}
    INVENTORY = f"{base}/C/{{t}}/samples"

def use_runs(runs: Dict[str, str]) -> None:
    """Explicit output folders of extract_property_records.py, {name: dir}.
    Cost comes from the dump manifests (agent mode) or llm_calls*.json (API mode)."""
    global INVENTORY
    METHODS.clear()
    for name, d in runs.items():
        d = Path(d).resolve()
        METHODS[name] = {"records": str(d / "records.jsonl"),
                         "manifests": [str(d / f) for f in (
                             "prompts/manifest.json", "prompts_1/manifest.json",
                             "prompts_2/manifest.json", "llm_calls.json",
                             "llm_calls_1.json", "llm_calls_2.json")]}
        if (d / "samples").is_dir():
            INVENTORY = str(d / "samples")

def _true(x) -> bool:
    return x is True or str(x).lower() == "true"

_DASHES = dict.fromkeys(map(ord, "−–—‐‑"), "-")
_CITATION_RE = re.compile(r"\[\d+(?:\s*[,–\-]\s*\d+)*\]|\bet al\b")
_CONTROL_RE = re.compile(r"\b(control|reference|commercial|pristine|blank|liquid electrolyte|"
                         r"celgard|3M)\b", re.I)

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("\xad", "").translate(_DASHES)
    return re.sub(r"\s+", " ", s).strip().lower()

def norm_value(v: str) -> str:
    v = norm(v).replace("x 10", "×10").replace("× 10", "×10").replace(" ", "")
    return v.lstrip("~≈")

def load_records(path: Path) -> List[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        paper = json.loads(line)
        for r in paper["records"]:
            rows.append({**r, "paper_id": paper["paper_id"],
                         "paper_type": (paper.get("paper_type") or "").lower()})
    return rows

def load_cost(paths: List[Path]) -> Tuple[int, int]:
    calls = chars = 0
    for p in paths:
        if p.exists():
            entries = json.loads(p.read_text(encoding="utf-8"))
            calls += len(entries)
            chars += sum(e.get("chars", 0) for e in entries)
    return calls, chars

def load_alias_index(topic: str) -> Dict[str, List[Tuple[str, List[str]]]]:
    """paper_id -> [(canonical name, [name + aliases normalized])] from method C step 1."""
    idx = {}
    for f in sorted((TP / INVENTORY.format(t=topic)).glob("*.json")):
        inv = json.loads(f.read_text(encoding="utf-8"))
        idx[f.stem] = [(s["name"], [norm(s["name"])] + [norm(a) for a in s["aliases"]])
                       for s in inv["samples"]]
    return idx

def canonical_sample(paper: str, sample: str, alias_idx) -> str:
    """Map a method's sample string to C's canonical name: exact alias match,
    else the longest alias contained in the string; fallback = normalized string."""
    s = norm(sample)
    entries = alias_idx.get(paper, [])
    for name, terms in entries:
        if s in terms:
            return name
    best, best_len = None, 0
    for name, terms in entries:
        for t in terms:
            if len(t) > best_len and len(t) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", s):
                best, best_len = name, len(t)
    return best or s

_TOK_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")

# Descriptive words that do not change which sample is meant.
_SAMPLE_STOP = {"mn", "kda", "initiator", "initiated", "by", "with", "of", "the", "a",
                "sample", "film", "films", "polymer", "material"}

def _sample_tokens(s: str) -> frozenset:
    s = re.sub(r"-(initiated|initiator|based)\b", r" \1", norm(s))  # "FeCl3-initiated"
    s = re.sub(r"(?<=[a-z0-9])-(?=[a-z0-9])", "_", s)              # keep "v-spe-peg" whole
    return frozenset(t for t in re.findall(r"[a-z0-9_]+(?:\.[0-9]+)?", s) if t not in _SAMPLE_STOP)

def same_sample(paper: str, a: str, b: str, alias_idx) -> bool:
    """Same sample only if C's alias table maps both strings to the SAME
    inventory entry by exact alias match, or the two names have identical
    word sets after dropping descriptive words ("PTA (Mn = 300.4 kDa)" vs
    "PTA (Mn 300.4 kDa)"; "SSH-PIL2 (healed)" vs "healed SSH-PIL2").
    Containment is NOT enough: "V-SPE" vs "V-SPE-PEG" are different samples."""
    ca =[name for name, terms in alias_idx.get(paper, []) if norm(a) in terms]
    cb = [name for name, terms in alias_idx.get(paper, []) if norm(b) in terms]
    if ca and cb:
        return bool(set(ca) & set(cb))
    ta, tb = _sample_tokens(a), _sample_tokens(b)
    return bool(ta) and ta == tb

def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"

def evaluate(topics: List[str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data: Dict[str, List[dict]] = {}
    cost: Dict[str, Tuple[int, int]] = {}
    alias_idx = {}
    for t in topics:
        alias_idx.update(load_alias_index(t))
    for m, spec in METHODS.items():
        data[m] = [dict(r, topic=t) for t in topics
                   for r in load_records(TP / spec["records"].format(t=t))]
        c = [load_cost([TP / mf.format(t=t) for mf in spec["manifests"]]) for t in topics]
        cost[m] = (sum(x[0] for x in c), sum(x[1] for x in c))

    # ---- per-method metrics
    summary = []
    for m, rows in data.items():
        research = [r for r in rows if r["paper_type"] != "review"]
        n = len(rows)
        papers_with = len({r["paper_id"] for r in research})
        summary.append({
            "method": m,
            "records": n,
            "research_papers_with_records": papers_with,
            "records_per_research_paper": f"{len(research) / papers_with:.1f}" if papers_with else "-",
            "distinct_samples": len({(r["paper_id"], norm(r["sample"])) for r in rows}),
            "distinct_properties": len({r["property"] for r in rows}),
            "evidence_verified": pct(sum(bool(r.get("evidence_verified")) for r in rows), n),
            "page_located": pct(sum(bool(r.get("page")) for r in rows), n),
            "LEAK_review_records": sum(r["paper_type"] == "review" for r in rows),
            "LEAK_intro_section": sum(r.get("section") == "introduction" for r in rows),
            "LEAK_citation_in_evidence": sum(bool(_CITATION_RE.search(r["evidence"])) for r in rows),
            "LEAK_control_like_sample": sum(bool(_CONTROL_RE.search(r["sample"])) for r in rows),
            "LEAK_outside_main_text": sum(bool(r.get("evidence_verified")) and not r.get("section")
                                          for r in rows),
            "unit_filled": pct(sum(bool(r["unit"]) for r in rows), n),
            "condition_filled": pct(sum(bool(r["test_condition"]) for r in rows), n),
            "application_filled": pct(sum(bool(r["application"]) for r in rows), n),
            "material_class_filled": pct(sum(bool(r["material_class"]) for r in rows), n),
            # traceability (only meaningful for answers produced with the
            # sample_evidence / condition_evidence / derivation fields)
            "TRACE_value_in_evidence": pct(sum(_true(r.get("value_in_evidence")) for r in rows),
                                           sum(r.get("value_in_evidence", "") != "" for r in rows)),
            "TRACE_unit_in_evidence": pct(sum(_true(r.get("unit_in_evidence")) for r in rows),
                                          sum(r.get("unit_in_evidence", "") != "" for r in rows)),
            "TRACE_sample_evidence_verified": pct(sum(_true(r.get("sample_evidence_verified")) for r in rows), n),
            "TRACE_condition_evidence_verified": pct(
                sum(_true(r.get("condition_evidence_verified")) for r in rows),
                sum(r.get("condition_evidence_verified", "") != "" for r in rows)),
            "TRACE_records_with_flags": sum(bool(r.get("flags")) for r in rows),
            "TRACE_derivation": ", ".join(f"{k}:{v}" for k, v in Counter(
                d for r in rows for d in (r.get("derivation") or "").split(",") if d).most_common()),
            "llm_calls": cost[m][0],
            "prompt_chars": cost[m][1],
            "prompt_chars_per_record": f"{cost[m][1] / n:,.0f}" if n else "-",
        })

    # ---- agreement between methods
    def keyset(rows, loose=False) -> Dict[tuple, dict]:
        ks = {}
        for r in rows:
            k = ((r["paper_id"], norm_value(r["value"]), norm(r["unit"])) if loose
                 else (r["paper_id"], r["property"], norm_value(r["value"])))
            ks.setdefault(k, r)
        return ks
    methods = list(data)
    agree_rows = []
    for loose in (False, True):
        ks = {m: keyset(data[m], loose) for m in methods}
        union: Set[tuple] = set().union(*ks.values())
        found_by = {k: [m for m in methods if k in ks[m]] for k in union}
        majority = {k for k, ms in found_by.items() if len(ms) >= 2}
        for m in methods:
            agree_rows.append({
                "key": "paper+value+unit" if loose else "paper+property+value",
                "method": m, "unique_values": len(ks[m]),
                "found_only_by_this_method": sum(1 for k, ms in found_by.items() if ms == [m]),
                "majority_recall": pct(len(majority & set(ks[m])), len(majority)),
                "share_confirmed_by_another_method": pct(sum(1 for k in ks[m] if len(found_by[k]) >= 2), len(ks[m])),
            })
        agree_rows.append({"key": "paper+value+unit" if loose else "paper+property+value",
                           "method": "ALL", "unique_values": len(union),
                           "found_only_by_this_method": "",
                           "majority_recall": f"{len(majority)} majority values",
                           "share_confirmed_by_another_method":
                               f"{sum(1 for ms in found_by.values() if len(ms) == len(methods))} found by all"})

    # ---- sample binding: on shared values, do methods name the same sample?
    ks = {m: keyset(data[m]) for m in methods}
    binding = []
    for i, a in enumerate(methods):
        for b in methods[i + 1:]:
            shared = set(ks[a]) & set(ks[b])
            same = sum(same_sample(k[0], ks[a][k]["sample"], ks[b][k]["sample"], alias_idx)
                       for k in shared)
            binding.append({"pair": f"{a} vs {b}", "shared_values": len(shared),
                            "same_sample": same, "agreement": pct(same, len(shared))})

    # ---- adjudication sheet (union, strict key)
    union = sorted(set().union(*[set(k) for k in ks.values()]))
    with open(out_dir / "adjudication_sheet.csv", "w", encoding="utf-8", newline="") as fh:
        cols = ["paper_id", "property", "value", "unit", "found_by"] + \
               [f"{m}_sample" for m in methods] + ["evidence", "page", "section", "label"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for k in union:
            src = next(ks[m][k] for m in methods if k in ks[m])
            row = {"paper_id": k[0], "property": k[1], "value": src["value"], "unit": src["unit"],
                   "found_by": "+".join(m.split("_")[0] for m in methods if k in ks[m]),
                   "evidence": src["evidence"], "page": src.get("page", ""),
                   "section": src.get("section", ""), "label": ""}
            for m in methods:
                row[f"{m}_sample"] = ks[m][k]["sample"] if k in ks[m] else ""
            w.writerow(row)

    # ---- per-paper record counts
    papers = sorted({r["paper_id"] for rows in data.values() for r in rows})
    per_paper = [{"paper_id": p, **{m: sum(r["paper_id"] == p for r in data[m]) for m in methods}}
                 for p in papers]

    for name, rows in (("summary.csv", summary), ("agreement.csv", agree_rows),
                       ("sample_binding.csv", binding), ("per_paper.csv", per_paper)):
        if not rows:          # e.g. agreement / binding with a single method
            continue
        with open(out_dir / name, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    def md(rows: List[dict]) -> str:
        if not rows:
            return "(needs at least two methods)"
        cols = list(rows[0].keys())
        lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
        return "\n".join(lines)
    transposed = [{"metric": k, **{s["method"]: s[k] for s in summary}}
                  for k in summary[0] if k != "method"]
    report = (f"# Extraction method comparison ({', '.join(topics)})\n\n"
              f"## Per-method metrics\n\n{md(transposed)}\n\n"
              f"## Agreement between methods\n\n{md(agree_rows)}\n\n"
              f"## Sample binding on shared values\n\n{md(binding)}\n\n"
              f"## Records per paper\n\n{md(per_paper)}\n")
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--topics", nargs="+", default=["disulfide", "battery"])
    p.add_argument("--out_dir", default=str(TP / "compare" / "report"))
    p.add_argument("--base", default=None,
                   help="Folder (relative to tests/polymer) holding A/, B/, C/ per topic, e.g. compare_v2.")
    p.add_argument("--run", action="append", default=None, metavar="NAME=DIR",
                   help="Output folder of extract_property_records.py; repeat to compare methods.")
    a = p.parse_args()
    if a.run:
        use_runs(dict(r.split("=", 1) for r in a.run))
        a.topics = ["run"]          # paths have no {t}; load each folder once
    elif a.base:
        use_base(a.base)
    evaluate(a.topics, Path(a.out_dir))

if __name__ == "__main__":
    main()
