# Pipeline Architecture

INSIGHT is organized around small command-line scripts in `harmony/`.

## Main Entry Points

- `cgm_ingest.py`: runs one dataset through model-assisted ingestion.
- `run_ingest_all.py`: runs ingestion across split directories and optionally
  evaluates against reference outputs.
- `run_ingest_new_data.py`: runs arbitrary new datasets; `--stats-only` reports
  dataset-level counts without writing harmonized CSVs.
- `reference_eval.py`: builds reference outputs and compares harmonized outputs.
- `compare_csvs.py`: compares two standardized CGM CSV files.
- `build_results_workbook.py`: builds a local workbook from generated run and
  evaluation artifacts.

## Data Flow

1. Walk files in a dataset directory.
2. Build previews and file profiles.
3. Triage files into CGM and non-CGM roles.
4. Generate or infer parse specifications for accepted CGM files.
5. Extract and normalize `Timestamp`, `Glucose`, and `Subject_ID`.
6. Build source-overlap and dataset QC artifacts.
7. Write a combined output for full ingestion, or counts for stats-only runs.
8. Optionally generate reference outputs and comparison reports.

## Provider Boundaries

LLMs are used for bounded classification and structured parse-spec generation.
The deterministic Python pipeline performs parsing, cleaning, validation,
merging, and comparison.

Provider selection is handled by `model_registry.py`. Provider credentials are
read from environment variables or from `harmony/.env` when present.
