#!/usr/bin/env python
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    from .model_registry import DEFAULT_MODEL_MATRIX, parse_model_list, model_slug, resolve_model_spec
except ImportError:  # pragma: no cover - script execution path
    from model_registry import DEFAULT_MODEL_MATRIX, parse_model_list, model_slug, resolve_model_spec


SPLIT_TO_DIR = {
    "training": Path("Data/Training_data"),
    "testing": Path("Data/Testing_data"),
}

load_dotenv(Path(__file__).parent / ".env")

PROVIDER_ENV_VARS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
}


def assert_provider_credentials(models: list[str]) -> None:
    missing: dict[str, tuple[str, ...]] = {}
    for model_name in models:
        provider = resolve_model_spec(model_name).provider
        required = PROVIDER_ENV_VARS.get(provider)
        if required and not any(os.environ.get(name) for name in required):
            missing[provider] = required

    if missing:
        lines = ["Missing provider credentials for the selected models:"]
        for provider, env_vars in sorted(missing.items()):
            lines.append(f"  {provider}: set one of {', '.join(env_vars)}")
        raise RuntimeError("\n".join(lines))


def existing_ingest_is_complete(output_dir: Path) -> bool:
    combined = output_dir / "combined_cgm.csv"
    manifest = output_dir / "manifest.json"
    if not combined.exists() or not manifest.exists() or combined.stat().st_size == 0:
        return False

    try:
        manifest_payload = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        return False

    files = manifest_payload.get("files")
    if not isinstance(files, list) or not files:
        return False
    return any(bool(entry.get("clean")) for entry in files if isinstance(entry, dict))


def run_ingest_for_dataset(
    dataset_dir: Path,
    output_dir: Path,
    model_name: str,
    *,
    skip_existing: bool = False,
    request_delay: float = 0.0,
) -> str:
    if skip_existing and existing_ingest_is_complete(output_dir):
        return "skipped"

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "harmony/cgm_ingest.py",
        str(dataset_dir),
        "--out",
        str(output_dir),
        "--cgm-model",
        model_name,
        "--default-model",
        model_name,
    ]
    env = os.environ.copy()
    if request_delay > 0:
        env["LLM_REQUEST_DELAY_SECONDS"] = str(request_delay)
    subprocess.run(cmd, check=True, env=env)
    return "processed"


