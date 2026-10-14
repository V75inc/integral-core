"""Console verbosity for database instrumentation, independent of metrics."""

import logging


def configure_db_operation_logging(level: str) -> None:
    """Set the jvspatial database logger threshold; it logs failures only."""
    logging.getLogger("jvspatial.db.observable").setLevel(level)
