import json


def table(items):
    """Items as an aligned text table, sorted by name, with a total line."""
    rows = [(it.name, str(it.qty), f"{it.price:.2f}") for it in sorted(items, key=lambda it: it.name)]
    head = ("name", "qty", "price")
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(3)]
    fmt = lambda r: f"{r[0]:<{widths[0]}}  {r[1]:>{widths[1]}}  {r[2]:>{widths[2]}}"
    total = sum(it.value for it in items)
    return "\n".join([fmt(head), *map(fmt, rows), f"total value: {total:.2f}"])


def as_json(items):
    """Items as a JSON array, sorted by name like the table."""
    return json.dumps([{"name": it.name, "qty": it.qty, "price": it.price}
                       for it in sorted(items, key=lambda it: it.name)], indent=2)
