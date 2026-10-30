import unittest
from shop.pricing import calc_total
from shop.invoice import invoice_line
from shop.report import grand_total


class T(unittest.TestCase):
    def test_total(self): self.assertEqual(calc_total([(2.0, 3), (1.5, 2)]), 9.0)
    def test_invoice(self): self.assertEqual(invoice_line([(1.0, 2)]), "TOTAL: 2.00")
    def test_grand(self): self.assertEqual(grand_total([[(1.0, 1)], [(2.0, 2)]]), 5.0)


if __name__ == "__main__":
    unittest.main()
