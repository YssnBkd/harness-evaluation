import csv
from dataclasses import dataclass
from pathlib import Path

FIELDS = ["name", "qty", "price"]


@dataclass
class Item:
    name: str
    qty: int
    price: float

    @property
    def value(self):
        return self.qty * self.price


def load(path):
    if not Path(path).exists():
        return []
    with open(path, newline="") as f:
        return [Item(r["name"], int(r["qty"]), float(r["price"])) for r in csv.DictReader(f)]


def save(path, items):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for it in items:
            w.writerow({"name": it.name, "qty": it.qty, "price": it.price})
