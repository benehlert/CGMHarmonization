from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd


DEFAULT_BASEDATE = dt.date(1970, 1, 1)


def default_dataset_root() -> Path:
    return Path(__file__).resolve().parents[3] / "Data" / "Testing_data" / "Aleppo2017"


def load_aleppo2017(dataset_root: Path) -> pd.DataFrame:
    source_file = dataset_root / "Replace-BG Dataset" / "Data Tables" / "HDeviceCGM.txt"
    df = pd.read_csv(source_file, sep="|", low_memory=False)

    day_offsets = pd.to_numeric(df["DeviceDtTmDaysFromEnroll"], errors="raise").astype(int)
    reading_time = pd.to_timedelta(df["DeviceTm"], errors="raise")
    timestamp = pd.Timestamp(DEFAULT_BASEDATE) + pd.to_timedelta(day_offsets, unit="D") + reading_time

    result = pd.DataFrame(
        {
            "id": df["PtID"].astype(str),
            "time": timestamp,
            "gl": pd.to_numeric(df["GlucoseValue"], errors="coerce"),
        }
    )
    return result.dropna(subset=["time", "gl"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Awesome-CGM Aleppo2017 preprocessor.")
    parser.add_argument("--dataset-root", type=Path, default=default_dataset_root())
    parser.add_argument("--output", type=Path, default=Path("Aleppo2017_processed.csv"))
    args = parser.parse_args()

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    processed = load_aleppo2017(args.dataset_root.resolve())
    processed.to_csv(output_path, index=False)
    print(output_path)


if __name__ == "__main__":
    main()
