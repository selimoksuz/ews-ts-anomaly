from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def default_params() -> dict[str, Any]:
    return {
        # Sample generation
        "random_seed": 42,
        "n_customers": 650,
        "sample_start_month": "2023-01",
        "sample_end_month": "2026-04",
        "min_customer_months": 4,
        "max_customer_months": 18,
        "average_customer_months": 10,
        "monthly_inflation_mean": 0.026,
        "monthly_inflation_std": 0.010,
        "idiosyncratic_noise_std": 0.15,
        "missing_invoice_row_share": 0.025,
        "missing_invoice_value_share": 0.010,
        "zero_bill_share": 0.008,
        "negative_invoice_share": 0.006,
        "duplicate_invoice_share": 0.060,
        "structural_under_peer_customer_share": 0.040,
        "single_month_low_anomaly_share": 0.050,
        "single_month_high_anomaly_share": 0.035,
        # Data contract / eligibility
        "score_start_month": "2025-01",
        "min_portfolio_history_months_to_score": 12,
        "min_customer_prior_bills_to_score": 2,
        "min_months_for_seasonality": 12,
        "recommended_months_for_seasonality": 24,
        "min_customer_trend_points": 5,
        "min_customer_same_month_obs": 2,
        # Peer support constraints
        "min_peer_customers": 12,
        "min_peer_history_rows": 50,
        "min_month_of_year_rows": 8,
        "min_calendar_mom_customers": 8,
        "strong_peer_history_rows": 250,
        "strong_month_of_year_rows": 50,
        "strong_calendar_mom_customers": 50,
        # Shrinkage
        "base_shrinkage_k": 120,
        "seasonality_shrinkage_k": 40,
        "calendar_mom_shrinkage_k": 35,
        "trend_shrinkage_k": 12,
        "customer_trend_shrinkage_k": 8,
        "customer_seasonality_shrinkage_k": 3,
        # Robust scoring
        "winsor_lower_q": 0.01,
        "winsor_upper_q": 0.99,
        "target_history_months": 12,
        "max_customer_weight": 0.70,
        "z_score_cap": 3.0,
        "min_level_scale": 0.12,
        "min_trend_scale": 0.08,
        "min_relative_scale": 0.12,
        # Score component weights
        "base_self_weight": 0.15,
        "variable_self_weight": 0.25,
        "base_trend_weight": 0.35,
        "trend_weight_decay": 0.10,
        "relative_gap_weight": 0.10,
        # Decision calibration
        "watchlist_threshold_floor": 60,
        "high_anomaly_threshold_floor": 80,
        "watchlist_top_rate": 0.030,
        "high_anomaly_top_rate": 0.0075,
        "reason_score_threshold": 60,
        "low_confidence_threshold": 45,
    }


ELECTRICITY_REAL_PRICE_SHOCKS = {
    "2024-07": 0.08,
    "2025-03": 0.10,
    "2025-09": 0.14,
    "2026-02": 0.08,
}


PEER_LEVELS = [
    ["sector", "customer_segment", "region", "turnover_bucket"],
    ["sector", "customer_segment", "turnover_bucket"],
    ["sector", "region", "turnover_bucket"],
    ["sector", "turnover_bucket"],
    ["sector", "customer_segment"],
    ["sector"],
    [],
]

PEER_LEVEL_NAMES = {
    1: "sector+segment+region+turnover_bucket",
    2: "sector+segment+turnover_bucket",
    3: "sector+region+turnover_bucket",
    4: "sector+turnover_bucket",
    5: "sector+segment",
    6: "sector",
    7: "global",
}


def period_range(start: str, end: str) -> pd.PeriodIndex:
    return pd.period_range(start=start, end=end, freq="M")


def to_period(value: Any) -> pd.Period:
    return pd.Period(value, freq="M")


def month_ordinal(period_value: Any) -> int:
    p = pd.Period(period_value, freq="M")
    return int(p.year * 12 + p.month)


def shrink_value(detail: float, broad: float, n: float, k: float) -> tuple[float, float]:
    if not np.isfinite(detail):
        return float(broad) if np.isfinite(broad) else 0.0, 0.0
    if not np.isfinite(broad):
        return float(detail), 1.0
    reliability = float(n / (n + k)) if (n + k) > 0 else 0.0
    reliability = min(max(reliability, 0.0), 1.0)
    return float(reliability * detail + (1.0 - reliability) * broad), reliability


def robust_mad(values: Any, min_scale: float = 0.0) -> float:
    s = pd.Series(values).dropna().astype(float)
    if len(s) == 0:
        return float(min_scale)
    med = s.median()
    mad = np.median(np.abs(s - med))
    scale = 1.4826 * mad
    if not np.isfinite(scale):
        return float(min_scale)
    return max(float(scale), float(min_scale))


def winsorized(values: Any, lower_q: float, upper_q: float) -> pd.Series:
    s = pd.Series(values).dropna().astype(float)
    if len(s) < 5:
        return s
    lo, hi = s.quantile([lower_q, upper_q])
    return s.clip(lo, hi)


def safe_median(values: Any, default: float = 0.0) -> float:
    s = pd.Series(values).dropna().astype(float)
    if len(s) == 0:
        return float(default)
    return float(s.median())


def robust_line(x: Any, y: Any, min_points: int = 3) -> tuple[float, float, int]:
    df = pd.DataFrame({"x": x, "y": y}).replace([np.inf, -np.inf], np.nan).dropna()
    df = df.drop_duplicates("x").sort_values("x")
    n = len(df)
    if n < min_points:
        intercept = safe_median(df["y"], 0.0) if n else 0.0
        return float(intercept), 0.0, n
    xs = df["x"].to_numpy(dtype=float)
    ys = df["y"].to_numpy(dtype=float)
    slopes = []
    for i in range(n):
        dx = xs[i + 1 :] - xs[i]
        valid = dx != 0
        if valid.any():
            slopes.extend(((ys[i + 1 :][valid] - ys[i]) / dx[valid]).tolist())
    slope = float(np.median(slopes)) if slopes else 0.0
    intercept = float(np.median(ys - slope * xs))
    return intercept, slope, n


def score_from_deviation(deviation: float, scale: float, z_cap: float) -> tuple[float, float]:
    if not np.isfinite(deviation) or not np.isfinite(scale) or scale <= 0:
        return np.nan, np.nan
    z = float(deviation / scale)
    score = min(100.0, abs(z) / z_cap * 100.0)
    return z, score


def group_mask(df: pd.DataFrame, row: pd.Series, cols: list[str]) -> pd.Series:
    if not cols:
        return pd.Series(True, index=df.index)
    mask = pd.Series(True, index=df.index)
    for col in cols:
        mask &= df[col].eq(row[col])
    return mask


