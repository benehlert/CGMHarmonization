# Reference Evaluation

Reference-backed evaluation is available when users have the relevant source
datasets locally. The public repository includes adapted Awesome-CGM reference
preprocessors, but it does not include the datasets those scripts operate on.

## Modes

Run INSIGHT without references when applying the pipeline to arbitrary user
data:

```bash
python harmony/cgm_ingest.py /path/to/dataset \
  --out local_outputs/runs/gpt-5-4/my_dataset \
  --cgm-model gpt-5.4 \
  --default-model gpt-5.4
```

Run reference-backed evaluation when local datasets match the registered
reference dataset layout:

```bash
python harmony/run_ingest_all.py \
  --training-root /path/to/training_datasets \
  --testing-root /path/to/testing_datasets \
  --models gpt-5.4 \
  --reuse-references \
  --output-root local_outputs/runs \
  --evaluation-root local_outputs/evaluation \
  --results-workbook local_outputs/results.xlsx
```

Evaluate an existing harmonized root:

```bash
python harmony/reference_eval.py \
  --split testing \
  --harmonized-root local_outputs/runs/gpt-5-4/testing \
  --evaluation-root local_outputs/evaluation/gpt-5-4
```

## Reference Script Attribution

The scripts under `Awesome-CGM/` are adapted from the MIT-licensed Awesome-CGM
repository. See `NOTICE` and `Awesome-CGM/LICENSE`.

## Comparison Outputs

Evaluation writes local artifacts under the selected evaluation root, including
comparison JSON, text summaries, benchmark status, and split summaries. These
files are generated outputs and are intentionally ignored by Git.
