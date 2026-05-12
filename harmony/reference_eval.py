from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

import pandas as pd

try:
    from .compare_csvs import compare_csvs, load_standardized_csv_with_diagnostics, render_report
except ImportError:  # pragma: no cover - script execution path
    from compare_csvs import compare_csvs, load_standardized_csv_with_diagnostics, render_report


@dataclass(frozen=True)
class ReferenceProducer:
    script_path: str
    runner: str
    expected_output: Optional[str] = None


@dataclass(frozen=True)
class DatasetReference:
    dataset: str
    display_name: str
    split: str
    gold_patterns: tuple[str, ...]
    gold_label: str
    reference_patterns: tuple[str, ...]
    reference_label: str
    producers: tuple[ReferenceProducer, ...]


TRAINING_DIRNAME = "Training_data"
TESTING_DIRNAME = "Testing_data"

REFERENCE_QUARANTINE_REASONS: dict[str, str] = {}

COMPLETE_RAW_REFERENCE_DATASETS = {
    "Anderson2016",
    "Brown2019",
    "Buckingham2007",
    "Lynch2022",
    "Wadwa2023",
}

REFERENCE_DATASETS: tuple[DatasetReference, ...] = (
    DatasetReference(
        dataset="Aleppo2017",
        display_name="Aleppo",
        split="testing",
        gold_patterns=("**/HDeviceCGM.txt",),
        gold_label="HDeviceCGM.txt",
        reference_patterns=("**/HDeviceCGM.txt",),
        reference_label="HDeviceCGM.txt",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/Python/Aleppo2017/preprocessor.py",
                runner="python_cli",
            ),
        ),
    ),
    DatasetReference(
        dataset="Anderson2016",
        display_name="Anderson",
        split="testing",
        gold_patterns=("**/CGM.txt", "**/MonitorCGM.txt"),
        gold_label="CGM.txt, MonitorCGM.txt",
        reference_patterns=("**/CGM.txt", "**/MonitorCGM.txt"),
        reference_label="CGM.txt, MonitorCGM.txt",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Anderson2016/preprocessor.r",
                runner="legacy_r",
                expected_output="Anderson2016_processed.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Brown2019",
        display_name="Brown",
        split="testing",
        gold_patterns=(
            "**/cgm.txt",
            "**/DexcomClarityCGM_a.txt",
            "**/DiasendCGM_a.txt",
            "**/OtherCGM_a.txt",
            "**/Pump_CGMGlucoseValue.txt",
        ),
        gold_label="cgm.txt, DexcomClarityCGM_a.txt, DiasendCGM_a.txt, OtherCGM_a.txt, Pump_CGMGlucoseValue.txt",
        reference_patterns=(
            "**/cgm.txt",
            "**/DexcomClarityCGM_a.txt",
            "**/DiasendCGM_a.txt",
            "**/OtherCGM_a.txt",
            "**/Pump_CGMGlucoseValue.txt",
        ),
        reference_label="cgm.txt, DexcomClarityCGM_a.txt, DiasendCGM_a.txt, OtherCGM_a.txt, Pump_CGMGlucoseValue.txt",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Brown2019/Brown2019_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/o_malley2021.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Buckingham2007",
        display_name="Buckingham",
        split="testing",
        gold_patterns=("**/tblFNavGlucose.csv", "**/*_Blinded.csv"),
        gold_label="tblFNavGlucose.csv, **_blinded.csv",
        reference_patterns=("**/tblFNavGlucose.csv", "**/*_Blinded.csv"),
        reference_label="tblFNavGlucose.csv, **_blinded.csv",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Buckingham2007/Buckingham2007_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/buckingham2007.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Chase2005",
        display_name="Chase",
        split="testing",
        gold_patterns=("**/tblCDataCGMS.csv", "**/tblCDataGWB.csv"),
        gold_label="tblCDataCGMS.csv, tblCDataGWB.csv",
        reference_patterns=("**/tblCDataCGMS.csv", "**/tblCDataGWB.csv"),
        reference_label="tblCDataCGMS.csv, tblCDataGWB.csv",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Chase2005/preprocessor-CGMS.R",
                runner="legacy_r",
                expected_output="Chase2005CGMS_processed.csv",
            ),
            ReferenceProducer(
                script_path="Awesome-CGM/R/Chase2005/preprocessor-GWB.R",
                runner="legacy_r",
                expected_output="Chase2005GWB_processed.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Dubosson2018",
        display_name="Dubosson",
        split="testing",
        gold_patterns=("diabetes_subset_pictures-glucose-food-insulin/**/glucose.csv",),
        gold_label="diabetes_subset_pictures-glucose-food-insulin/**/glucose.csv",
        reference_patterns=("diabetes_subset_pictures-glucose-food-insulin/**/glucose.csv",),
        reference_label="diabetes_subset_pictures-glucose-food-insulin/**/glucose.csv",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Dubosson2018/preprocessor.r",
                runner="legacy_r",
                expected_output="Dubosson2018_processed.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Lynch2022",
        display_name="Lynch",
        split="testing",
        gold_patterns=("**/IOBP2DeviceCGM.txt", "**/IOBP2DeviceiLet.txt"),
        gold_label="IOBP2DeviceCGM.txt, IOBP2DeviceiLet.txt",
        reference_patterns=("**/IOBP2DeviceCGM.txt", "**/IOBP2DeviceiLet.txt"),
        reference_label="IOBP2DeviceCGM.txt, IOBP2DeviceiLet.txt",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Lynch2022/Lynch2022_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/lynch2022.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Tamborlane2008",
        display_name="Tamborlane",
        split="testing",
        gold_patterns=(
            "**/tblADataRTCGM_Blind_Baseline.csv",
            "**/tblADataRTCGM_Blind_ControlGroup.csv",
            "**/tblADataRTCGM_Unblinded_ControlGroup_*.csv",
            "**/tblADataRTCGM_Unblinded_RTCGMGroup_*.csv",
        ),
        gold_label="tblADataRTCGM_Blind_Baseline.csv, tblADataRTCGM_Blind_ControlGroup.csv, tblADataRTCGM_Unblinded_ControlGroup_**.csv ",
        reference_patterns=(
            "**/tblADataRTCGM_Blind_Baseline.csv",
            "**/tblADataRTCGM_Blind_ControlGroup.csv",
            "**/tblADataRTCGM_Unblinded_ControlGroup_*.csv",
            "**/tblADataRTCGM_Unblinded_RTCGMGroup_*.csv",
        ),
        reference_label="tblADataRTCGM_Blind_Baseline.csv, tblADataRTCGM_Blind_ControlGroup.csv, tblADataRTCGM_Unblinded_ControlGroup_**.csv ",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Tamborlane2008/preprocessor.r",
                runner="legacy_r",
                expected_output="Tamborlane2008_processed.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Tsalikian2005",
        display_name="Tsalikian",
        split="testing",
        gold_patterns=("**/tblDDataCGMS.csv",),
        gold_label="tblDDataCGMS.csv",
        reference_patterns=("**/tblDDataCGMS.csv",),
        reference_label="tblDDataCGMS.csv",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Tsalikian2005/preprocessor.r",
                runner="legacy_r",
                expected_output="Tsalikian2005_processed.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Wadwa2023",
        display_name="Wadwa",
        split="testing",
        gold_patterns=(
            "**/PEDAPDexcomClarityCGM.txt",
            "**/PEDAPOtherCGM.txt",
            "**/PEDAPTandemCGMDATAGXB.txt",
        ),
        gold_label="PEDAPDexcomClarityCGM.txt, PEDAPOtherCGM.txt, PEDAPTandemCGMDATAGXB.txt",
        reference_patterns=(
            "**/PEDAPDexcomClarityCGM.txt",
            "**/PEDAPOtherCGM.txt",
            "**/PEDAPTandemCGMDATAGXB.txt",
        ),
        reference_label="PEDAPDexcomClarityCGM.txt, PEDAPOtherCGM.txt, PEDAPTandemCGMDATAGXB.txt",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Wadwa2023/Wadwa2023_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/wadwa2023.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Colas2019",
        display_name="Colas",
        split="training",
        gold_patterns=("S1/case *.csv",),
        gold_label="case_**.csv",
        reference_patterns=("S1/case *.csv",),
        reference_label="case_**.csv",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Colas2019/Colas2019_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/colas2019.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Hall2018",
        display_name="Hall",
        split="training",
        gold_patterns=("pbio.2005143.s010", "**/pbio.2005143.s010"),
        gold_label="pbio.2005143.s010",
        reference_patterns=("pbio.2005143.s010", "**/pbio.2005143.s010"),
        reference_label="pbio.2005143.s010",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Hall2018/Hall2018_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/hall2018.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Shah2019",
        display_name="Shah",
        split="training",
        gold_patterns=("**/NonDiabDeviceCGM.csv",),
        gold_label="NonDiabDeviceCGM.csv",
        reference_patterns=("**/NonDiabDeviceCGM.csv",),
        reference_label="NonDiabDeviceCGM.csv",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/R/Shah2019/Shah2019_preprocessor.R",
                runner="legacy_r",
                expected_output="csv_data/shah2019.csv",
            ),
        ),
    ),
    DatasetReference(
        dataset="Weinstock2016",
        display_name="Weinstock",
        split="training",
        gold_patterns=("**/BDataCGM.txt",),
        gold_label="BDataCGM.txt",
        reference_patterns=("**/BDataCGM.txt",),
        reference_label="BDataCGM.txt",
        producers=(
            ReferenceProducer(
                script_path="Awesome-CGM/Python/Weinstock2016/preprocessor.py",
                runner="python_cli",
            ),
        ),
    ),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def raw_data_root(split: str) -> Path:
    dirname = TRAINING_DIRNAME if split == "training" else TESTING_DIRNAME
    return repo_root() / "Data" / dirname