def build_parameter_sheet(params: dict[str, Any]) -> pd.DataFrame:
    rows = [
        ("data_contract", "min_portfolio_history_months_to_score", params["min_portfolio_history_months_to_score"], "Skor uretmeden once portfoyde kac aylik tarih olmasi gerektigini belirler.", "Daha konservatif production icin 24 yapin.", "E01,M05"),
        ("data_contract", "min_customer_prior_bills_to_score", params["min_customer_prior_bills_to_score"], "Musteriyi skorlamak icin skor ayindan once minimum valid fatura sayisi.", "4 aylik musteri skorlanacaksa 1-2 arasi makuldur.", "E01,M05"),
        ("data_contract", "missing/zero/negative rules", "missing!=0; zero valid; negative excluded", "Missing veri yoktur, 0 gercek sifir fatura olabilir, negatif iade/mahsup olabilir.", "Bu kural model tercihi degil veri kontrati oldugu icin sabit tutulmali.", "D01,D02"),
        ("peer_support", "min_peer_customers", params["min_peer_customers"], "Peer level secimi icin minimum distinct customer.", "Cok fallback varsa dusurulur, noisy peer varsa artirilir.", "P02"),
        ("peer_support", "min_peer_history_rows", params["min_peer_history_rows"], "Peer base/scale icin minimum customer-month history.", "Kisa tarihli portfoyde dusurulebilir.", "P02"),
        ("peer_support", "min_month_of_year_rows", params["min_month_of_year_rows"], "Month-of-year mevsimsellik icin minimum ayni ay gozlemi.", "Daha guvenli seasonality icin artirilir.", "P03"),
        ("peer_support", "min_calendar_mom_customers", params["min_calendar_mom_customers"], "Skorlanan calendar_month icin t ve t-1 faturasi olan minimum peer musteri.", "Calendar shock stabil degilse artirilir.", "P04"),
        ("shrinkage", "seasonality_shrinkage_k", params["seasonality_shrinkage_k"], "Az month-of-year gozleminde detayli peer seasonality'yi genis peer seviyesine yaklastirir.", "Buyuk k daha fazla shrinkage demektir.", "P03"),
        ("shrinkage", "calendar_mom_shrinkage_k", params["calendar_mom_shrinkage_k"], "Az MoM musterisinde calendar shock'u genis peer seviyesine yaklastirir.", "Zam aylarinda noisy score varsa artirilir.", "P04"),
        ("shrinkage", "customer_trend_shrinkage_k", params["customer_trend_shrinkage_k"], "Kisa musteri gecmisinde customer trend'i peer trend'e yaklastirir.", "Musteri trendine daha zor guvenmek icin artirilir.", "C01"),
        ("score", "z_score_cap", params["z_score_cap"], "Robust z-score kac seviyede 100 skora ulassin.", "Daha hassas skor icin dusurulur.", "S01"),
        ("score", "target_history_months", params["target_history_months"], "Self-history guveni icin referans ay sayisi.", "Daha uzun tarih varsa 18/24 denenebilir.", "S01"),
        ("decision", "watchlist/high anomaly thresholds", f'{params["watchlist_threshold_floor"]}/{params["high_anomaly_threshold_floor"]}', "Rule layer icin minimum skor tabani.", "Operasyonel kapasiteye gore quantile esikleriyle birlikte kalibre edilir.", "R01"),
        ("decision", "watchlist/high top rate", f'{params["watchlist_top_rate"]}/{params["high_anomaly_top_rate"]}', "Target yokken alarm hacmini skor dagilimina gore kalibre eder.", "Business aylik inceleme kapasitesine gore degistirilir.", "R01"),
    ]
    return pd.DataFrame(rows, columns=["section", "parameter", "value", "why", "how_to_change", "used_in_cell"])


def build_sample_data(params: dict[str, Any]) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(params["random_seed"])
    sectors = pd.DataFrame(
        [
            {"sector": "ICE_CREAM_MFG", "sector_label": "Dondurma imalati", "energy_intensity": 1.15, "base_turnover_multiple": 9.0},
            {"sector": "COLD_STORAGE", "sector_label": "Soguk hava depolama", "energy_intensity": 1.35, "base_turnover_multiple": 7.5},
            {"sector": "HOTEL_TOURISM", "sector_label": "Otel ve turizm", "energy_intensity": 1.05, "base_turnover_multiple": 10.0},
            {"sector": "TEXTILE", "sector_label": "Tekstil uretimi", "energy_intensity": 0.95, "base_turnover_multiple": 12.0},
            {"sector": "RETAIL_FOOD", "sector_label": "Gida perakende", "energy_intensity": 0.85, "base_turnover_multiple": 14.0},
            {"sector": "OFFICE_SERVICES", "sector_label": "Ofis hizmetleri", "energy_intensity": 0.55, "base_turnover_multiple": 18.0},
        ]
    )
    sector_probs = np.array([0.14, 0.12, 0.16, 0.20, 0.20, 0.18])
    regions = np.array(["Marmara", "Ege", "Akdeniz", "Ic Anadolu"])
    region_probs = np.array([0.42, 0.18, 0.22, 0.18])
    segments = np.array(["SME", "Commercial", "Corporate"])
    segment_probs = np.array([0.52, 0.33, 0.15])
    segment_base = {"SME": 65_000, "Commercial": 260_000, "Corporate": 1_250_000}

    n = params["n_customers"]
    customer_master = pd.DataFrame(
        {
            "customer_id": [f"C{idx:04d}" for idx in range(1, n + 1)],
            "sector": rng.choice(sectors["sector"], size=n, p=sector_probs),
            "region": rng.choice(regions, size=n, p=region_probs),
            "customer_segment": rng.choice(segments, size=n, p=segment_probs),
        }
    ).merge(sectors, on="sector", how="left")
    customer_master["size_noise"] = rng.lognormal(mean=0.0, sigma=0.50, size=n)
    customer_master["base_monthly_real_bill"] = (
        customer_master["customer_segment"].map(segment_base)
        * customer_master["energy_intensity"]
        * customer_master["size_noise"]
    )
    customer_master["annual_turnover"] = (
        customer_master["base_monthly_real_bill"]
        * customer_master["base_turnover_multiple"]
        * 12
        * rng.lognormal(mean=0.0, sigma=0.35, size=n)
    )

    all_months = period_range(params["sample_start_month"], params["sample_end_month"])
    obs_counts = np.rint(rng.normal(params["average_customer_months"], 3.2, size=n)).astype(int)
    obs_counts = np.clip(obs_counts, params["min_customer_months"], params["max_customer_months"])
    possible_end_months = all_months[params["min_customer_months"] - 1 :]
    customer_master["obs_month_count"] = obs_counts
    customer_master["last_invoice_month"] = rng.choice(possible_end_months, size=n, replace=True)
    customer_master["first_invoice_month"] = [
        last_m - int(cnt - 1)
        for last_m, cnt in zip(customer_master["last_invoice_month"], customer_master["obs_month_count"])
    ]

    macro = _build_macro(params, all_months, rng)
    seasonality = _build_true_seasonality()
    monthly_truth = _build_monthly_truth(customer_master, macro, seasonality, params, rng)
    raw_invoices = _build_raw_invoices(monthly_truth, params, rng)

    return {
        "customer_master": customer_master,
        "macro": macro,
        "true_seasonality": seasonality,
        "monthly_truth": monthly_truth,
        "raw_invoices": raw_invoices,
    }


