"""mido wrapper + Launchpad Pro port discovery (MK1 + MK3)."""
from __future__ import annotations

import logging
from typing import Literal, Protocol

import mido

log = logging.getLogger(__name__)


Model = Literal["mk1", "mk3"]


PORT_NAME_HINTS_STANDALONE: tuple[str, ...] = (
    "Launchpad Pro Standalone",
    "Standalone Port",
)
PORT_NAME_HINTS_LIVE: tuple[str, ...] = ("Launchpad Pro Live",)

# MK3 exposes three ports: "...LPProMK3 MIDI", "...DIN", "...DAW".
# Only MIDI accepts host-driven Programmer-mode lighting; DIN is for an
# external 5-pin device and DAW is reserved for Ableton-style host control.
PORT_NAME_HINTS_MK3: tuple[str, ...] = ("LPProMK3 MIDI",)
PORT_NAME_HINTS_MK3_EXCLUDE: tuple[str, ...] = ("DAW", "DIN")


class MidiOut(Protocol):
    def send(self, msg: "mido.Message") -> None: ...
    def close(self) -> None: ...


class MidiIn(Protocol):
    def close(self) -> None: ...


def list_output_ports() -> list[str]:
    return list(mido.get_output_names())


def list_input_ports() -> list[str]:
    return list(mido.get_input_names())


def _find_match(
    ports: list[str],
    hints: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> str | None:
    for port in ports:
        lower = port.lower()
        if any(ex.lower() in lower for ex in exclude):
            continue
        for hint in hints:
            if hint.lower() in lower:
                return port
    return None


def find_launchpad_pro_standalone_output() -> str | None:
    return _find_match(list_output_ports(), PORT_NAME_HINTS_STANDALONE)


def find_launchpad_pro_standalone_input() -> str | None:
    return _find_match(list_input_ports(), PORT_NAME_HINTS_STANDALONE)


def find_launchpad_pro_mk3_output() -> str | None:
    return _find_match(
        list_output_ports(), PORT_NAME_HINTS_MK3, exclude=PORT_NAME_HINTS_MK3_EXCLUDE
    )


def find_launchpad_pro_mk3_input() -> str | None:
    return _find_match(
        list_input_ports(), PORT_NAME_HINTS_MK3, exclude=PORT_NAME_HINTS_MK3_EXCLUDE
    )


def detect_launchpad() -> tuple[str, Model] | None:
    """Return (port_name, model) for the first device we find. MK1 wins ties
    because its port name (\"Standalone\") is unambiguous; MK3 names can
    appear with the same root but for the DAW/DIN sub-ports."""
    mk1 = find_launchpad_pro_standalone_output()
    if mk1:
        return mk1, "mk1"
    mk3 = find_launchpad_pro_mk3_output()
    if mk3:
        return mk3, "mk3"
    return None


def classify_port(port_name: str) -> Model:
    """Classify an explicit --port string. Defaults to mk1 (the legacy
    behaviour) when no MK3 marker is present."""
    lower = port_name.lower()
    if "lppromk3" in lower or "launchpad pro mk3" in lower:
        return "mk3"
    return "mk1"


def open_output(port_name: str) -> MidiOut:
    return mido.open_output(port_name)
