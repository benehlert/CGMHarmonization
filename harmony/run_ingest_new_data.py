#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from .model_registry import DEFAULT_MODEL_MATRIX, parse_model_list, model_slug
    from .structured_ingest import configure_logging, process_dataset_stats_only
except ImportError:  # pragma: no cover - script execution path
    from model_registry import DEFAULT_MODEL_MATRIX, parse_model_list, model_slug
    from structured_ingest import configure_logging, process_dataset_stats_only


STATS_SUMMARY_HEADERS = [
    "dataset",
    "model",
    "participants",
    "participants_including_unknown",
    "unknown_subject_rows",
    "glucose_measurements",
    "cgm_source_files",
    "cgm_source_files_used_after_merge",
    "warnings",
]


def existing_stats_is_complete(output_dir: Path) -> bool:
    stats_path = output_dir / "stats_summary.json"
    manifest_path = output_dir / "manifest.json"
    if not stats_path.exists() or not manifest_path.exists():
        return False

    try:
        stats = json.loads(stats_path.read_text())
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return False

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return False
    has_processed_file = any(
        bool(entry.get("clean")) and int(entry.get("rows_out") or 0) > 0
        for entry in files
        if isinstance(entry, dict)
    )
    return has_processed_file and int(stats.get("cgm_source_files") or 0) > 0


def write_model_summary(model_root: Path, datasets: list[Path]) -> Path:
    rows: list[dict[str, object]] = []
    for dataset_dir in datasets:
        stats_path = model_root / dataset_dir.name / "stats_summary.json"
        if not stats_path.exists():
            continue
        stats = json.loads(stats_path.read_text())
        rows.append(
            {
                "dataset": stats.get("dataset", dataset_dir.name),
                "model": (stats.get("models") or {}).get("default_model"),
                "participants": stats.get("participants"),
                "participants_including_unknown": stats.get("participants_including_unknown"),
                "unknown_subject_rows": stats.get("unknown_subject_rows"),
                "glucose_measurements": stats.get("glucose_measurements"),
                "cgm_source_files": stats.get("cgm_source_files"),
                "cgm_source_files_used_after_merge": stats.get("cgm_source_files_used_after_merge"),
                "warnings": "; ".join(stats.get("warnings") or []),
            }
        )

    summary_path = model_root / "summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STATS_SUMMARY_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CGM ingestion for new datasets across selected LLM models.")
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("Data/New_Data"),
        help="Directory containing new raw datasets.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Base directory for outputs. Defaults to harmony/new_data_runs, or harmony/new_data_stats with --stats-only.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help=f"Model names to evaluate. Defaults to {', '.join(DEFAULT_MODEL_MATRIX)}.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip dataset ingestion when the expected output artifacts are complete.",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Run INSIGHT extraction only far enough to report dataset statistics; do not write clean or combined CGM CSVs.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="Seconds to sleep before each LLM request inside a dataset ingest. Useful for provider rate limits.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of datasets to ingest concurrently per model. Keep at 1 to avoid API rate limits.",
    )
    parser.add_argument("--datasets", nargs="*", default=None, help="Optional dataset filter.")
    args = parser.parse_args()

    if not args.input_root.exists():
        raise FileNotFoundError(f"Input root does not exist: {args.input_root}")

    output_root = args.output_root or Path("harmony/new_data_stats" if args.stats_only else "harmony/new_data_runs")
    if args.stats_only and args.request_delay > 0:
        os.environ["LLM_REQUEST_DELAY_SECONDS"] = str(max(args.request_delay, 0.0))

    dataset_filter = set(args.datasets or [])
    datasets = sorted(path for path in args.input_root.iterdir() if path.is_dir())
    if dataset_filter:
        datasets = [path for path in datasets if path.name in dataset_filter]

    models = parse_model_list(args.models)
    print(f"Datasets: {[path.name for path in datasets]}")

    for model_name in models:
        slug = model_slug(model_name)
        print(f"\nModel: {model_name}")
        jobs = max(args.jobs, 1)

        def run_one(dataset_dir: Path) -> tuple[str, str]:
            output_dir = output_root / slug / dataset_dir.name
            if args.skip_existing and args.stats_only and existing_stats_is_complete(output_dir):
                return dataset_dir.name, "skipped"
            if args.skip_existing and not args.stats_only and (output_dir / "combined_cgm.csv").exists() and (output_dir / "manifest.json").exists():
                return dataset_dir.name, "skipped"
            output_dir.mkdir(parents=True, exist_ok=True)
            if args.stats_only:
                configure_logging(output_dir / "ingest.log")
                process_dataset_stats_only(
                    dataset_dir,
                    output_dir,
                    cgm_model=model_name,
                    default_model=model_name,
                )
                return dataset_dir.name, "processed"

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
            if args.request_delay > 0:
                env["LLM_REQUEST_DELAY_SECONDS"] = str(max(args.request_delay, 0.0))
            subprocess.run(cmd, check=True, env=env)
            return dataset_dir.name, "processed"

        if jobs <= 1:
            for dataset_dir in datasets:
                output_dir = output_root / slug / dataset_dir.name
                print(f"  Processing {dataset_dir.name} -> {output_dir}")
                name, status = run_one(dataset_dir)
                if status == "skipped":
                    print(f"  Skipped {name}; existing output found.")
        else:
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                futures = {}
                for dataset_dir in datasets:
                    output_dir = output_root / slug / dataset_dir.name
                    print(f"  Queueing {dataset_dir.name} -> {output_dir}")
                    futures[executor.submit(run_one, dataset_dir)] = dataset_dir.name
                for future in as_completed(futures):
                    name, status = future.result()
                    print(f"  {name}: {status}")

        if args.stats_only:
            summary_path = write_model_summary(output_root / slug, datasets)
            print(f"  Stats summary: {summary_path}")


if __name__ == "__main__":
    main()
