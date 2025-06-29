"""This module contains the gunicorn hooks configuration for the application."""

# Third Party Library
from gunicorn.arbiter import Arbiter
from prometheus_client import multiprocess

# Package Library
from mcp_demo.entries.fastapi_app import Worker


def child_exit(server: Arbiter, worker: Worker) -> None:  # pylint: disable=W0613
    """Multiprocess mode requires to mark the process as dead.

    Parameters
    ----------
    server
        The arbiter instance.
    worker
        The worker instance.
    """

    multiprocess.mark_process_dead(worker.pid)
