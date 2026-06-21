# scalp/run.py — CLI Entry Point for Scalping System
# ===================================================
# Launches producer + executor + (optional) dashboard.
#
# Usage:
#   python -m scalp.run producer    → Start data producer only
#   python -m scalp.run executor    → Start executor only
#   python -m scalp.run all         → Start producer + executor (2 processes)
#   python scalp/run.py producer    → Same as above

from __future__ import annotations

import logging
import multiprocessing
import os
import sys

_sys_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sys_root not in sys.path:
    sys.path.insert(0, _sys_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] run: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scalp.run")


def launch_producer() -> None:
    """Launch the data producer process."""
    from scalp.producer import run_producer
    logger.info("Starting PRODUCER process...")
    run_producer()


def launch_executor() -> None:
    """Launch the trade executor process."""
    from scalp.executor import run_executor
    logger.info("Starting EXECUTOR process...")
    run_executor()


def launch_all() -> None:
    """Launch producer + executor in separate processes."""
    logger.info("=" * 50)
    logger.info("  SCALPING SYSTEM v2.0 — STARTING ALL COMPONENTS")
    logger.info("  Producer: 1m OHLCV → SQLite (30s cycle)")
    logger.info("  Executor: signals + trailing stop + risk management")
    logger.info("  Shared alerts: Discord + Telegram (via AlertManager)")
    logger.info("  Kill switch: 5-level hierarchy (shared with swing)")
    logger.info("=" * 50)

    p1 = multiprocessing.Process(target=launch_producer, name="scalp-producer")
    p2 = multiprocessing.Process(target=launch_executor, name="scalp-executor")

    p1.start()
    logger.info("Producer PID: %d", p1.pid)
    p2.start()
    logger.info("Executor PID: %d", p2.pid)

    try:
        p1.join()
        p2.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        p1.terminate()
        p2.terminate()
        p1.join(timeout=5)
        p2.join(timeout=5)
        logger.info("All processes stopped.")


def print_help() -> None:
    print("""
Scalping System v2.0 — CLI

Usage:
  python -m scalp.run <command>
  python scalp/run.py <command>

Commands:
  producer     Start data producer (1m OHLCV → SQLite)
  executor     Start trade executor (signals + trailing stop + risk)
  all          Start producer + executor together
  help         Show this message
""")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] in ("help", "--help", "-h"):
        print_help()
    elif args[0] == "producer":
        launch_producer()
    elif args[0] == "executor":
        launch_executor()
    elif args[0] == "all":
        launch_all()
    else:
        print(f"Unknown command: {args[0]}")
        print_help()
