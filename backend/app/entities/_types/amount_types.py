"""Amount formatting and the small arithmetic the validation, amount and decision stages share."""

from decimal import Decimal
from typing import Final


class AmountTypes:
    """Format amounts for sentences and finding data, and compare prices."""

    CENT: Final = Decimal("0.01")
    HUNDRED: Final = Decimal(100)

    @staticmethod
    def money(value: Decimal) -> str:
        """Return a grouped two-decimal amount for a sentence, for example 24,000.00."""
        return f"{value:,.2f}"

    @staticmethod
    def plain(value: Decimal) -> str:
        """Return a two-decimal amount for finding data, for example 24000.00."""
        return f"{value:.2f}"

    @staticmethod
    def net_price(price: Decimal | None, rate: Decimal | None, inclusive: bool) -> Decimal | None:
        """Bring a tax-inclusive unit price to the pre-tax basis; leave it alone otherwise."""
        if price is None or not inclusive or rate is None:
            return price

        return (price / (1 + rate / AmountTypes.HUNDRED)).quantize(AmountTypes.CENT)

    @staticmethod
    def percent_apart(left: Decimal, right: Decimal) -> Decimal:
        """Return how far left is from right, in percent of right."""
        return abs(left - right) / right * AmountTypes.HUNDRED if right else Decimal(0 if left == right else 100)
