"""간단한 콘솔 + 파일 로거."""
import logging
import os
from datetime import datetime


def get_logger(name: str = "kis", log_dir: str = "logs") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    try:
        os.makedirs(log_dir, exist_ok=True)
        fname = os.path.join(log_dir, f"{datetime.now():%Y%m%d}.log")
        fh = logging.FileHandler(fname, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    return logger
