import argparse

from . import report, store


def main(argv=None):
    p = argparse.ArgumentParser(prog="inv", description="A tiny inventory.")
    p.add_argument("--db", default="inventory.csv", help="CSV file (default: inventory.csv)")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="show the inventory")
    ls.add_argument("--format", choices=["text", "json"], default="text", help="output format (default: text)")
    ls.add_argument("--below", type=int, metavar="N", help="only items whose qty is below N")
    add = sub.add_parser("add", help="add or restock an item")
    add.add_argument("name")
    add.add_argument("qty", type=int)
    add.add_argument("price", type=float, nargs="?")
    a = p.parse_args(argv)

    items = store.load(a.db)
    if a.cmd == "list":
        if a.below is not None:
            items = [it for it in items if it.qty < a.below]
        print(report.as_json(items) if a.format == "json" else report.table(items))
    elif a.cmd == "add":
        found = next((it for it in items if it.name == a.name), None)
        if found:
            found.qty += a.qty
            if a.price is not None:
                found.price = a.price
        elif a.price is None:
            p.error(f"new item {a.name!r} needs a price")
        else:
            items.append(store.Item(a.name, a.qty, a.price))
        store.save(a.db, items)
    return 0