def _build_macro(params: dict[str, Any], months: pd.PeriodIndex, rng: np.random.Generator) -> pd.DataFrame:
    monthly_inflation = rng.normal(params["monthly_inflation_mean"], params["monthly_inflation_std"], size=len(months))
    monthly_inflation = np.clip(monthly_inflation, 0.005, 0.065)
    inflation_index = 100 * np.cumprod(1 + monthly_inflation)
    current_factor = 1.0
    electricity_factor = []
    for m in months:
        current_factor *= 1 + ELECTRICITY_REAL_PRICE_SHOCKS.get(str(m), 0.0)
        electricity_factor.append(current_factor)
    return pd.DataFrame(
        {
            "invoice_month": months,
            "month_of_year": [m.month for m in months],
            "monthly_inflation": monthly_inflation,
            "inflation_index": inflation_index,
            "true_electricity_real_price_factor": electricity_factor,
            "true_calendar_shock_log": np.log(electricity_factor),
        }
    )


def _build_true_seasonality() -> pd.DataFrame:
    raw = {
        "ICE_CREAM_MFG": {1: -0.72, 2: -0.65, 3: -0.35, 4: 0.00, 5: 0.35, 6: 0.62, 7: 0.78, 8: 0.72, 9: 0.30, 10: -0.08, 11: -0.38, 12: -0.60},
        "COLD_STORAGE": {1: -0.22, 2: -0.18, 3: -0.10, 4: 0.05, 5: 0.28, 6: 0.42, 7: 0.55, 8: 0.52, 9: 0.28, 10: 0.05, 11: -0.12, 12: -0.22},
        "HOTEL_TOURISM": {1: -0.35, 2: -0.30, 3: -0.18, 4: 0.00, 5: 0.18, 6: 0.35, 7: 0.55, 8: 0.58, 9: 0.35, 10: 0.05, 11: -0.18, 12: -0.25},
        "TEXTILE": {1: -0.08, 2: -0.04, 3: 0.06, 4: 0.12, 5: 0.10, 6: 0.04, 7: -0.06, 8: -0.08, 9: 0.05, 10: 0.10, 11: 0.04, 12: -0.06},
        "RETAIL_FOOD": {1: 0.06, 2: -0.02, 3: 0.00, 4: 0.02, 5: 0.04, 6: 0.08, 7: 0.10, 8: 0.08, 9: 0.02, 10: 0.00, 11: 0.05, 12: 0.18},
        "OFFICE_SERVICES": {1: 0.02, 2: 0.00, 3: 0.02, 4: 0.03, 5: 0.02, 6: 0.04, 7: 0.05, 8: 0.03, 9: 0.02, 10: 0.01, 11: 0.00, 12: 0.02},
    }
    rows = []
    for sector, month_map in raw.items():
        mean_effect = np.mean(list(month_map.values()))
        for month_of_year, effect in month_map.items():
            rows.append({"sector": sector, "month_of_year": month_of_year, "true_seasonality_log": effect - mean_effect})
    return pd.DataFrame(rows)


def _build_monthly_truth(
    customer_master: pd.DataFrame,
    macro: pd.DataFrame,
    seasonality: pd.DataFrame,
    params: dict[str, Any],
    rng: np.random.Generator,
) -> pd.DataFrame:
    rows = []
    for _, c in customer_master.iterrows():
        months = pd.period_range(c["first_invoice_month"], c["last_invoice_month"], freq="M")
        drift = rng.normal(0.0, 0.018)
        noise_scale = params["idiosyncratic_noise_std"] * rng.uniform(0.75, 1.35)
        for k, m in enumerate(months):
            rows.append(
                {
                    "customer_id": c["customer_id"],
                    "invoice_month": m,
                    "sector": c["sector"],
                    "sector_label": c["sector_label"],
                    "region": c["region"],
                    "customer_segment": c["customer_segment"],
                    "annual_turnover": c["annual_turnover"],
                    "base_monthly_real_bill": c["base_monthly_real_bill"],
                    "customer_trend_log": drift * k,
                    "idiosyncratic_noise": rng.normal(0.0, noise_scale),
                }
            )
    panel = pd.DataFrame(rows)
    panel = panel.merge(macro, on="invoice_month", how="left")
    panel = panel.merge(seasonality, on=["sector", "month_of_year"], how="left")
    panel["injected_anomaly_type"] = "NORMAL"
    panel["injected_anomaly_effect_log"] = 0.0

    structural_n = max(1, int(params["structural_under_peer_customer_share"] * params["n_customers"]))
    structural_pool = customer_master.loc[customer_master["obs_month_count"] <= 8, "customer_id"].to_numpy()
    if len(structural_pool) < structural_n:
        structural_pool = customer_master["customer_id"].to_numpy()
    structural_customers = rng.choice(structural_pool, size=structural_n, replace=False)
    structural_mask = panel["customer_id"].isin(structural_customers)
    panel.loc[structural_mask, "injected_anomaly_type"] = "STRUCTURAL_UNDER_PEER"
    panel.loc[structural_mask, "injected_anomaly_effect_log"] += rng.uniform(-1.25, -0.85, size=structural_mask.sum())

    eligible = customer_master.loc[~customer_master["customer_id"].isin(structural_customers), "customer_id"].to_numpy()
    low_n = max(1, int(params["single_month_low_anomaly_share"] * params["n_customers"]))
    high_n = max(1, int(params["single_month_high_anomaly_share"] * params["n_customers"]))
    low_customers = rng.choice(eligible, size=low_n, replace=False)
    remaining = np.array([c for c in eligible if c not in set(low_customers)])
    high_customers = rng.choice(remaining, size=high_n, replace=False)

    def inject_single(customers: np.ndarray, label: str, low: float, high: float) -> None:
        for cust in customers:
            idx = panel.index[panel["customer_id"].eq(cust)].to_numpy()
            if len(idx) <= 3:
                continue
            chosen = rng.choice(idx[2:])
            panel.loc[chosen, "injected_anomaly_type"] = label
            panel.loc[chosen, "injected_anomaly_effect_log"] += rng.uniform(low, high)

    inject_single(low_customers, "LOW_OPERATION_DROP", -1.85, -1.05)
    inject_single(high_customers, "HIGH_BILL_SPIKE", 0.95, 1.55)

    panel["log_real_bill_true"] = (
        np.log1p(panel["base_monthly_real_bill"])
        + panel["true_seasonality_log"]
        + panel["true_calendar_shock_log"]
        + panel["customer_trend_log"]
        + panel["idiosyncratic_noise"]
        + panel["injected_anomaly_effect_log"]
    )
    panel["real_bill_true"] = np.expm1(panel["log_real_bill_true"]).clip(lower=0)
    panel["electricity_bill_amount_true"] = panel["real_bill_true"] * (panel["inflation_index"] / 100.0)
    return panel.sort_values(["customer_id", "invoice_month"]).reset_index(drop=True)


