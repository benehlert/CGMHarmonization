from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def default_dataset_root() -> Path:
    return Path(__file__).resolve().parents[3] / "Data" / "Training_data" / "Weinstock2016"


def load_weinstock2016(dataset_root: Path) -> pd.DataFrame:
    source_file = (
        dataset_root
        / "SevereHypoDataset-c14d3739-6a20-449c-bbae-c02ff1764a91"
        / "Data Tables"
        / "BDataCGM.txt"
    )
    df = pd.read_csv(source_file, sep="|", low_memory=False)
    df = df.rename(
        columns={
            "PtID": "id",
            "DeviceDaysFromEnroll": "day_offset",
            "DeviceTm": "reading_time",
            "Glucose": "gl",
        }
    )

    base_date = pd.Timestamp("1970-01-01")
    day_offset = pd.to_numeric(df["day_offset"], errors="raise").astype(int)
    reading_date = base_date + pd.to_timedelta(day_offset, unit="D")
    reading_time = pd.to_datetime(df["reading_time"], format="%H:%M:%S", errors="raise")
    timestamp = reading_date + pd.to_timedelta(reading_time.dt.strftime("%H:%M:%S"))

    result = pd.DataFrame(
        {
            "id": df["id"].astype(str),
            "time": timestamp,
            "gl": pd.to_numeric(df["gl"], errors="coerce"),
        }
    )
    return result.dropna(subset=["time", "gl"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Awesome-CGM Weinstock2016 preprocessor.")
    parser.add_argument("--dataset-root", type=Path, default=default_dataset_root())
    parser.add_argument("--output", type=Path, default=Path("Weinstock2016_processed.csv"))
    args = parser.parse_args()

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    processed = load_weinstock2016(args.dataset_root.resolve())
    processed.to_csv(output_path, index=False)
    print(output_path)


if __name__ == "__main__":
    main()
