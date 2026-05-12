# Stats-Only Runs

Use stats-only mode when a dataset does not have a reference preprocessor or
when the goal is to report dataset-level counts without materializing a full
harmonized CSV.

```bash
python harmony/run_ingest_new_data.py \
  --input-root /path/to/new_dataset_root \
  --output-root local_outputs/new_data_stats \
  --models gpt-5.4 \
  --stats-only \
  --skip-existing \
  --jobs 1
```

The input root must contain one subdirectory per dataset:

```text
/path/to/new_dataset_root/
├── DatasetA/
├── DatasetB/
└── DatasetC/
```

Top-level stats include participant counts, unknown-subject rows, glucose
measurement counts, CGM source file counts, and post-merge CGM source file
counts.

Outputs are local artifacts and are ignored by Git.
