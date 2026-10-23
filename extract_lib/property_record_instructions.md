# Property record extraction — instructions

You read passages from ONE research paper and extract every property
measurement the paper reports for its own polymers / samples, as one record per
measured value. You return a single JSON object (format at the end).

The passages are numbered and labeled, e.g.
`[Passage 12 | page 5 | Results | 3.2 Mechanical properties | cites prior work]`.
The label tells you the page, the section, the subheading, whether the passage
is a TABLE, and whether it contains citation markers.

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
| `sample` | The specific polymer / sample / formulation the value belongs to, using the paper's own name or code, plus the distinguishing parameter if the paper uses one (e.g. `PTA ([TA]0/[TfOH] = 50)`, `MLCE-2`, `V-SPE-PEG`). | never empty — skip the value if you cannot tell which sample it belongs to |
| `material_class` | Short description of what the sample is (e.g. `poly(disulfide) supramolecular polymer`, `imine vitrimer solid polymer electrolyte`). | `""` |
| `property` | Umbrella property name in snake_case (see §3). | never empty |
| `property_as_written` | The property exactly as the paper words it (e.g. `Td5%`, `breaking strength`). | `""` |
| `value` | The number(s) only, as written: `2.7 × 10−4`, `0.383`, `~2`, `15.0–189.2`, `>1100`. Use the paper's notation; keep ranges and approximate signs. For a qualitative result (e.g. `amorphous`, `no dendrites`), put the short term here. | never empty |
| `unit` | The unit, separated from the value: `S cm−1`, `MPa`, `°C`, `%`, `kDa`, `mAh g−1`, `h`. | `""` for unitless values (dispersity, ratios, transference number, qualitative) |
| `test_condition` | Conditions under which the value was measured: temperature, rate, current density, C-rate, cycle number, frequency, atmosphere, humidity, healing time/temperature, measurement method (DSC, TGA, DMA, EIS, lap-shear). Short, semicolon-separated: `30 °C; EIS`, `0.5C; 200 cycles; 60 °C`, `healed 2 h at 25 °C`. | `""` |
| `application` | The application the paper states for this sample (e.g. `solid electrolyte for Li-metal batteries`, `underwater adhesive`, `soft actuator`). Use the application stated in the passages — abstract/conclusion statements about the material apply to its records. | `""` |
| `evidence` | A verbatim quote (≤ 40 words) from the passage that states this value. Copy exactly, including symbols. | never empty |
| `passage` | The passage number the quote comes from (integer). | never empty |

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

- Extract values ONLY for THIS paper's own materials, including their
  variants (different formulations, molecular weights, compositions or
  processing of the material the paper proposes, e.g. `MLCE-1/2/3`,
  `PTA` at different [TA]0/[TfOH]).
- Do NOT extract values of control / comparison samples, even when the
  authors prepared or measured them in this paper: non-dynamic or
  component-missing analogues made only to show the effect of the design,
  plain reference polymers (e.g. PEO), liquid electrolytes, commercial
  products or benchmarks (e.g. a commercial tape). Words such as "control",
  "reference", "compared with", "without", "pristine" usually mark them.
- Do NOT extract values the paper attributes to other work: passages in the
  Introduction or marked `cites prior work` that describe other groups'
  materials, and literature rows of comparison tables (keep only the row for
  this work).
- Reagents (monomers, initiators, catalysts, salts, solvents, fillers) are not
  samples. Their purity, supplier or amount used are not property records.
- Synthesis parameters (reaction time, feed ratio, curing temperature) are not
  property records; use them in `sample` or `test_condition` only when they
  identify the sample or the measurement.
- Review articles: extract only values the review itself measured. If it only
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
      "passage": 0
    }
  ]
}
```

### Example (invented paper)

Passage 7 says: "The tensile strength of P2 reached 4.1 MPa with an elongation
at break of 820%, whereas the non-dynamic control C-P2 broke at 2.3 MPa." and
the abstract calls P2 "a recyclable elastomer for flexible electronics".

```json
{"sample": "P2", "material_class": "dynamic covalent elastomer",
 "property": "tensile_strength", "property_as_written": "tensile strength",
 "value": "4.1", "unit": "MPa", "test_condition": "", "application": "flexible electronics",
 "evidence": "The tensile strength of P2 reached 4.1 MPa with an elongation at break of 820%", "passage": 7}
{"sample": "P2", ..., "property": "elongation_at_break", "value": "820", "unit": "%", ...}
```

The 2.3 MPa of the non-dynamic control C-P2 is NOT extracted (§4).
