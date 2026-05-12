# INSIGHT CGM Harmonization

INSIGHT is a methodology repository for model-assisted harmonization of
continuous glucose monitoring (CGM) datasets. It provides the code needed to
triage heterogeneous source files, infer constrained parse specifications,
standardize extracted CGM observations, and optionally evaluate outputs against
reference preprocessors.

This public repository is intended to support scientific reproducibility and
transparency. It contains methodology code, reference scripts, tests, and
documentation. It intentionally does not contain source datasets, generated
harmonized outputs, evaluation results, workbooks, manuscript drafts, or local
run artifacts.

## What INSIGHT Produces

The standard harmonized output uses three core columns:

- `Timestamp`: parsed timestamp for the CGM observation
- `Glucose`: glucose value normalized to mg/dL when units are known
- `Subject_ID`: participant identifier inferred from a column, file, or dataset

A full ingestion run writes local audit artifacts such as:

- `combined_cgm.csv`
- `manifest.json`
- `dataset_qc.json`
- `source_overlap.json`
- per-file debug triage and parse-spec JSON files

These outputs are generated locally and ignored by Git.

## Repository Layout

```text
.
├── harmony/                       # INSIGHT ingestion, evaluation, and reporting code
├── Awesome-CGM/                   # MIT-licensed adapted reference preprocessors
├── docs/                          # Methodology and reproducibility documentation
├── tests/                         # Synthetic-data tests
├── requirements.txt               # Python dependency entrypoint
├── .env.example                   # Provider-key template
├── LICENSE                        # INSIGHT license
├── NOTICE                         # Reference-code attribution
└── CITATION.cff                   # Citation metadata
```

## Installation

Use Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Some reference preprocessors are written in R. Reference-backed evaluation for
those datasets requires a local R installation and the R packages used by the
corresponding `Awesome-CGM/R/...` script.

## Provider Keys

INSIGHT calls provider-hosted LLMs for file triage and parse-spec inference.
Copy `.env.example` to `.env` or export the variables in your shell.

```bash
cp .env.example .env
```

Set only the keys for providers you plan to use:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`

## Data Policy

This repository does not redistribute CGM datasets. Place externally obtained
datasets outside Git-tracked paths and pass them explicitly to the scripts.

For a model-matrix benchmark with training and testing split directories:

```bash
python harmony/run_ingest_all.py \
  --training-root /path/to/training_datasets \
  --testing-root /path/to/testing_datasets \
  --models gpt-5.4 \
  --output-root local_outputs/runs \
  --evaluation-root local_outputs/evaluation \
  --results-workbook local_outputs/results.xlsx
```

Each split root should contain one subdirectory per dataset.

For arbitrary new datasets where no reference output is available and only
dataset-level counts are needed:

```bash
python harmony/run_ingest_new_data.py \
  --input-root /path/to/new_dataset_root \
  --output-root local_outputs/new_data_stats \
  --models gpt-5.4 \
  --stats-only \
  --skip-existing \
  --jobs 1
```

`--input-root` should contain one subdirectory per dataset.

## Common Commands

Run one dataset through ingestion:

```bash
python harmony/cgm_ingest.py /path/to/dataset \
  --out local_outputs/runs/gpt-5-4/example_dataset \
  --cgm-model gpt-5.4 \
  --default-model gpt-5.4
```

Run a resumable benchmark:

```bash
python harmony/run_ingest_all.py \
  --training-root /path/to/training_datasets \
  --testing-root /path/to/testing_datasets \
  --models gpt-5.4 gpt-5.4-mini \
  --skip-existing \
  --reuse-references \
  --jobs 1 \
  --output-root local_outputs/runs \
  --evaluation-root local_outputs/evaluation \
  --results-workbook local_outputs/results.xlsx
```

Run reference evaluation for an existing harmonized output:

```bash
python harmony/reference_eval.py \
  --split testing \
  --harmonized-root local_outputs/runs/gpt-5-4/testing \
  --evaluation-root local_outputs/evaluation/gpt-5-4
```

Compare any two standardized CGM CSVs:

```bash
python harmony/compare_csvs.py candidate.csv reference.csv --json-out comparison.json
```

## Documentation

- [Methods](docs/methods.md)
- [Pipeline architecture](docs/pipeline_architecture.md)
- [Reference evaluation](docs/reference_evaluation.md)
- [Output artifacts](docs/output_artifacts.md)
- [Stats-only runs](docs/stats_only.md)
- [Using external datasets](docs/user_data.md)

## Attribution

The `Awesome-CGM/` reference preprocessors are adapted from the MIT-licensed
Awesome-CGM project. See `NOTICE` and `Awesome-CGM/LICENSE`.

## Citation

If you use INSIGHT, cite the accompanying manuscript and this repository. The
software citation metadata are provided in `CITATION.cff`.
