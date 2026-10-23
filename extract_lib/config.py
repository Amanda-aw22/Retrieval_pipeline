# ----- Default I/O paths -----
DEFAULT_PDF_DIR = "data/phase2_pdfs"
DEFAULT_SCHEMA_PATH = "runs/chem/cof_iterative/final_schemas/final_schema.json"
DEFAULT_OUT_DIR = "outputs/phase2_extractions"

# ----- Per-paper text cap -----
DEFAULT_MAX_CHARS_PER_PAPER = 0

# ----- Corpus iteration -----
N_PAPERS = 200                # cap on how many PDFs to process; set to None for all
SKIP_IF_DONE = True           # resume mode: skip paper_ids already present in extractions.jsonl

# ----- Retrieval (per-field paragraph selection) -----
PARAGRAPH_MIN_CHARS = 80      # drop very short fragments (headers, page numbers)
PARAGRAPH_MAX_CHARS = 1800    # split overly long paragraphs into windows of this size
TOP_K_PER_FIELD = 6           # how many top-scoring paragraphs to keep per schema field
GLOBAL_PARAGRAPH_BUDGET = 60  # hard cap on total distinct paragraphs sent to the LLM
ALIAS_HIT_WEIGHT = 2.0        # score multiplier for alias keyword hits
NAME_HIT_WEIGHT = 3.0         # extra boost when the canonical field name appears
EVIDENCE_HIT_WEIGHT = 0.5     # small boost from overlap with phase-1 evidence snippets

# ----- Retrieval: structure-aware re-weighting -----
# Applied only to paragraphs that already match a field (keyword score > 0),
# so these re-rank relevant passages; they never pull in unrelated ones.
# Section labels come from extract_lib.pdf_io.extract_pdf_blocks.
SECTION_WEIGHTS = {
    "abstract":     1.5,      # this paper's own material + headline numbers
    "results":      1.3,      # most property values live here
    "conclusion":   1.1,
    "experimental": 1.0,
    "unknown":      1.0,      # plain-text fallback / unrecognized layout
    "front":        0.6,      # title, authors, keywords
    "introduction": 0.5,      # mostly background + prior work
}
DROP_SECTIONS = {"back_matter"}   # references, acknowledgements, CRediT, ...
TABLE_WEIGHT = 1.4                # tables concentrate property values
MATERIALS_SUBSECTION_WEIGHT = 0.7 # "2.1. Materials": reagent lists (kept for monomer-type fields)
CITATION_PENALTY = 0.15           # per citation marker like [12] or "et al."
CITATION_FLOOR = 0.4              # citation penalty never multiplies below this
PRIOR_WORK_CUE_WEIGHT = 0.7       # "has been reported", "previously", ...
REAGENT_CUE_WEIGHT = 0.8          # "purchased from", "used as received", ...
THIS_WORK_CUE_BONUS = 1.0         # "Herein", "in this work", "denoted as", ... (additive)
NUMERIC_UNIT_BONUS = 0.5          # per number-with-unit, additive
NUMERIC_UNIT_BONUS_CAP = 2.0
# Always send the abstract + "Herein/In this work" paragraphs so the LLM can
# anchor which materials belong to THIS paper (abstract-first anchoring).
PIN_ANCHOR_PASSAGES = True
MAX_PINNED_PASSAGES = 4

# ----- LLM -----
DEFAULT_MODEL_DEPLOYMENT = "gpt-4o"
MAX_COMPLETION_TOKENS = 4000  # array values + evidence per field need room
LLM_RETRIES = 3
LLM_RETRY_BACKOFF_SEC = 2.0
LLM_TEMPERATURE = 0.0         # deterministic extraction

# ----- Output formatting -----
MULTIVALUE_JOINER = " | "     # CSV cell joiner for array-typed fields
NA_VALUE = "N/A"
