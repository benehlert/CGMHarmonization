# Awesome-CGM

This repository was copied from another GitHub repository with minor modifications to meet our specific requirements.

## Modifications Made

The following changes were applied to the original codebase:

1. **Default Date Changes**: Modified any code that sets a default date to use `1970-01-01` as the default instead of the original default dates.

2. **Subject ID Preservation**: Removed any code modifications that alter or generate new subject IDs, ensuring that the most similar subject ID to the original data is preserved.

3. **Robust Local Execution**: Added direct CLI support for the Python preprocessors and a repo-local runner so the reference scripts can be executed reproducibly from this repository layout.

## Running References

The harmonization pipeline now evaluates against `Awesome-CGM` by rebuilding reference outputs directly from the scripts in this folder.

Examples:

```bash
python Awesome-CGM/Python/Aleppo2017/preprocessor.py \
  --dataset-root Data/Testing_data/Aleppo2017 \
  --output /tmp/aleppo2017_reference.csv
```

```bash
python harmony/reference_eval.py \
  --split testing \
  --harmonized-root harmony/runs/gpt-5/testing \
  --evaluation-root harmony/evaluation/gpt-5
```

## Original Repository

This codebase is based on the original [Awesome-CGM repository](https://github.com/IrinaStatsLab/Awesome-CGM), which contains various CGM (Continuous Glucose Monitoring) data preprocessing scripts and tools.

## Citation

If you use this codebase in your research, please cite the original Awesome-CGM repository:

**Xinran Xu, Neo Kok, Junyan Tan, Mary Martin, David Buchanan, Elizabeth Chun, Rucha Bhat, Shaun Cass, Eric Wang, Sangaman Senthil, & Irina Gaynanova. (2024). IrinaStatsLab/Awesome-CGM: Updated release with additional public CGM dataset and enhanced processing (v2.0.0). Zenodo. DOI**

## Contents

Please refer to the original repository for the complete documentation and original implementation details.
