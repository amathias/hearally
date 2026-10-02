"""Where to cut a long recording.

Parakeet transcribes up to about 24 minutes at once with full attention. A
Plaud left running records for hours (a real test file was 4 h 42 min), so
long recordings are cut into pieces of about ten minutes and the transcripts
are stitched back together with their times shifted.

A cut through the middle of a word garbles it on both sides, so each cut is
moved to the nearest quiet moment within a minute of the ten-minute mark. Only
if there is no silence there is the cut made on the mark itself.

Pure functions: no audio, no ffmpeg, no GPU, so the choices can be tested.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

TARGET_SECONDS = 600.0
WINDOW_SECONDS = 60.0
# A last piece shorter than this is merged into the one before it.
MIN_TAIL_SECONDS = 30.0

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*([\d.]+)")


def parse_silences(ffmpeg_log: str) -> list[tuple[float, float]]:
    """Silences from ffmpeg's `silencedetect` filter output, as (start, end)."""
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in ffmpeg_log.splitlines():
        if (m := _START.search(line)) is not None:
            start = max(0.0, float(m.group(1)))
        elif (m := _END.search(line)) is not None and start is not None:
            end = float(m.group(1))
            if end > start:
                silences.append((start, end))
            start = None
    return silences


def plan_chunks(
    duration: float,
    silences: Sequence[tuple[float, float]],
    *,
    target: float = TARGET_SECONDS,
    window: float = WINDOW_SECONDS,
    min_tail: float = MIN_TAIL_SECONDS,
) -> list[tuple[float, float]]:
    """Pieces as (start, end) seconds, covering the whole recording in order."""
    if duration <= 0:
        return []
    if duration <= target + window:
        return [(0.0, duration)]

    midpoints = sorted((a + b) / 2 for a, b in silences)
    cuts: list[float] = []
    previous = 0.0
    while duration - previous > target + window:
        mark = previous + target
        nearby = [m for m in midpoints if abs(m - mark) <= window and m > previous + min_tail]
        cut = min(nearby, key=lambda m: abs(m - mark)) if nearby else mark
        cuts.append(cut)
        previous = cut

    bounds = [0.0, *cuts, duration]
    pieces = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    if len(pieces) > 1 and pieces[-1][1] - pieces[-1][0] < min_tail:
        last = pieces.pop()
        pieces[-1] = (pieces[-1][0], last[1])
    return pieces
