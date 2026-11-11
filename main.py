#!/usr/bin/env python3
"""
FinPulse Analytics Engine
A local-first CLI financial ledger, budget burn-rate forecaster, and 
statistical anomaly detection engine backed by SQLite3.
"""

import sqlite3
import json
import csv
import math
import argparse
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("FinPulse")


@dataclass
class Transaction:
    id: Optional[int]
    timestamp: str
    category: str
    amount: float
    tx_type: str  # 'INCOME' or 'EXPENSE'
    description: str
    merchant: str


class DatabaseManager:
    """Handles SQLite connection lifecycle, schema migrations, and ACID transactions."""

    def __init__(self, db_path: str = "finpulse_ledger.db"):
        self.db_path = db_path
        self._initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount REAL NOT NULL CHECK(amount > 0),
                    tx_type TEXT NOT NULL CHECK(tx_type IN ('INCOME', 'EXPENSE')),
                    description TEXT,
                    merchant TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    category TEXT PRIMARY KEY,
                    monthly_limit REAL NOT NULL CHECK(monthly_limit > 0)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_timestamp ON transactions(timestamp)")
            conn.commit()

    def add_transaction(self, tx: Transaction) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO transactions (timestamp, category, amount, tx_type, description, merchant)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (tx.timestamp, tx.category.lower(), tx.amount, tx.tx_type.upper(), tx.description, tx.merchant))
            conn.commit()
            return cursor.lastrowid

    def set_budget(self, category: str, monthly_limit: float) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO budgets (category, monthly_limit)
                VALUES (?, ?)
                ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
            """, (category.lower(), monthly_limit))
            conn.commit()

    def fetch_transactions(self, tx_type: Optional[str] = None, days: Optional[int] = None) -> List[Transaction]:
        query = "SELECT * FROM transactions WHERE 1=1"
        params: List[object] = []

        if tx_type:
            query += " AND tx_type = ?"
            params.append(tx_type.upper())

        if days:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(cutoff)

        query += " ORDER BY timestamp DESC, id DESC"

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                Transaction(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    category=row["category"],
                    amount=row["amount"],
                    tx_type=row["tx_type"],
                    description=row["description"],
                    merchant=row["merchant"]
                )
                for row in rows
            ]

    def fetch_budgets(self) -> Dict[str, float]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT category, monthly_limit FROM budgets").fetchall()
            return {row["category"]: row["monthly_limit"] for row in rows}


class AnalyticsEngine:
    """Performs statistical anomaly detection, burn-rate forecasting, and ledger aggregations."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def generate_cashflow_summary(self, days: int = 30) -> Dict[str, float]:
        transactions = self.db.fetch_transactions(days=days)
        total_income = sum(t.amount for t in transactions if t.tx_type == "INCOME")
        total_expense = sum(t.amount for t in transactions if t.tx_type == "EXPENSE")
        net_savings = total_income - total_expense
        savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0.0

        return {
            "period_days": days,
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "net_savings": round(net_savings, 2),
            "savings_rate_pct": round(savings_rate, 2)
        }

    def detect_spending_anomalies(self, z_threshold: float = 1.8) -> List[Dict[str, object]]:
        """
        Uses Z-score statistical analysis per category to flag unusually high expenses.
        Formula: Z = (X - mean) / std_dev
        """
        expenses = self.db.fetch_transactions(tx_type="EXPENSE")
        by_category: Dict[str, List[Transaction]] = {}
        for tx in expenses:
            by_category.setdefault(tx.category, []).append(tx)

        anomalies = []
        for category, tx_list in by_category.items():
            if len(tx_list) < 4:
                continue  # Minimum sample size needed for meaningful variance

            amounts = [t.amount for t in tx_list]
            mean = sum(amounts) / len(amounts)
            variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
            std_dev = math.sqrt(variance)

            if std_dev == 0:
                continue

            for tx in tx_list:
                z_score = (tx.amount - mean) / std_dev
                if z_score >= z_threshold:
                    anomalies.append({
                        "transaction_id": tx.id,
                        "date": tx.timestamp,
                        "category": category,
                        "merchant": tx.merchant,
                        "amount": tx.amount,
                        "category_mean": round(mean, 2),
                        "z_score": round(z_score, 2)
                    })

        return sorted(anomalies, key=lambda x: float(str(x["z_score"])), reverse=True)

    def evaluate_budget_burn_rate(self) -> List[Dict[str, object]]:
        """Calculates current monthly spend against category budgets and projects end-of-month burn."""
        budgets = self.db.fetch_budgets()
        now = datetime.now()
        month_start = now.strftime("%Y-%m-01")
        days_elapsed = max(now.day, 1)
        days_in_month = 30

        expenses = [
            t for t in self.db.fetch_transactions(tx_type="EXPENSE")
            if t.timestamp >= month_start
        ]

        spend_by_cat: Dict[str, float] = {}
        for tx in expenses:
            spend_by_cat[tx.category] = spend_by_cat.get(tx.category, 0.0) + tx.amount

        report = []
        for category, limit in budgets.items():
            current_spend = spend_by_cat.get(category, 0.0)
            daily_burn = current_spend / days_elapsed
            projected_spend = daily_burn * days_in_month
            utilization_pct = (current_spend / limit) * 100

            status = "HEALTHY"
            if utilization_pct >= 100:
                status = "EXCEEDED"
            elif projected_spend > limit:
                status = "AT_RISK"

            report.append({
                "category": category,
                "budget_limit": round(limit, 2),
                "current_spend": round(current_spend, 2),
                "utilization_pct": round(utilization_pct, 1),
                "projected_month_end": round(projected_spend, 2),
                "status": status
            })

        return report


