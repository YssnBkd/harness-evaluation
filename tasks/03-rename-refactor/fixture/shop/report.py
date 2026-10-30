import shop.pricing as pricing


def grand_total(orders):
    return sum(pricing.calc_total(o) for o in orders)
