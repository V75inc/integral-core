"""Console verbosity for database instrumentation, independent of metrics."""

import logging


def configure_db_operation_logging(level: str) -> None:
    """Keep slow-query warnings while normal query timings are opt-in."""
    logging.getLogger("jvspatial.db.observable").setLevel(level)
