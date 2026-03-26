"""
Logging configuration for PitBox Controller.
Safe for PyInstaller EXE environment - configures before uvicorn import.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, debug: bool = False):
    """
    Configure logging to write to rotating file.
    
    Args:
        log_dir: Directory for log files
        debug: Enable DEBUG level logging
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "controller.log"
    
    # Create rotating file handler (10MB max, 3 backups)
    handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=3,
        encoding='utf-8'
    )
    
    # Set format
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    
    # Suppress verbose uvicorn access logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    
    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized: {log_file}")
    if debug:
        logger.info("Debug logging enabled")
