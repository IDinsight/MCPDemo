"""This module contains the gunicorn hooks configuration for the application."""

# Standard Library
import os

# Third Party Library
from gunicorn.arbiter import Arbiter
from prometheus_client import multiprocess
from uvicorn_worker import UvicornWorker


class Worker(UvicornWorker):
    """Custom worker class to allow `root_path` to be passed to Uvicorn."""

    CONFIG_KWARGS = {"root_path": os.getenv("API_BACKEND_ROOT", "")}


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
