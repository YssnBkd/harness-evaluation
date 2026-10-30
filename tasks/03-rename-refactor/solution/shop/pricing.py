def compute_order_total(items, tax_rate=0.0):
    """Return the sum of price * qty for (price, qty) pairs, times (1 + tax_rate). See also README."""
    return sum(p * q for p, q in items) * (1 + tax_rate)
