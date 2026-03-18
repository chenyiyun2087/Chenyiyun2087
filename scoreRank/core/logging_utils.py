from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock


_CONFIG_LOCK = Lock()
_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_score_rank_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    with _CONFIG_LOCK:
        if _CONFIGURED:
            return

        log_dir = Path(__file__).resolve().parents[2] / "logs" / "scoreRank"
        log_dir.mkdir(parents=True, exist_ok=True)

        formatter = JsonFormatter()
        root_logger = logging.getLogger("scoreRank")
        root_logger.setLevel(level)
        root_logger.propagate = False

        info_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        info_handler.setLevel(logging.INFO)
        info_handler.setFormatter(formatter)

        error_handler = RotatingFileHandler(
            log_dir / "error.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)

        root_logger.handlers.clear()
        root_logger.addHandler(info_handler)
        root_logger.addHandler(error_handler)
        root_logger.addHandler(stream_handler)

        _CONFIGURED = True


def get_score_rank_logger(name: str) -> logging.Logger:
    configure_score_rank_logging()
    if name.startswith("scoreRank"):
        return logging.getLogger(name)
    return logging.getLogger(f"scoreRank.{name}")
