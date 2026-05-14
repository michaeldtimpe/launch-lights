"""launch-lights CLI."""
from __future__ import annotations

import atexit
import logging
import signal
import sys
import time
from typing import Optional

import click
import numpy as np
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.table import Table

from launch_lights.engine.plan import (
    NoOpPlan,
    PaletteDiffPlan,
    RGBDiffPlan,
    RGBFullFramePlan,
)

from launch_lights.device.launchpad_pro import LaunchpadProOut
from launch_lights.device.midi_io import (
    find_launchpad_pro_standalone_output,
    list_input_ports,
    list_output_ports,
)
from launch_lights.engine.renderer import Renderer
from launch_lights.engine.scheduler import Scheduler
from launch_lights.video.patterns import PATTERN_NAMES, build_pattern
from launch_lights.video.pipeline import (
    apply_gamma_brightness,
    bgr_to_rgb,
    fit_and_downsample,
    quantize_to_6bit,
    to_frame,
)
from launch_lights.video.source import (
    FileSource,
    TestPatternSource,
    WebcamSource,
)
from launch_lights.video.audio_source import AudioSource
from launch_lights.util.color import floyd_steinberg_to_6bit
from launch_lights.video.palette import Palette

console = Console()
log = logging.getLogger("launch_lights")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console, show_path=False)],
    )


def _resolve_output_port(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    found = find_launchpad_pro_standalone_output()
    if not found:
        console.print(
            "[red]No Launchpad Pro Standalone Port found.[/red] "
            "Connect the device or pass --port NAME (see `launch-lights list-ports`)."
        )
        sys.exit(2)
    return found


def _install_shutdown(dev: LaunchpadProOut, renderer: Renderer | None = None) -> None:
    """Wire SIGINT/SIGTERM/atexit to a clean shutdown.

    LaunchpadProOut.close() is idempotent — repeated firing is safe."""

    def shutdown(*_args):
        try:
            dev.close()
            if renderer is not None:
                renderer.blackout()
        finally:
            pass

    atexit.register(shutdown)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: (shutdown(), sys.exit(0)))
        except (ValueError, OSError):
            pass


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """Drive a Novation Launchpad Pro (2015 / MK1) as an 8x8 RGB display."""
    _setup_logging(verbose)


@cli.command("list-ports")
def list_ports() -> None:
    """List MIDI input and output ports."""
    out_table = Table(title="MIDI output ports")
    out_table.add_column("name")
    for name in list_output_ports():
        out_table.add_row(name)
    console.print(out_table)

    in_table = Table(title="MIDI input ports")
    in_table.add_column("name")
    for name in list_input_ports():
        in_table.add_row(name)
    console.print(in_table)

    found = find_launchpad_pro_standalone_output()
    if found:
        console.print(f"[green]Auto-detected:[/green] {found}")
    else:
        console.print(
            "[yellow]No Launchpad Pro Standalone Port detected.[/yellow] "
            "Connect the device, or pass --port NAME to other commands."
        )


@cli.command()
@click.option("--port", default=None, help="MIDI output port name.")
def blackout(port: Optional[str]) -> None:
    """Turn every LED off and return the device to Note layout."""
    port_name = _resolve_output_port(port)
    dev = LaunchpadProOut(port_name)
    try:
        dev.blackout()
        dev.exit_programmer_mode()
        console.print("[green]Blackout sent.[/green]")
    finally:
        dev.close()


@cli.command()
@click.option(
    "--pattern",
    type=click.Choice(PATTERN_NAMES),
    default="sweep",
    show_default=True,
    help="Test pattern to display.",
)
@click.option("--duration", default=10.0, show_default=True, help="Seconds to run.")
@click.option("--fps", default=30.0, show_default=True, help="Frame rate.")
@click.option("--flood-color", default="#ff0000", show_default=True, help="Color for the flood pattern.")
@click.option("--port", default=None, help="MIDI output port name.")
def test(pattern: str, duration: float, fps: float, flood_color: str, port: Optional[str]) -> None:
    """Display a test pattern on the Launchpad."""
    port_name = _resolve_output_port(port)
    dev = LaunchpadProOut(port_name)
    renderer = Renderer(mode="rgb", prefer_full_frame=(pattern == "sweep"))

    _install_shutdown(dev, renderer)

    dev.enter_programmer_mode()
    pattern_fn = build_pattern(pattern, flood_color=flood_color)

    deadline = time.perf_counter() + duration

    def tick(elapsed: float, dt: float) -> None:
        if time.perf_counter() >= deadline:
            sch.stop()
            return
        frame = pattern_fn(elapsed)
        plan = renderer.plan(frame)
        dev.execute(plan)
        renderer.commit(plan)

    sch = Scheduler(tick=tick, fps=fps)
    console.print(f"[cyan]test[/cyan] pattern={pattern} duration={duration}s fps={fps}")
    try:
        sch.run()
    finally:
        dev.close()
        console.print(
            f"[green]done[/green] ticks={sch.stats.ticks} skips={sch.stats.skips} "
            f"max_drift={sch.stats.max_drift_s * 1000:.1f}ms"
        )


