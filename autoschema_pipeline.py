"""One command from seed PDFs to evaluated property records.

Runs the existing scripts as subprocesses and passes each step's outputs to
the next one:

  1 phase1   iterative_expand.py        seeds -> corpus, metadata, frozen schema
  2 screen   screen_papers.py           topic relevance + schema coverage filter
  3 vocab    build_property_vocab.py    schema -> umbrella property names
  4 extract  extract_property_records.py  (per method in --methods)
  5 eval     evaluate_records.py

Resume: every step's status is kept in <run_dir>/run_manifest.json. Rerunning
the same command skips finished steps; inside `extract`, answers already on
disk are reused, so only missing papers are sent to the LLM.
--redo STEP marks STEP and every later step as pending again.

LLM modes:
  --llm_mode api    (default) call the LLM configured in .env
  --llm_mode agent  extraction prompts are written to files and the pipeline
                    stops (exit code 3); answer every prompt listed in the
                    printed manifest.json (e.g. with Claude subagents), then
                    rerun the same command to continue. Phase I and screening
                    always use .env (embeddings fall back to keywords).

Examples:
  # full run
  python autoschema_pipeline.py --domain polymer --last_tier battery \\
      --seed_dir data/seeds_battery --run_dir runs/polymer/battery/run_01

  # skip Phase I: use an existing corpus (and optionally schema / metadata)
  python autoschema_pipeline.py --domain polymer --last_tier battery \\
      --corpus_dir data/seeds_battery --run_dir runs/polymer/battery/seeds_only \\
      --schema_path tests/polymer/battery/trial_4papers/schema/final_schema.json \\
      --llm_mode agent --no_screen
"""
from __future__ import annotations

import argparse, json, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
STEPS = ["phase1", "screen", "vocab", "extract", "eval"]
WAITING = 3          # exit code: agent mode is waiting for responses

class Wait(Exception):
    """Agent mode: prompts are written, answers are missing."""

class StepFailed(Exception):
    pass

# ---------------------------------------------------------------- arguments
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AutoSchema end-to-end pipeline.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("Examples:")[1])
    p.add_argument("--domain", required=True)
    p.add_argument("--last_tier", default=None)
    p.add_argument("--run_dir", required=True, help="All outputs + run_manifest.json go here.")
    src = p.add_argument_group("input (one of)")
    src.add_argument("--seed_dir", default=None, help="Seed PDFs: run Phase I.")
    src.add_argument("--corpus_dir", default=None, help="Existing PDF corpus: skip Phase I.")
    p.add_argument("--schema_path", default=None,
                   help="With --corpus_dir: a final_schema.json to use for screening / vocab.")
    p.add_argument("--metadata", default=None,
                   help="With --corpus_dir: a cumulative_metadata.jsonl for DOI / title.")

    p1 = p.add_argument_group("phase1 (passed to iterative_expand.py; its defaults if omitted)")
    for name, typ in (("rounds", int), ("target_total_papers", int), ("results_per_source", int),
                      ("max_schema_papers", int), ("max_chars_per_paper", int),
                      ("max_final_fields", int), ("min_support", int), ("sources", str)):
        p1.add_argument(f"--{name}", type=typ, default=None)

    sc = p.add_argument_group("screen")
    sc.add_argument("--no_screen", action="store_true", help="Pass the corpus through unfiltered.")
    sc.add_argument("--drop_fraction", type=float, default=0.25)
    sc.add_argument("--min_fields", type=int, default=1)
    sc.add_argument("--top_n", type=int, default=None)

    ex = p.add_argument_group("extract")
    ex.add_argument("--methods", default="B",
                    help="Comma-separated extraction methods, e.g. B or A,B,C.")
    ex.add_argument("--llm_mode", choices=["api", "agent"], default="api")
    ex.add_argument("--model", default=None)
    ex.add_argument("--max_papers", type=int, default=-1)

    p.add_argument("--redo", choices=STEPS, default=None,
                   help="Rerun this step and all later steps.")
    a = p.parse_args()
    if bool(a.seed_dir) == bool(a.corpus_dir):
        p.error("give exactly one of --seed_dir (run Phase I) or --corpus_dir (skip it)")
    a.methods = [m.strip().upper() for m in a.methods.split(",") if m.strip()]
    if any(m not in "ABC" for m in a.methods):
        p.error("--methods takes A, B and/or C")
    return a