def seed_demo_data(db: DatabaseManager) -> None:
    """Populates the database with realistic sample data for immediate evaluation."""
    if db.fetch_transactions():
        logger.info("Database already contains records. Skipping seed.")
        return

    logger.info("Seeding realistic financial dataset...")
    today = datetime.now()

    sample_budgets = {
        "groceries": 12000.0,
        "cloud_infra": 5000.0,
        "dining": 4500.0,
        "utilities": 3500.0
    }
    for cat, limit in sample_budgets.items():
        db.set_budget(cat, limit)

    sample_txs = [
        (-25, "salary", 85000.0, "INCOME", "Monthly retainer payout", "Stripe Connect"),
        (-22, "groceries", 1450.0, "EXPENSE", "Weekly fresh produce", "Reliance Fresh"),
        (-19, "groceries", 1620.0, "EXPENSE", "Household supplies", "DMart"),
        (-15, "groceries", 1380.0, "EXPENSE", "Dairy and staples", "Blinkit"),
        (-10, "groceries", 1510.0, "EXPENSE", "Weekly groceries", "BigBasket"),
        (-3, "groceries", 6800.0, "EXPENSE", "Bulk party catering order", "Wholesale Mart"),  # Anomaly
        (-20, "cloud_infra", 850.0, "EXPENSE", "VPS hosting droplet", "DigitalOcean"),
        (-14, "cloud_infra", 920.0, "EXPENSE", "LLM API token usage", "Anthropic"),
        (-8, "cloud_infra", 880.0, "EXPENSE", "Storage bucket bandwidth", "AWS S3"),
        (-4, "cloud_infra", 910.0, "EXPENSE", "Domain renewal", "Cloudflare"),
        (-1, "cloud_infra", 4950.0, "EXPENSE", "Unthrottled GPU instance spike", "AWS EC2"),  # Anomaly
        (-18, "dining", 650.0, "EXPENSE", "Team coffee meetup", "Third Wave Coffee"),
        (-12, "dining", 820.0, "EXPENSE", "Dinner order", "Zomato"),
        (-6, "dining", 740.0, "EXPENSE", "Lunch combo", "Swiggy"),
        (-2, "utilities", 2100.0, "EXPENSE", "Fiber broadband bill", "Airtel Xstream"),
    ]

    for offset, cat, amt, t_type, desc, merch in sample_txs:
        tx_date = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        db.add_transaction(Transaction(
            id=None,
            timestamp=tx_date,
            category=cat,
            amount=amt,
            tx_type=t_type,
            description=desc,
            merchant=merch
        ))
    logger.info("Successfully seeded 15 sample transactions and 4 category budgets.")


