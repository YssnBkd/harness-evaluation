from shop.pricing import calc_total


def invoice_line(items):
    return f"TOTAL: {calc_total(items):.2f}"
