"""Entry point for the sample package."""

from sample_pkg.core import Calculator


def main():
    """Run the sample app."""
    calc = Calculator()
    print(calc.compute(2, 3))


if __name__ == "__main__":
    main()
