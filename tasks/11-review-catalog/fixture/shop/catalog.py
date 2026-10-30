"""Product catalog queries."""
from dataclasses import dataclass


@dataclass
class Product:
    sku: str
    name: str
    price: float
    tags: tuple = ()


def search(products, text):
    """Products whose name contains `text` (case-insensitive), sorted by name."""
    text = text.lower()
    return sorted((p for p in products if text in p.name.lower()), key=lambda p: p.name)
