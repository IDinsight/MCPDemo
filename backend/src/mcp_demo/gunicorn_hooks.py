"""This module contains the gunicorn hooks configuration for the application."""

# Third Party Library
from gunicorn.arbiter import Arbiter
from prometheus_client import multiprocess
from uvicorn_worker import UvicornWorker


class Worker(UvicornWorker):
    """Custom worker class to allow `root_path` to be passed to Uvicorn."""


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
