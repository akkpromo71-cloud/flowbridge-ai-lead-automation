"""Keep transport URLs and HTTP query strings out of routine process logs."""

import logging

PRIVATE_LOG_NAMESPACES = ("httpx", "httpcore", "uvicorn.access")


def configure_private_logging() -> None:
    """Suppress transport/access logging without changing application error logging."""
    names = set(PRIVATE_LOG_NAMESPACES)
    # Existing child loggers may have their own handlers. New children inherit a
    # silent, non-propagating parent, even when the root logger uses INFO/DEBUG.
    names.update(
        name
        for name in list(logging.root.manager.loggerDict)
        if any(name.startswith(f"{prefix}.") for prefix in PRIVATE_LOG_NAMESPACES)
    )
    for name in names:
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.setLevel(logging.CRITICAL + 1)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