# ----------------------------------------------------------------- manifest
class Manifest:
    """<run_dir>/run_manifest.json: status + outputs of every step."""
    def __init__(self, run_dir: Path, args: argparse.Namespace):
        self.path = run_dir / "run_manifest.json"
        cfg = {k: v for k, v in vars(args).items() if k != "redo"}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            changed = sorted(k for k in cfg if self.data["config"].get(k) != cfg[k])
            if changed:
                print(f"⚠️  settings differ from the first run of this run_dir: {changed}\n"
                      f"   finished steps are NOT redone automatically; use --redo STEP if needed.")
        else:
            self.data = {"created": _now(), "config": cfg,
                         "steps": {s: {"status": "pending"} for s in STEPS}}
        self.data["config_last_run"] = cfg

    def step(self, name: str) -> Dict[str, Any]:
        return self.data["steps"][name]

    def set(self, name: str, **kw) -> None:
        self.data["steps"][name].update(kw, updated=_now())
        self.save()

    def reset_from(self, name: str) -> None:
        for s in STEPS[STEPS.index(name):]:
            self.data["steps"][s] = {"status": "pending"}
        self.save()

    def output(self, step: str, key: str) -> Optional[str]:
        return self.data["steps"][step].get("outputs", {}).get(key)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

def _run(script: str, *args: Any) -> None:
    cmd = [sys.executable, str(ROOT / script), *[str(a) for a in args]]
    print(f"\n$ {' '.join(cmd[1:])}", flush=True)
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        raise StepFailed(f"{script} exited with code {res.returncode}")

def _abs(p: Optional[str]) -> Optional[str]:
    return str(Path(p).resolve()) if p else None

def _existing(p: Path) -> Optional[str]:
    return str(p) if p.exists() else None

# -------------------------------------------------------------------- steps
def step_phase1(a, run_dir: Path, man: Manifest) -> Dict[str, Any]:
    if a.corpus_dir:
        return {"mode": "external", "corpus": _abs(a.corpus_dir),
                "schema": _abs(a.schema_path), "metadata": _abs(a.metadata)}
    out = run_dir / "01_phase1"
    cmd = ["--domain", a.domain, "--seed_dir", _abs(a.seed_dir), "--output_root", out]
    if a.last_tier:
        cmd += ["--last_tier", a.last_tier]
    for name in ("rounds", "target_total_papers", "results_per_source", "max_schema_papers",
                 "max_chars_per_paper", "max_final_fields", "min_support", "sources"):
        if getattr(a, name) is not None:
            cmd += [f"--{name}", getattr(a, name)]
    _run("iterative_expand.py", *cmd)
    return {"mode": "iterative_expand", "corpus": str(out / "cumulative_corpus"),
            "schema": _existing(out / "final_schemas" / "final_schema.json"),
            "metadata": _existing(out / "cumulative_metadata.jsonl")}

def step_screen(a, run_dir: Path, man: Manifest) -> Dict[str, Any]:
    corpus = man.output("phase1", "corpus")
    if a.no_screen:
        return {"mode": "skipped", "corpus": corpus}
    out = run_dir / "02_screen"
    cmd = ["--domain", a.domain, "--pdf_dir", corpus, "--out_dir", out,
           "--drop_fraction", a.drop_fraction, "--min_fields", a.min_fields]
    if a.last_tier:
        cmd += ["--last_tier", a.last_tier]
    if man.output("phase1", "schema"):
        cmd += ["--schema_path", man.output("phase1", "schema")]
    if a.top_n:
        cmd += ["--top_n", a.top_n]
    _run("screen_papers.py", *cmd)
    return {"mode": "screened", "corpus": str(out / "corpus"), "report": str(out / "screen.csv")}

def step_vocab(a, run_dir: Path, man: Manifest) -> Dict[str, Any]:
    schema = man.output("phase1", "schema")
    if not schema:
        print("[pipeline] no Phase I schema -> extraction uses the built-in vocabulary")
        return {"mode": "skipped", "vocab": None}
    out = run_dir / "03_vocab" / "property_vocab.json"
    _run("build_property_vocab.py", "--schema_path", schema, "--out", out)
    return {"mode": "schema", "vocab": str(out)}

