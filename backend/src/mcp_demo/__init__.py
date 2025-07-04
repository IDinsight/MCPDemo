"""This module contains the FastAPI application for the backend."""

# Package Library
from mcp_demo.config import Settings
from mcp_demo.utils.logging_ import initialize_logger

# Only need to initialize loguru once for the entire backend!
logger = initialize_logger(logging_level=Settings.LOGGING_LOG_LEVEL)
