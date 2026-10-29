"""Connect Hermes' native Kanban steering to Claude's streaming user input."""

from __future__ import annotations

import json
from typing import BinaryIO


class ClaudeCommentInput:
    def __init__(self, stream: BinaryIO, session_id: str):
        self.stream = stream
        self.session_id = session_id or "default"
        self.pending_turns = 0
        self.closed = False

    def steer(self, text: str) -> bool:
        """The interface ``inject_new_comments_from_env`` calls; no comment rules live here."""
        if self.closed:
            return False
        message = {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "session_id": self.session_id,
        }
        try:
            self.stream.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            self.stream.flush()
        except (BrokenPipeError, OSError, ValueError):
            self.close()
            return False
        self.pending_turns += 1
        return True

    def poll(self) -> bool:
        from tools.kanban_tools import inject_new_comments_from_env

        return inject_new_comments_from_env(self)

    def finish_turn(self, event: dict) -> None:
        if self.closed or event.get("type") != "result":
            return
        self.pending_turns = max(0, self.pending_turns - 1)
        if event.get("is_error") or event.get("subtype") != "success":
            self.close()
            return
        self.poll()
        if self.pending_turns == 0:
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.stream.close()
        except (BrokenPipeError, OSError):
            pass
