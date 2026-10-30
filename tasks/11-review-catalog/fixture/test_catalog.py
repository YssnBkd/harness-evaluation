import unittest

from shop.catalog import Product, search


class Search(unittest.TestCase):
    def test_case_insensitive_sorted(self):
        ps = [Product("2", "Blue Mug", 9.0), Product("1", "blue plate", 12.0), Product("3", "Red Mug", 9.0)]
        self.assertEqual([p.sku for p in search(ps, "BLUE")], ["2", "1"])


if __name__ == "__main__":
    unittest.main()
