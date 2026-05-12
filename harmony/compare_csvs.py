from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import pandas as pd

COMPARISON_CACHE_VERSION = 1
KEY_COLUMNS = ["Subject_ID", "Timestamp", "Glucose"]
SCALAR_CGM_FEATURE_TOLERANCES = {
    "mean_glucose": 10.0,
    "median_glucose": 10.0,
    "glucose_sd": 10.0,
    "glucose_iqr": 10.0,
    "coefficient_of_variation": 0.05,
    "gmi": 0.25,
    "time_below_54_rate": 0.02,
    "time_below_70_rate": 0.03,
    "time_in_70_180_rate": 0.05,
    "time_above_180_rate": 0.05,
    "time_above_250_rate": 0.03,
}
COLUMN_ALIASES = {
    "id": "Subject_ID",
    "subject": "Subject_ID",
    "subject_id": "Subject_ID",
    "time": "Timestamp",
    "timestamp": "Timestamp",
    "gl": "Glucose",
    "glucose": "Glucose",
}


def _safe_rate(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return float(2 * precision * recall / (precision + recall)) if precision and recall else 0.0


def _normalize_subject(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).str.strip()
    normalized = normalized.str.replace(r"\.0$", "", regex=True)
    return normalized


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _safe_percent(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return float(numerator / denominator)


def _isoformat_or_none(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat(sep=" ")


def _median_cadence_minutes(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    ordered = df.sort_values(["Subject_ID", "Timestamp"])
    deltas = ordered.groupby("Subject_ID")["Timestamp"].diff().dt.total_seconds().div(60)
    deltas = deltas[(deltas.notna()) & (deltas > 0)]
    if deltas.empty:
        return None
    return float(deltas.median())


def _detect_mixed_timestamp_formats(series: pd.Series) -> bool:
    cleaned = series.astype("string").str.strip().fillna("")
    categories = {
        "date_only": cleaned.str.fullmatch(r"\d{4}-\d{2}-\d{2}").fillna(False).any(),
        "datetime_seconds": cleaned.str.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}").fillna(False).any(),
        "datetime_fractional": cleaned.str.contains(r"\.\d+", regex=True, na=False).any(),
        "slash_datetime": cleaned.str.contains(r"/", regex=False, na=False).any(),
        "am_pm": cleaned.str.contains(r"\bAM\b|\bPM\b", regex=True, na=False).any(),
    }
    return sum(bool(value) for value in categories.values()) > 1


def load_standardized_csv_with_diagnostics(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    csv_path = Path(path)
    df = pd.read_csv(csv_path)
    rename_map = {
        column: COLUMN_ALIASES[column.lower()]
        for column in df.columns
        if column.lower() in COLUMN_ALIASES
    }
    df = df.rename(columns=rename_map)

    missing = [column for column in KEY_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"{csv_path} is missing required columns: {missing}")

    cleaned = df[KEY_COLUMNS].copy()
    cleaned["Subject_ID"] = _normalize_subject(cleaned["Subject_ID"])
    raw_timestamp = cleaned["Timestamp"].copy()
    parsed_timestamp = pd.to_datetime(raw_timestamp, errors="coerce", utc=True, format="mixed")
    cleaned["Timestamp"] = parsed_timestamp.dt.tz_convert(None).dt.floor("s")
    raw_glucose = cleaned["Glucose"].copy()
    cleaned["Glucose"] = pd.to_numeric(raw_glucose, errors="coerce").astype(float)

    diagnostics = {
        "rows_loaded": int(len(cleaned)),
        "timestamp_parse_success_rate": _safe_percent(int(parsed_timestamp.notna().sum()), int(len(parsed_timestamp))),
        "glucose_parse_success_rate": _safe_percent(int(cleaned["Glucose"].notna().sum()), int(len(cleaned))),
        "mixed_timestamp_formats_detected": _detect_mixed_timestamp_formats(raw_timestamp),
    }
    return cleaned, diagnostics


def load_standardized_csv(path: str | Path) -> pd.DataFrame:
    cleaned, _ = load_standardized_csv_with_diagnostics(path)
    return cleaned


def _prepare_for_comparison(df: pd.DataFrame, diagnostics: dict[str, Any] | None = None) -> tuple[pd.DataFrame, Dict[str, Any]]:
    summary: Dict[str, Any] = {
        "rows_loaded": int(len(df)),
        "duplicates_removed": int(df.duplicated().sum()),
    }
    if diagnostics:
        summary.update(diagnostics)
    deduped = df.drop_duplicates()

    missing_mask = deduped[KEY_COLUMNS].isna().any(axis=1)
    summary["rows_with_missing_keys_removed"] = int(missing_mask.sum())
    cleaned = deduped.loc[~missing_mask].copy()
    summary["rows_compared"] = int(len(cleaned))
    summary["unique_subjects"] = int(cleaned["Subject_ID"].nunique())

    if cleaned.empty:
        summary.update(
            {
                "timestamp_min": None,
                "timestamp_max": None,
                "subject_timestamp_pairs": 0,
                "subject_timestamp_collisions": 0,
                "glucose_mean": None,
                "glucose_median": None,
                "glucose_std": None,
                "median_cadence_minutes": None,
            }
        )
        return cleaned, summary

    subject_time_pairs = cleaned[["Subject_ID", "Timestamp"]].drop_duplicates()
    summary["timestamp_min"] = _isoformat_or_none(cleaned["Timestamp"].min())
    summary["timestamp_max"] = _isoformat_or_none(cleaned["Timestamp"].max())
    summary["subject_timestamp_pairs"] = int(len(subject_time_pairs))
    summary["subject_timestamp_collisions"] = int(len(cleaned) - len(subject_time_pairs))
    summary["glucose_mean"] = _optional_float(cleaned["Glucose"].mean())
    summary["glucose_median"] = _optional_float(cleaned["Glucose"].median())
    summary["glucose_std"] = _optional_float(cleaned["Glucose"].std())
    summary["median_cadence_minutes"] = _median_cadence_minutes(cleaned)
    return cleaned, summary


@dataclass(frozen=True)
class PreparedComparisonData:
    summary: dict[str, Any]
    pairs: pd.DataFrame

    @property
    def subjects(self) -> set[str]:
        if self.pairs.empty:
            return set()
        return set(self.pairs["Subject_ID"].unique())


def _build_subject_time_table(df: pd.DataFrame, label: str | None = None) -> pd.DataFrame:
    grouped = (
        df.groupby(["Subject_ID", "Timestamp"], as_index=False)
        .agg(
            glucose_value=("Glucose", "mean"),
            glucose_nunique=("Glucose", "nunique"),
        )
    )
    grouped["has_conflict"] = grouped["glucose_nunique"].gt(1).astype(int)
    grouped = grouped.drop(columns=["glucose_nunique"])
    if label:
        grouped = grouped.rename(
            columns={
                "glucose_value": f"glucose_value_{label}",
                "has_conflict": f"has_conflict_{label}",
            }
        )
    return grouped


def _source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _cache_key(path: Path) -> str:
    payload = json.dumps(
        {
            "version": COMPARISON_CACHE_VERSION,
            **_source_fingerprint(path),
        },
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _cache_paths(cache_dir: Path, path: Path) -> tuple[Path, Path]:
    key = _cache_key(path)
    return cache_dir / f"{key}.summary.json", cache_dir / f"{key}.pairs.parquet"


def prepare_comparison_data(
    path: str | Path,
    *,
    cache_dir: str | Path | None = None,
) -> PreparedComparisonData:
    csv_path = Path(path)
    if cache_dir is not None:
        cache_path = Path(cache_dir)
        summary_path, pairs_path = _cache_paths(cache_path, csv_path)
        if summary_path.exists() and pairs_path.exists():
            summary_payload = json.loads(summary_path.read_text())
            if (
                summary_payload.get("cache_version") == COMPARISON_CACHE_VERSION
                and summary_payload.get("source") == _source_fingerprint(csv_path)
            ):
                return PreparedComparisonData(
                    summary=summary_payload["summary"],
                    pairs=pd.read_parquet(pairs_path),
                )

    raw, diagnostics = load_standardized_csv_with_diagnostics(csv_path)
    cleaned, summary = _prepare_for_comparison(raw, diagnostics)
    pairs = _build_subject_time_table(cleaned)

    if cache_dir is not None:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        summary_path, pairs_path = _cache_paths(cache_path, csv_path)
        pairs.to_parquet(pairs_path, index=False)
        summary_path.write_text(
            json.dumps(
                {
                    "cache_version": COMPARISON_CACHE_VERSION,
                    "source": _source_fingerprint(csv_path),
                    "summary": summary,
                },
                indent=2,
            )
        )

    return PreparedComparisonData(summary=summary, pairs=pairs)


def _label_pairs(pairs: pd.DataFrame, label: str) -> pd.DataFrame:
    return pairs.rename(
        columns={
            "glucose_value": f"glucose_value_{label}",
            "has_conflict": f"has_conflict_{label}",
        }
    )


def _subject_pair_metrics(
    candidate_pairs: pd.DataFrame,
    reference_pairs: pd.DataFrame,
    matched_pairs: pd.DataFrame,
) -> dict[str, Any]:
    candidate_counts = (
        candidate_pairs.groupby("Subject_ID").size().rename("candidate_pairs")
        if not candidate_pairs.empty
        else pd.Series(dtype="int64", name="candidate_pairs")
    )
    reference_counts = (
        reference_pairs.groupby("Subject_ID").size().rename("reference_pairs")
        if not reference_pairs.empty
        else pd.Series(dtype="int64", name="reference_pairs")
    )
    matched_counts = (
        matched_pairs.groupby("Subject_ID").size().rename("matched_pairs")
        if not matched_pairs.empty
        else pd.Series(dtype="int64", name="matched_pairs")
    )

    by_subject = (
        pd.concat([candidate_counts, reference_counts, matched_counts], axis=1)
        .fillna(0)
        .reset_index()
    )
    if by_subject.empty:
        return {
            "subjects_evaluated": 0,
            "median_pair_precision": 0.0,
            "median_pair_recall": 0.0,
            "median_pair_f1": 0.0,
            "subjects_with_perfect_pair_recall": 0,
            "subjects_with_perfect_pair_precision": 0,
            "subjects_with_at_least_95pct_pair_recall": 0,
            "subjects_with_at_least_95pct_pair_precision": 0,
        }

    by_subject["pair_precision"] = 0.0
    candidate_mask = by_subject["candidate_pairs"].ne(0)
    by_subject.loc[candidate_mask, "pair_precision"] = (
        by_subject.loc[candidate_mask, "matched_pairs"]
        / by_subject.loc[candidate_mask, "candidate_pairs"]
    )
    by_subject["pair_recall"] = 0.0
    reference_mask = by_subject["reference_pairs"].ne(0)
    by_subject.loc[reference_mask, "pair_recall"] = (
        by_subject.loc[reference_mask, "matched_pairs"]
        / by_subject.loc[reference_mask, "reference_pairs"]
    )
    by_subject["pair_f1"] = 0.0
    f1_mask = by_subject["pair_precision"].ne(0) & by_subject["pair_recall"].ne(0)
    by_subject.loc[f1_mask, "pair_f1"] = (
        2
        * by_subject.loc[f1_mask, "pair_precision"]
        * by_subject.loc[f1_mask, "pair_recall"]
        / (by_subject.loc[f1_mask, "pair_precision"] + by_subject.loc[f1_mask, "pair_recall"])
    )

    return {
        "subjects_evaluated": int(len(by_subject)),
        "median_pair_precision": float(by_subject["pair_precision"].median()),
        "median_pair_recall": float(by_subject["pair_recall"].median()),
        "median_pair_f1": float(by_subject["pair_f1"].median()),
        "subjects_with_perfect_pair_recall": int((by_subject["pair_recall"] >= 0.999999).sum()),
        "subjects_with_perfect_pair_precision": int((by_subject["pair_precision"] >= 0.999999).sum()),
        "subjects_with_at_least_95pct_pair_recall": int((by_subject["pair_recall"] >= 0.95).sum()),
        "subjects_with_at_least_95pct_pair_precision": int((by_subject["pair_precision"] >= 0.95).sum()),
    }


def _time_span_metrics(candidate_summary: Dict[str, Any], reference_summary: Dict[str, Any]) -> dict[str, Any]:
    candidate_min = candidate_summary.get("timestamp_min")
    candidate_max = candidate_summary.get("timestamp_max")
    reference_min = reference_summary.get("timestamp_min")
    reference_max = reference_summary.get("timestamp_max")

    if not all([candidate_min, candidate_max, reference_min, reference_max]):
        return {
            "candidate_span_hours": None,
            "reference_span_hours": None,
            "overlap_hours": None,
            "overlap_ratio": None,
            "median_cadence_delta_minutes": None,
            "glucose_mean_delta": None,
            "glucose_std_delta": None,
        }

    candidate_min_ts = pd.Timestamp(candidate_min)
    candidate_max_ts = pd.Timestamp(candidate_max)
    reference_min_ts = pd.Timestamp(reference_min)
    reference_max_ts = pd.Timestamp(reference_max)

    candidate_span_seconds = max((candidate_max_ts - candidate_min_ts).total_seconds(), 0.0)
    reference_span_seconds = max((reference_max_ts - reference_min_ts).total_seconds(), 0.0)
    overlap_seconds = max(
        min(candidate_max_ts, reference_max_ts) - max(candidate_min_ts, reference_min_ts),
        pd.Timedelta(0),
    ).total_seconds()
    union_seconds = max(candidate_max_ts, reference_max_ts) - min(candidate_min_ts, reference_min_ts)
    union_seconds_value = max(union_seconds.total_seconds(), 0.0)

    candidate_cadence = candidate_summary.get("median_cadence_minutes")
    reference_cadence = reference_summary.get("median_cadence_minutes")
    cadence_delta = None
    if candidate_cadence is not None and reference_cadence is not None:
        cadence_delta = float(abs(candidate_cadence - reference_cadence))

    glucose_mean_delta = None
    if candidate_summary.get("glucose_mean") is not None and reference_summary.get("glucose_mean") is not None:
        glucose_mean_delta = float(abs(candidate_summary["glucose_mean"] - reference_summary["glucose_mean"]))

    glucose_std_delta = None
    if candidate_summary.get("glucose_std") is not None and reference_summary.get("glucose_std") is not None:
        glucose_std_delta = float(abs(candidate_summary["glucose_std"] - reference_summary["glucose_std"]))

    return {
        "candidate_span_hours": candidate_span_seconds / 3600.0,
        "reference_span_hours": reference_span_seconds / 3600.0,
        "overlap_hours": overlap_seconds / 3600.0,
        "overlap_ratio": _safe_rate(overlap_seconds, union_seconds_value) if union_seconds_value else 1.0,
        "median_cadence_delta_minutes": cadence_delta,
        "glucose_mean_delta": glucose_mean_delta,
        "glucose_std_delta": glucose_std_delta,
    }


def _scalar_cgm_features(pairs: pd.DataFrame) -> dict[str, Any]:
    empty_features = {
        "observation_count": 0,
        "subject_count": 0,
        "mean_glucose": None,
        "median_glucose": None,
        "glucose_sd": None,
        "glucose_iqr": None,
        "min_glucose": None,
        "max_glucose": None,
        "coefficient_of_variation": None,
        "gmi": None,
        "time_below_54_rate": None,
        "time_below_70_rate": None,
        "time_in_70_180_rate": None,
        "time_above_180_rate": None,
        "time_above_250_rate": None,
    }
    if pairs.empty:
        return empty_features

    glucose = pd.to_numeric(pairs["glucose_value"], errors="coerce").dropna()
    if glucose.empty:
        return {
            **empty_features,
            "subject_count": int(pairs["Subject_ID"].nunique()),
        }

    mean_glucose = float(glucose.mean())
    glucose_sd = float(glucose.std(ddof=0))
    q25 = float(glucose.quantile(0.25))
    q75 = float(glucose.quantile(0.75))
    coefficient_of_variation = None
    if mean_glucose:
        coefficient_of_variation = float(glucose_sd / mean_glucose)

    return {
        "observation_count": int(len(glucose)),
        "subject_count": int(pairs["Subject_ID"].nunique()),
        "mean_glucose": mean_glucose,
        "median_glucose": float(glucose.median()),
        "glucose_sd": glucose_sd,
        "glucose_iqr": q75 - q25,
        "min_glucose": float(glucose.min()),
        "max_glucose": float(glucose.max()),
        "coefficient_of_variation": coefficient_of_variation,
        "gmi": float(3.31 + (0.02392 * mean_glucose)),
        "time_below_54_rate": float((glucose < 54).mean()),
        "time_below_70_rate": float((glucose < 70).mean()),
        "time_in_70_180_rate": float(((glucose >= 70) & (glucose <= 180)).mean()),
        "time_above_180_rate": float((glucose > 180).mean()),
        "time_above_250_rate": float((glucose > 250).mean()),
    }


def _compare_scalar_features(
    candidate_features: dict[str, Any],
    reference_features: dict[str, Any],
) -> dict[str, Any]:
    feature_comparisons: dict[str, dict[str, Any]] = {}
    within_tolerance_values: list[bool] = []
    similarity_scores: list[float] = []

    for feature_name, tolerance in SCALAR_CGM_FEATURE_TOLERANCES.items():
        candidate_value = candidate_features.get(feature_name)
        reference_value = reference_features.get(feature_name)
        if candidate_value is None or reference_value is None:
            continue

        absolute_delta = float(abs(float(candidate_value) - float(reference_value)))
        relative_delta = None
        if float(reference_value) != 0.0:
            relative_delta = float(absolute_delta / abs(float(reference_value)))
        within_tolerance = absolute_delta <= tolerance
        within_tolerance_values.append(within_tolerance)
        similarity_scores.append(max(0.0, 1.0 - (absolute_delta / tolerance)))
        feature_comparisons[feature_name] = {
            "candidate": float(candidate_value),
            "reference": float(reference_value),
            "absolute_delta": absolute_delta,
            "relative_delta": relative_delta,
            "tolerance": tolerance,
            "within_tolerance": within_tolerance,
        }

    observation_delta_rate = None
    reference_observations = reference_features.get("observation_count") or 0
    if reference_observations:
        observation_delta_rate = float(
            abs((candidate_features.get("observation_count") or 0) - reference_observations)
            / reference_observations
        )

    return {
        "candidate_features": candidate_features,
        "reference_features": reference_features,
        "feature_comparisons": feature_comparisons,
        "features_compared": int(len(feature_comparisons)),
        "scalar_feature_score": float(sum(within_tolerance_values) / len(within_tolerance_values))
        if within_tolerance_values
        else None,
        "scalar_feature_similarity": float(sum(similarity_scores) / len(similarity_scores))
        if similarity_scores
        else None,
        "observation_count_delta_rate": observation_delta_rate,
    }


def _per_subject_scalar_features(pairs: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty:
        return pd.DataFrame(columns=["Subject_ID", *SCALAR_CGM_FEATURE_TOLERANCES.keys()])

    records: list[dict[str, Any]] = []
    for subject_id, subject_pairs in pairs.groupby("Subject_ID", sort=False):
        features = _scalar_cgm_features(subject_pairs)
        records.append({"Subject_ID": subject_id, **features})
    return pd.DataFrame(records)


def _compare_per_subject_scalar_features(
    candidate_pairs: pd.DataFrame,
    reference_pairs: pd.DataFrame,
) -> dict[str, Any]:
    candidate_features = _per_subject_scalar_features(candidate_pairs)
    reference_features = _per_subject_scalar_features(reference_pairs)
    if candidate_features.empty or reference_features.empty:
        return {
            "subjects_compared": 0,
            "feature_summaries": {},
            "per_subject_scalar_feature_score": None,
        }

    merged = candidate_features.merge(
        reference_features,
        how="inner",
        on="Subject_ID",
        suffixes=("_candidate", "_reference"),
    )
    if merged.empty:
        return {
            "subjects_compared": 0,
            "feature_summaries": {},
            "per_subject_scalar_feature_score": None,
        }

    feature_summaries: dict[str, dict[str, Any]] = {}
    within_rates: list[float] = []
    for feature_name, tolerance in SCALAR_CGM_FEATURE_TOLERANCES.items():
        candidate_column = f"{feature_name}_candidate"
        reference_column = f"{feature_name}_reference"
        if candidate_column not in merged.columns or reference_column not in merged.columns:
            continue
        deltas = (merged[candidate_column] - merged[reference_column]).abs().dropna()
        if deltas.empty:
            continue
        within_rate = float((deltas <= tolerance).mean())
        within_rates.append(within_rate)
        feature_summaries[feature_name] = {
            "mean_abs_delta": float(deltas.mean()),
            "median_abs_delta": float(deltas.median()),
            "max_abs_delta": float(deltas.max()),
            "within_tolerance_rate": within_rate,
            "tolerance": tolerance,
        }

    return {
        "subjects_compared": int(len(merged)),
        "feature_summaries": feature_summaries,
        "per_subject_scalar_feature_score": float(sum(within_rates) / len(within_rates))
        if within_rates
        else None,
    }


def _weighted_score(components: dict[str, tuple[float | None, float]]) -> tuple[float, dict[str, float]]:
    available = {
        name: (score, weight)
        for name, (score, weight) in components.items()
        if score is not None
    }
    total_weight = sum(weight for _, weight in available.values())
    if not total_weight:
        return 0.0, {}
    normalized_weights = {
        name: float(weight / total_weight)
        for name, (_, weight) in available.items()
    }
    score = sum(float(score) * normalized_weights[name] for name, (score, _) in available.items())
    return float(score), normalized_weights


def compare_csvs(
    candidate_file: str | Path,
    reference_file: str | Path,
    *,
    label_candidate: str = "candidate",
    label_reference: str = "reference",
    reference_cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    candidate_prepared = prepare_comparison_data(candidate_file)
    reference_prepared = prepare_comparison_data(reference_file, cache_dir=reference_cache_dir)

    candidate_summary = candidate_prepared.summary
    reference_summary = reference_prepared.summary
    candidate_subjects = candidate_prepared.subjects
    reference_subjects = reference_prepared.subjects
    shared_subjects = sorted(candidate_subjects & reference_subjects)

    subject_precision = _safe_rate(len(shared_subjects), len(candidate_subjects))
    subject_recall = _safe_rate(len(shared_subjects), len(reference_subjects))
    subject_f1 = _f1(subject_precision, subject_recall)

    candidate_pairs = _label_pairs(candidate_prepared.pairs, label_candidate)
    reference_pairs = _label_pairs(reference_prepared.pairs, label_reference)
    matched_pairs = candidate_pairs.merge(
        reference_pairs,
        how="inner",
        on=["Subject_ID", "Timestamp"],
    )

    matched_pair_count = int(len(matched_pairs))
    candidate_pair_count = int(len(candidate_pairs))
    reference_pair_count = int(len(reference_pairs))
    temporal_precision = _safe_rate(matched_pair_count, candidate_pair_count)
    temporal_recall = _safe_rate(matched_pair_count, reference_pair_count)
    temporal_f1 = _f1(temporal_precision, temporal_recall)

    glucose_mae = None
    glucose_rmse = None
    glucose_median_abs_error = None
    glucose_bias = None
    glucose_max_abs = None
    exact_glucose_rate = None
    within_5_rate = None
    within_10_rate = None
    within_20_rate = None
    candidate_conflict_rate = None
    reference_conflict_rate = None
    if matched_pair_count:
        signed_diff = (
            matched_pairs[f"glucose_value_{label_candidate}"] - matched_pairs[f"glucose_value_{label_reference}"]
        )
        abs_diff = signed_diff.abs()
        glucose_mae = float(abs_diff.mean())
        glucose_rmse = float((signed_diff.pow(2).mean()) ** 0.5)
        glucose_median_abs_error = float(abs_diff.median())
        glucose_bias = float(signed_diff.mean())
        glucose_max_abs = float(abs_diff.max())
        exact_glucose_rate = float((abs_diff <= 1e-6).mean())
        within_5_rate = float((abs_diff <= 5).mean())
        within_10_rate = float((abs_diff <= 10).mean())
        within_20_rate = float((abs_diff <= 20).mean())
        candidate_conflict_rate = float(matched_pairs[f"has_conflict_{label_candidate}"].mean())
        reference_conflict_rate = float(matched_pairs[f"has_conflict_{label_reference}"].mean())

    exact_match_count = 0
    if matched_pair_count and exact_glucose_rate is not None:
        exact_match_count = int(
            (
                (
                    matched_pairs[f"glucose_value_{label_candidate}"]
                    - matched_pairs[f"glucose_value_{label_reference}"]
                ).abs() <= 1e-6
            ).sum()
        )
    strict_precision = _safe_rate(exact_match_count, candidate_summary["rows_compared"])
    strict_recall = _safe_rate(exact_match_count, reference_summary["rows_compared"])
    strict_f1 = _f1(strict_precision, strict_recall)

    glucose_agreement_score = 0.0
    if within_5_rate is not None and within_10_rate is not None and within_20_rate is not None:
        glucose_agreement_score = float(
            (0.5 * within_5_rate) + (0.3 * within_10_rate) + (0.2 * within_20_rate)
        )
    insight_score = float((0.2 * subject_f1) + (0.5 * temporal_f1) + (0.3 * glucose_agreement_score))

    if (
        subject_f1 >= 0.999999
        and temporal_f1 >= 0.999
        and (within_10_rate or 0.0) >= 0.999
    ):
        assessment = "near_exact"
    elif subject_f1 >= 0.99 and temporal_f1 >= 0.99 and (within_10_rate or 0.0) >= 0.99:
        assessment = "high_quality"
    elif subject_f1 >= 0.95 and temporal_f1 >= 0.95 and (within_20_rate or 0.0) >= 0.95:
        assessment = "usable"
    else:
        assessment = "needs_review"

    subject_pair_metrics = _subject_pair_metrics(candidate_pairs, reference_pairs, matched_pairs)
    time_span_metrics = _time_span_metrics(candidate_summary, reference_summary)
    candidate_scalar_features = _scalar_cgm_features(candidate_prepared.pairs)
    reference_scalar_features = _scalar_cgm_features(reference_prepared.pairs)
    scalar_feature_metrics = _compare_scalar_features(
        candidate_scalar_features,
        reference_scalar_features,
    )
    per_subject_scalar_feature_metrics = _compare_per_subject_scalar_features(
        candidate_prepared.pairs,
        reference_prepared.pairs,
    )
    scalar_score = scalar_feature_metrics["scalar_feature_score"]
    robust_score, robust_weights = _weighted_score(
        {
            "subject_f1": (subject_f1, 0.15),
            "temporal_f1": (temporal_f1, 0.35),
            "glucose_agreement_score": (glucose_agreement_score, 0.25),
            "scalar_feature_score": (scalar_score, 0.25),
        }
    )

    result = {
        "candidate_file": str(candidate_file),
        "reference_file": str(reference_file),
        "labels": {
            "candidate": label_candidate,
            "reference": label_reference,
        },
        "candidate_summary": candidate_summary,
        "reference_summary": reference_summary,
        "subject_metrics": {
            "shared_subjects": len(shared_subjects),
            "candidate_subjects": len(candidate_subjects),
            "reference_subjects": len(reference_subjects),
            "precision": subject_precision,
            "recall": subject_recall,
            "f1": subject_f1,
            "subjects_only_in_candidate": sorted(candidate_subjects - reference_subjects),
            "subjects_only_in_reference": sorted(reference_subjects - candidate_subjects),
        },
        "temporal_alignment_metrics": {
            "matched_pairs": matched_pair_count,
            "candidate_pairs": candidate_pair_count,
            "reference_pairs": reference_pair_count,
            "precision": temporal_precision,
            "recall": temporal_recall,
            "f1": temporal_f1,
            **subject_pair_metrics,
        },
        "glucose_agreement_metrics": {
            "aligned_pairs": matched_pair_count,
            "glucose_mae": glucose_mae,
            "glucose_rmse": glucose_rmse,
            "glucose_median_abs_error": glucose_median_abs_error,
            "glucose_bias": glucose_bias,
            "glucose_max_abs_error": glucose_max_abs,
            "exact_glucose_rate": exact_glucose_rate,
            "within_5mgdl_rate": within_5_rate,
            "within_10mgdl_rate": within_10_rate,
            "within_20mgdl_rate": within_20_rate,
            "candidate_conflict_rate": candidate_conflict_rate,
            "reference_conflict_rate": reference_conflict_rate,
        },
        "dataset_profile_metrics": time_span_metrics,
        "scalar_cgm_feature_metrics": scalar_feature_metrics,
        "per_subject_scalar_cgm_feature_metrics": per_subject_scalar_feature_metrics,
        "insight_metrics": {
            "subject_f1": subject_f1,
            "temporal_f1": temporal_f1,
            "glucose_agreement_score": glucose_agreement_score,
            "overall_score": insight_score,
            "assessment": assessment,
            "weights": {
                "subject_f1": 0.2,
                "temporal_f1": 0.5,
                "glucose_agreement_score": 0.3,
            },
        },
        "robust_insight_metrics": {
            "subject_f1": subject_f1,
            "temporal_f1": temporal_f1,
            "glucose_agreement_score": glucose_agreement_score,
            "scalar_feature_score": scalar_score,
            "overall_score": robust_score,
            "weights": robust_weights,
        },
        "row_metrics": {
            "exact_match_rows": exact_match_count,
            "candidate_rows": int(candidate_summary["rows_compared"]),
            "reference_rows": int(reference_summary["rows_compared"]),
            "precision": strict_precision,
            "recall": strict_recall,
            "f1": strict_f1,
        },
        "aligned_subject_time_metrics": {
            "matched_pairs": matched_pair_count,
            "glucose_mae": glucose_mae,
            "glucose_max_abs_error": glucose_max_abs,
            "exact_glucose_rate": exact_glucose_rate,
        },
    }
    return result


def render_report(result: dict[str, Any]) -> str:
    subject_metrics = result["subject_metrics"]
    temporal_metrics = result["temporal_alignment_metrics"]
    glucose_metrics = result["glucose_agreement_metrics"]
    profile_metrics = result["dataset_profile_metrics"]
    scalar_metrics = result.get("scalar_cgm_feature_metrics", {})
    robust_metrics = result.get("robust_insight_metrics", {})
    insight_metrics = result["insight_metrics"]
    row_metrics = result["row_metrics"]
    candidate_summary = result["candidate_summary"]
    reference_summary = result["reference_summary"]
    reference_scope = result.get("reference_scope", {})
    scalar_comparisons = scalar_metrics.get("feature_comparisons", {})

    lines = [
        f"Candidate: {result['candidate_file']}",
        f"Reference: {result['reference_file']}",
        f"Reference comparison scope: {reference_scope.get('comparison_scope', 'complete_reference_assumed')}",
        f"Reference note: {reference_scope.get('note')}",
        "",
        "Candidate summary:",
        f"  rows loaded: {candidate_summary['rows_loaded']}",
        f"  duplicates removed: {candidate_summary['duplicates_removed']}",
        f"  rows with missing keys removed: {candidate_summary['rows_with_missing_keys_removed']}",
        f"  rows compared: {candidate_summary['rows_compared']}",
        f"  unique subjects: {candidate_summary['unique_subjects']}",
        f"  subject+timestamp pairs: {candidate_summary['subject_timestamp_pairs']}",
        f"  subject+timestamp collisions: {candidate_summary['subject_timestamp_collisions']}",
        f"  median cadence minutes: {candidate_summary['median_cadence_minutes']}",
        f"  timestamp parse success rate: {candidate_summary.get('timestamp_parse_success_rate')}",
        f"  glucose parse success rate: {candidate_summary.get('glucose_parse_success_rate')}",
        f"  mixed timestamp formats detected: {candidate_summary.get('mixed_timestamp_formats_detected')}",
        "",
        "Reference summary:",
        f"  rows loaded: {reference_summary['rows_loaded']}",
        f"  duplicates removed: {reference_summary['duplicates_removed']}",
        f"  rows with missing keys removed: {reference_summary['rows_with_missing_keys_removed']}",
        f"  rows compared: {reference_summary['rows_compared']}",
        f"  unique subjects: {reference_summary['unique_subjects']}",
        f"  subject+timestamp pairs: {reference_summary['subject_timestamp_pairs']}",
        f"  subject+timestamp collisions: {reference_summary['subject_timestamp_collisions']}",
        f"  median cadence minutes: {reference_summary['median_cadence_minutes']}",
        f"  timestamp parse success rate: {reference_summary.get('timestamp_parse_success_rate')}",
        f"  glucose parse success rate: {reference_summary.get('glucose_parse_success_rate')}",
        f"  mixed timestamp formats detected: {reference_summary.get('mixed_timestamp_formats_detected')}",
        "",
        "INSIGHT evaluation framework:",
        f"  overall score: {insight_metrics['overall_score']:.4f}",
        f"  assessment: {insight_metrics['assessment']}",
        f"  robust score with scalar features: {robust_metrics.get('overall_score')}",
        "",
        "Subject coverage:",
        f"  shared subjects: {subject_metrics['shared_subjects']}",
        f"  precision: {subject_metrics['precision']:.4f}",
        f"  recall: {subject_metrics['recall']:.4f}",
        f"  f1: {subject_metrics['f1']:.4f}",
        "",
        "Temporal alignment:",
        f"  matched subject+timestamp pairs: {temporal_metrics['matched_pairs']}",
        f"  precision: {temporal_metrics['precision']:.4f}",
        f"  recall: {temporal_metrics['recall']:.4f}",
        f"  f1: {temporal_metrics['f1']:.4f}",
        f"  median per-subject recall: {temporal_metrics['median_pair_recall']:.4f}",
        "",
        "Glucose agreement on aligned pairs:",
        f"  MAE: {glucose_metrics['glucose_mae']}",
        f"  RMSE: {glucose_metrics['glucose_rmse']}",
        f"  within 5 mg/dL: {glucose_metrics['within_5mgdl_rate']}",
        f"  within 10 mg/dL: {glucose_metrics['within_10mgdl_rate']}",
        f"  within 20 mg/dL: {glucose_metrics['within_20mgdl_rate']}",
        "",
        "Dataset profile agreement:",
        f"  time-span overlap ratio: {profile_metrics['overlap_ratio']}",
        f"  cadence delta (minutes): {profile_metrics['median_cadence_delta_minutes']}",
        f"  glucose mean delta: {profile_metrics['glucose_mean_delta']}",
        "",
        "Scalar CGM feature agreement:",
        f"  scalar feature score: {scalar_metrics.get('scalar_feature_score')}",
        f"  scalar feature similarity: {scalar_metrics.get('scalar_feature_similarity')}",
        f"  mean glucose delta: {scalar_comparisons.get('mean_glucose', {}).get('absolute_delta')}",
        f"  time in 70-180 mg/dL delta: {scalar_comparisons.get('time_in_70_180_rate', {}).get('absolute_delta')}",
        f"  GMI delta: {scalar_comparisons.get('gmi', {}).get('absolute_delta')}",
        "",
        "Strict row overlap (legacy diagnostic):",
        f"  exact matches: {row_metrics['exact_match_rows']}",
        f"  precision: {row_metrics['precision']:.4f}",
        f"  recall: {row_metrics['recall']:.4f}",
        f"  f1: {row_metrics['f1']:.4f}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two CGM CSV files.")
    parser.add_argument("candidate_file", help="Pipeline CSV file")
    parser.add_argument("reference_file", help="Reference CSV file")
    parser.add_argument("--json-out", type=Path, help="Optional JSON output path")
    parser.add_argument("--report-out", type=Path, help="Optional text report output path")
    parser.add_argument(
        "--reference-cache-dir",
        type=Path,
        default=None,
        help="Optional directory for cached prepared reference comparison data.",
    )
    args = parser.parse_args()

    result = compare_csvs(
        args.candidate_file,
        args.reference_file,
        reference_cache_dir=args.reference_cache_dir,
    )
    report = render_report(result)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2))

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report)

    print(report)


if __name__ == "__main__":
    main()
