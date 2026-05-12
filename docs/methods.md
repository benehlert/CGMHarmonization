# INSIGHT Methods

INSIGHT harmonizes heterogeneous continuous glucose monitoring (CGM) datasets
into a common tabular representation and records an audit trail for every
dataset run. The workflow is designed for reproducibility: model outputs are
constrained to structured decisions, deterministic code performs extraction,
and all generated data products are local artifacts rather than repository
content.

## Input Discovery

For each dataset, INSIGHT walks the dataset directory and builds file metadata
for each supported text-like source. Metadata include relative path, inferred
MIME type, delimiter hints, header candidates, sibling context, and a bounded
preview of file contents. Spreadsheet and JSON inputs are converted into
profiled table-like previews when possible.

Unsupported binary material is not parsed by the model-assisted ingestion path.
Users can still keep those files in their local dataset directory; they are
recorded or skipped according to the file-walking logic.

## File Triage

Each candidate source is assigned a semantic role. Roles include primary CGM
data, secondary CGM data, overlapping exports, calibration-only data, pump-only
data, lab or meter data, metadata dictionaries, documentation, and non-CGM
tables.

Only CGM-bearing roles proceed to extraction. Triage records candidate
timestamp, glucose, and subject fields, expected units, a schema fingerprint,
confidence, and a short rationale. These decisions are written as debug
artifacts so a reviewer can inspect why a file was included or excluded.

## Parse Specification

INSIGHT does not execute arbitrary model-generated loader code for the main
structured ingestion path. Instead, accepted files are parsed through a
validated `ParseSpec`. The specification describes how to read the source,
which timestamp and glucose fields to use, how to infer subjects, how to handle
units, and which row filters or rollover rules apply.

The pipeline also builds a heuristic parse specification from observed file
metadata. Extraction can try both the model-provided specification and the
heuristic specification, then select the result with better parse quality. If
schema detection appears incomplete, INSIGHT can request a repair
specification using an expanded preview.

## Normalization

Extracted rows are normalized to:

- `Timestamp`
- `Glucose`
- `Subject_ID`

Glucose values are numeric and converted to mg/dL when the parse specification
indicates mmol/L. Subject identifiers come from explicit source fields when
available, otherwise from deterministic file- or dataset-derived strategies.

## Merge Planning And Audit Artifacts

After per-file extraction, INSIGHT evaluates overlap among clean source files
and builds a merge plan. Files can be included, excluded, or flagged for review
depending on overlap and source role. The standard full-ingest output is
`combined_cgm.csv`.

Each dataset run also writes:

- `manifest.json`
- `dataset_qc.json`
- `source_overlap.json`
- debug triage decisions
- debug parse specifications

A run should be treated as complete only when expected outputs exist and the
manifest contains at least one processed clean source file.

## Reference-Backed Evaluation

When local reference datasets are available, INSIGHT can run adapted
Awesome-CGM preprocessors and compare model-produced harmonized outputs against
reference outputs. The comparison emphasizes subject coverage, temporal
alignment, and glucose agreement rather than exact row equality alone.

Reference-backed evaluation is optional. Users applying INSIGHT to their own
datasets can run ingestion or stats-only extraction without reference scripts.
