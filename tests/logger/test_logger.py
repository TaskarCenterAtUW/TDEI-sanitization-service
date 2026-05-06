import unittest

from src.logger import Logger


class TestLogger(unittest.TestCase):
    def test_info_does_not_raise(self):
        Logger.info("test info message")

    def test_error_does_not_raise(self):
        Logger.error("test error message")

    def test_warning_does_not_raise(self):
        Logger.warning("test warning message")

    def test_debug_does_not_raise(self):
        Logger.debug("test debug message")

    def test_configure_logger_returns_same_instance_on_repeated_calls(self):
        logger1 = Logger.configure_logger()
        logger2 = Logger.configure_logger()
        self.assertIs(logger1, logger2)


if __name__ == "__main__":
    unittest.main()
