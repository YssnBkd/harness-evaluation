import shop.pricing as pricing


def grand_total(orders):
    return sum(pricing.compute_order_total(o) for o in orders)
