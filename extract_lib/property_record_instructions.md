# Property record extraction — shared rules

These rules are shared by every extraction method (A full text, B sectioned
passages, C sample-first). Each method prepends a short "INPUT" section from
extract_lib/method_instructions/ that describes what text you are given.

Task: from ONE research paper,
1. list every candidate polymer the paper mentions (§0), whether or not any
   property value is reported for it, and
2. extract every property measurement reported for the polymers prepared or
   measured in THIS paper, as one record per measured value, each linked to
   one polymer of the list.
Return a single JSON object (format in §6).

## 0. Candidate polymers

One entry per distinct polymer or polymer-based material (formulation,
composite, blend, network, electrolyte membrane) the paper mentions.

- Include polymers that have no reported values: polymers that are only
  synthesized, characterized qualitatively, proposed, or mentioned as related
  materials.
- Variants are separate entries when the paper names or measures them
  separately: different formulations, compositions, molecular weights,
  crosslink ratios or processing (e.g. `MLCE-1`, `MLCE-2`, `MLCE-3`;
  `PTA ([TA]0/[TfOH] = 50)` and `PTA ([TA]0/[TfOH] = 1000)`).
- One umbrella name per polymer: its full name, abbreviation, code and any
  other wording of the SAME material are `aliases` of one entry, never
  separate entries. Use the paper's most specific code as `name`.
- Reagents are not candidates: monomers, crosslinkers, initiators,
  catalysts, salts, solvents, small-molecule additives, inorganic fillers.
  A polymer used as a component or matrix (e.g. the PEO in a blend) is a
  candidate.

| field | what to put |
|---|---|
| `name` | Canonical polymer name / code as used in the paper. |
| `aliases` | Every other way the paper refers to this exact material. `[]` if none. |
| `description` | One line: what it is and what distinguishes it from the other entries (composition, ratio, Mn, processing). |
| `material_class` | Short class, e.g. `imine vitrimer solid polymer electrolyte`. |
| `role` | `this_work`: the material the paper proposes, and its variants.<br>`control`: comparison materials prepared or measured in this paper (non-dynamic or component-missing analogues, reference polymers such as PEO, commercial polymers it tests).<br>`prior_work`: polymers from other studies (Introduction, cited text, literature rows of comparison tables). |
| `application` | Application the paper states for this polymer, `""` if none. |
| `evidence` | Verbatim quote (≤ 40 words) that defines or introduces the polymer. |
| `passage` | Passage number of that quote (`0` when the text has no passage numbers). |

## 1. What is one record

One record = one value of one property for one sample under one test
condition. If a sentence gives tensile strength for three samples, that is
three records. If a table gives conductivity at 30 °C and 80 °C, that is two
records.

Extract from text, tables and figure captions alike. Do not compute, convert
or average values; record what the paper states.

## 2. Fields

