import unittest
from lru import LRUCache


class T(unittest.TestCase):
    def test_basic(self):
        c = LRUCache(2); c.put("a", 1); c.put("b", 2)
        self.assertEqual(c.get("a"), 1)
        c.put("c", 3)
        self.assertNotIn("b", c); self.assertEqual(c.keys(), ["a", "c"]); self.assertEqual(len(c), 2)

    def test_update_and_default(self):
        c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.put("a", 9); c.put("c", 3)
        self.assertEqual(c.get("a"), 9); self.assertEqual(c.get("b", "x"), "x")

    def test_contains_no_recency(self):
        c = LRUCache(2); c.put("a", 1); c.put("b", 2)
        self.assertIn("a", c); c.put("c", 3)
        self.assertNotIn("a", c)

    def test_bad_capacity(self):
        with self.assertRaises(ValueError): LRUCache(0)


if __name__ == "__main__":
    unittest.main()
