import unittest

from src.main import ping, root


class TestMain(unittest.TestCase):
    def test_root_health(self):
        self.assertEqual(root(), "I'm healthy !!")

    def test_ping_health(self):
        self.assertEqual(ping(), "I'm healthy !!")


if __name__ == "__main__":
    unittest.main()
