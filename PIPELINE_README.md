# AutoSchema Polymer Pipeline — From Seed Papers to Property Records

This document describes the end-to-end pipeline on branch `Retrieval_generation`.
It extends the KDD 2026 AutoSchema artifact (see [README.md](README.md)) to polymer
literature (disulfide-bond dynamic polymers; self-healing polymer electrolytes for
batteries) and replaces the per-field table output of Phase II with
**evidence-grounded property records**: one row per measured value.

> **Status legend.** ✅ implemented and tested · ⚠️ implemented; needs API keys for a real run.

---

## 1. Overview

```
seed PDFs
  │  Phase I — corpus building (original AutoSchema, unchanged)
  ├─ 1. Query generation      prompt_pipeline.py            ⚠️
  ├─ 2. Retrieval + download  expand_round.py               ⚠️
  ├─ 3. Iterative expansion   iterative_expand.py           ⚠️  (dedup, top-off, seed refresh)
  ├─ 4. Schema induction      design_extraction_prompt.py   ⚠️  (per round, frozen at the end)
  │
  │  Bridge
  ├─ 5. Paper screening       screen_papers.py              ✅  (old-pipeline relevance signals)
  ├─ 6. Schema → vocabulary   build_property_vocab.py       ✅
  │
  │  Phase II — evidence-grounded property-record extraction (new)
  ├─ 7. Structure-aware parsing  extract_lib/pdf_io.py      ✅
  ├─ 8. Record extraction        extract_property_records.py ✅  (methods A / B / C)
  ├─ 9. Verification + flags     extract_lib/record_extract.py ✅
  └─ 10. Evaluation              evaluate_records.py        ✅

One command for all steps, with resume: autoschema_pipeline.py  ✅
```

### Same core logic as the original artifact

The new pipeline keeps the original design. Each step maps one to one:

| Step | Original artifact | This pipeline |
|---|---|---|
| Corpus | seeds → queries → multi-source retrieval → dedup → iterative rounds | **identical** (same scripts) |
| Schema | LLM proposes fields → relevance filter → semantic merge → frozen schema | identical; also groups synonyms before `min_support` and keeps aliases |
| Parse | PDF → section-labeled blocks | same idea; adds two-column order, header/footer removal, tables, page numbers |
| Context | passages scored with section weights, this-work cues, citation penalty | same scoring (`extract_lib/retrieval.py`); method B sends all labeled passages |
| Extract | one LLM call per paper, JSON with values + verbatim evidence | same; the unit changes from *field* to *record* |
| Post-process | normalize JSON → JSONL audit log + CSV | same; adds verbatim checks and traceability flags |
| Resume | skip papers already in the output | `run_manifest.json` per step + saved answers per paper (API and agent mode) |

**What changes:** (1) output unit: `paper × field` cell → `sample × property × value × condition` record;
(2) the schema becomes a vocabulary of property names instead of the column set;
(3) traceability for sample and test condition; (4) method comparison and evaluation;
(5) LLM calls can run through Claude subagents instead of an API (Phase II only).

---

## 2. Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # API keys; required for Phase I and for Phase II in API mode
```

Run every command with the project environment (`.venv/bin/python ...`). Without
PyMuPDF, PDF parsing returns nothing and every evidence quote fails verification.
`autoschema_pipeline.py` checks this at start.

The LLM and retrieval environment variables are the same as in [README.md](README.md#configuration).

Put the seed PDFs here (DOIs listed in [data/README.md](data/README.md)):

```text
data/seeds_disulfide/          # --domain polymer --last_tier disulfide
data/seeds_batteries/          # --domain polymer --last_tier batteries
data/seeds_glass_transition/   # --domain polymer --last_tier glass_transition (no seeds yet)
```

The polymer subdomain keys, texts and query-generation settings come from the
`task1-queries` branch (see [QUERY_GENERATION_README.md](QUERY_GENERATION_README.md)).
Older result folders under `tests/polymer/battery/` keep their original name.

---

## 3. Phase I — build the corpus and schema ✅

Polymer is registered in `prompt_lib/domain.py`, `expand_lib/domain_args.py` and
`schema_lib/domain.py`. The default sources are `openalex,nature,core,arxiv`.

**Run all Phase I steps (recommended):**

```bash
python iterative_expand.py \
  --domain polymer --last_tier batteries \
  --seed_dir data/seeds_batteries \
  --output_root runs/polymer/batteries/phase1 \
  --rounds 2 --target_total_papers 30 \
  --max_schema_papers 18 --max_chars_per_paper 12000 \
  --sources openalex,nature,core
