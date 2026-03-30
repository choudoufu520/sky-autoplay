from __future__ import annotations

import os
import platform
import tempfile
from pathlib import Path

import typer
from rich.console import Console

from src.application.converter import ConvertOptions, MappingError, convert_midi_to_chart
from src.application.player import PlayOptions, play_chart
from src.infrastructure.input_backends import PynputInputBackend
from src.infrastructure.midi_reader import export_single_track_midi, list_midi_tracks
from src.infrastructure.repository import load_chart, load_mapping, load_play_config, save_chart

app = typer.Typer(help="Sky music automation toolkit (CLI-first architecture).")
console = Console()


@app.command("tracks")
def tracks_command(
    midi: Path = typer.Argument(..., exists=True, readable=True, help="Input MIDI file"),
) -> None:
    """Show MIDI track list summary."""
    ppq, tracks = list_midi_tracks(midi)
    console.print(f"[cyan]PPQ:[/cyan] {ppq}  [cyan]Track count:[/cyan] {len(tracks)}")
    for item in tracks:
        programs = ",".join(str(x) for x in item.program_changes) if item.program_changes else "-"
        console.print(
            f"[{item.index:>2}] {item.name} | msg={item.message_count} | "
            f"note_on={item.note_on_count} | tempo={'Y' if item.has_tempo else 'N'} | "
            f"programs={programs}"
        )


