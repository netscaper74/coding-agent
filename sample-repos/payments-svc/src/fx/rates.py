"""Currency conversion with explicit half-even rounding (SCS-4.2)."""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from ..ledger.models import MINOR_UNITS


class RateProvider:
    def __init__(self, rates: dict[tuple[str, str], Decimal]):
        self._rates = rates

    def rate(self, source: str, target: str) -> Decimal:
        if source == target:
            return Decimal(1)
        try:
            return self._rates[(source, target)]
        except KeyError:
            raise LookupError(f"no FX rate {source}->{target}") from None


def convert(amount: Decimal, source: str, target: str, provider: RateProvider) -> Decimal:
    """Converts and rounds half-even to the target currency's minor units."""
    quantum = Decimal(1).scaleb(-MINOR_UNITS[target])
    return (amount * provider.rate(source, target)).quantize(quantum, rounding=ROUND_HALF_EVEN)