def run_ingests_for_model(
    datasets: list[Path],
    harmonized_root: Path,
    model_name: str,
    *,
    jobs: int = 1,
    skip_existing: bool = False,
    request_delay: float = 0.0,
) -> None:
    if jobs <= 1:
        for dataset_dir in datasets:
            output_dir = harmonized_root / dataset_dir.name
            print(f"  Processing {dataset_dir.name} -> {output_dir}")
            status = run_ingest_for_dataset(
                dataset_dir,
                output_dir,
                model_name,
                skip_existing=skip_existing,
                request_delay=request_delay,
            )
            if status == "skipped":
                print(f"  Skipped {dataset_dir.name}; existing combined output found.")
        return

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {}
        for dataset_dir in datasets:
            output_dir = harmonized_root / dataset_dir.name
            print(f"  Queueing {dataset_dir.name} -> {output_dir}")
            future = executor.submit(
                run_ingest_for_dataset,
                dataset_dir,
                output_dir,
                model_name,
                skip_existing=skip_existing,
                request_delay=request_delay,
            )
            futures[future] = dataset_dir.name

        for future in as_completed(futures):
            dataset_name = futures[future]
            status = future.result()
            print(f"  {dataset_name}: {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run INSIGHT CGM harmonization across dataset directories.")
    parser.add_argument(
        "--splits",
        nargs="+",
        choices=["training", "testing"],
        default=["training", "testing"],
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help=f"Model names to evaluate. Defaults to {', '.join(DEFAULT_MODEL_MATRIX)}.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("harmony/runs"),
        help="Base directory for harmonized outputs.",
    )
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("harmony/evaluation"),
        help="Base directory for reference comparisons.",
    )
    parser.add_argument(
        "--training-root",
        type=Path,
        default=SPLIT_TO_DIR["training"],
        help="Directory containing training dataset subdirectories.",
    )
    parser.add_argument(
        "--testing-root",
        type=Path,
        default=SPLIT_TO_DIR["testing"],
        help="Directory containing testing dataset subdirectories.",
    )
    parser.add_argument("--datasets", nargs="*", default=None, help="Optional dataset filter.")
    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="Disable reference evaluation. This is the default unless --run-evaluation is supplied.",
    )
    parser.add_argument(
        "--run-evaluation",
        action="store_true",
        help="Run benchmark reference evaluation after harmonization. This uses dataset-specific reference utilities.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip dataset ingestion when combined_cgm.csv and manifest.json already exist.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of datasets to ingest concurrently per model. Keep at 1 to avoid API rate limits.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="Seconds to sleep before each LLM request inside a dataset ingest. Useful for provider rate limits.",
    )
    parser.add_argument(
        "--reference-output-root",
        type=Path,
        default=None,
        help="Shared root for model-independent reference outputs. Defaults to <evaluation-root>/reference_outputs.",
    )
    parser.add_argument(
        "--reuse-references",
        action="store_true",
        help="Reuse existing reference_combined.csv files under the shared reference root.",
    )
    parser.add_argument(
        "--comparison-cache-root",
        type=Path,
        default=None,
        help="Shared root for model-independent prepared reference comparison caches. Defaults to <evaluation-root>/comparison_cache.",
    )
    parser.add_argument(
        "--skip-workbook",
        action="store_true",
        help="Skip building the benchmark summary workbook when --run-evaluation is supplied.",
    )
    parser.add_argument(
        "--results-workbook",
        type=Path,
        default=Path("Results.gpt54.xlsx"),
        help="Workbook path for the Results.xlsx-style summary.",
    )
    args = parser.parse_args()

    models = parse_model_list(args.models)
    assert_provider_credentials(models)
    dataset_filter = set(args.datasets or [])
    run_evaluation = args.run_evaluation and not args.skip_evaluation
    split_roots = {
        "training": args.training_root,
        "testing": args.testing_root,
    }

    for split in args.splits:
        raw_root = split_roots[split]
        if not raw_root.exists():
            raise FileNotFoundError(f"{split} root does not exist: {raw_root}")
        datasets = sorted(path for path in raw_root.iterdir() if path.is_dir())
        if dataset_filter:
            datasets = [path for path in datasets if path.name in dataset_filter]

        print(f"Split: {split}")
        print(f"Datasets: {[path.name for path in datasets]}")

        for model_name in models:
            slug = model_slug(model_name)
            harmonized_root = args.output_root / slug / split
            print(f"\nModel: {model_name}")

            run_ingests_for_model(
                datasets,
                harmonized_root,
                model_name,
                jobs=max(args.jobs, 1),
                skip_existing=args.skip_existing,
                request_delay=max(args.request_delay, 0.0),
            )

            if run_evaluation:
                try:
                    from .reference_eval import evaluate_against_reference
                except ImportError:  # pragma: no cover - script execution path
                    from reference_eval import evaluate_against_reference

                reference_output_root = args.reference_output_root or (args.evaluation_root / "reference_outputs")
                comparison_cache_root = args.comparison_cache_root or (args.evaluation_root / "comparison_cache")
                summary_path = evaluate_against_reference(
                    harmonized_root=harmonized_root,
                    split=split,
                    evaluation_root=args.evaluation_root / slug,
                    dataset_names=dataset_filter or None,
                    reference_output_root=reference_output_root,
                    reuse_references=args.reuse_references,
                    comparison_cache_root=comparison_cache_root,
                )
                print(f"  Evaluation summary: {summary_path}")

    if run_evaluation and not args.skip_workbook:
        try:
            from .build_results_workbook import build_results_workbook
        except ImportError:  # pragma: no cover - script execution path
            from build_results_workbook import build_results_workbook

        workbook_path = build_results_workbook(
            models=models,
            runs_root=args.output_root,
            evaluation_root=args.evaluation_root,
            output_path=args.results_workbook,
            splits=args.splits,
        )
        print(f"\nResults workbook: {workbook_path}")


if __name__ == "__main__":
    main()
