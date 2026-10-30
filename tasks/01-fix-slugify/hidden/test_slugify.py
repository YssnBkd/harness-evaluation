import unittest
from textutil import slugify


class T(unittest.TestCase):
    def test_cases(self):
        for src, want in [("Hello, World!", "hello-world"), ("  a   b  ", "a-b"), ("Crème brûlée", "creme-brulee"),
                          ("--x--", "x"), ("A_B", "a-b"), ("", ""), ("été 2024", "ete-2024"), ("!!!", "")]:
            self.assertEqual(slugify(src), want, src)


if __name__ == "__main__":
    unittest.main()
