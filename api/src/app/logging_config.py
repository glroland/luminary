import logging

from .config import get_log_level


def configure_logging() -> None:
    logging.basicConfig(
        level=get_log_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
