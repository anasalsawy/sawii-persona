"""Drift detector.

Sample real production traffic and re-score. If the harmony score drops
below the locked threshold, alert. The detector is the long-term memory of
the architectural fine-tune process: it tells you whether the configuration
you locked is still producing harmony under real conditions.

Sampling strategy:
  - Keep a rolling buffer of N recent turns (default 200) with their captures.
  - Periodically (every K turns, or every T minutes) re-score the buffer.
  - Compare against the locked score.
  - If below threshold, append to the alert log.

For v1, the detector reads a JSONL file of capture dicts (one per turn).
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .runner import CorpusRun, CapturedTurn
from .scorer import HarmonyScore, score_run


@dataclass
class DriftAlert:
    detected_at: float
    locked_score: float
    current_score: float
    worst_axis: str
    worst_axis_score: float
    buffer_size: int

    def summary(self) -> str:
        return (f"[drift] locked={self.locked_score:.3f} current={self.current_score:.3f} "
                f"worst_axis={self.worst_axis}@{self.worst_axis_score:.3f} "
                f"buffer_n={self.buffer_size}")


class DriftDetector:
    """Sample real traffic; alert when harmony drops below threshold."""

    def __init__(self, *, locked_score: float, threshold: float = 0.10,
                 buffer_size: int = 200, alert_log_path: str | None = None):
        self.locked_score = locked_score
        self.threshold = threshold  # max acceptable drop
        self.buffer_size = buffer_size
        self._buffer: deque = deque(maxlen=buffer_size)
        self._alerts: list[DriftAlert] = []
        self._alert_log_path = alert_log_path

    def observe(self, turn: CapturedTurn) -> None:
        self._buffer.append(turn)

    def check(self) -> DriftAlert | None:
        """Score the buffer. Return an alert if drift exceeds threshold."""
        if len(self._buffer) < 20:
            return None
        run = CorpusRun(config_id="drift-check", started_at=time.time(),
                        turns=list(self._buffer))
        sc = score_run(run)
        delta = self.locked_score - sc.aggregate
        if delta <= self.threshold:
            return None
        # Find worst axis.
        axes = {
            "internal_consistency": sc.internal_consistency,
            "lobe_agreement": sc.lobe_agreement,
            "regulation_effectiveness": sc.regulation_effectiveness,
            "memory_coherence": sc.memory_coherence,
            "affective_trajectory": sc.affective_trajectory,
            "no_silent_cancellation": sc.no_silent_cancellation,
        }
        worst = min(axes.items(), key=lambda kv: kv[1])
        alert = DriftAlert(
            detected_at=time.time(),
            locked_score=self.locked_score,
            current_score=sc.aggregate,
            worst_axis=worst[0],
            worst_axis_score=worst[1],
            buffer_size=len(self._buffer),
        )
        self._alerts.append(alert)
        if self._alert_log_path:
            self._append_alert(alert)
        return alert

    def _append_alert(self, alert: DriftAlert) -> None:
        os.makedirs(os.path.dirname(self._alert_log_path) or ".", exist_ok=True)
        with open(self._alert_log_path, "a") as f:
            f.write(json.dumps({
                "detected_at": alert.detected_at,
                "locked_score": alert.locked_score,
                "current_score": alert.current_score,
                "worst_axis": alert.worst_axis,
                "worst_axis_score": alert.worst_axis_score,
                "buffer_size": alert.buffer_size,
            }) + "\n")

    @property
    def alerts(self) -> list[DriftAlert]:
        return list(self._alerts)


def load_captures_jsonl(path: str) -> Iterable[CapturedTurn]:
    """Replay captures from a JSONL file (one turn per line)."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            yield CapturedTurn(
                scenario=d.get("scenario", "replay"),
                turn_index=d.get("turn_index", 0),
                user=d.get("user", ""),
                a_text=d.get("a_text", ""),
                a_chord=d.get("a_chord", {}),
                c_chord=d.get("c_chord", {}),
                b_level=d.get("b_level", ""),
                b_correction=d.get("b_correction", ""),
                b_regulate=d.get("b_regulate", ""),
                surfaced_memory=d.get("surfaced_memory", ""),
                inner=d.get("inner", []),
                recall_text=d.get("recall_text", ""),
                expect=d.get("expect", {}),
            )