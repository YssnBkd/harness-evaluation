import json, os, subprocess, sys, tempfile, unittest


def inv(db, *args):
    r = subprocess.run([sys.executable, "-m", "inv", "--db", db, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


class NewOptions(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "inv.csv")
        for args in (("nuts", "15", "0.20"), ("bolts", "100", "0.05"), ("gears", "3", "12.5")):
            inv(self.db, "add", *args)

    def test_json(self):
        code, out = inv(self.db, "list", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"name": "bolts", "qty": 100, "price": 0.05},
                                           {"name": "gears", "qty": 3, "price": 12.5},
                                           {"name": "nuts", "qty": 15, "price": 0.2}])

    def test_below_both_formats(self):
        code, out = inv(self.db, "list", "--below", "20", "--format", "json")
        self.assertEqual([i["name"] for i in json.loads(out)], ["gears", "nuts"])
        code, out = inv(self.db, "list", "--below", "20")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["name   qty  price", "gears    3  12.50", "nuts    15   0.20",
                                            "total value: 40.50"])

    def test_text_is_default_and_unchanged(self):
        self.assertEqual(inv(self.db, "list"), inv(self.db, "list", "--format", "text"))
        self.assertEqual(inv(self.db, "list")[1].splitlines()[-1], "total value: 45.50")

    def test_bad_values_are_usage_errors(self):
        self.assertEqual(inv(self.db, "list", "--format", "xml")[0], 2)
        self.assertEqual(inv(self.db, "list", "--below", "many")[0], 2)

    def test_readme_documents_options(self):
        text = open("README.md").read()
        self.assertIn("--format", text)
        self.assertIn("--below", text)


if __name__ == "__main__":
    unittest.main()
