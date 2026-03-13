import structlog
import logging
from pathlib import Path


def setup_logger(service_name: str, level: str = "INFO", log_dir: str = "logs") -> None:
    """Configure structlog for the service with console + JSON file output."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Ensure log directory exists
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    log_file = log_path / f"{service_name}.log"

    # Open file handle for JSON logging
    file_handle = open(log_file, "a")

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            _TeeRenderer(
                console=structlog.dev.ConsoleRenderer(),
                file=structlog.processors.JSONRenderer(),
                file_handle=file_handle,
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bind service name to all loggers
    structlog.contextvars.bind_contextvars(service=service_name)


class _TeeRenderer:
    """Render to console (colored) and write JSON to file."""

    def __init__(self, console, file, file_handle):
        self._console = console
        self._file = file
        self._file_handle = file_handle

    def __call__(self, logger, method_name, event_dict):
        # Write JSON line to file (copy dict so renderers don't interfere)
        json_line = self._file(logger, method_name, dict(event_dict))
        self._file_handle.write(json_line + "\n")
        self._file_handle.flush()

        # Return console-rendered string for stdout
        return self._console(logger, method_name, event_dict)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
