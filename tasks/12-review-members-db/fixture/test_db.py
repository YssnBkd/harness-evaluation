import unittest

from members import db


class Users(unittest.TestCase):
    def test_add_and_get(self):
        conn = db.connect()
        uid = db.add_user(conn, "Ada", "ada@example.com")
        self.assertEqual(db.get_user(conn, uid), {"id": uid, "name": "Ada", "email": "ada@example.com"})
        self.assertIsNone(db.get_user(conn, uid + 1))


if __name__ == "__main__":
    unittest.main()
