import logging


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
