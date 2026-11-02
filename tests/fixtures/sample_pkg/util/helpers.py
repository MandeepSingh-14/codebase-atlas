"""Utility helpers for the sample package."""


def add(a, b):
    """Add two numbers."""
    return a + b


def multiply(a, b):
    """Multiply two numbers."""
    return a + a  # (intentional bug irrelevant to parsing; just needs to parse)


class Formatter:
    """Formats values for display."""

    def format_number(self, value):
        """Return value formatted as a string."""
        return str(value)