def export_report(engine: AnalyticsEngine, output_path: str) -> None:
    """Exports a comprehensive JSON audit report."""
    payload = {
        "generated_at": datetime.now().isoformat(),
        "cashflow_30d": engine.generate_cashflow_summary(days=30),
        "budget_burn_analysis": engine.evaluate_budget_burn_rate(),
        "anomalies_detected": engine.detect_spending_anomalies()
    }
    Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(f"Audit report exported to {output_path}")


def print_dashboard(engine: AnalyticsEngine) -> None:
    summary = engine.generate_cashflow_summary(30)
    print("\n" + "=" * 60)
    print(" FINPULSE ANALYTICS ENGINE - 30 DAY EXECUTIVE SUMMARY")
    print("=" * 60)
    print(f" Total Income   : INR {summary['total_income']:,.2f}")
    print(f" Total Expense  : INR {summary['total_expense']:,.2f}")
    print(f" Net Savings    : INR {summary['net_savings']:,.2f} ({summary['savings_rate_pct']}%)")

    print("\n" + "-" * 60)
    print(" BUDGET BURN-RATE & FORECASTING")
    print("-" * 60)
    print(f"{'Category':<15} | {'Spent':>10} | {'Budget':>10} | {'Projected':>10} | {'Status'}")
    print("-" * 60)
    for b in engine.evaluate_budget_burn_rate():
        print(
            f"{str(b['category']):<15} | "
            f"{float(str(b['current_spend'])):>10.2f} | "
            f"{float(str(b['budget_limit'])):>10.2f} | "
            f"{float(str(b['projected_month_end'])):>10.2f} | "
            f"{b['status']}"
        )

    print("\n" + "-" * 60)
    print(" STATISTICAL ANOMALIES (Z-SCORE SPIKE DETECTION)")
    print("-" * 60)
    anomalies = engine.detect_spending_anomalies()
    if not anomalies:
        print(" No anomalous spending spikes detected.")
    else:
        for a in anomalies:
            print(
                f" [ALERT] {a['date']} | {str(a['category']).upper()} | "
                f"INR {a['amount']} at {a['merchant']} "
                f"(Avg: {a['category_mean']}, Z-Score: +{a['z_score']})"
            )
    print("=" * 60 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FinPulse CLI Financial & Anomaly Analytics Engine")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    subparsers.add_parser("seed", help="Populate database with realistic demo transactions")
    subparsers.add_parser("dashboard", help="Display terminal analytics dashboard")

    add_p = subparsers.add_parser("add", help="Record a new transaction")
    add_p.add_argument("--type", choices=["INCOME", "EXPENSE"], required=True)
    add_p.add_argument("--category", required=True, help="Transaction category")
    add_p.add_argument("--amount", type=float, required=True, help="Transaction amount")
    add_p.add_argument("--merchant", default="Unknown", help="Merchant or counterparty")
    add_p.add_argument("--desc", default="", help="Short description")

    exp_p = subparsers.add_parser("export", help="Export JSON analytics audit report")
    exp_p.add_argument("--out", default="finpulse_audit.json", help="Output JSON filepath")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    db = DatabaseManager()
    engine = AnalyticsEngine(db)

    if args.command == "seed":
        seed_demo_data(db)
        print_dashboard(engine)
    elif args.command == "add":
        tx = Transaction(
            id=None,
            timestamp=datetime.now().strftime("%Y-%m-%d"),
            category=args.category,
            amount=args.amount,
            tx_type=args.type,
            description=args.desc,
            merchant=args.merchant
        )
        tx_id = db.add_transaction(tx)
        logger.info(f"Recorded transaction #{tx_id} successfully.")
    elif args.command == "export":
        export_report(engine, args.out)
    elif args.command == "dashboard":
        print_dashboard(engine)
    else:
        # Default behavior: seed if empty and display dashboard
        seed_demo_data(db)
        print_dashboard(engine)


if __name__ == "__main__":
    main()
    
