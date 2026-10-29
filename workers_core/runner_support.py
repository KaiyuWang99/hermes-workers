"""Signal handling shared by the plugin-owned worker runners."""

from __future__ import annotations

import signal
from typing import Any, Callable


def terminate_child(child: Any) -> None:
    if child is not None and child.poll() is None:
        child.terminate()


def install_terminate_handlers(get_child: Callable[[], Any]) -> None:
    """Forward SIGTERM/SIGINT to the runner's active CLI child."""

    def _terminate(_signum, _frame) -> None:
        terminate_child(get_child())

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