@app.command("convert")
def convert_command(
    midi: Path = typer.Argument(..., exists=True, readable=True, help="Input MIDI file"),
    mapping: Path = typer.Option(..., "--mapping", "-m", exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o", help="Output chart JSON path"),
    profile: str | None = typer.Option(None, "--profile", help="Mapping profile id"),
    transpose: int = typer.Option(0, "--transpose", help="Semitone shift (CLI-level)"),
    octave: int = typer.Option(0, "--octave", help="Octave shift (CLI-level)"),
    strict: bool = typer.Option(False, "--strict", help="Fail on any unmapped note"),
    note_mode: str = typer.Option("tap", "--note-mode", help="tap or hold"),
    single_track: int | None = typer.Option(None, "--single-track", help="Read a specific MIDI track"),
) -> None:
    """Convert MIDI into chart JSON."""
    if note_mode not in {"tap", "hold"}:
        raise typer.BadParameter("note-mode must be one of: tap, hold")

    mapping_config = load_mapping(mapping)
    options = ConvertOptions(
        profile=profile,
        transpose=transpose,
        octave=octave,
        strict=strict,
        note_mode=note_mode,
        single_track=single_track,
    )

    try:
        chart, warnings = convert_midi_to_chart(midi, mapping_config, options)
    except MappingError as exc:
        console.print(f"[red]convert failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    save_chart(output, chart)
    console.print(f"[green]chart saved:[/green] {output}")
    console.print(f"[cyan]events:[/cyan] {len(chart.events)}")
    if warnings:
        console.print(f"[yellow]warnings:[/yellow] {len(warnings)}")
        for item in warnings[:20]:
            console.print(f"  - {item}")
        if len(warnings) > 20:
            console.print(f"  - ... truncated {len(warnings) - 20} more")


@app.command("preview-track")
def preview_track_command(
    midi: Path = typer.Argument(..., exists=True, readable=True, help="Input MIDI file"),
    track: int = typer.Option(..., "--track", "-t", help="Track index to preview"),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional output midi path. If omitted, export to temp file.",
    ),
    include_tempo_track: bool = typer.Option(
        True,
        "--include-tempo-track/--no-include-tempo-track",
        help="Copy track 0 as tempo/meta track for better playback timing.",
    ),
    open_file: bool = typer.Option(
        True,
        "--open/--no-open",
        help="Open exported MIDI with system default player.",
    ),
) -> None:
    """Export one MIDI track and open it with system player for audio preview."""
    export_path: Path
    if output is None:
        temp_file = tempfile.NamedTemporaryFile(prefix="sky_preview_track_", suffix=".mid", delete=False)
        temp_file.close()
        export_path = Path(temp_file.name)
    else:
        export_path = output

    try:
        export_single_track_midi(
            midi_path=midi,
            track_index=track,
            output_path=export_path,
            include_tempo_track=include_tempo_track,
        )
    except IndexError as exc:
        console.print(f"[red]preview-track failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]track midi exported:[/green] {export_path}")
    if open_file:
        _open_with_system_player(export_path)
        console.print("[cyan]opened with default player[/cyan]")
    else:
        console.print("[yellow]not opened (--no-open).[/yellow]")


@app.command("preview-track-game")
def preview_track_game_command(
    midi: Path = typer.Argument(..., exists=True, readable=True, help="Input MIDI file"),
    track: int = typer.Option(..., "--track", "-t", help="Track index to preview"),
    mapping: Path = typer.Option(..., "--mapping", "-m", exists=True, readable=True),
    profile: str | None = typer.Option(None, "--profile", help="Mapping profile id"),
    transpose: int = typer.Option(0, "--transpose", help="Semitone shift"),
    octave: int = typer.Option(0, "--octave", help="Octave shift"),
    note_mode: str = typer.Option("tap", "--note-mode", help="tap or hold"),
    strict: bool = typer.Option(False, "--strict", help="Fail on unmapped note"),
    latency_offset_ms: int = typer.Option(0, "--latency-offset-ms"),
    countdown_sec: int = typer.Option(3, "--countdown-sec"),
    chord_stagger_ms: int = typer.Option(0, "--chord-stagger-ms"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview without key output"),
    debug: bool = typer.Option(True, "--debug/--no-debug"),
) -> None:
    """Convert one MIDI track in-memory and preview by game key output."""
    if note_mode not in {"tap", "hold"}:
        raise typer.BadParameter("note-mode must be one of: tap, hold")

    mapping_config = load_mapping(mapping)
    convert_options = ConvertOptions(
        profile=profile,
        transpose=transpose,
        octave=octave,
        strict=strict,
        note_mode=note_mode,
        single_track=track,
    )

    try:
        chart, warnings = convert_midi_to_chart(midi, mapping_config, convert_options)
    except MappingError as exc:
        console.print(f"[red]preview-track-game convert failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    except IndexError as exc:
        console.print(f"[red]preview-track-game failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if warnings:
        console.print(f"[yellow]warnings:[/yellow] {len(warnings)}")
    console.print(f"[cyan]preview events:[/cyan] {len(chart.events)}")

    play_options = PlayOptions(
        latency_offset_ms=latency_offset_ms,
        countdown_sec=countdown_sec,
        chord_stagger_ms=chord_stagger_ms,
        dry_run=dry_run,
        debug=debug,
    )
    backend = None if play_options.dry_run else PynputInputBackend()
    play_chart(chart, backend, play_options)


@app.command("play")
def play_command(
    chart: Path = typer.Argument(..., exists=True, readable=True, help="Input chart JSON"),
    config: Path | None = typer.Option(None, "--config", "-c", exists=True, readable=True),
    latency_offset_ms: int = typer.Option(0, "--latency-offset-ms"),
    countdown_sec: int = typer.Option(3, "--countdown-sec"),
    chord_stagger_ms: int = typer.Option(0, "--chord-stagger-ms"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Play chart events by sending keyboard input."""
    play_cfg = load_play_config(config)

    options = PlayOptions(
        latency_offset_ms=int(play_cfg.get("latency_offset_ms", latency_offset_ms)),
        countdown_sec=int(play_cfg.get("countdown_sec", countdown_sec)),
        chord_stagger_ms=int(play_cfg.get("chord_stagger_ms", chord_stagger_ms)),
        dry_run=bool(play_cfg.get("dry_run", dry_run)),
        debug=bool(play_cfg.get("debug", debug)),
    )

    chart_doc = load_chart(chart)
    backend = None if options.dry_run else PynputInputBackend()

    console.print("[blue]Start playing... Focus game window before countdown ends.[/blue]")
    play_chart(chart_doc, backend, options)
    console.print("[green]Playback complete.[/green]")


def _open_with_system_player(path: Path) -> None:
    system_name = platform.system().lower()
    if system_name.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    raise RuntimeError("Automatic open is only implemented for Windows now.")


if __name__ == "__main__":
    app()
