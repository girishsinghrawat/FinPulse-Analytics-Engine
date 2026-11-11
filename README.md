# FinPulse Analytics Engine

FinPulse is a zero-dependency, local-first command-line financial ledger, budget burn-rate forecaster, and statistical anomaly detection engine written in Python and backed by SQLite3.

## Key Features

- **ACID-Compliant Storage:** Uses native `sqlite3` with strict schema constraints (`CHECK` clauses) and indexed query paths on `category` and `timestamp`.
- **Statistical Anomaly Detection:** Implements category-partitioned Z-score analysis ($Z = \frac{X - \mu}{\sigma}$) to automatically identify unusual spending spikes without relying on hardcoded static thresholds.
- **Linear Burn-Rate Forecasting:** Evaluates daily spending velocity against monthly category limits to flag budgets as `HEALTHY`, `AT_RISK`, or `EXCEEDED` prior to month-end.
- **Structured JSON Auditing:** Exports machine-readable financial health and anomaly reports for downstream pipelines.

## Engineering Trade-offs & Architecture

1. **Zero External Dependencies (Standard Library Only):**
   - *Decision:* Built exclusively with Python's standard library (`sqlite3`, `dataclasses`, `argparse`, `math`, `json`) rather than heavy ORM frameworks like SQLAlchemy or Pandas.
   - *Trade-off:* Slightly more verbose SQL query construction in exchange for instant portability, zero virtual-environment bloat, and sub-50ms cold-start CLI execution in any POSIX terminal environment.

2. **Per-Category Z-Score vs. Global Thresholding:**
   - *Decision:* Anomalies are computed relative to each specific category's historical mean and standard deviation rather than a flat global amount limit.
   - *Trade-off:* Requires a minimum sample size ($n \ge 4$) per category before anomaly detection activates, preventing false positives on sparse categories while accurately catching spikes in high-frequency buckets (e.g., cloud infrastructure or groceries).

## Quick Start

### 1. Run with Demo Seed Data
Running the script without arguments automatically initializes the SQLite schema, seeds realistic sample data, and renders the analytics dashboard:

    python3 main.py

### 2. Record a New Transaction

    python3 main.py add --type EXPENSE --category cloud_infra --amount 1250 --merchant "AWS" --desc "S3 storage"

### 3. View Analytics Dashboard

    python3 main.py dashboard

### 4. Export JSON Audit Report

    python3 main.py export --out audit_report.json
    
