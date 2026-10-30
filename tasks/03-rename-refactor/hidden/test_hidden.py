import unittest
from shop.pricing import compute_order_total
from shop.invoice import invoice_line
from shop.report import grand_total


class T(unittest.TestCase):
    def test_tax(self): self.assertAlmostEqual(compute_order_total([(10.0, 2)], tax_rate=0.1), 22.0)
    def test_default(self): self.assertEqual(compute_order_total([(2.0, 3)]), 6.0)
    def test_callers(self):
        self.assertEqual(invoice_line([(1.0, 2)]), "TOTAL: 2.00"); self.assertEqual(grand_total([[(1.0, 1)], [(2.0, 2)]]), 5.0)


if __name__ == "__main__":
    unittest.main()
