"""In-memory MIDI output that records every message for protocol tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeMidiOut:
    """Stand-in for ``mido.open_output(...)``'s returned object.

    Records SysEx data tuples and note_on triples separately so tests can
    assert exact byte streams without depending on mido's repr."""

    sysex: list[tuple[int, ...]] = field(default_factory=list)
    notes: list[tuple[int, int, int]] = field(default_factory=list)
    closed: bool = False

    def send(self, msg: Any) -> None:
        if getattr(msg, "type", None) == "sysex":
            self.sysex.append(tuple(msg.data))
        elif getattr(msg, "type", None) == "note_on":
            self.notes.append((msg.channel, msg.note, msg.velocity))
        else:
            raise AssertionError(f"unexpected midi message type: {msg}")

    def close(self) -> None:
        self.closed = True

    def reset(self) -> None:
        self.sysex.clear()
        self.notes.clear()
