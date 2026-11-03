# Method C, step 1 — sample inventory

You are given numbered, labeled passages from ONE research paper, e.g.
`[Passage 12 | page 5 | Results | 3.2 Mechanical properties | cites prior work]`.
References and acknowledgements have been removed.

Task: list every distinct polymer / sample / formulation in the paper for which
at least one property value is reported. Do NOT extract property values in
this step — only identify the samples.

## What is one sample

- One entry per distinct material the paper measures. Variants of the paper's
  material are separate samples when the paper reports values for them
  separately: different formulations, compositions, molecular weights,
  crosslink ratios, processing (e.g. `MLCE-1`, `MLCE-2`, `MLCE-3`;
  `PTA ([TA]0/[TfOH] = 50)` and `PTA ([TA]0/[TfOH] = 1000)`; healed vs.
  original only if the paper reports them as separate samples).
- One umbrella name per sample: the full name, abbreviation, code, and any
  other wording the paper uses for the SAME sample are aliases of one entry,
  never separate entries. Pick the paper's most specific code as `name`.

## Fields

| field | what to put |
|---|---|
| `name` | Canonical sample name / code as used in the paper. |
| `aliases` | Every other way the paper refers to this exact sample (full name, abbreviation, code, "the as-prepared film", "electrolyte with 20 wt% LiTFSI"). `[]` if none. |
| `description` | One line: what the sample is and what distinguishes it from the other samples (composition, ratio, Mn, processing). |
| `material_class` | Short class, e.g. `imine vitrimer solid polymer electrolyte`. |
| `role` | `this_work` — the material the paper proposes, and its variants.<br>`control` — comparison samples prepared/measured in this paper: non-dynamic or component-missing analogues, reference polymers (PEO), liquid electrolytes, commercial products.<br>`prior_work` — materials from other studies (Introduction, cited text, literature rows of comparison tables). |
| `application` | Application the paper states for this sample, `""` if none. |
| `evidence` | Verbatim quote (≤ 40 words) that defines or introduces the sample. |
| `passage` | Passage number of that quote. |

List `control` and `prior_work` samples too (they help step 2 avoid assigning
their values to the paper's own samples); they are not extracted later.

Review articles: if the paper only summarizes other work, every sample is
`prior_work` (or return an empty list).

## Output

Return ONLY this JSON object (no Markdown, no commentary):

```json
{
  "paper_id": "<given paper_id>",
  "paper_type": "research | review",
  "samples": [
    {"name": "", "aliases": [], "description": "", "material_class": "",
     "role": "this_work", "application": "", "evidence": "", "passage": 0}
  ]
}
```
