"""Agent entrypoint — runs the LangGraph monitoring loop continuously.

The loop:
  1. Invokes the full graph (monitor → classify → RAG → plan → fix → verify → report)
  2. Sleeps for LOOP_INTERVAL_S seconds
  3. Repeats indefinitely

LOOP_INTERVAL_S env var controls the sleep between cycles (default: 60s).

The RAG index is pre-built at startup so the first cycle does not incur
the model download latency.
"""

import logging
import os
import time

from agent.graph import graph
from agent.knowledge import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

LOOP_INTERVAL_S = int(os.environ.get("LOOP_INTERVAL_S", "60"))


def main() -> None:
    logger.info("Agent starting — building RAG index")
    try:
        build_index()
        logger.info("RAG index ready")
    except Exception as exc:
        logger.warning("RAG index build failed (%s): %s — will retry on first cycle", type(exc).__name__, exc)

    logger.info("Entering monitoring loop — interval %ds", LOOP_INTERVAL_S)
    while True:
        try:
            logger.info("--- cycle start ---")
            result = graph.invoke({})
            report = result.get("report", "")
            if report:
                logger.info("Cycle complete:\n%s", report)
            else:
                logger.info("Cycle complete — no actionable alerts")
        except Exception as exc:
            logger.error("Cycle failed (%s): %s", type(exc).__name__, exc, exc_info=True)

        logger.info("Sleeping %ds until next cycle", LOOP_INTERVAL_S)
        time.sleep(LOOP_INTERVAL_S)


if __name__ == "__main__":
    main()
