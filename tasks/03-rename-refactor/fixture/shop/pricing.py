def calc_total(items):
    """Return the sum of price * qty for (price, qty) pairs. See also calc_total in README."""
    return sum(p * q for p, q in items)