def get_reference_datasets(split: str, dataset_names: Optional[Iterable[str]] = None) -> list[DatasetReference]:
    selected = [entry for entry in REFERENCE_DATASETS if entry.split == split]
    if dataset_names is None:
        return selected
    requested = {name.strip() for name in dataset_names if name.strip()}
    return [entry for entry in selected if entry.dataset in requested]


def expand_dataset_patterns(dataset_ref: DatasetReference, patterns: Iterable[str]) -> list[str]:
    root = raw_data_root(dataset_ref.split) / dataset_ref.dataset
    available = []
    for path in root.rglob("*"):
        if path.is_file():
            available.append(path.relative_to(root).as_posix())

    matches: list[str] = []
    seen: set[str] = set()
    for rel_path in sorted(available):
        rel_lower = rel_path.lower()
        for pattern in patterns:
            if fnmatch.fnmatch(rel_lower, pattern.lower()):
                if rel_path not in seen:
                    matches.append(rel_path)
                    seen.add(rel_path)
                break
    return matches


def ensure_workspace_links(workspace: Path) -> None:
    raw_root = workspace / "RawData"
    raw_root.mkdir(parents=True, exist_ok=True)

    training_path = raw_data_root("training")
    testing_path = raw_data_root("testing")

    for alias, target in (
        ("Training_Data", training_path),
        ("Training_data", training_path),
        ("Testing_Data", testing_path),
        ("Testing_data", testing_path),
    ):
        link = raw_root / alias
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)