| field | what to put | if not reported |
|---|---|---|
| `sample` | The polymer the value belongs to: exactly the `name` of one entry of `polymers` (§0), even when the sentence uses one of its aliases. | never empty — skip the value if you cannot tell which polymer it belongs to |
| `material_class` | Short description of what the sample is (e.g. `poly(disulfide) supramolecular polymer`, `imine vitrimer solid polymer electrolyte`). | `""` |
| `property` | Umbrella property name in snake_case (see §3). | never empty |
| `property_as_written` | The property exactly as the paper words it (e.g. `Td5%`, `breaking strength`). | `""` |
| `value` | The number(s) only, as written: `2.7 × 10−4`, `0.383`, `~2`, `15.0–189.2`, `>1100`. Use the paper's notation; keep ranges and approximate signs. For a qualitative result (e.g. `amorphous`, `no dendrites`), put the short term here. | never empty |
| `unit` | The unit, separated from the value: `S cm−1`, `MPa`, `°C`, `%`, `kDa`, `mAh g−1`, `h`. | `""` for unitless values (dispersity, ratios, transference number, qualitative) |
| `test_condition` | Conditions under which the value was measured: temperature, rate, current density, C-rate, cycle number, frequency, atmosphere, humidity, healing time/temperature, measurement method (DSC, TGA, DMA, EIS, lap-shear). Short, semicolon-separated: `30 °C; EIS`, `0.5C; 200 cycles; 60 °C`, `healed 2 h at 25 °C`. | `""` |
| `application` | The application the paper states for this sample (e.g. `solid electrolyte for Li-metal batteries`, `underwater adhesive`, `soft actuator`). Use the application stated in the text — abstract/conclusion statements about the material apply to its records. | `""` |
| `evidence` | A verbatim quote (≤ 40 words) from the text that states this value. Copy exactly, including symbols. The quote MUST contain the value itself. | never empty |
| `passage` | The number of the passage the quote comes from (integer), when the text is given as numbered passages. | `0` when the text has no passage numbers |
| `sample_evidence` | A verbatim quote (≤ 40 words) where the paper names the sample in a way that ties it to THIS value — often the same sentence as `evidence` (then repeat that sentence, extended to include the sample name), otherwise the sentence / table header / caption that identifies it. | never empty |
| `sample_passage` | Passage number of `sample_evidence`. | `0` when the text has no passage numbers |
| `condition_evidence` | A list of `{"quote": "<verbatim ≤ 40 words>", "passage": N}`, one per source of the `test_condition` parts: the same sentence, the experimental/methods text describing the test, a figure or table caption, etc. Every part of `test_condition` must be supported by one of these quotes. | `[]` when `test_condition` is empty |
| `derivation` | How sample and conditions were tied to the value — a list of one or more of: `stated` (sample, value and conditions all in the `evidence` sentence), `positional` (matched by order, e.g. "A, B and C … 1, 2 and 3", "respectively", table row/column), `cross_passage` (sample or a condition taken from another passage, e.g. the methods section or a caption), `corrected` (you fixed an apparent typo or reinterpreted the wording, e.g. "0.5 ◦C" read as "0.5C"), `inferred` (any other reasoning). | never empty |

### Traceability

`sample` and `test_condition` are your normalized summaries; they do not have
to be verbatim. But every piece of information in them must be traceable to a
verbatim quote in `evidence`, `sample_evidence` or `condition_evidence`. If you
cannot point to text that supports a condition, leave that condition out.
Whenever you use `positional`, `cross_passage`, `corrected` or `inferred`, say
so in `derivation` — do not hide inference.

## 3. Umbrella property names

Different wordings of the same quantity MUST map to one umbrella name; keep
the paper's wording in `property_as_written`. Use these names when they fit:

- Molecular: `number_average_molecular_weight` (Mn), `weight_average_molecular_weight` (Mw), `dispersity` (Đ, PDI, Mw/Mn), `monomer_conversion`, `gel_fraction`, `crosslink_density`, `swelling_ratio`
- Thermal: `glass_transition_temperature` (Tg), `melting_temperature` (Tm), `decomposition_temperature` (Td, Td5%, onset of weight loss), `flow_temperature`
- Mechanical: `tensile_strength` (breaking strength, stress at break), `elongation_at_break` (strain at break), `youngs_modulus`, `toughness`, `storage_modulus`, `adhesion_strength` (lap-shear strength)
- Healing / recycling: `self_healing_efficiency` (healing ratio, recovery of a property after healing), `healing_time`, `healing_temperature`, `monomer_recovery_yield`, `reprocessing_cycles`
- Electrochemical: `ionic_conductivity`, `transference_number` (tLi+, tZn2+), `electrochemical_stability_window`, `activation_energy`, `specific_capacity`, `capacity_retention`, `coulombic_efficiency`, `symmetric_cell_cycle_life`, `interfacial_resistance`, `overpotential`, `energy_density`
- Other: `water_contact_angle`, `water_retention`, `actuation_strain`, `photothermal_temperature`

If a property is not in this list, create a clear snake_case umbrella name and
use the SAME name for every wording of that property in this paper.

