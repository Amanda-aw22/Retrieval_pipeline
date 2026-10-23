from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

@dataclass
class SchemaDomainSpec:
    name: str # "chem" | "ad"
    extractor_role: str # Used in extraction prompt header
    task_noun: str # "structured ... database"
    valid_last_tiers: Dict[str, str] # Key -> full text
    default_last_tier: Optional[str] # None => --last_tier REQUIRED
    avoid_fields: List[str] = field(default_factory=list)
    generic_field_names: List[str] = field(default_factory=list)
    # Domain-specific guidance for the final semantic-duplicate merge pass.
    merge_rules: List[str] = field(default_factory=list)

SCHEMA_DOMAINS: Dict[str, SchemaDomainSpec] = {
    "chem": SchemaDomainSpec(
        name="chem",
        extractor_role="high-precision materials chemistry literature extractor",
        task_noun="structured materials chemistry database",
        valid_last_tiers={
            "cof": "Covalent Organic Frameworks (COFs)",
            "mof": "Metal-Organic Frameworks (MOFs)",
            "zif": "Zeolitic Imidazolate Frameworks (ZIFs)",
        },
        default_last_tier=None,
        avoid_fields=[
            "bibliographic metadata (title, authors, doi, year, journal)",
            "fields too specific to a single paper",
            "fields not reusable across papers in the same material family",
        ],
        generic_field_names=[
            "data", "result", "results", "value", "values",
            "info", "information", "details", "summary",
            "method", "methods", "approach", "type",
        ],
        merge_rules=[
            "Keep only ONE field for linkage / bond chemistry "
            "(e.g. merge linkage_type and linkage_chemistry into a single field).",
            "Keep only ONE field for dimensionality / topology variants "
            "(e.g. 2D vs 3D, network/framework dimensionality).",
            "Keep only ONE field for surface-area variants "
            "(e.g. merge bet_surface_area, specific_surface_area, surface_area).",
            "Drop overly broad fields such as material_class or material_type "
            "if more specific fields (compound name, framework family, linkage, "
            "topology) already exist.",
        ],
    ),
    "ad": SchemaDomainSpec(
        name="ad",
        extractor_role="high-precision Alzheimer's disease literature extractor",
        task_noun="structured Alzheimer's disease study database",
        valid_last_tiers={
            "amyloid": "Amyloid production and APP processing",
        },
        default_last_tier="amyloid",
        avoid_fields=[
            "bibliographic metadata (title, authors, doi, year, journal)",
            "fields too specific to a single cohort or single paper",
            "fields not reusable across papers about the same subdomain",
        ],
        generic_field_names=[
            "data", "result", "results", "value", "values",
            "info", "information", "details", "summary",
            "method", "methods", "approach", "type",
        ],
        merge_rules=[
            "Keep only ONE field for amyloid-beta level / load measurements "
            "(e.g. merge abeta_level, amyloid_load, ab42_concentration into one).",
            "Keep only ONE field when several fields capture the same assay "
            "readout or the same biomarker quantity.",
            "Drop overly broad fields such as study_type or disease_type if "
            "more specific fields (model system, pathway, biomarker, "
            "intervention) already exist.",
        ],
    ),
    "polymer": SchemaDomainSpec(
        name="polymer",
        extractor_role="high-precision polymer science literature extractor",
        task_noun="structured polymer materials database",
        valid_last_tiers={
            "disulfide": "Disulfide-bond dynamic polymers",
            "battery":   "Self-healing polymer electrolytes for batteries",
        },
        default_last_tier=None,
        avoid_fields=[
            "bibliographic metadata (title, authors, doi, year, journal)",
            "reagent supplier, purity, or purchase details",
            "properties of materials cited from prior work rather than this paper",
            "fields too specific to a single paper",
        ],
        generic_field_names=[
            "data", "result", "results", "value", "values",
            "info", "information", "details", "summary",
            "method", "methods", "approach", "type",
        ],
        merge_rules=[
            "Keep only ONE field for molecular weight "
            "(e.g. merge mn, mw, number_average_molecular_weight, molecular_weight).",
            "Keep only ONE field for thermal decomposition "
            "(e.g. merge td, td5, decomposition_temperature, thermal_stability).",
            "Keep only ONE field for self-healing efficiency / recovery ratio, "
            "and ONE field for healing conditions (time, temperature, stimulus).",
            "Keep only ONE field for ionic conductivity variants "
            "(room-temperature vs. temperature-dependent conductivity).",
            "Drop overly broad fields such as polymer_type or material_class if "
            "more specific fields (polymer name, dynamic bond type, backbone) "
            "already exist.",
        ],
    ),
}

def resolve_domain_and_last_tier(args) -> Tuple[SchemaDomainSpec, str, str]:
    """Returns (spec, last_tier_key, last_tier_text)."""
    if args.domain not in SCHEMA_DOMAINS:
        raise SystemExit(
            f"--domain '{args.domain}' is not supported for schema induction. "
            f"KDD artifact supports: {sorted(SCHEMA_DOMAINS.keys())}"
        )
    spec = SCHEMA_DOMAINS[args.domain]
    chosen = (args.last_tier or spec.default_last_tier or "").lower() or None
    if not chosen:
        raise SystemExit(
            f"--last_tier is required for --domain {args.domain}. "
            f"Valid options: {sorted(spec.valid_last_tiers.keys())}"
        )
    if chosen not in spec.valid_last_tiers:
        raise SystemExit(
            f"--last_tier '{chosen}' is not valid for --domain {args.domain}. "
            f"Valid options: {sorted(spec.valid_last_tiers.keys())}"
        )
    return spec, chosen, spec.valid_last_tiers[chosen]