```

**Or run the steps one at a time:**

```bash
python prompt_pipeline.py --domain polymer --last_tier batteries \
  --seeds data/seeds_batteries --output runs/polymer/batteries/1_queries
python expand_round.py --domain polymer --last_tier batteries \
  --queries runs/polymer/batteries/1_queries/queries.json --output runs/polymer/batteries/2_retrieve
python design_extraction_prompt.py --domain polymer --last_tier batteries \
  --pdf_dir runs/polymer/batteries/2_retrieve/downloads --output runs/polymer/batteries/schema_r1
```

**Outputs used by Phase II:**

| File | Used for |
|---|---|
| `phase1/cumulative_corpus/*.pdf` | `--pdf_dir` of Phase II |
| `phase1/cumulative_metadata.jsonl` | DOI / title / source / round of each PDF (links records to papers) |
| `phase1/final_schemas/final_schema.json` | property vocabulary (step 6) |

Polymer-specific changes in Phase I:
- **Query generation** (from `task1-queries`): polymer DomainSpec with three subdomains (`disulfide`, `batteries`, `glass_transition`), aliases, domain aspects and `POLYMER_EXTRACT_TEMPLATE`. `prompt_lib/validation.py` keeps battery queries that mention both polymer and battery terms. `evaluate_queries.py` scores generated queries with an LLM judge (standalone).
- **Retrieval / schema / orchestrator:** the same three subdomain keys and texts in `expand_lib/domain_args.py`, `schema_lib/domain.py` and `iterative_expand.py`. Retrieval aliases are the union of both branches' aliases. The schema merge rules include one field for glass transition temperature.
- **Schema induction:** synonym grouping runs before `min_support`, so variant wordings are not dropped. Aliases are kept in `merged_from` / `name_variants`. Sampling is section-aware instead of using the first 12k characters.

---

## 4. Bridge — screening and schema hand-off ✅

### 4.1 Paper screening (`screen_papers.py`)

The screen adds no new scoring. It takes the two relevance signals the original
artifact already has and applies them to the corpus that Phase II reads. In the
artifact, they only steer schema sampling and the optional `--top_n` of
`extract_schema_values.py`.

| Check | Old-pipeline code reused | Rule |
|---|---|---|
| Topic relevance | `schema_lib.sampling.relevance_filtered_sample` | Embedding similarity to the last-tier text (keyword overlap without an embedding key). Drop the least relevant `--drop_fraction` (default 0.25). |
| Schema coverage | `extract_lib.retrieval.paper_relevance_signal` | Number of schema fields with a matching passage. Keep papers with `≥ --min_fields` (default 1). Optional `--top_n`. |

```bash
python screen_papers.py --domain polymer --last_tier batteries \
  --pdf_dir RUN/01_phase1/cumulative_corpus \
  --schema_path RUN/01_phase1/final_schemas/final_schema.json --out_dir RUN/02_screen
```

Outputs: `screen.csv` / `screen.json` (both scores, decision and reason per PDF) and
`corpus/` (copies of the kept PDFs).

> **Known limitation (tested on the 9 retrieved battery PDFs, keyword fallback).**
> Relevance scores do not tell research papers from reviews or near-topic papers. The
> screen kept the asphalt paper and two reviews, and dropped two relevant
> battery-electrolyte papers because tied scores were cut at the 25 % line. Embeddings
> (set `EMBEDDING_*` in `.env`) should rank better. Use `--drop_fraction 0` to keep
> everything, or `--no_screen` in the pipeline. Reviews are still handled at
> extraction: the rules return no records for papers that only summarize others.

### 4.2 Schema → property vocabulary (`build_property_vocab.py`)

```bash
python build_property_vocab.py --schema_path RUN/01_phase1/final_schemas/final_schema.json \
  --out RUN/03_vocab/property_vocab.json
```

Logic is in `extract_lib/property_vocab.py`:

1. **Split the fields.** Each schema field is classified as:
   - `known`: its name, after the base umbrella map, is already a §3 umbrella name. Example: `polydispersity_index` → `dispersity`.
   - `descriptive`: its name contains words such as method, type, condition, monomer, initiator, application or configuration.
   - `new`: a property not yet in §3, recognized by numeric examples. Example: `membrane_thickness`.
2. **Build the umbrella map.** Each property's `aliases`, `name_variants` and `merged_from` are snake-cased and folded into its umbrella name. They are merged on top of `property_umbrella_map.json`. An alias that would point to a different umbrella name is not applied; it is listed under `conflicts`.
3. **Write the prompt section.** `property_vocab_prompt.md` lists the corpus properties with their aliases. It also lists the descriptive fields as hints for `sample`, `material_class` and `test_condition`.

`extract_property_records.py --vocab property_vocab.json` uses the map in
post-processing and appends the section to the prompt for A, B and C step 2.
Without `--vocab`, the built-in §3 list and `property_umbrella_map.json` are used.

Example (trial battery schema): 4 property fields (`ionic_conductivity`,
`membrane_thickness`, `self_healing_efficiency`, `capacity_retention`) and
7 descriptive fields (synthesis method, cell configuration, bond type, ...).

---

## 5. Phase II — property-record extraction ✅

### 5.1 Output record

One record = one value of one property for one sample under one test condition.
Values from controls, prior work (Introduction or cited text), and reagents are **not** extracted.
Fields that are not reported stay empty.

| Field | Content |
|---|---|
| `sample`, `material_class` | the paper's own sample name or code, plus its distinguishing parameter |
| `property` | umbrella name in snake_case (e.g. `tensile_strength`) |
| `property_as_written` | the paper's wording (e.g. `breaking strength`) |
| `value`, `unit` | as written; no conversion |
| `test_condition` | e.g. `30 °C; EIS`, `0.5C; 200 cycles` |
| `application` | as stated by the paper |
| `evidence`, `passage`, `page` | verbatim quote containing the value, and its location |
| `sample_evidence`, `sample_passage` | quote that ties the sample to this value |
| `condition_evidence` | list of `{quote, passage}`, one per source of the conditions |
| `derivation` | `stated` / `positional` / `cross_passage` / `corrected` / `inferred` |
| `flags`, `evidence_verified`, `value_in_evidence`, `unit_in_evidence` | automatic checks |

The rules shared by all methods are in [extract_lib/property_record_instructions.md](extract_lib/property_record_instructions.md).
Different wordings of the same property always map to one umbrella name, and the paper's wording is kept as an alias.

### 5.2 Three extraction methods

| ID | Name | Mechanism | LLM calls / paper |
|---|---|---|---|
| **A — FDE** | Zero-shot Full-Document Extraction | raw page text, no structure labels, one call | 1 |
| **B — SAPE** (default) | Structure-Aware Passage-Grounded Extraction | numbered passages labeled with page, section, heading, TABLE and "cites prior work"; one call | 1 |
| **C — EA-2S** | Entity-Anchored Two-Stage Extraction | step 1 lists samples, aliases and roles; step 2 runs one call per own sample, using only passages that mention it; records are merged and conflicts reported | 1 + #samples |

Method-specific input notes: `extract_lib/method_instructions/{A_fulltext, B_sectioned, C1_sample_inventory, C2_sample_properties}.md`.

### 5.3 Commands

**API mode** (calls the LLM configured in `.env`):

```bash
python extract_property_records.py --method B \
  --pdf_dir runs/polymer/batteries/phase1/cumulative_corpus \
  --out_dir runs/polymer/batteries/phase2_B
```

**Agent mode** (no API key; Claude subagents answer the prompts):

```bash
# 1) write prompts + manifest.json (lists each prompt and where its answer goes)
python extract_property_records.py --method B --pdf_dir PDFS --out_dir OUT --dump_prompts OUT/prompts
# 2) a subagent reads each OUT/prompts/<paper>.txt and writes OUT/responses/<paper>.json
# 3) read the answers and post-process
python extract_property_records.py --method B --pdf_dir PDFS --out_dir OUT --from_responses OUT/responses
```

**Method C** runs in two stages. Each stage supports the same three modes:

```bash
python extract_property_records.py --method C --stage 1 --pdf_dir PDFS --out_dir OUT --dump_prompts OUT/prompts_1
python extract_property_records.py --method C --stage 1 --pdf_dir PDFS --out_dir OUT --from_responses OUT/responses_1
python extract_property_records.py --method C --stage 2 --pdf_dir PDFS --out_dir OUT --dump_prompts OUT/prompts_2
python extract_property_records.py --method C --stage 2 --pdf_dir PDFS --out_dir OUT --from_responses OUT/responses_2
```

Useful options:

| Option | Effect |
|---|---|
| `--paper <pdf stem>` (repeatable), `--max_papers N` | choose which papers to process |
| `--model NAME` | override the chat model or deployment |
| `--vocab property_vocab.json` | Phase I vocabulary (§4.2) |
| `--metadata cumulative_metadata.jsonl` | adds `doi`, `title`, `year` and `retrieval_source` to every paper and record |
| `--no_cache` | in API mode, answers are normally cached under `OUT/responses*` so a rerun only calls the LLM for papers without an answer; this switches that off |

**Outputs:**
- `records.jsonl` and `records.csv`.
- In API mode, also `llm_calls*.json`, a call log used for cost.
- Method C also writes `samples/<paper>.json`, `samples.csv` and `conflicts.csv`.

### 5.4 Verification flags

Each record is checked against the parsed text:

| Flag | Meaning |
|---|---|
| `evidence_not_found` | the evidence quote is not verbatim in the paper |
| `value_not_in_evidence` / `unit_not_in_evidence` | the quote does not contain the value or unit |
| `sample_evidence_missing` / `_not_found` | no verbatim quote ties the sample to this value |
| `condition_evidence_missing` / `_not_found` | the test condition has no verbatim support |
| `review:inferred` / `review:corrected` | the model inferred or corrected something; needs human review |

---

## 6. Evaluation ✅

`evaluate_records.py` compares methods A/B/C on the same papers without human labels.
It also writes a sheet for human labeling:

```bash
# any output folders of extract_property_records.py (one method, or several to compare)
python evaluate_records.py --run B=RUN/04_records/B --run A=RUN/04_records/A --out_dir RUN/05_eval
# the method-comparison folders used in development
python evaluate_records.py --base compare_v2 --topics disulfide battery \
  --out_dir tests/polymer/compare_v2/report
```

- **Metrics:** volume; grounding (verbatim evidence, page found); leakage (reviews, Introduction text, citations, controls); field completeness; cost (calls, prompt size); cross-method agreement and majority-vote recall; sample binding (strict and loose).
- **Human labels:** `adjudication_sheet.csv` has one row per unique value. Labels: `correct` / `wrong_value` / `wrong_sample` / `not_this_work` / `not_a_property`. These labels give true precision, recall and F1.

With a single method, the agreement and sample-binding sections are skipped.

### Results on 4 seed papers (batteries 1, batteries 2, Cationic, Healable)

| | A — FDE | B — SAPE | C — EA-2S |
|---|---|---|---|
| Records | 199 | 182 | 175 |
| Majority-vote recall | 99 % | 95 % | 73 % |
| Confirmed by another method | 91 % | 92 % | 78 % |
| Strict sample binding | 77 % | 90 % | 85 % |
| Inferred / corrected | 12 % | 14 % | 27 % |
| Evidence traceable | ≈100 % | ≈100 % | ≈100 % |
| LLM calls | 4 | 4 | 51 |
| Input tokens | ≈73k | ≈60k | ≈276k |

**Conclusion:** B is the default (best balance). A has the highest recall but weaker sample binding.
C is the most expensive and weakest on papers with many samples. Report:
`tests/polymer/compare_v2/report/`.

---

## 7. One command: `autoschema_pipeline.py` ✅

A thin wrapper. It runs the scripts above as subprocesses, the same way
`iterative_expand.py` does, and passes each step's outputs to the next step.

```bash
# full run: seeds -> Phase I -> screen -> vocab -> extract (B) -> eval
.venv/bin/python autoschema_pipeline.py --domain polymer --last_tier batteries \
  --seed_dir data/seeds_batteries --run_dir runs/polymer/batteries/run_01 \
  --rounds 2 --target_total_papers 30

# skip Phase I: start from an existing corpus (schema / metadata optional)
.venv/bin/python autoschema_pipeline.py --domain polymer --last_tier batteries \
  --corpus_dir data/seeds_batteries --run_dir runs/polymer/batteries/seeds_only \
  --schema_path tests/polymer/battery/trial_4papers/schema/final_schema.json \
  --no_screen --methods A,B,C --llm_mode agent
```

```text
runs/polymer/batteries/run_01/
├── 01_phase1/        iterative_expand.py → cumulative_corpus/, cumulative_metadata.jsonl, final_schemas/
├── 02_screen/        screen_papers.py    → screen.csv, corpus/
├── 03_vocab/         build_property_vocab.py → property_vocab.json, property_vocab_prompt.md
├── 04_records/<M>/   extract_property_records.py per method → records.jsonl/csv (+ doi, title, …)
├── 05_eval/          evaluate_records.py → report.md, summary.csv, adjudication_sheet.csv
└── run_manifest.json status and outputs of every step
```

How each output is handed to the next step:

| From | To |
|---|---|
| `phase1.corpus` | `screen --pdf_dir` |
| `phase1.schema` | `screen --schema_path` and `vocab --schema_path` |
| `screen.corpus` | `extract --pdf_dir` |
| `vocab.vocab` | `extract --vocab` |
| `phase1.metadata` | `extract --metadata` |
| `extract.<M>` | `eval --run M=…` |

When there is no schema (e.g. `--corpus_dir` without `--schema_path`), screening only
uses topic relevance and extraction uses the built-in vocabulary.

### Options

| Group | Options |
|---|---|
| Input | `--seed_dir` (run Phase I) **or** `--corpus_dir` (skip it), plus optional `--schema_path` / `--metadata` |
| Phase I | `--rounds`, `--target_total_papers`, `--results_per_source`, `--sources`, `--max_schema_papers`, `--max_chars_per_paper`, `--max_final_fields`, `--min_support` (omitted → `iterative_expand.py` defaults) |
| Screen | `--drop_fraction`, `--min_fields`, `--top_n`, `--no_screen` |
| Extract | `--methods B` (or `A,B,C`), `--llm_mode api\|agent`, `--model`, `--max_papers` |
| Control | `--redo STEP` (STEP ∈ phase1, screen, vocab, extract, eval) |

### Resume

- `run_manifest.json` stores each step's status (`pending` / `running` / `waiting` / `done` / `failed`) and its outputs. For `extract`, it also stores the status of every method and stage (`B`, `C1`, `C2`, …).
- **Rerun the same command** to continue. Finished steps are skipped. A failed step is retried; its error is recorded in the manifest.
- **Inside `extract`**, saved answers are reused, so only papers without an answer go to the LLM. This works in both modes: API mode caches under `responses*`, and agent mode waits for missing `responses*` files.
- **`--redo STEP`** resets that step and every later one. Saved answers are kept. Delete `04_records/<M>/responses*` if the prompts changed and you want new answers.
- **Changed settings.** If you change settings for an existing `run_dir`, the pipeline warns but does not redo finished steps. Use `--redo` or a new `run_dir`.

### Agent mode (`--llm_mode agent`)

1. The pipeline writes the extraction prompts and stops with **exit code 3**. It prints the path of `prompts*/manifest.json`; every entry gives a `prompt` file and the `response` path to write.
2. Answer each prompt, for example with Claude subagents, and write the JSON answer to its `response` path.
3. Rerun the same command. Method C stops twice: once for step 1 (the sample inventory) and once for step 2 (one prompt per sample).

Phase I and screening always use `.env`. Without an embedding key, screening falls back to keyword overlap.

### Tested

All tests used `.venv`, with saved v2 answers standing in for the agents:

- **Method B**: stopped with 0/2 answers, then 1/2; finished once 2/2 were present (83 records, all evidence verified). A third run skipped every step.
- **Method C**: stopped at `C1`, then at `C2`; then finished (75 records).
- **`--redo eval`**: reran only the evaluation.
- **Phase I**: `iterative_expand.py → prompt_pipeline.py` started with the correct arguments. It stopped because `.env` has no `CHAT_API_KEY`, and the manifest recorded `failed` with the reason.

## 8. File map

| Path | Role |
|---|---|
| `prompt_pipeline.py`, `prompt_lib/` | Phase I query generation (polymer domain added) |
| `expand_round.py`, `expand_lib/` | Phase I retrieval and download |
| `design_extraction_prompt.py`, `schema_lib/` | Phase I schema induction (synonym grouping, aliases) |
| `iterative_expand.py` | Phase I multi-round orchestrator |
| `extract_schema_values.py` | original Phase II (per-field table); kept as a baseline |
| `extract_property_records.py` | new Phase II CLI (methods A/B/C, API / dump / from-responses) |
| `extract_lib/pdf_io.py` | structure-aware PDF parsing (sections, columns, tables, pages) |
| `extract_lib/retrieval.py`, `config.py` | passages, section weights, this-work cues, citation penalty |
| `extract_lib/record_extract.py` | prompts for method B, normalization, verification, CSV/JSONL |
| `extract_lib/fulltext_extract.py` | method A |
| `extract_lib/sample_first_extract.py` | method C (inventory, per-sample passages, merge) |
| `extract_lib/property_record_instructions.md` | shared extraction rules |
| `extract_lib/method_instructions/` | method-specific input notes |
| `extract_lib/property_umbrella_map.json` | umbrella property names and aliases |
| `screen_papers.py` | corpus screening with the old-pipeline relevance signals |
| `build_property_vocab.py`, `extract_lib/property_vocab.py` | Phase I schema → property vocabulary |
| `autoschema_pipeline.py` | one command for all steps, with `run_manifest.json` resume |
| `evaluate_records.py` | metrics for one run or several methods, and the adjudication sheet |

Generated outputs (`runs/`, `tests/`, PDFs, `.env`) are git-ignored and must not be committed.
