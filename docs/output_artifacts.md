# Output Artifacts

INSIGHT writes local artifacts that support auditability and reruns. These files
are not committed to the public repository.

## Full Ingestion

Typical full-ingest artifacts:

- `combined_cgm.csv`: merged harmonized CGM table.
- `manifest.json`: source-file decisions, parse QC, warnings, and output paths.
- `dataset_qc.json`: dataset-level quality-control summary.
- `source_overlap.json`: overlap and merge-plan diagnostics.
- `debug/triage/*.json`: per-file triage decisions.
- `debug/specs/*.json`: parse specifications and repairs.

## Stats-Only Ingestion

Stats-only runs write:

- `stats_summary.json`
- `manifest.json`
- `dataset_qc.json`
- `source_overlap.json`
- debug triage and parse-spec artifacts
- model-level `summary.csv`

Stats-only runs do not write `combined_cgm.csv` or per-source clean CSV files.

## Completion Criteria

For full ingestion, treat a dataset as complete only when `combined_cgm.csv`
exists and `manifest.json` contains at least one clean processed file.

For stats-only ingestion, treat a dataset as complete only when
`stats_summary.json` exists and the manifest contains at least one processed
source file with retained rows.
