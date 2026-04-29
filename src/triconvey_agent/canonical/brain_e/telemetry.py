from __future__ import annotations

import json
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from triconvey_agent.backend.runtime import ensure_runtime_dirs


def _utc_stamp() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str, ensure_ascii=True) + "\n")


@dataclass
class _StageSummary:
    count: int = 0
    total_ms: int = 0
    max_ms: int = 0
    reasons: Counter[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = Counter()


class BrainETelemetry:
    """Per-run Brain E debug and learning telemetry."""

    def __init__(self, run_dir: str | Path | None, *, run_id: str | None = None) -> None:
        self.enabled = run_dir is not None
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.run_id = run_id or (self.run_dir.name if self.run_dir is not None else None)
        runtime = ensure_runtime_dirs()
        self.debug_log_path = runtime.cache_dir / "brain_e" / "brain_e_debug.jsonl" if self.enabled else None
        self.learning_log_path = runtime.local_app_dir / "brain_e" / "brain_e_learning.jsonl" if self.enabled else None
        self.summary_json_path = runtime.local_app_dir / "brain_e" / "brain_e_debug_summary.json" if self.enabled else None
        self.summary_text_path = runtime.local_app_dir / "brain_e" / "brain_e_debug_summary.txt" if self.enabled else None
        self.profile_path = runtime.local_app_dir / "brain_e" / "learning_profile.json"
        self.profile_snapshot_path = self.run_dir / "brain_e_learning_profile.json" if self.run_dir else None
        self._lock = threading.Lock()
        self._stage_stats: dict[str, _StageSummary] = defaultdict(_StageSummary)
        self._learning_notes: list[dict[str, Any]] = []
        self._run_started_at = _utc_stamp()

    def log_debug(self, stage: str, message: str, *, kind: str = "debug", **data: Any) -> None:
        if not self.enabled or self.debug_log_path is None:
            return
        payload = {
            "ts": _utc_stamp(),
            "kind": kind,
            "stage": stage,
            "message": message,
            **data,
        }
        _append_jsonl(self.debug_log_path, payload)

    def log_learning(self, stage: str, message: str, *, success: bool | None = None, **data: Any) -> None:
        if not self.enabled or self.learning_log_path is None:
            return
        payload = {
            "ts": _utc_stamp(),
            "kind": "learning",
            "stage": stage,
            "message": message,
            "success": success,
            **data,
        }
        self._learning_notes.append(payload)
        _append_jsonl(self.learning_log_path, payload)

    def record_stage(self, stage: str, duration_ms: int, *, reason: str = "", **data: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            stats = self._stage_stats[stage]
            stats.count += 1
            stats.total_ms += max(0, int(duration_ms))
            stats.max_ms = max(stats.max_ms, int(duration_ms))
            if reason:
                stats.reasons[reason] += 1
        self.log_debug(stage, "stage_complete", kind="timing", duration_ms=int(duration_ms), reason=reason, **data)

    @contextmanager
    def trace(self, stage: str, *, reason: str = "", **data: Any) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = time.perf_counter()
        self.log_debug(stage, "stage_start", reason=reason, **data)
        try:
            yield
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.record_stage(stage, elapsed_ms, reason=reason, **data)

    def suggest_learning(self) -> list[str]:
        suggestions: list[str] = []
        with self._lock:
            stage_rows = sorted(self._stage_stats.items(), key=lambda item: item[1].total_ms, reverse=True)
            top_stage = stage_rows[0][0] if stage_rows else ""
            top_total = stage_rows[0][1].total_ms if stage_rows else 0
            total_time = sum(stats.total_ms for stats in self._stage_stats.values()) or 1
            top_share = top_total / total_time
            if top_stage == "property_details_scroll" and top_share > 0.18:
                suggestions.append(
                    "Property Details navigation is the main bottleneck. Keep using the vision-selected scroll direction first and avoid extra pane scans when the label is already visible."
                )
            if top_stage == "matter_search" and top_share > 0.18:
                suggestions.append(
                    "Matter search is the main bottleneck. Cache the winning search field path and avoid re-focusing or re-submitting unless the OCR pass clearly failed."
                )
            if top_stage == "fill_action" and top_share > 0.18:
                suggestions.append(
                    "Field filling is the main bottleneck. Prefer the first confident locator match and skip repeated fallback scans after a control has already been identified."
                )
            if self._stage_stats.get("property_details_scroll"):
                stats = self._stage_stats["property_details_scroll"]
                avg = stats.total_ms / max(stats.count, 1)
                if avg > 1500:
                    suggestions.append("Property Details finding is slow. Prefer vision-led scroll direction selection before extra retries.")
            if self._stage_stats.get("matter_search"):
                stats = self._stage_stats["matter_search"]
                avg = stats.total_ms / max(stats.count, 1)
                if avg > 1200:
                    suggestions.append("Matter search is slow. Cache the winning search path and reduce re-submits when the first OCR pass succeeds.")
            if self._stage_stats.get("fill_action"):
                stats = self._stage_stats["fill_action"]
                avg = stats.total_ms / max(stats.count, 1)
                if avg > 900:
                    suggestions.append("Field filling is slow. Use the fastest locator strategy first and skip repeated fallback scans after a confident match.")
        return suggestions

    def _navigation_notes(self) -> list[dict[str, Any]]:
        notes = []
        for note in self._learning_notes:
            stage = str(note.get("stage") or "")
            if "navigation" in stage or stage in {"matter_search", "property_details_scroll", "fill_action"}:
                notes.append(note)
        return notes[-25:]

    def build_summary(self, *, total_duration_ms: int | None = None, run_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            stage_rows = []
            total_ms = sum(stats.total_ms for stats in self._stage_stats.values()) or 1
            for stage, stats in sorted(self._stage_stats.items(), key=lambda item: item[1].total_ms, reverse=True):
                share = round((stats.total_ms / total_ms) * 100, 1)
                stage_rows.append(
                    {
                        "stage": stage,
                        "count": stats.count,
                        "total_ms": stats.total_ms,
                        "average_ms": round(stats.total_ms / max(stats.count, 1), 1),
                        "max_ms": stats.max_ms,
                        "share_pct": share,
                        "top_reasons": stats.reasons.most_common(3),
                    }
                )
            summary = {
                "run_id": run_id or self.run_id,
                "run_started_at": self._run_started_at,
                "generated_at": _utc_stamp(),
                "total_duration_ms": total_duration_ms,
                "stage_breakdown": stage_rows,
                "slowest_stages": stage_rows[:5],
                "learning_notes": self._learning_notes[-100:],
                "navigation_learnings": self._navigation_notes(),
                "suggestions": self.suggest_learning(),
            }
        return summary

    def finalize(self, *, total_duration_ms: int | None = None, run_id: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {}
        summary = self.build_summary(total_duration_ms=total_duration_ms, run_id=run_id)
        if self.summary_json_path is not None:
            self.summary_json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        if self.summary_text_path is not None:
            self.summary_text_path.write_text(self._format_summary(summary), encoding="utf-8")
        self._persist_profile(summary)
        return summary

    def _persist_profile(self, summary: dict[str, Any]) -> None:
        profile_path = self.profile_path
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if not isinstance(profile, dict):
                profile = {}
        except Exception:
            profile = {}

        stages: dict[str, Any] = profile.setdefault("stages", {})
        profile["version"] = 1
        profile["last_updated_at"] = _utc_stamp()
        profile["last_run_id"] = summary.get("run_id")
        profile["runs_completed"] = int(profile.get("runs_completed") or 0) + 1

        for row in summary.get("stage_breakdown", []):
            stage = str(row.get("stage") or "unknown")
            existing = stages.get(stage) or {}
            total_ms = int(existing.get("total_ms") or 0) + int(row.get("total_ms") or 0)
            count = int(existing.get("count") or 0) + int(row.get("count") or 0)
            stages[stage] = {
                "count": count,
                "total_ms": total_ms,
                "average_ms": round(total_ms / max(count, 1), 1),
                "max_ms": max(int(existing.get("max_ms") or 0), int(row.get("max_ms") or 0)),
                "top_reasons": row.get("top_reasons") or [],
                "last_reason": (row.get("top_reasons") or [["", 0]])[0][0] if row.get("top_reasons") else "",
            }

        profile["suggestions"] = summary.get("suggestions", [])
        profile["navigation_learnings"] = summary.get("navigation_learnings", [])
        profile["last_summary"] = summary
        profile_path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
        if self.profile_snapshot_path is not None:
            self.profile_snapshot_path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")

    def _format_summary(self, summary: dict[str, Any]) -> str:
        lines = [
            "Brain E Debug Summary",
            f"Run: {summary.get('run_id') or 'unknown'}",
            f"Generated: {summary.get('generated_at') or _utc_stamp()}",
            f"Total duration: {summary.get('total_duration_ms') or 'n/a'} ms",
            "",
            "Where time goes:",
        ]
        for row in summary.get("slowest_stages", [])[:10]:
            reasons = ", ".join(f"{reason} ({count})" for reason, count in row.get("top_reasons", [])) or "none"
            lines.append(
                f"- {row.get('stage')}: {row.get('total_ms')} ms ({row.get('share_pct')}%) over {row.get('count')} run(s); avg {row.get('average_ms')} ms; reasons: {reasons}"
            )
        suggestions = summary.get("suggestions") or []
        lines.append("")
        lines.append("How to speed it up:")
        if suggestions:
            for item in suggestions:
                lines.append(f"- {item}")
        else:
            lines.append("- No clear bottleneck yet; Brain E is still learning the fastest path.")
        lines.append("")
        lines.append("Navigation learnings:")
        nav_notes = summary.get("navigation_learnings") or []
        if nav_notes:
            for note in nav_notes[-10:]:
                lines.append(
                    f"- {note.get('stage')}: {note.get('message')} "
                    f"({ 'success' if note.get('success') else 'unknown' if note.get('success') is None else 'failed' })"
                )
        else:
            lines.append("- No navigation learnings recorded.")
        lines.append("")
        lines.append("Recent learning notes:")
        notes = summary.get("learning_notes") or []
        if notes:
            for note in notes[-10:]:
                lines.append(
                    f"- {note.get('stage')}: {note.get('message')} "
                    f"({ 'success' if note.get('success') else 'unknown' if note.get('success') is None else 'failed' })"
                )
        else:
            lines.append("- No learning notes recorded.")
        return "\n".join(lines) + "\n"
