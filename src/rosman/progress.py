"""Live progress rendering for Docker operations that can silently take a
long time (image build/pull/push) -- so a slow first-run build doesn't look
like rosman has hung. See `_ensure_running_with_notice` in cli.py, which
this exists to support: printing a "starting..." notice up front only helps
if what follows also shows visible progress.

`NullReporter` drains the same Docker JSON stream silently (still raising on
error entries) so `lifecycle.py`'s image logic works identically under test
or when stdout isn't a tty, without importing `rich` there.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

_TERMINAL_LAYER_STATUSES = {
    "Pull complete",
    "Already exists",
    "Pushed",
    "Layer already exists",
}


class DockerStreamError(Exception):
    """An `{"error": ...}` (or `errorDetail`) entry surfaced by a decoded
    Docker build/pull/push stream."""


def _raise_on_error(chunk: dict) -> None:
    if "error" in chunk:
        raise DockerStreamError(chunk["error"])
    detail = chunk.get("errorDetail")
    if detail:
        raise DockerStreamError(detail.get("message", str(detail)))


class ProgressReporter(Protocol):
    def build(self, stream: Iterable[dict]) -> None: ...
    def build_finished(self, elapsed_seconds: float, succeeded: bool) -> None: ...
    def pull(self, stream: Iterable[dict]) -> None: ...
    def push(self, stream: Iterable[dict]) -> None: ...


class NullReporter:
    """Drains a decoded Docker stream without displaying anything."""

    def build(self, stream: Iterable[dict]) -> None:
        for chunk in stream:
            _raise_on_error(chunk)

    def build_finished(self, elapsed_seconds: float, succeeded: bool) -> None:
        pass

    def pull(self, stream: Iterable[dict]) -> None:
        for chunk in stream:
            _raise_on_error(chunk)

    def push(self, stream: Iterable[dict]) -> None:
        for chunk in stream:
            _raise_on_error(chunk)


class RichReporter:
    """A spinner with the current build-log line for `build` (byte progress
    isn't available for build steps), and real per-layer download/upload bars
    for `pull`/`push` (Docker reports `progressDetail.current`/`total` per
    layer `id` for those)."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def build(self, stream: Iterable[dict]) -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        ) as progress:
            task = progress.add_task("Building image...", total=None)
            for chunk in stream:
                _raise_on_error(chunk)
                text = (chunk.get("stream") or "").strip()
                if text:
                    progress.update(task, description=text[:100])

    def build_finished(self, elapsed_seconds: float, succeeded: bool) -> None:
        message = (
            f"Image built in {elapsed_seconds:.1f}s."
            if succeeded
            else f"Image build failed after {elapsed_seconds:.1f}s."
        )
        self._console.print(message)

    def pull(self, stream: Iterable[dict]) -> None:
        self._layered(stream, "Pulling")

    def push(self, stream: Iterable[dict]) -> None:
        self._layered(stream, "Pushing")

    def _layered(self, stream: Iterable[dict], verb: str) -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=self._console,
            transient=True,
        ) as progress:
            tasks: dict[str, TaskID] = {}
            totals: dict[str, int] = {}
            for chunk in stream:
                _raise_on_error(chunk)
                layer_id = chunk.get("id")
                if not layer_id:
                    continue
                status = chunk.get("status", "")
                detail = chunk.get("progressDetail") or {}
                total = detail.get("total")
                current = detail.get("current")
                if total:
                    totals[layer_id] = total

                if layer_id not in tasks:
                    tasks[layer_id] = progress.add_task(
                        f"{layer_id}: {status}", total=totals.get(layer_id)
                    )
                update_kwargs: dict = {"description": f"{layer_id}: {status}"}
                if layer_id in totals:
                    update_kwargs["total"] = totals[layer_id]
                if current is not None:
                    update_kwargs["completed"] = current
                elif status in _TERMINAL_LAYER_STATUSES:
                    update_kwargs["completed"] = totals.get(layer_id, 1)
                progress.update(tasks[layer_id], **update_kwargs)
