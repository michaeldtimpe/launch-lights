"""mido wrapper + Launchpad Pro port discovery."""
from __future__ import annotations

import logging
from typing import Protocol

import mido

log = logging.getLogger(__name__)


PORT_NAME_HINTS_STANDALONE: tuple[str, ...] = (
    "Launchpad Pro Standalone",
    "Standalone Port",
)
PORT_NAME_HINTS_LIVE: tuple[str, ...] = ("Launchpad Pro Live",)


class MidiOut(Protocol):
    def send(self, msg: "mido.Message") -> None: ...
    def close(self) -> None: ...


class MidiIn(Protocol):
    def close(self) -> None: ...


def list_output_ports() -> list[str]:
    return list(mido.get_output_names())


def list_input_ports() -> list[str]:
    return list(mido.get_input_names())


def _find_match(ports: list[str], hints: tuple[str, ...]) -> str | None:
    for port in ports:
        for hint in hints:
            if hint.lower() in port.lower():
                return port
    return None


def find_launchpad_pro_standalone_output() -> str | None:
    return _find_match(list_output_ports(), PORT_NAME_HINTS_STANDALONE)


def find_launchpad_pro_standalone_input() -> str | None:
    return _find_match(list_input_ports(), PORT_NAME_HINTS_STANDALONE)


def open_output(port_name: str) -> MidiOut:
    return mido.open_output(port_name)
