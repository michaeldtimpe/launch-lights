"""Drive the whole pipeline (sans MIDI hardware) and check it emits sane bytes."""
from __future__ import annotations

import numpy as np

from launch_lights.device.launchpad_pro import HEADER, LaunchpadProOut
from launch_lights.engine.renderer import Renderer
from launch_lights.video.patterns import bars, orientation
from launch_lights.video.pipeline import (
    apply_gamma_brightness,
    bgr_to_rgb,
    fit_and_downsample,
    quantize_to_6bit,
    to_frame,
)
from tests.fixtures.fake_midi import FakeMidiOut


def test_test_pattern_to_device_emits_full_frame():
    fake = FakeMidiOut()
    dev = LaunchpadProOut(port_name="<fake>", _port=fake)
    renderer = Renderer(mode="rgb", prefer_full_frame=True)

    frame = bars()
    plan = renderer.plan(frame)
    dev.execute(plan)
    renderer.commit(plan)

    assert len(fake.sysex) == 1
    payload = fake.sysex[0]
    assert payload[:7] == (*HEADER, 0x0F, 0x01)
    # Body is 192 bytes
    body = payload[7:]
    assert len(body) == 192
    # First triple (sent first = bottom row, col 0) should be red, the first
    # bar's color, since 'bars' is constant across rows (only varies by col).
    assert body[0:3] == (63, 0, 0)


def test_video_frame_to_device_round_trip():
    """Simulate a webcam frame end-to-end."""
    fake = FakeMidiOut()
    dev = LaunchpadProOut(port_name="<fake>", _port=fake)
    renderer = Renderer(mode="rgb", prefer_full_frame=True)

    # Synthesize a 1080p "webcam" frame: solid red BGR
    bgr = np.zeros((1080, 1920, 3), dtype=np.uint8)
    bgr[:, :] = (0, 0, 255)  # red in BGR
    rgb = bgr_to_rgb(bgr)
    small = fit_and_downsample(rgb, "crop")
    small = apply_gamma_brightness(small, gamma=2.2, brightness=1.0)
    small = quantize_to_6bit(small)
    frame = to_frame(small)

    plan = renderer.plan(frame)
    dev.execute(plan)

    payload = fake.sysex[0]
    body = payload[7:]
    # All pixels should be roughly the same red value (gamma-corrected from 255)
    # 255 -> gamma 2.2 -> 255 -> /4 -> 63 (since x^2.2 at x=1 is 1)
    for i in range(0, 192, 3):
        r, g, b = body[i], body[i + 1], body[i + 2]
        assert r >= 60  # red dominant
        assert g <= 2
        assert b <= 2


def test_orientation_pattern_corners_in_expected_payload_slots():
    fake = FakeMidiOut()
    dev = LaunchpadProOut(port_name="<fake>", _port=fake)
    renderer = Renderer(mode="rgb", prefer_full_frame=True)

    plan = renderer.plan(orientation())
    dev.execute(plan)

    body = fake.sysex[0][7:]
    # In bottom-up dump order:
    #   first triple = (7,0) BLUE  -> (0,0,63)
    #   last triple in last row sent (=top row) at col 7 = (0,7) GREEN
    #   first triple of last row = (0,0) RED
    #   last triple of first row = (7,7) WHITE
    assert body[0:3] == (0, 0, 63)        # (7,0) blue
    assert body[21:24] == (63, 63, 63)    # (7,7) white
    assert body[-24:-21] == (63, 0, 0)    # (0,0) red
    assert body[-3:] == (0, 63, 0)        # (0,7) green
