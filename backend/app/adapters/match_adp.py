"""Fuzzy text similarity (the only importer of rapidfuzz)."""

import re

from rapidfuzz import fuzz, utils


class MatchAdapterClass:
    """Score how alike two short texts are."""

    def ratio(self, left: str, right: str) -> float:
        """Return the 0-100 token-set ratio after lowercasing and stripping punctuation."""
        return fuzz.token_set_ratio(left, right, processor=utils.default_process)

    def coverage(self, left: str, right: str) -> float:
        """
        Return the 0-100 token-sort ratio: how much of both texts the other covers, unlike ratio().

        ratio() gives 100 when one text's words all sit inside the other ("Monitor" in "Monitor arm (dual, gas
        spring)"); coverage() stays low there (41), because most of the longer text is not matched.
        """
        return fuzz.token_sort_ratio(left, right, processor=utils.default_process)

    def same_item(self, left: str, right: str) -> bool:
        """
        Return whether two descriptions name the same item: nearly the same text and exactly the same numbers.

        Stricter than ratio(), which forgives extra or missing words: "Freight to Mumbai" and "Freight to Delhi" share
        most words but are different services, and "laptop (14 inch)" and "laptop (15 inch)" differ only in a number.
        """
        digits = re.findall(r"\d+", left) == re.findall(r"\d+", right)
        return digits and fuzz.ratio(left, right, processor=utils.default_process) >= 95