def _run_command(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def _write_canonical_standardized_csv(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    out["Timestamp"] = (
        pd.to_datetime(out["Timestamp"], errors="coerce", format="mixed", utc=True)
        .dt.tz_convert(None)
        .dt.floor("s")
        .dt.strftime("%Y-%m-%d %H:%M:%S")
    )
    out["Glucose"] = pd.to_numeric(out["Glucose"], errors="coerce").astype(float)
    out["Subject_ID"] = out["Subject_ID"].astype(str)
    out.to_csv(path, index=False)


def _normalize_reference_subject(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .replace({"": "UNKNOWN", "nan": "UNKNOWN", "NaN": "UNKNOWN", "None": "UNKNOWN"})
    )


def _read_reference_table(path: Path) -> pd.DataFrame:
    separator = "|" if path.suffix.lower() == ".txt" else ","
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin1"):
        try:
            df = pd.read_csv(path, sep=separator, dtype=str, encoding=encoding, low_memory=False)
            df.columns = [str(column).replace("\ufeff", "").strip() for column in df.columns]
            return df
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise ValueError(f"Unable to read reference input {path}: {last_error}") from last_error


def _parse_datetime(series: pd.Series, date_format: str | None = None) -> pd.Series:
    raw = series.astype(str).str.strip()
    return pd.to_datetime(raw, format=date_format, errors="coerce")


def _coalesced_datetime(df: pd.DataFrame, columns: Iterable[str]) -> pd.Series:
    combined = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    for column in columns:
        if column not in df.columns:
            continue
        parsed = _parse_datetime(df[column])
        combined = combined.where(combined.notna(), parsed)
    return combined


def _standardized_raw_reference_part(dataset: str, path: Path) -> pd.DataFrame:
    df = _read_reference_table(path)
    name = path.name

    if dataset == "Anderson2016":
        timestamp = (
            _coalesced_datetime(df, ("DisplayTimeAdjusted", "DisplayTime", "InternalTime"))
            if name == "CGM.txt"
            else _coalesced_datetime(df, ("LocalDtTmAdjusted", "LocalDtTm"))
        )
        glucose = pd.to_numeric(df["CGM"], errors="coerce")
        subject = _normalize_reference_subject(df["DeidentID"])
    elif dataset == "Brown2019":
        if name == "cgm.txt":
            timestamp = _parse_datetime(df["DataDtTm"], "%d%b%y:%H:%M:%S")
            glucose_column = "CGM"
        elif name == "Pump_CGMGlucoseValue.txt":
            timestamp = _parse_datetime(df["DataDtTm"], "%Y-%m-%d %H:%M:%S")
            glucose_column = "CGMValue"
        else:
            adjusted = df.get("DataDtTm_adjusted")
            timestamp_column = (
                "DataDtTm_adjusted"
                if adjusted is not None and adjusted.fillna("").astype(str).str.strip().ne("").any()
                else "DataDtTm"
            )
            timestamp = _parse_datetime(df[timestamp_column], "%Y-%m-%d %H:%M:%S")
            glucose_column = "CGM"
        glucose = pd.to_numeric(df[glucose_column], errors="coerce")
        subject = _normalize_reference_subject(df["PtID"])
    elif dataset == "Buckingham2007":
        if name == "tblFNavGlucose.csv":
            nav_date = _parse_datetime(df["NavReadDt"], "%Y-%m-%d %H:%M:%S")
            nav_time = pd.to_timedelta(df["NavReadTm"].astype(str).str.strip(), errors="coerce")
            timestamp = nav_date + nav_time
            glucose = pd.to_numeric(df["Gl"], errors="coerce")
            subject = _normalize_reference_subject(df["PtID"])
        else:
            if "ReadingDtTm" in df.columns:
                timestamp = _parse_datetime(df["ReadingDtTm"], "%m/%d/%Y %H:%M")
            else:
                timestamp = _parse_datetime(
                    df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip(),
                    "%m/%d/%Y %I:%M %p",
                )
            glucose = pd.to_numeric(df["SensorGlucose"], errors="coerce")
            subject = _normalize_reference_subject(df["ID"])
    elif dataset == "Lynch2022":
        if name == "IOBP2DeviceCGM.txt" and "RecordType" in df.columns:
            df = df.loc[df["RecordType"].astype(str).str.strip().str.lower().eq("cgm")].copy()
            glucose_column = "Value"
        else:
            glucose_column = "CGMVal"
        timestamp = _parse_datetime(df["DeviceDtTm"], "%m/%d/%Y %I:%M:%S %p")
        glucose = pd.to_numeric(df[glucose_column], errors="coerce")
        subject = _normalize_reference_subject(df["PtID"])
    elif dataset == "Wadwa2023":
        if "IsCalibration" in df.columns:
            calibration = df["IsCalibration"].astype(str).str.strip().str.lower()
            df = df.loc[~calibration.isin({"1", "true", "yes", "y"})].copy()
        glucose_column = "CGMValue" if "CGMValue" in df.columns else "CGM"
        timestamp = _parse_datetime(df["DeviceDtTm"], "%m/%d/%Y %I:%M:%S %p")
        glucose = pd.to_numeric(df[glucose_column], errors="coerce")
        subject = _normalize_reference_subject(df["PtID"])
    else:
        raise ValueError(f"No complete raw reference parser configured for {dataset}")

    return pd.DataFrame(
        {
            "Timestamp": timestamp,
            "Glucose": glucose,
            "Subject_ID": subject,
            "__source_name": name,
        }
    )


def _merge_raw_reference_parts(dataset: str, parts: list[pd.DataFrame]) -> pd.DataFrame:
    combined = pd.concat(parts, ignore_index=True)
    combined["Timestamp"] = pd.to_datetime(combined["Timestamp"], errors="coerce").dt.floor("s")
    combined["Glucose"] = pd.to_numeric(combined["Glucose"], errors="coerce").astype(float)
    combined["Subject_ID"] = _normalize_reference_subject(combined["Subject_ID"])
    combined = combined.dropna(subset=["Timestamp", "Glucose", "Subject_ID"]).copy()

    if dataset == "Brown2019":
        source_priority = combined["__source_name"].str.lower().map(lambda source: 0 if source == "cgm.txt" else 2)
        combined = (
            combined.assign(__priority=source_priority)
            .sort_values(["__priority", "Subject_ID", "Timestamp", "Glucose"])
            .drop_duplicates(subset=["Subject_ID", "Timestamp"], keep="first")
        )
    else:
        combined = combined.drop_duplicates()

    combined = combined.drop(columns=["__source_name", "__priority"], errors="ignore")
    return combined.sort_values(["Subject_ID", "Timestamp", "Glucose"]).reset_index(drop=True)


def run_complete_raw_reference_dataset(
    dataset_ref: DatasetReference,
    output_root: Path,
) -> tuple[Path, dict[str, object]]:
    dataset_output_dir = output_root / dataset_ref.split / dataset_ref.dataset
    raw_output_dir = dataset_output_dir / "raw_outputs"
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    dataset_root = raw_data_root(dataset_ref.split) / dataset_ref.dataset
    reference_parts: list[pd.DataFrame] = []
    part_diagnostics: list[dict[str, object]] = []
    for index, relative_path in enumerate(expand_dataset_patterns(dataset_ref, dataset_ref.reference_patterns), start=1):
        raw_path = dataset_root / relative_path
        part = _standardized_raw_reference_part(dataset_ref.dataset, raw_path)
        part_output = raw_output_dir / f"part{index}_normalized.csv"
        _write_canonical_standardized_csv(part.drop(columns=["__source_name"]), part_output)
        _, diagnostics = load_standardized_csv_with_diagnostics(part_output)
        diagnostics["source_file"] = relative_path
        part_diagnostics.append(diagnostics)
        reference_parts.append(part)

    combined = _merge_raw_reference_parts(dataset_ref.dataset, reference_parts)
    reference_output = dataset_output_dir / "reference_combined.csv"
    _write_canonical_standardized_csv(combined, reference_output)
    _, reference_diagnostics = load_standardized_csv_with_diagnostics(reference_output)
    reference_diagnostics["part_diagnostics"] = part_diagnostics
    reference_diagnostics["unique_subjects"] = int(combined["Subject_ID"].nunique())
    return reference_output, reference_diagnostics


def _assess_reference_sanity(
    dataset_ref: DatasetReference,
    reference_file: Path,
    reference_diagnostics: dict[str, object],
) -> dict[str, object]:
    gold_files = expand_dataset_patterns(dataset_ref, dataset_ref.gold_patterns)
    reference_files = expand_dataset_patterns(dataset_ref, dataset_ref.reference_patterns)
    partial_reference = len(reference_files) < len(gold_files)
    mixed_formats = bool(reference_diagnostics.get("mixed_timestamp_formats_detected"))
    parse_rate = reference_diagnostics.get("timestamp_parse_success_rate")
    parse_ok = parse_rate is not None and float(parse_rate) >= 0.99
    row_count = int(reference_diagnostics.get("rows_loaded") or 0)
    subject_count = int(reference_diagnostics.get("unique_subjects") or 0)

    coverage_status = "partial_reference_scope" if partial_reference else "complete"
    if mixed_formats:
        parse_status = "mixed_timestamp_formats_detected"
    elif not parse_ok:
        parse_status = "low_timestamp_parse_success"
    else:
        parse_status = "ok"

    status = "active"
    reason = None
    if dataset_ref.dataset in REFERENCE_QUARANTINE_REASONS:
        status = "quarantined"
        reason = REFERENCE_QUARANTINE_REASONS[dataset_ref.dataset]
    elif partial_reference:
        status = "quarantined"
        reason = "Reference repository covers only a subset of the gold CGM files."
    elif mixed_formats or not parse_ok or row_count <= 0 or subject_count <= 0:
        status = "quarantined"
        reason = "Reference normalization failed sanity checks."

    return {
        "dataset": dataset_ref.dataset,
        "reference_file": str(reference_file),
        "status": status,
        "reason": reason,
        "reference_coverage_status": coverage_status,
        "reference_parse_status": parse_status,
        "gold_file_count": len(gold_files),
        "reference_file_count": len(reference_files),
        "reference_rows_loaded": row_count,
        "reference_subject_count": subject_count,
    }


def _reference_scope_payload(benchmark_status: dict[str, object]) -> dict[str, object]:
    gold_count = int(benchmark_status.get("gold_file_count") or 0)
    reference_count = int(benchmark_status.get("reference_file_count") or 0)
    coverage_status = benchmark_status.get("reference_coverage_status")
    parse_status = benchmark_status.get("reference_parse_status")
    status = benchmark_status.get("status")
    reason = benchmark_status.get("reason")

    if reference_count < gold_count or coverage_status != "complete":
        comparison_scope = "reference_present_only"
        note = (
            f"Reference covers {reference_count} of {gold_count} expected CGM files; "
            "metrics compare the candidate only against normalized reference content that is present."
        )
    elif status == "quarantined":
        comparison_scope = "reference_present_with_quality_warning"
        note = (
            "Reference output was normalized and compared, but it is excluded from headline "
            f"benchmark summaries because: {reason}"
        )
    elif parse_status != "ok":
        comparison_scope = "reference_present_with_parse_warning"
        note = (
            "Reference output was normalized and compared, but reference parsing diagnostics "
            f"reported {parse_status}."
        )
    else:
        comparison_scope = "complete_reference"
        note = "Reference appears complete for the configured CGM file scope."

    return {
        "comparison_scope": comparison_scope,
        "note": note,
        "gold_file_count": gold_count,
        "reference_file_count": reference_count,
        "reference_coverage_status": coverage_status,
        "reference_parse_status": parse_status,
        "benchmark_status": status,
        "benchmark_reason": reason,
    }


def run_reference_dataset(dataset_ref: DatasetReference, output_root: Path) -> tuple[Path, dict[str, object]]:
    return run_reference_dataset_cached(dataset_ref, output_root, reuse_existing=False)


def _load_reference_diagnostics(reference_output: Path) -> dict[str, object]:
    combined, reference_diagnostics = load_standardized_csv_with_diagnostics(reference_output)
    reference_diagnostics["unique_subjects"] = int(combined["Subject_ID"].nunique())
    return reference_diagnostics


def run_reference_dataset_cached(
    dataset_ref: DatasetReference,
    output_root: Path,
    *,
    reuse_existing: bool = False,
) -> tuple[Path, dict[str, object]]:
    reference_output = output_root / dataset_ref.split / dataset_ref.dataset / "reference_combined.csv"
    if reuse_existing and reference_output.exists():
        return reference_output, _load_reference_diagnostics(reference_output)

    if dataset_ref.dataset in COMPLETE_RAW_REFERENCE_DATASETS:
        return run_complete_raw_reference_dataset(dataset_ref, output_root)

    repo = repo_root()
    dataset_output_dir = output_root / dataset_ref.split / dataset_ref.dataset
    raw_output_dir = dataset_output_dir / "raw_outputs"
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    normalized_parts: list[pd.DataFrame] = []
    part_diagnostics: list[dict[str, object]] = []

    for index, producer in enumerate(dataset_ref.producers, start=1):
        part_name = f"part{index}"
        raw_output_path = raw_output_dir / f"{part_name}.csv"

        if producer.runner == "python_cli":
            dataset_root = raw_data_root(dataset_ref.split) / dataset_ref.dataset
            cmd = [
                sys.executable,
                str(repo / producer.script_path),
                "--dataset-root",
                str(dataset_root),
                "--output",
                str(raw_output_path),
            ]
            _run_command(cmd, cwd=repo)
        elif producer.runner == "legacy_r":
            with tempfile.TemporaryDirectory(prefix=f"{dataset_ref.dataset.lower()}_ref_") as tmpdir:
                workspace = Path(tmpdir)
                ensure_workspace_links(workspace)
                _run_command(["Rscript", str(repo / producer.script_path)], cwd=workspace)

                produced_file = workspace / str(producer.expected_output)
                if not produced_file.exists():
                    raise FileNotFoundError(
                        f"Reference script did not produce expected file: {produced_file}"
                    )
                shutil.copy2(produced_file, raw_output_path)
        else:
            raise ValueError(f"Unsupported runner: {producer.runner}")

        normalized, diagnostics = load_standardized_csv_with_diagnostics(raw_output_path)
        _write_canonical_standardized_csv(normalized, raw_output_dir / f"{part_name}_normalized.csv")
        normalized_parts.append(normalized)
        part_diagnostics.append(diagnostics)

    combined = (
        pd.concat(normalized_parts, ignore_index=True)
        .drop_duplicates()
        .sort_values(["Subject_ID", "Timestamp", "Glucose"])
        .reset_index(drop=True)
    )
    reference_output = dataset_output_dir / "reference_combined.csv"
    _write_canonical_standardized_csv(combined, reference_output)
    reference_diagnostics = _load_reference_diagnostics(reference_output)
    reference_diagnostics["part_diagnostics"] = part_diagnostics
    return reference_output, reference_diagnostics


def evaluate_against_reference(
    harmonized_root: Path,
    split: str,
    evaluation_root: Path,
    dataset_names: Optional[Iterable[str]] = None,
    reference_output_root: Optional[Path] = None,
    reuse_references: bool = False,
    comparison_cache_root: Optional[Path] = None,
) -> Path:
    datasets = get_reference_datasets(split, dataset_names)
    summary_rows: list[dict[str, object]] = []
    reference_root = reference_output_root or (evaluation_root / "reference_outputs")

    for dataset_ref in datasets:
        reference_file, reference_diagnostics = run_reference_dataset_cached(
            dataset_ref,
            reference_root,
            reuse_existing=reuse_references,
        )
        candidate_file = harmonized_root / dataset_ref.dataset / "combined_cgm.csv"
        comparison_dir = evaluation_root / "comparisons" / split / dataset_ref.dataset
        comparison_dir.mkdir(parents=True, exist_ok=True)
        benchmark_status = _assess_reference_sanity(
            dataset_ref=dataset_ref,
            reference_file=reference_file,
            reference_diagnostics=reference_diagnostics,
        )
        reference_scope = _reference_scope_payload(benchmark_status)
        (comparison_dir / "benchmark_status.json").write_text(json.dumps(benchmark_status, indent=2))
        if not candidate_file.exists():
            summary_rows.append(
                {
                    "split": split,
                    "dataset": dataset_ref.dataset,
                    "candidate_file": str(candidate_file),
                    "reference_file": str(reference_file),
                    "status": "missing_candidate",
                    "benchmark_status": benchmark_status["status"],
                    "benchmark_reason": benchmark_status["reason"],
                    "reference_coverage_status": benchmark_status["reference_coverage_status"],
                    "reference_parse_status": benchmark_status["reference_parse_status"],
                    "reference_comparison_scope": reference_scope["comparison_scope"],
                    "reference_scope_note": reference_scope["note"],
                }
            )
            continue

        reference_cache_dir = None
        if comparison_cache_root is not None:
            reference_cache_dir = comparison_cache_root / split / dataset_ref.dataset

        result = compare_csvs(
            candidate_file,
            reference_file,
            reference_cache_dir=reference_cache_dir,
        )
        result["benchmark_status"] = benchmark_status
        result["reference_scope"] = reference_scope
        report = render_report(result)

        (comparison_dir / "comparison.json").write_text(json.dumps(result, indent=2))
        (comparison_dir / "comparison.txt").write_text(report)

        summary_rows.append(
            {
                "split": split,
                "dataset": dataset_ref.dataset,
                "candidate_file": str(candidate_file),
                "reference_file": str(reference_file),
                "status": "compared",
                "candidate_rows": result["candidate_summary"]["rows_compared"],
                "reference_rows": result["reference_summary"]["rows_compared"],
                "candidate_subjects": result["candidate_summary"]["unique_subjects"],
                "reference_subjects": result["reference_summary"]["unique_subjects"],
                "exact_match_rows": result["row_metrics"]["exact_match_rows"],
                "row_precision": result["row_metrics"]["precision"],
                "row_recall": result["row_metrics"]["recall"],
                "row_f1": result["row_metrics"]["f1"],
                "subject_precision": result["subject_metrics"]["precision"],
                "subject_recall": result["subject_metrics"]["recall"],
                "subject_f1": result["subject_metrics"]["f1"],
                "aligned_pairs": result["temporal_alignment_metrics"]["matched_pairs"],
                "temporal_precision": result["temporal_alignment_metrics"]["precision"],
                "temporal_recall": result["temporal_alignment_metrics"]["recall"],
                "temporal_f1": result["temporal_alignment_metrics"]["f1"],
                "median_subject_temporal_recall": result["temporal_alignment_metrics"]["median_pair_recall"],
                "median_subject_temporal_precision": result["temporal_alignment_metrics"]["median_pair_precision"],
                "glucose_mae": result["glucose_agreement_metrics"]["glucose_mae"],
                "glucose_rmse": result["glucose_agreement_metrics"]["glucose_rmse"],
                "glucose_within_5mgdl_rate": result["glucose_agreement_metrics"]["within_5mgdl_rate"],
                "glucose_within_10mgdl_rate": result["glucose_agreement_metrics"]["within_10mgdl_rate"],
                "glucose_within_20mgdl_rate": result["glucose_agreement_metrics"]["within_20mgdl_rate"],
                "time_span_overlap_ratio": result["dataset_profile_metrics"]["overlap_ratio"],
                "cadence_delta_minutes": result["dataset_profile_metrics"]["median_cadence_delta_minutes"],
                "scalar_feature_score": result["scalar_cgm_feature_metrics"]["scalar_feature_score"],
                "scalar_feature_similarity": result["scalar_cgm_feature_metrics"]["scalar_feature_similarity"],
                "scalar_mean_glucose_delta": result["scalar_cgm_feature_metrics"]["feature_comparisons"].get("mean_glucose", {}).get("absolute_delta"),
                "scalar_time_in_range_delta": result["scalar_cgm_feature_metrics"]["feature_comparisons"].get("time_in_70_180_rate", {}).get("absolute_delta"),
                "scalar_gmi_delta": result["scalar_cgm_feature_metrics"]["feature_comparisons"].get("gmi", {}).get("absolute_delta"),
                "insight_score": result["insight_metrics"]["overall_score"],
                "insight_assessment": result["insight_metrics"]["assessment"],
                "robust_insight_score": result["robust_insight_metrics"]["overall_score"],
                "reference_comparison_scope": result["reference_scope"]["comparison_scope"],
                "reference_scope_note": result["reference_scope"]["note"],
                "benchmark_status": benchmark_status["status"],
                "benchmark_reason": benchmark_status["reason"],
                "reference_coverage_status": benchmark_status["reference_coverage_status"],
                "reference_parse_status": benchmark_status["reference_parse_status"],
            }
        )

    summary = pd.DataFrame(summary_rows)
    summary_dir = evaluation_root / "comparisons" / split
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / "summary.csv"
    summary.to_csv(summary_path, index=False)
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Awesome-CGM references and compare them to harmonized outputs.")
    parser.add_argument("--split", choices=["training", "testing"], required=True)
    parser.add_argument("--harmonized-root", type=Path, required=True, help="Directory containing per-dataset harmonized outputs.")
    parser.add_argument("--evaluation-root", type=Path, default=Path("harmony/evaluation"))
    parser.add_argument(
        "--reference-output-root",
        type=Path,
        default=None,
        help="Optional shared root for model-independent reference outputs.",
    )
    parser.add_argument(
        "--reuse-references",
        action="store_true",
        help="Reuse existing reference_combined.csv files when present.",
    )
    parser.add_argument(
        "--comparison-cache-root",
        type=Path,
        default=None,
        help="Optional root for cached prepared reference comparison data.",
    )
    parser.add_argument("--datasets", nargs="*", default=None)
    args = parser.parse_args()

    summary_path = evaluate_against_reference(
        harmonized_root=args.harmonized_root,
        split=args.split,
        evaluation_root=args.evaluation_root,
        dataset_names=args.datasets,
        reference_output_root=args.reference_output_root,
        reuse_references=args.reuse_references,
        comparison_cache_root=args.comparison_cache_root,
    )
    print(summary_path)


if __name__ == "__main__":
    main()
