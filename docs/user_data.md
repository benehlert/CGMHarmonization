# Using External Datasets

INSIGHT expects users to supply datasets locally. Do not place source datasets
inside Git-tracked repository paths.

## One Dataset

```bash
python harmony/cgm_ingest.py /path/to/dataset \
  --out local_outputs/runs/gpt-5-4/my_dataset \
  --cgm-model gpt-5.4 \
  --default-model gpt-5.4
```

## Split Benchmark Layout

For `run_ingest_all.py`, each split root should contain one subdirectory per
dataset:

```text
/path/to/training_datasets/
├── TrainingDatasetA/
└── TrainingDatasetB/

/path/to/testing_datasets/
├── TestingDatasetA/
└── TestingDatasetB/
```

Then run:

```bash
python harmony/run_ingest_all.py \
  --training-root /path/to/training_datasets \
  --testing-root /path/to/testing_datasets \
  --models gpt-5.4 \
  --output-root local_outputs/runs \
  --evaluation-root local_outputs/evaluation \
  --skip-evaluation \
  --skip-workbook
```

Use `--skip-evaluation` when no reference outputs are available.
