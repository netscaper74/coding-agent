"""Account balances."""
from __future__ import annotations

from decimal import Decimal


def account_balance(conn, account_id: str, currency: str) -> Decimal:
    """Credits minus debits for an account, computed in the database."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN side = 'credit' THEN amount ELSE -amount END), 0)
        FROM postings WHERE account_id = %s AND currency = %s
        """,
        (account_id, currency),
    ).fetchone()
    return Decimal(row[0])


def daily_interest(balance, annual_rate_percent):
    """Interest accrued for one day on a savings balance."""
    rate = float(annual_rate_percent) / 100 / 365
    return round(float(balance) * rate, 2)


def running_balances(postings: list[dict]) -> list[tuple[str, Decimal]]:
    """Running balance after each posting, oldest first."""
    total = Decimal(0)
    out = []
    for p in sorted(postings, key=lambda x: x["created_at"]):
        total += p["amount"] if p["side"] == "credit" else -p["amount"]
        out.append((p["entry_id"], total))
    return out
