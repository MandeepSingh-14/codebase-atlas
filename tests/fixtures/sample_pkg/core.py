"""Core logic for the sample package."""

from sample_pkg.util.helpers import add, Formatter


class Calculator:
    """A small calculator built on the helpers."""

    def __init__(self):
        self.formatter = Formatter()

    def compute(self, a, b):
        """Compute a result using the add helper."""
        total = add(a, b)
        return self.formatter.format_number(total)