def _build_raw_invoices(monthly_truth: pd.DataFrame, params: dict[str, Any], rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for _, r in monthly_truth.iterrows():
        amount = float(r["electricity_bill_amount_true"])
        if rng.random() < params["missing_invoice_row_share"]:
            continue
        if rng.random() < params["missing_invoice_value_share"]:
            rows.append({"customer_id": r["customer_id"], "invoice_month": r["invoice_month"], "invoice_amount": np.nan, "raw_record_type": "MISSING_VALUE"})
            continue
        if rng.random() < params["negative_invoice_share"]:
            rows.append({"customer_id": r["customer_id"], "invoice_month": r["invoice_month"], "invoice_amount": -abs(amount * rng.uniform(0.10, 0.60)), "raw_record_type": "NEGATIVE_ADJUSTMENT"})
            continue
        if rng.random() < params["zero_bill_share"]:
            rows.append({"customer_id": r["customer_id"], "invoice_month": r["invoice_month"], "invoice_amount": 0.0, "raw_record_type": "ZERO_BILL"})
            continue
        if rng.random() < params["duplicate_invoice_share"]:
            split = rng.uniform(0.25, 0.75)
            rows.append({"customer_id": r["customer_id"], "invoice_month": r["invoice_month"], "invoice_amount": amount * split, "raw_record_type": "SPLIT_INVOICE_A"})
            rows.append({"customer_id": r["customer_id"], "invoice_month": r["invoice_month"], "invoice_amount": amount * (1 - split), "raw_record_type": "SPLIT_INVOICE_B"})
            continue
        rows.append({"customer_id": r["customer_id"], "invoice_month": r["invoice_month"], "invoice_amount": amount, "raw_record_type": "NORMAL_INVOICE"})
    raw = pd.DataFrame(rows)
    raw["invoice_id"] = [f"INV{i:07d}" for i in range(1, len(raw) + 1)]
    return raw[["invoice_id", "customer_id", "invoice_month", "invoice_amount", "raw_record_type"]]


def prepare_model_panel(
    raw_invoices: pd.DataFrame,
    customer_master: pd.DataFrame,
    macro: pd.DataFrame,
) -> pd.DataFrame:
    expected_rows = []
    for _, c in customer_master.iterrows():
        for m in pd.period_range(c["first_invoice_month"], c["last_invoice_month"], freq="M"):
            expected_rows.append(
                {
                    "customer_id": c["customer_id"],
                    "invoice_month": m,
                    "sector": c["sector"],
                    "sector_label": c["sector_label"],
                    "region": c["region"],
                    "customer_segment": c["customer_segment"],
                    "annual_turnover": c["annual_turnover"],
                    "first_invoice_month": c["first_invoice_month"],
                    "last_invoice_month": c["last_invoice_month"],
                }
            )
    panel = pd.DataFrame(expected_rows)

    raw = raw_invoices.copy()
    raw["is_nonnull"] = raw["invoice_amount"].notna()
    raw["is_negative"] = raw["invoice_amount"].lt(0).fillna(False)
    raw["is_zero"] = raw["invoice_amount"].eq(0).fillna(False)
    raw["is_positive"] = raw["invoice_amount"].gt(0).fillna(False)
    agg = (
        raw.groupby(["customer_id", "invoice_month"])
        .agg(
            raw_invoice_count=("invoice_id", "count"),
            nonnull_invoice_count=("is_nonnull", "sum"),
            missing_invoice_value_count=("is_nonnull", lambda s: int((~s).sum())),
            negative_invoice_count=("is_negative", "sum"),
            zero_invoice_count=("is_zero", "sum"),
            positive_invoice_count=("is_positive", "sum"),
            positive_amount_sum=("invoice_amount", lambda s: float(s[s > 0].sum())),
            nonnegative_amount_sum=("invoice_amount", lambda s: float(s[s >= 0].sum())),
        )
        .reset_index()
    )
    panel = panel.merge(agg, on=["customer_id", "invoice_month"], how="left")
    for col in [
        "raw_invoice_count",
        "nonnull_invoice_count",
        "missing_invoice_value_count",
        "negative_invoice_count",
        "zero_invoice_count",
        "positive_invoice_count",
    ]:
        panel[col] = panel[col].fillna(0).astype(int)
    panel[["positive_amount_sum", "nonnegative_amount_sum"]] = panel[["positive_amount_sum", "nonnegative_amount_sum"]].fillna(0.0)

    panel = panel.merge(macro[["invoice_month", "month_of_year", "inflation_index"]], on="invoice_month", how="left")
    panel["has_invoice_row"] = panel["raw_invoice_count"].gt(0)
    panel["negative_adjustment_flag"] = panel["negative_invoice_count"].gt(0)
    panel["bill_missing_flag"] = (~panel["has_invoice_row"]) | (panel["missing_invoice_value_count"].gt(0) & panel["nonnull_invoice_count"].eq(0))
    panel["zero_bill_flag"] = panel["has_invoice_row"] & panel["negative_invoice_count"].eq(0) & panel["positive_invoice_count"].eq(0) & panel["zero_invoice_count"].gt(0)
    panel["duplicate_invoice_flag"] = panel["raw_invoice_count"].gt(1)
    panel["bad_inflation_flag"] = panel["inflation_index"].isna() | panel["inflation_index"].le(0)

    panel["electricity_bill_amount"] = np.where(
        (~panel["negative_adjustment_flag"]) & (~panel["bill_missing_flag"]),
        panel["nonnegative_amount_sum"],
        np.nan,
    )
    panel["valid_bill_for_model"] = (
        panel["electricity_bill_amount"].notna()
        & panel["electricity_bill_amount"].ge(0)
        & (~panel["negative_adjustment_flag"])
        & (~panel["bad_inflation_flag"])
    )
    panel["data_quality_status"] = "VALID"
    panel.loc[panel["bill_missing_flag"], "data_quality_status"] = "MISSING_BILL"
    panel.loc[panel["negative_adjustment_flag"], "data_quality_status"] = "NEGATIVE_ADJUSTMENT"
    panel.loc[panel["bad_inflation_flag"], "data_quality_status"] = "BAD_INFLATION"

    panel["model_real_bill"] = np.where(
        panel["valid_bill_for_model"],
        panel["electricity_bill_amount"] / (panel["inflation_index"] / 100.0),
        np.nan,
    )
    panel["log_real_bill"] = np.log1p(panel["model_real_bill"])
    panel["calendar_month"] = panel["invoice_month"].astype(str)
    panel["month_ord"] = panel["invoice_month"].map(month_ordinal)
    panel = panel.sort_values(["customer_id", "invoice_month"]).reset_index(drop=True)

    panel["previous_invoice_month"] = panel.groupby("customer_id")["invoice_month"].shift(1)
    panel["previous_log_real_bill"] = panel.groupby("customer_id")["log_real_bill"].shift(1)
    panel["previous_valid_bill_for_model"] = panel.groupby("customer_id")["valid_bill_for_model"].shift(1).fillna(False)
    panel["has_valid_previous_month"] = (
        panel["previous_invoice_month"].eq(panel["invoice_month"] - 1)
        & panel["previous_valid_bill_for_model"]
        & panel["valid_bill_for_model"]
    )
    panel["customer_mom_log_change"] = np.where(
        panel["has_valid_previous_month"],
        panel["log_real_bill"] - panel["previous_log_real_bill"],
        np.nan,
    )
    panel["customer_valid_obs_count_prior"] = (
        panel.groupby("customer_id")["valid_bill_for_model"].cumsum().shift(1).fillna(0).astype(int)
    )
    panel["customer_missing_count_prior"] = (
        panel.groupby("customer_id")["bill_missing_flag"].cumsum().shift(1).fillna(0).astype(int)
    )
    return panel


def assign_turnover_buckets(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    out = out.drop(columns=[c for c in ["log_turnover", "turnover_bucket"] if c in out.columns])
    customer_turnover = out[["customer_id", "sector", "annual_turnover"]].drop_duplicates().copy()
    customer_turnover["log_turnover"] = np.log1p(customer_turnover["annual_turnover"])
    customer_turnover["turnover_bucket"] = (
        customer_turnover.groupby("sector")["log_turnover"]
        .transform(lambda s: pd.qcut(s.rank(method="first"), q=5, labels=["very_small", "small", "medium", "large", "very_large"]))
        .astype(str)
    )
    out = out.merge(customer_turnover[["customer_id", "log_turnover", "turnover_bucket"]], on="customer_id", how="left")
    return out


def peer_definition_diagnostics(panel: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    valid = panel.loc[panel["valid_bill_for_model"]].copy()

    def one(cols: list[str], label: str) -> dict[str, Any]:
        group_cols = cols + ["month_of_year"]
        if cols:
            med = valid.groupby(group_cols)["log_real_bill"].transform("median")
            customers = valid.groupby(group_cols)["customer_id"].transform("nunique")
            groups = valid[cols].drop_duplicates().shape[0]
        else:
            med = valid.groupby(["month_of_year"])["log_real_bill"].transform("median")
            customers = valid.groupby(["month_of_year"])["customer_id"].transform("nunique")
            groups = 1
        residual = valid["log_real_bill"] - med
        return {
            "peer_definition": label,
            "columns": "+".join(cols) if cols else "global",
            "median_abs_residual": float(np.median(np.abs(residual.dropna()))),
            "residual_mad_scale": robust_mad(residual),
            "avg_group_customer_count": float(customers.mean()),
            "p10_group_customer_count": float(customers.quantile(0.10)),
            "small_group_row_share": float((customers < params["min_peer_customers"]).mean()),
            "group_count": int(groups),
        }

    candidates = [
        (["sector"], "sector_only"),
        (["sector", "turnover_bucket"], "sector+turnover"),
        (["sector", "customer_segment"], "sector+segment"),
        (["sector", "customer_segment", "turnover_bucket"], "sector+segment+turnover"),
        (["sector", "customer_segment", "region", "turnover_bucket"], "full_candidate"),
    ]
    return pd.DataFrame([one(cols, label) for cols, label in candidates]).sort_values("median_abs_residual")


def _context(row: pd.Series, hist: pd.DataFrame, current_mom: pd.DataFrame, level_idx: int) -> dict[str, Any]:
    cols = PEER_LEVELS[level_idx - 1]
    hist_mask = group_mask(hist, row, cols)
    mom_mask = group_mask(current_mom, row, cols)
    hist_group = hist.loc[hist_mask & hist["customer_id"].ne(row["customer_id"])].copy()
    mom_group = current_mom.loc[mom_mask & current_mom["customer_id"].ne(row["customer_id"])].copy()
    month = int(row["month_of_year"])
    prev_month = 12 if month == 1 else month - 1
    return {
        "level_idx": level_idx,
        "level_name": PEER_LEVEL_NAMES[level_idx],
        "cols": cols,
        "hist_group": hist_group,
        "mom_group": mom_group,
        "hist_rows": len(hist_group),
        "hist_customers": hist_group["customer_id"].nunique(),
        "current_mom_customers": mom_group["customer_id"].nunique(),
        "month_rows": int(hist_group["month_of_year"].eq(month).sum()),
        "prev_month_rows": int(hist_group["month_of_year"].eq(prev_month).sum()),
    }


def _select_peer_context(row: pd.Series, hist: pd.DataFrame, current_mom: pd.DataFrame, params: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    contexts = [_context(row, hist, current_mom, i) for i in range(1, len(PEER_LEVELS) + 1)]
    selected = None
    for ctx in contexts:
        passes = (
            ctx["hist_customers"] >= params["min_peer_customers"]
            and ctx["hist_rows"] >= params["min_peer_history_rows"]
            and ctx["month_rows"] >= params["min_month_of_year_rows"]
            and ctx["prev_month_rows"] >= params["min_month_of_year_rows"]
            and ctx["current_mom_customers"] >= params["min_calendar_mom_customers"]
        )
        if passes:
            selected = ctx
            break
    if selected is None:
        return None, None, contexts
    broad = contexts[-1]
    for ctx in contexts:
        if ctx["level_idx"] > selected["level_idx"] and ctx["hist_rows"] > selected["hist_rows"]:
            broad = ctx
            break
    return selected, broad, contexts


def _peer_model(row: pd.Series, hist: pd.DataFrame, current_mom: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any] | None:
    selected, broad, contexts = _select_peer_context(row, hist, current_mom, params)
    if selected is None or broad is None:
        return None
    detail = selected["hist_group"]
    broad_hist = broad["hist_group"]
    month = int(row["month_of_year"])
    prev_month = 12 if month == 1 else month - 1
    x_t = int(row["month_ord"])

    detail_base_raw = safe_median(winsorized(detail["log_real_bill"], params["winsor_lower_q"], params["winsor_upper_q"]))
    broad_base_raw = safe_median(winsorized(broad_hist["log_real_bill"], params["winsor_lower_q"], params["winsor_upper_q"]))
    peer_base, base_reliability = shrink_value(detail_base_raw, broad_base_raw, selected["hist_rows"], params["base_shrinkage_k"])

    def month_effect(hist_df: pd.DataFrame, base: float, m: int) -> tuple[float, int]:
        vals = hist_df.loc[hist_df["month_of_year"].eq(m), "log_real_bill"]
        return safe_median(vals, default=base) - base, int(vals.notna().sum())

    season_lookup = {}
    season_reliability_lookup = {}
    portfolio_months = hist["invoice_month"].nunique()
    for m in range(1, 13):
        detail_eff, detail_n = month_effect(detail, detail_base_raw, m)
        broad_eff, _ = month_effect(broad_hist, broad_base_raw, m)
        if portfolio_months < params["min_months_for_seasonality"]:
            final_eff, rel = 0.0, 0.0
        else:
            final_eff, rel = shrink_value(detail_eff, broad_eff, detail_n, params["seasonality_shrinkage_k"])
            if portfolio_months < params["recommended_months_for_seasonality"]:
                history_rel = portfolio_months / params["recommended_months_for_seasonality"]
                final_eff *= history_rel
                rel *= history_rel
        season_lookup[m] = final_eff
        season_reliability_lookup[m] = rel

    def line_for(hist_df: pd.DataFrame, lookup: dict[int, float]) -> tuple[float, float, int]:
        tmp = hist_df.copy()
        tmp["season_adj_y"] = tmp["log_real_bill"] - tmp["month_of_year"].map(lookup).fillna(0.0)
        monthly = tmp.groupby("invoice_month").agg(y=("season_adj_y", "median"), x=("month_ord", "median")).reset_index()
        return robust_line(monthly["x"], monthly["y"], min_points=4)

    d_intercept, d_slope, d_months = line_for(detail, season_lookup)
    b_intercept, b_slope, b_months = line_for(broad_hist, season_lookup)
    peer_slope, trend_reliability = shrink_value(d_slope, b_slope, d_months, params["trend_shrinkage_k"])
    peer_intercept, _ = shrink_value(d_intercept, b_intercept, d_months, params["trend_shrinkage_k"])

    detail_mom = safe_median(selected["mom_group"]["customer_mom_log_change"], default=np.nan)
    broad_mom = safe_median(broad["mom_group"]["customer_mom_log_change"], default=0.0)
    raw_peer_mom, mom_reliability = shrink_value(detail_mom, broad_mom, selected["current_mom_customers"], params["calendar_mom_shrinkage_k"])
    seasonal_mom = season_lookup[month] - season_lookup[prev_month]
    calendar_shock = raw_peer_mom - seasonal_mom - peer_slope

    detail_for_scale = detail.copy()
    detail_for_scale["pred"] = peer_intercept + peer_slope * detail_for_scale["month_ord"] + detail_for_scale["month_of_year"].map(season_lookup).fillna(0.0)
    level_scale = robust_mad(detail_for_scale["log_real_bill"] - detail_for_scale["pred"], params["min_level_scale"])
    trend_scale = robust_mad(detail["customer_mom_log_change"], params["min_trend_scale"])

    return {
        "selected": selected,
        "broad": broad,
        "contexts": contexts,
        "peer_base": peer_base,
        "peer_intercept": peer_intercept,
        "peer_slope": peer_slope,
        "peer_base_at_t": peer_intercept + peer_slope * x_t,
        "season_lookup": season_lookup,
        "seasonality_effect": season_lookup[month],
        "previous_seasonality_effect": season_lookup[prev_month],
        "seasonality_reliability": season_reliability_lookup[month],
        "raw_peer_mom": raw_peer_mom,
        "seasonal_mom": seasonal_mom,
        "calendar_shock": calendar_shock,
        "base_reliability": base_reliability,
        "trend_reliability": trend_reliability,
        "mom_reliability": mom_reliability,
        "level_scale": level_scale,
        "trend_scale": trend_scale,
        "portfolio_history_months": portfolio_months,
    }


def _customer_model(row: pd.Series, hist: pd.DataFrame, peer: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    cust_hist = hist.loc[hist["customer_id"].eq(row["customer_id"])].copy()
    x_t = int(row["month_ord"])
    season_lookup = peer["season_lookup"]
    if cust_hist.empty:
        return {
            "customer_intercept": peer["peer_intercept"],
            "customer_slope": peer["peer_slope"],
            "customer_base_at_t": peer["peer_base_at_t"],
            "customer_trend_reliability": 0.0,
            "customer_seasonality_effect": peer["seasonality_effect"],
            "customer_seasonality_reliability": 0.0,
        }
    cust_hist["season_adj_y"] = cust_hist["log_real_bill"] - cust_hist["month_of_year"].map(season_lookup).fillna(0.0)
    c_intercept_raw, c_slope_raw, n_points = robust_line(cust_hist["month_ord"], cust_hist["season_adj_y"], params["min_customer_trend_points"])
    c_slope, trend_rel = shrink_value(c_slope_raw, peer["peer_slope"], n_points, params["customer_trend_shrinkage_k"])
    c_intercept = safe_median(cust_hist["season_adj_y"] - c_slope * cust_hist["month_ord"], default=peer["peer_intercept"])
    customer_base_at_t = c_intercept + c_slope * x_t

    same_month = cust_hist.loc[cust_hist["month_of_year"].eq(int(row["month_of_year"]))].copy()
    if len(same_month) >= params["min_customer_same_month_obs"]:
        same_month_pred = c_intercept + c_slope * same_month["month_ord"]
        cust_season_raw = safe_median(same_month["log_real_bill"] - same_month_pred, default=peer["seasonality_effect"])
        cust_season, season_rel = shrink_value(cust_season_raw, peer["seasonality_effect"], len(same_month), params["customer_seasonality_shrinkage_k"])
    else:
        cust_season, season_rel = peer["seasonality_effect"], 0.0

    return {
        "customer_intercept": c_intercept,
        "customer_slope": c_slope,
        "customer_base_at_t": customer_base_at_t,
        "customer_trend_reliability": trend_rel,
        "customer_seasonality_effect": cust_season,
        "customer_seasonality_reliability": season_rel,
    }


def _score_one(row: pd.Series, full_panel: pd.DataFrame, params: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    t = row["invoice_month"]
    hist = full_panel.loc[(full_panel["invoice_month"] < t) & (full_panel["valid_bill_for_model"])].copy()
    current_mom = full_panel.loc[(full_panel["invoice_month"] == t) & (full_panel["customer_mom_log_change"].notna())].copy()

    if not row["valid_bill_for_model"]:
        return None, {"customer_id": row["customer_id"], "invoice_month": t, "not_scored_reason": row["data_quality_status"]}
    if row["customer_valid_obs_count_prior"] < params["min_customer_prior_bills_to_score"]:
        return None, {"customer_id": row["customer_id"], "invoice_month": t, "not_scored_reason": "INSUFFICIENT_CUSTOMER_HISTORY"}
    if not row["has_valid_previous_month"]:
        return None, {"customer_id": row["customer_id"], "invoice_month": t, "not_scored_reason": "NO_VALID_PREVIOUS_MONTH"}
    if hist["invoice_month"].nunique() < params["min_portfolio_history_months_to_score"]:
        return None, {"customer_id": row["customer_id"], "invoice_month": t, "not_scored_reason": "INSUFFICIENT_PORTFOLIO_HISTORY"}

    peer = _peer_model(row, hist, current_mom, params)
    if peer is None:
        return None, {"customer_id": row["customer_id"], "invoice_month": t, "not_scored_reason": "INSUFFICIENT_PEER_SUPPORT"}
    customer = _customer_model(row, hist, peer, params)

    customer_expected = customer["customer_base_at_t"] + customer["customer_seasonality_effect"] + peer["calendar_shock"]
    peer_expected = peer["peer_base_at_t"] + peer["seasonality_effect"] + peer["calendar_shock"]
    obs_count = int(row["customer_valid_obs_count_prior"])
    w_customer = min(obs_count / params["target_history_months"], params["max_customer_weight"])
    w_peer = 1.0 - w_customer
    expected = w_customer * customer_expected + w_peer * peer_expected

    self_dev = row["log_real_bill"] - customer_expected
    peer_dev = row["log_real_bill"] - peer_expected
    main_residual = row["log_real_bill"] - expected
    trend_dev = row["customer_mom_log_change"] - peer["raw_peer_mom"]
    relative_gap = customer["customer_base_at_t"] - peer["peer_base_at_t"]

    main_z, main_score = score_from_deviation(main_residual, peer["level_scale"], params["z_score_cap"])
    self_z, self_score = score_from_deviation(self_dev, peer["level_scale"], params["z_score_cap"])
    peer_z, peer_score = score_from_deviation(peer_dev, peer["level_scale"], params["z_score_cap"])
    trend_z, trend_score = score_from_deviation(trend_dev, peer["trend_scale"], params["z_score_cap"])
    relative_z, relative_score = score_from_deviation(relative_gap, max(peer["level_scale"], params["min_relative_scale"]), params["z_score_cap"])

    history_strength = min(obs_count / params["target_history_months"], 1.0)
    self_weight = params["base_self_weight"] + params["variable_self_weight"] * history_strength
    trend_weight = params["base_trend_weight"] - params["trend_weight_decay"] * history_strength
    relative_weight = params["relative_gap_weight"]
    peer_weight = 1.0 - self_weight - trend_weight - relative_weight
    final_score = (
        self_weight * self_score
        + peer_weight * peer_score
        + trend_weight * trend_score
        + relative_weight * relative_score
    )

    selected = peer["selected"]
    history_confidence = min(obs_count / params["target_history_months"], 1.0)
    peer_confidence = min(selected["hist_rows"] / params["strong_peer_history_rows"], 1.0)
    month_confidence = min(selected["month_rows"] / params["strong_month_of_year_rows"], 1.0)
    mom_confidence = min(selected["current_mom_customers"] / params["strong_calendar_mom_customers"], 1.0)
    data_quality_confidence = 0.90 if row["duplicate_invoice_flag"] else 1.0
    confidence = 100.0 * (
        0.25 * history_confidence
        + 0.30 * peer_confidence
        + 0.20 * month_confidence
        + 0.15 * mom_confidence
        + 0.10 * data_quality_confidence
    )

    scored = {
        "customer_id": row["customer_id"],
        "invoice_month": t,
        "calendar_month": str(t),
        "sector": row["sector"],
        "sector_label": row["sector_label"],
        "region": row["region"],
        "customer_segment": row["customer_segment"],
        "turnover_bucket": row["turnover_bucket"],
        "actual_bill_amount": row["electricity_bill_amount"],
        "log_real_bill": row["log_real_bill"],
        "customer_valid_obs_count_prior": obs_count,
        "customer_missing_count_prior": row["customer_missing_count_prior"],
        "zero_bill_flag": row["zero_bill_flag"],
        "duplicate_invoice_flag": row["duplicate_invoice_flag"],
        "peer_group_level_used": selected["level_idx"],
        "peer_group_level_name": selected["level_name"],
        "peer_hist_rows": selected["hist_rows"],
        "peer_hist_customers": selected["hist_customers"],
        "peer_month_of_year_rows": selected["month_rows"],
        "peer_calendar_mom_customers": selected["current_mom_customers"],
        "portfolio_history_months": peer["portfolio_history_months"],
        "peer_base_at_t": peer["peer_base_at_t"],
        "peer_slope": peer["peer_slope"],
        "customer_base_at_t": customer["customer_base_at_t"],
        "customer_slope": customer["customer_slope"],
        "seasonality_effect": peer["seasonality_effect"],
        "customer_seasonality_effect": customer["customer_seasonality_effect"],
        "raw_peer_mom": peer["raw_peer_mom"],
        "seasonal_mom": peer["seasonal_mom"],
        "calendar_shock": peer["calendar_shock"],
        "customer_expected": customer_expected,
        "peer_expected": peer_expected,
        "expected_log_real_bill": expected,
        "main_residual": main_residual,
        "self_deviation": self_dev,
        "peer_deviation": peer_dev,
        "trend_deviation": trend_dev,
        "relative_peer_gap": relative_gap,
        "level_scale": peer["level_scale"],
        "trend_scale": peer["trend_scale"],
        "main_z": main_z,
        "self_z": self_z,
        "peer_z": peer_z,
        "trend_z": trend_z,
        "relative_z": relative_z,
        "main_score": main_score,
        "self_score": self_score,
        "peer_score": peer_score,
        "trend_score": trend_score,
        "relative_gap_score": relative_score,
        "self_weight": self_weight,
        "peer_weight": peer_weight,
        "trend_weight": trend_weight,
        "relative_weight": relative_weight,
        "final_anomaly_score": final_score,
        "confidence": confidence,
        "seasonality_reliability": peer["seasonality_reliability"],
        "calendar_mom_reliability": peer["mom_reliability"],
        "customer_trend_reliability": customer["customer_trend_reliability"],
        "customer_seasonality_reliability": customer["customer_seasonality_reliability"],
    }
    return scored, None


def score_panel(panel: pd.DataFrame, params: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    full = assign_turnover_buckets(panel)
    score_start = to_period(params["score_start_month"])
    candidates = full.loc[full["invoice_month"] >= score_start].copy()
    scored_rows = []
    not_scored_rows = []
    for _, row in candidates.iterrows():
        scored, not_scored = _score_one(row, full, params)
        if scored is not None:
            scored_rows.append(scored)
        if not_scored is not None:
            not_scored_rows.append(not_scored)
    scores = pd.DataFrame(scored_rows)
    not_scored = pd.DataFrame(not_scored_rows)
    if len(scores):
        scores = calibrate_labels(scores, params)
    return scores, not_scored


def calibrate_labels(scores: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    out = scores.copy()
    watch_q = max(0.0, min(1.0, 1.0 - params["watchlist_top_rate"]))
    high_q = max(0.0, min(1.0, 1.0 - params["high_anomaly_top_rate"]))
    watch_thr = max(params["watchlist_threshold_floor"], float(out["final_anomaly_score"].quantile(watch_q)))
    high_thr = max(params["high_anomaly_threshold_floor"], float(out["final_anomaly_score"].quantile(high_q)))
    out["watchlist_threshold_used"] = watch_thr
    out["high_anomaly_threshold_used"] = high_thr
    out["score_percentile"] = out["final_anomaly_score"].rank(pct=True)

    def label(row: pd.Series) -> str:
        score = row["final_anomaly_score"]
        residual = row["main_residual"]
        if score >= high_thr:
            return "LOW_BILL_ANOMALY" if residual < 0 else "HIGH_BILL_ANOMALY"
        if score >= watch_thr:
            return "WATCHLIST_LOW" if residual < 0 else "WATCHLIST_HIGH"
        return "NORMAL"

    out["anomaly_label"] = out.apply(label, axis=1)
    out["is_high_anomaly"] = out["anomaly_label"].isin(["LOW_BILL_ANOMALY", "HIGH_BILL_ANOMALY"])
    out["is_watchlist_or_anomaly"] = out["anomaly_label"].ne("NORMAL")
    out["reason_codes"] = out.apply(lambda row: reason_codes(row, params), axis=1)
    return out


def reason_codes(row: pd.Series, params: dict[str, Any]) -> str:
    thr = params["reason_score_threshold"]
    reasons: list[str] = []
    if row["self_score"] >= thr:
        reasons.append("OWN_HISTORY_DROP" if row["self_deviation"] < 0 else "OWN_HISTORY_JUMP")
    if row["peer_score"] >= thr:
        reasons.append("PEER_ADJUSTED_DROP" if row["peer_deviation"] < 0 else "PEER_ADJUSTED_JUMP")
    if row["trend_score"] >= thr:
        reasons.append("TREND_DIVERGENCE_DROP" if row["trend_deviation"] < 0 else "TREND_DIVERGENCE_JUMP")
    if row["relative_gap_score"] >= thr:
        reasons.append("STRUCTURAL_BELOW_PEER" if row["relative_peer_gap"] < 0 else "STRUCTURAL_ABOVE_PEER")
    if row["confidence"] < params["low_confidence_threshold"]:
        reasons.append("LOW_CONFIDENCE")
    if row["peer_group_level_used"] >= 6:
        reasons.append("COARSE_PEER_GROUP")
    if bool(row.get("zero_bill_flag", False)):
        reasons.append("ZERO_BILL")
    if bool(row.get("duplicate_invoice_flag", False)):
        reasons.append("MULTIPLE_INVOICE_SUMMED")
    return ", ".join(reasons) if reasons else "NO_MAJOR_DRIVER"


def build_validation_tables(scores: pd.DataFrame, not_scored: pd.DataFrame, panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    tables["data_quality_summary"] = (
        panel.groupby("data_quality_status")
        .agg(
            row_count=("customer_id", "size"),
            customer_count=("customer_id", "nunique"),
            missing_count=("bill_missing_flag", "sum"),
            zero_count=("zero_bill_flag", "sum"),
            negative_count=("negative_adjustment_flag", "sum"),
            duplicate_count=("duplicate_invoice_flag", "sum"),
        )
        .reset_index()
    )
    if len(not_scored):
        tables["not_scored_summary"] = not_scored["not_scored_reason"].value_counts().rename_axis("not_scored_reason").reset_index(name="row_count")
    else:
        tables["not_scored_summary"] = pd.DataFrame(columns=["not_scored_reason", "row_count"])
    if len(scores):
        tables["peer_fallback_summary"] = (
            scores.groupby(["peer_group_level_used", "peer_group_level_name"])
            .agg(
                row_count=("customer_id", "size"),
                customer_count=("customer_id", "nunique"),
                avg_peer_hist_rows=("peer_hist_rows", "mean"),
                avg_month_of_year_rows=("peer_month_of_year_rows", "mean"),
                avg_calendar_mom_customers=("peer_calendar_mom_customers", "mean"),
                median_confidence=("confidence", "median"),
                median_score=("final_anomaly_score", "median"),
            )
            .reset_index()
            .sort_values("peer_group_level_used")
        )
        tables["sector_residual_summary"] = (
            scores.groupby(["sector", "sector_label"])
            .agg(
                row_count=("customer_id", "size"),
                median_residual=("main_residual", "median"),
                residual_mad=("main_residual", lambda s: robust_mad(s)),
                median_score=("final_anomaly_score", "median"),
                high_anomaly_rate=("is_high_anomaly", "mean"),
                watchlist_or_anomaly_rate=("is_watchlist_or_anomaly", "mean"),
            )
            .reset_index()
        )
        tables["monthly_score_summary"] = (
            scores.groupby("calendar_month")
            .agg(
                scored_rows=("customer_id", "size"),
                median_score=("final_anomaly_score", "median"),
                p95_score=("final_anomaly_score", lambda s: s.quantile(0.95)),
                high_anomaly_count=("is_high_anomaly", "sum"),
                watchlist_or_anomaly_count=("is_watchlist_or_anomaly", "sum"),
                median_calendar_shock=("calendar_shock", "median"),
            )
            .reset_index()
        )
        tables["top_anomalies"] = scores.sort_values("final_anomaly_score", ascending=False).head(100)
    return tables


def write_outputs(
    output_path: Path,
    params: dict[str, Any],
    data: dict[str, pd.DataFrame],
    panel: pd.DataFrame,
    scores: pd.DataFrame,
    not_scored: pd.DataFrame,
    peer_diag: pd.DataFrame,
    validation: dict[str, pd.DataFrame],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def export_df(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in out.columns:
            if pd.api.types.is_period_dtype(out[col]):
                out[col] = out[col].astype(str)
        return out

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        build_parameter_sheet(params).to_excel(writer, index=False, sheet_name="parameter_sheet")
        export_df(data["customer_master"]).to_excel(writer, index=False, sheet_name="customer_master")
        export_df(data["raw_invoices"]).to_excel(writer, index=False, sheet_name="raw_invoices")
        export_df(panel).to_excel(writer, index=False, sheet_name="model_panel")
        export_df(scores).to_excel(writer, index=False, sheet_name="scored_customers")
        export_df(not_scored).to_excel(writer, index=False, sheet_name="not_scored_rows")
        export_df(peer_diag).to_excel(writer, index=False, sheet_name="peer_definition_test")
        for name, table in validation.items():
            export_df(table).to_excel(writer, index=False, sheet_name=name[:31])
