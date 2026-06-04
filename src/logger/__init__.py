import logging
import time
from contextlib import contextmanager


class Logger:
    logger = None

    @staticmethod
    def configure_logger(level=logging.DEBUG):
        if Logger.logger is None:
            logging.basicConfig(
                format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
                level=level,
            )
            Logger.logger = logging.getLogger("OSW SANITIZATION SERVICE")
            Logger.logger.setLevel(level)
        return Logger.logger

    @staticmethod
    def info(message):
        Logger.configure_logger().info(message, stacklevel=2)

    @staticmethod
    def error(message):
        Logger.configure_logger().error(message, stacklevel=2)

    @staticmethod
    def warning(message):
        Logger.configure_logger().warning(message, stacklevel=2)

    @staticmethod
    def debug(message):
        Logger.configure_logger(level=logging.DEBUG).debug(message, stacklevel=2)

    @staticmethod
    @contextmanager
    def timer(label: str):
        """
        Context manager that logs when a block of work starts and how long it
        took. Logs an error and re-raises if the block fails, including the
        elapsed time before failure.
        """
        Logger.configure_logger().info(f"[TIMER] {label} - in progress...", stacklevel=3)
        start = time.perf_counter()
        try:
            yield
        except Exception:
            elapsed = time.perf_counter() - start
            Logger.configure_logger().error(
                f"[TIMER] {label} - FAILED after {elapsed:.3f}s", stacklevel=3
            )
            raise
        else:
            elapsed = time.perf_counter() - start
            Logger.configure_logger().info(
                f"[TIMER] {label} - completed in {elapsed:.3f}s", stacklevel=3
            )
