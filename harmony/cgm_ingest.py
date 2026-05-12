#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path

import typer

try:
    from .model_registry import DEFAULT_CGM_MODEL, DEFAULT_GENERAL_MODEL, resolve_model_name
    from .structured_ingest import configure_logging, process_dataset
except ImportError:  # pragma: no cover - script execution path
    from model_registry import DEFAULT_CGM_MODEL, DEFAULT_GENERAL_MODEL, resolve_model_name
    from structured_ingest import configure_logging, process_dataset


app = typer.Typer(add_completion=False, help="Standardise CGM datasets in a folder.")


@app.command()
def main(
    input_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Root directory containing raw dataset files.",
    ),
    out: Path = typer.Option(
        "./clean",
        "--out",
        help="Directory where clean CSVs, manifest, and diagnostics will be written.",
    ),
    cgm_model: str = typer.Option(DEFAULT_CGM_MODEL, help="LLM model name for triage prompts."),
    default_model: str = typer.Option(DEFAULT_GENERAL_MODEL, help="LLM model name for parse-spec prompts."),
    log_file: Path = typer.Option(None, "--log-file", help="Path for JSON logs"),
) -> None:
    if log_file is None:
        log_file = out / "ingest.log"
    configure_logging(log_file)
    process_dataset(
        input_dir,
        out,
        resolve_model_name(cgm_model),
        resolve_model_name(default_model),
    )


if __name__ == "__main__":
    app()