@cli.command()
@click.option(
    "--source",
    type=click.Choice(["webcam", "file", "test", "audio", "webcam-show"]),
    required=True,
    help="Video source.",
)
@click.option("--file", "file_path", default=None, help="Path to video file (--source file).")
@click.option("--camera", default=0, show_default=True, help="Camera index (--source webcam).")
@click.option(
    "--pattern",
    type=click.Choice(PATTERN_NAMES),
    default="sweep",
    show_default=True,
    help="Test pattern (--source test).",
)
@click.option(
    "--color-mode",
    type=click.Choice(["rgb", "palette"]),
    default="rgb",
    show_default=True,
    help="rgb = full RGB SysEx; palette = LP Pro built-in 128-color palette.",
)
@click.option(
    "--fit",
    type=click.Choice(["crop", "letterbox", "stretch"]),
    default="crop",
    show_default=True,
    help="How non-square video maps onto the 8x8 grid.",
)
@click.option("--fps", default=30.0, show_default=True, help="Display refresh rate.")
@click.option("--gamma", default=2.2, show_default=True, help="Gamma correction (LED non-linearity).")
@click.option("--brightness", default=1.0, show_default=True, help="Linear brightness scale 0..1.5.")
@click.option(
    "--full-frame/--diff",
    default=None,
    help="Force full-frame (0Fh) vs sparse-diff (0Bh). Defaults: video=full-frame, test=diff.",
)
@click.option(
    "--dither/--no-dither",
    default=False,
    show_default=True,
    help="Floyd-Steinberg error diffusion (reduces banding on the 6-bit grid).",
)
@click.option("--stats", is_flag=True, help="Live FPS / plan-type panel.")
@click.option("--port", default=None, help="MIDI output port name.")
@click.option("--mic-gain", default=1.0, show_default=True, help="Multiplier on the auto-normalised audio level (--source audio).")
@click.option("--control-port", default=None, type=int, help="Serve the web control panel on this port (--source audio only).")
def run(
    source: str,
    file_path: Optional[str],
    camera: int,
    pattern: str,
    color_mode: str,
    fit: str,
    fps: float,
    gamma: float,
    brightness: float,
    full_frame: Optional[bool],
    dither: bool,
    stats: bool,
    port: Optional[str],
    mic_gain: float,
    control_port: Optional[int],
) -> None:
    """Drive the Launchpad Pro from a webcam, video file, or test pattern."""
    port_name = _resolve_output_port(port)

    if source == "file" and not file_path:
        console.print("[red]--file PATH is required when --source file[/red]")
        sys.exit(2)
    # Defaults for the full-frame heuristic
    if full_frame is None:
        full_frame = source != "test"

    dev = LaunchpadProOut(port_name)
    palette: Optional[Palette] = None
    if color_mode == "palette":
        console.print("[dim]Building palette LUT...[/dim]")
        palette = Palette()
        # Palette mode emits 0Ah diff updates regardless of full_frame.
        renderer = Renderer(mode="palette", palette=palette, prefer_full_frame=False)
    else:
        renderer = Renderer(mode="rgb", prefer_full_frame=full_frame)
    _install_shutdown(dev, renderer)
    dev.enter_programmer_mode()

    # Build the source
    test_src: Optional[TestPatternSource] = None
    audio_src: Optional[AudioSource] = None
    video_src = None
    if source == "test":
        test_src = TestPatternSource(pattern)
    elif source == "audio":
        audio_src = AudioSource(gain=mic_gain)
        if control_port is not None:
            from launch_lights.web.server import ControlServer
            control_server = ControlServer(audio_src, port=control_port, source_label="audio")
            control_server.start()
            console.print(f"[cyan]control panel:[/cyan] http://127.0.0.1:{control_port}/")
    elif source == "webcam-show":
        # Webcam visuals routed through the audio-driven effect stack.
        audio_src = AudioSource(gain=mic_gain)
        video_src = WebcamSource(camera)
        if control_port is not None:
            from launch_lights.web.server import ControlServer
            control_server = ControlServer(audio_src, port=control_port, source_label="webcam-show")
            control_server.start()
            console.print(f"[cyan]control panel:[/cyan] http://127.0.0.1:{control_port}/")
    elif source == "webcam":
        video_src = WebcamSource(camera)
    else:
        assert file_path is not None
        video_src = FileSource(file_path)

    last_good_bgr: Optional[np.ndarray] = None
    plan_counts = {"RGBFullFrame": 0, "RGBDiff": 0, "PaletteDiff": 0, "NoOp": 0}
    source_frames = 0  # incremented only when a new BGR/test frame is produced
    last_stats_update = 0.0

    def tick(elapsed: float, dt: float) -> None:
        nonlocal last_good_bgr, source_frames, last_stats_update
        if test_src is not None:
            frame = test_src.read_frame(elapsed)
            source_frames += 1
        elif source == "webcam-show":
            # Drive the audio analysis (so effects can still react), then route
            # the webcam frame through the Show's effect stack only.
            assert audio_src is not None and video_src is not None
            state = audio_src.analyze(elapsed)
            bgr = video_src.read()
            if bgr is None:
                bgr = last_good_bgr
            else:
                last_good_bgr = bgr
                source_frames += 1
            if bgr is None:
                return
            rgb_img = bgr_to_rgb(bgr)
            small = fit_and_downsample(rgb_img, fit)
            small = apply_gamma_brightness(small, gamma, brightness)
            small_6 = quantize_to_6bit(small)
            from launch_lights.engine.frame import RGB as _RGB
            cells_in = {
                (r, c): _RGB(int(small_6[r, c, 0]), int(small_6[r, c, 1]), int(small_6[r, c, 2]))
                for r in range(8) for c in range(8)
            }
            cells_out = audio_src.show.paint_passthrough(cells_in, state)
            audio_src.write_grid_cache(cells_out)
            from launch_lights.engine.frame import OFF as _OFF
            full = {(r, c): _OFF for r in range(8) for c in range(8)}
            full.update(cells_out)
            from launch_lights.engine.frame import Frame as _Frame
            frame = _Frame(cells=full)
        elif audio_src is not None:
            frame = audio_src.read_frame(elapsed)
            source_frames += 1
        else:
            assert video_src is not None
            bgr = video_src.read()
            if bgr is None:
                bgr = last_good_bgr
            else:
                last_good_bgr = bgr
                source_frames += 1
            if bgr is None:
                return  # no frame yet; wait for the next tick
            rgb = bgr_to_rgb(bgr)
            small = fit_and_downsample(rgb, fit)
            small = apply_gamma_brightness(small, gamma, brightness)
            if dither:
                small_6 = floyd_steinberg_to_6bit(small)
            else:
                small_6 = quantize_to_6bit(small)
            frame = to_frame(small_6)
        plan = renderer.plan(frame)
        dev.execute(plan)
        renderer.commit(plan)

        if isinstance(plan, RGBFullFramePlan):
            plan_counts["RGBFullFrame"] += 1
        elif isinstance(plan, RGBDiffPlan):
            plan_counts["RGBDiff"] += 1
        elif isinstance(plan, PaletteDiffPlan):
            plan_counts["PaletteDiff"] += 1
        else:
            plan_counts["NoOp"] += 1

        if stats and elapsed - last_stats_update > 0.25:
            last_stats_update = elapsed
            live.update(_stats_panel(sch, source_frames, plan_counts, elapsed))

    sch = Scheduler(tick=tick, fps=fps)
    header = (
        f"[cyan]run[/cyan] source={source} color={color_mode} fit={fit} fps={fps} "
        f"full_frame={full_frame} dither={dither} (Ctrl-C to stop)"
    )
    console.print(header)
    live_ctx = (
        Live(_stats_panel(sch, 0, plan_counts, 0.0), console=console, refresh_per_second=4)
        if stats
        else _NullLive()
    )
    live = live_ctx  # captured by tick()
    try:
        with live_ctx:
            sch.run()
    except KeyboardInterrupt:
        pass
    finally:
        if video_src is not None:
            video_src.close()
        if audio_src is not None:
            audio_src.close()
        dev.close()
        console.print(
            f"[green]done[/green] ticks={sch.stats.ticks} skips={sch.stats.skips} "
            f"max_drift={sch.stats.max_drift_s * 1000:.1f}ms "
            f"plans={plan_counts}"
        )


def _stats_panel(sch: Scheduler, source_frames: int, counts: dict, elapsed: float) -> Table:
    t = Table(title="launch-lights", show_lines=False)
    t.add_column("metric")
    t.add_column("value")
    render_fps = sch.stats.ticks / elapsed if elapsed > 0 else 0.0
    source_fps = source_frames / elapsed if elapsed > 0 else 0.0
    t.add_row("elapsed", f"{elapsed:.1f}s")
    t.add_row("render fps", f"{render_fps:.1f}")
    t.add_row("source fps", f"{source_fps:.1f}")
    t.add_row("ticks", str(sch.stats.ticks))
    t.add_row("skips", str(sch.stats.skips))
    t.add_row("max drift", f"{sch.stats.max_drift_s * 1000:.1f}ms")
    for k, v in counts.items():
        t.add_row(f"plan: {k}", str(v))
    return t


class _NullLive:
    """No-op stand-in for rich.Live when --stats is off."""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def update(self, *_args, **_kwargs):
        pass


if __name__ == "__main__":
    cli()