def _extract_unit(a, man: Manifest, method: str, stage: Optional[int], out: Path,
                  state: Dict[str, str]) -> None:
    """One call of extract_property_records.py (one method, one C stage)."""
    unit = f"{method}{stage or ''}"
    if state.get(unit) == "done":
        return
    base = ["--method", method, "--pdf_dir", man.output("screen", "corpus"), "--out_dir", out,
            "--max_papers", a.max_papers]
    if stage:
        base += ["--stage", stage]
    if man.output("vocab", "vocab"):
        base += ["--vocab", man.output("vocab", "vocab")]
    if man.output("phase1", "metadata"):
        base += ["--metadata", man.output("phase1", "metadata")]
    if a.model:
        base += ["--model", a.model]

    if a.llm_mode == "api":
        _run("extract_property_records.py", *base)
    else:
        suffix = f"_{stage}" if stage else ""
        prompts, responses = out / f"prompts{suffix}", out / f"responses{suffix}"
        if state.get(unit) != "waiting":
            _run("extract_property_records.py", *base, "--dump_prompts", prompts)
            state[unit] = "waiting"
        entries = json.loads((prompts / "manifest.json").read_text(encoding="utf-8"))
        missing = [e for e in entries if not Path(e["response"]).exists()]
        if missing:
            print(f"\n⏸  {unit}: {len(entries) - len(missing)}/{len(entries)} answers present.\n"
                  f"   Answer each prompt listed in {prompts / 'manifest.json'}\n"
                  f"   (read 'prompt', write the JSON answer to 'response'), then rerun "
                  f"this command.")
            raise Wait(unit)
        _run("extract_property_records.py", *base, "--from_responses", responses)
    state[unit] = "done"

def step_extract(a, run_dir: Path, man: Manifest) -> Dict[str, Any]:
    state = man.step("extract").setdefault("units", {})
    outputs = {}
    try:
        for m in a.methods:
            out = run_dir / "04_records" / m
            for stage in ((1, 2) if m == "C" else (None,)):
                _extract_unit(a, man, m, stage, out, state)
            outputs[m] = str(out)
    finally:
        man.save()            # keep unit progress even when waiting / failing
    return outputs

def step_eval(a, run_dir: Path, man: Manifest) -> Dict[str, Any]:
    out = run_dir / "05_eval"
    runs: List[str] = []
    for m, d in man.step("extract")["outputs"].items():
        runs += ["--run", f"{m}={d}"]
    _run("evaluate_records.py", *runs, "--out_dir", out)
    return {"report": str(out / "report.md"),
            "adjudication_sheet": str(out / "adjudication_sheet.csv")}

STEP_FUNCS = {"phase1": step_phase1, "screen": step_screen, "vocab": step_vocab,
              "extract": step_extract, "eval": step_eval}

# --------------------------------------------------------------------- main
def main() -> int:
    a = _parse_args()
    try:
        import fitz  # noqa: F401  (PyMuPDF: without it every quote fails verification)
    except ImportError:
        print("❌ PyMuPDF is not installed for this Python. Use the project environment, "
              "e.g. .venv/bin/python autoschema_pipeline.py ...")
        return 1

    run_dir = Path(a.run_dir).resolve()
    man = Manifest(run_dir, a)
    if a.redo:
        man.reset_from(a.redo)
    man.save()

    for name in STEPS:
        st = man.step(name)
        if st["status"] == "done":
            print(f"[pipeline] ⏭  {name}: done ({st.get('updated', '')})")
            continue
        print(f"\n[pipeline] ▶ {name}")
        man.set(name, status="running")
        try:
            outputs = STEP_FUNCS[name](a, run_dir, man)
        except Wait as w:
            man.set(name, status="waiting", waiting_for=str(w))
            print(f"[pipeline] waiting for agent answers ({w}); manifest: {man.path}")
            return WAITING
        except StepFailed as e:
            man.set(name, status="failed", error=str(e))
            print(f"[pipeline] ❌ {name} failed: {e}\n   fix the cause and rerun the same command.")
            return 1
        man.set(name, status="done", outputs=outputs)
        man.data["steps"][name].pop("error", None)
        man.data["steps"][name].pop("waiting_for", None)
        man.save()
        print(f"[pipeline] ✅ {name}: {json.dumps(outputs, ensure_ascii=False)}")

    print(f"\n[pipeline] finished. Report: {man.output('eval', 'report')}\n"
          f"           Records: {run_dir / '04_records'}\n"
          f"           Manifest: {man.path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
