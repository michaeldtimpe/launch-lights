"""Port detection covers MK1, MK3, DAW/DIN exclusion, and explicit-port
classification."""
from __future__ import annotations

import pytest

from launch_lights.device import midi_io


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    # Ensure each test runs against a controlled port list.
    yield


def _patch_outputs(monkeypatch, names):
    monkeypatch.setattr(midi_io, "list_output_ports", lambda: list(names))


def test_detect_returns_mk1_when_only_mk1_present(monkeypatch):
    _patch_outputs(monkeypatch, ["Launchpad Pro Standalone Port"])
    assert midi_io.detect_launchpad() == (
        "Launchpad Pro Standalone Port",
        "mk1",
    )


def test_detect_returns_mk3_when_only_mk3_present(monkeypatch):
    _patch_outputs(
        monkeypatch,
        [
            "Launchpad Pro MK3 LPProMK3 DAW",
            "Launchpad Pro MK3 LPProMK3 DIN",
            "Launchpad Pro MK3 LPProMK3 MIDI",
        ],
    )
    assert midi_io.detect_launchpad() == (
        "Launchpad Pro MK3 LPProMK3 MIDI",
        "mk3",
    )


def test_mk3_detection_skips_daw_and_din(monkeypatch):
    # DAW listed first — must NOT be selected.
    _patch_outputs(
        monkeypatch,
        [
            "Launchpad Pro MK3 LPProMK3 DAW",
            "Launchpad Pro MK3 LPProMK3 MIDI",
        ],
    )
    port = midi_io.find_launchpad_pro_mk3_output()
    assert port == "Launchpad Pro MK3 LPProMK3 MIDI"


def test_detect_prefers_mk1_when_both_present(monkeypatch):
    _patch_outputs(
        monkeypatch,
        [
            "Launchpad Pro MK3 LPProMK3 MIDI",
            "Launchpad Pro Standalone Port",
        ],
    )
    found = midi_io.detect_launchpad()
    assert found is not None
    _, model = found
    assert model == "mk1"


def test_detect_returns_none_when_no_device(monkeypatch):
    _patch_outputs(monkeypatch, ["IAC Driver Bus 1"])
    assert midi_io.detect_launchpad() is None


def test_classify_port_handles_mk3_string():
    assert midi_io.classify_port("Launchpad Pro MK3 LPProMK3 MIDI") == "mk3"
    assert midi_io.classify_port("LPProMK3 MIDI") == "mk3"


def test_classify_port_defaults_to_mk1_for_unknown():
    assert midi_io.classify_port("Launchpad Pro Standalone Port") == "mk1"
    assert midi_io.classify_port("some random midi port") == "mk1"