Never encode a condition, statistic or sample in the property name — put it
in `test_condition` or `sample` instead:
`decomposition_temperature` + `10% weight loss` (not `decomposition_temperature_10pct`),
`degradation_time` + `DTT in DMSO` (not `reductive_degradation_time`),
`elongation_at_break` + `did not break at this strain` (not `strain_without_breaking`).

## 4. Whose values to extract

- Extract values for every polymer with role `this_work` or `control`: the
  paper's own materials and their variants (e.g. `MLCE-1/2/3`, `PTA` at
  different [TA]0/[TfOH]), and the comparison polymers the authors prepared or
  measured in this paper (non-dynamic or component-missing analogues,
  reference polymers such as PEO, commercial polymers). Assign each value to
  the right polymer: words such as "control", "reference", "compared with",
  "without", "pristine" usually mark the control.
- Do NOT extract values of materials that are not polymers (liquid
  electrolytes, inorganic benchmarks, a commercial tape that is not a listed
  polymer).
- Do NOT extract values the paper attributes to other work (`prior_work`
  polymers): Introduction /
  background text and sentences with citation markers ([12], "et al.",
  "has been reported") that describe other groups'
  materials, and literature rows of comparison tables (keep only the row for
  this work).
- Reagents (monomers, initiators, catalysts, salts, solvents, fillers) are not
  samples. Their purity, supplier or amount used are not property records.
- Synthesis parameters (reaction time, feed ratio, curing temperature) are not
  property records; use them in `sample` or `test_condition` only when they
  identify the sample or the measurement.
- Review articles: list the polymers it discusses (`prior_work`) in
  `polymers`; extract only values the review itself measured. If it only
  summarizes other papers, return an empty `records` list.

## 5. Empty is better than wrong

If a field is not reported, leave it `""`. Never guess a unit, condition or
application. If a value cannot be tied to a specific sample, skip it.

## 6. Output format

Return ONLY this JSON object (no Markdown, no commentary):

```json
{
  "paper_id": "<given paper_id>",
  "paper_type": "research | review",
  "polymers": [
    {"name": "", "aliases": [], "description": "", "material_class": "",
     "role": "this_work | control | prior_work", "application": "",
     "evidence": "", "passage": 0}
  ],
  "records": [
    {
      "sample": "",
      "material_class": "",
      "property": "",
      "property_as_written": "",
      "value": "",
      "unit": "",
      "test_condition": "",
      "application": "",
      "evidence": "",
      "passage": 0,
      "sample_evidence": "",
      "sample_passage": 0,
      "condition_evidence": [{"quote": "", "passage": 0}],
      "derivation": ["stated"]
    }
  ]
}
```

### Example (invented paper)

Passage 4 (Experimental) says: "Tensile tests were carried out at 25 °C at a
crosshead speed of 20 mm min−1." Passage 7 (Results) says: "The tensile
strength of P1, P2 and P3 was 2.0, 4.1 and 3.3 MPa, respectively, whereas the
non-dynamic control C-P2 broke at 2.3 MPa." The abstract calls the P-series
"recyclable elastomers for flexible electronics".

```json
{"sample": "P2", "material_class": "dynamic covalent elastomer",
 "property": "tensile_strength", "property_as_written": "tensile strength",
 "value": "4.1", "unit": "MPa", "test_condition": "25 °C; 20 mm min−1; tensile test",
 "application": "flexible electronics",
 "evidence": "The tensile strength of P1, P2 and P3 was 2.0, 4.1 and 3.3 MPa, respectively", "passage": 7,
 "sample_evidence": "The tensile strength of P1, P2 and P3 was 2.0, 4.1 and 3.3 MPa, respectively", "sample_passage": 7,
 "condition_evidence": [{"quote": "Tensile tests were carried out at 25 °C at a crosshead speed of 20 mm min−1.", "passage": 4}],
 "derivation": ["positional", "cross_passage"]}
```

P1 and P3 get their own records the same way. `polymers` lists P1, P2, P3
(`this_work`) and C-P2 (`control`); the 2.3 MPa of C-P2 is extracted as a
record with `"sample": "C-P2"`. A polymer the introduction cites from another
group would be listed as `prior_work` with no records.
