from shop.pricing import compute_order_total


def invoice_line(items):
    return f"TOTAL: {compute_order_total(items):.2f}"
