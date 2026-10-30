import os, subprocess, sys, tempfile, unittest


def inv(db, *args):
    r = subprocess.run([sys.executable, "-m", "inv", "--db", db, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


class Inventory(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "inv.csv")

    def test_add_and_list(self):
        inv(self.db, "add", "nuts", "10", "0.20")
        inv(self.db, "add", "bolts", "100", "0.05")
        inv(self.db, "add", "nuts", "5")
        code, out = inv(self.db, "list")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["name   qty  price", "bolts  100   0.05", "nuts    15   0.20",
                                            "total value: 8.00"])

    def test_new_item_needs_price(self):
        code, _ = inv(self.db, "add", "washers", "3")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
