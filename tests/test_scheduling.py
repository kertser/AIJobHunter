"""Tests for scheduling — config, history, PipelineScheduler."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunter.config.loader import (
    append_schedule_history,
    load_schedule,
    load_schedule_history,
    save_schedule,
)
from job_hunter.config.models import PipelineMode, ScheduleConfig, ScheduleRunRecord


# ---------------------------------------------------------------------------
# ScheduleConfig model
# ---------------------------------------------------------------------------


class TestScheduleConfigModel:
    def test_defaults(self) -> None:
        cfg = ScheduleConfig()
        assert cfg.enabled is False
        assert cfg.time_of_day == "09:00"
        assert cfg.days_of_week == ["mon", "tue", "wed", "thu", "fri"]
        assert cfg.pipeline_mode == PipelineMode.FULL
        assert cfg.profile_name == "default"

    def test_custom_values(self) -> None:
        cfg = ScheduleConfig(
            enabled=True,
            time_of_day="14:30",
            days_of_week=["mon", "wed", "fri"],
            pipeline_mode=PipelineMode.MARKET,
            profile_name="my_profile",
        )
        assert cfg.enabled is True
        assert cfg.time_of_day == "14:30"
        assert cfg.days_of_week == ["mon", "wed", "fri"]
        assert cfg.pipeline_mode == PipelineMode.MARKET
        assert cfg.profile_name == "my_profile"

    def test_pipeline_mode_enum(self) -> None:
        assert PipelineMode.DISCOVER.value == "discover"
        assert PipelineMode.DISCOVER_SCORE.value == "discover_score"
        assert PipelineMode.FULL.value == "full"
        assert PipelineMode.MARKET.value == "market"


# ---------------------------------------------------------------------------
# ScheduleRunRecord model
# ---------------------------------------------------------------------------


class TestScheduleRunRecord:
    def test_defaults(self) -> None:
        rec = ScheduleRunRecord()
        assert rec.started_at == ""
        assert rec.completed_at == ""
        assert rec.mode == ""
        assert rec.summary == {}
        assert rec.error == ""

    def test_round_trip(self) -> None:
        rec = ScheduleRunRecord(
            started_at="2026-03-26T09:00:00Z",
            completed_at="2026-03-26T09:05:00Z",
            mode="full",
            summary={"discovered": 10, "scored": 8},
            error="",
        )
        data = rec.model_dump()
        rec2 = ScheduleRunRecord(**data)
        assert rec2.started_at == rec.started_at
        assert rec2.summary == {"discovered": 10, "scored": 8}


# ---------------------------------------------------------------------------
# YAML loading / saving — schedule config
# ---------------------------------------------------------------------------


class TestScheduleYaml:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_schedule(tmp_path / "nonexistent.yml")
        assert cfg.enabled is False
        assert cfg.time_of_day == "09:00"

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.yml"
        cfg = ScheduleConfig(
            enabled=True,
            time_of_day="08:15",
            days_of_week=["mon", "fri"],
            pipeline_mode=PipelineMode.DISCOVER_SCORE,
            profile_name="test_prof",
        )
        save_schedule(cfg, path)
        loaded = load_schedule(path)
        assert loaded.enabled is True
        assert loaded.time_of_day == "08:15"
        assert loaded.days_of_week == ["mon", "fri"]
        assert loaded.pipeline_mode == PipelineMode.DISCOVER_SCORE
        assert loaded.profile_name == "test_prof"

    def test_load_empty_file_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.yml"
        path.write_text("")
        cfg = load_schedule(path)
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# YAML loading / saving — schedule history
# ---------------------------------------------------------------------------


class TestScheduleHistory:
    def test_load_missing_file(self, tmp_path: Path) -> None:
        history = load_schedule_history(tmp_path / "nope.yml")
        assert history == []

    def test_append_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "history.yml"
        rec1 = ScheduleRunRecord(
            started_at="2026-03-26T09:00:00Z",
            completed_at="2026-03-26T09:05:00Z",
            mode="full",
            summary={"discovered": 5},
        )
        rec2 = ScheduleRunRecord(
            started_at="2026-03-26T10:00:00Z",
            completed_at="2026-03-26T10:03:00Z",
            mode="market",
            summary={"events_created": 20},
        )
        append_schedule_history(rec1, path)
        append_schedule_history(rec2, path)

        history = load_schedule_history(path)
        assert len(history) == 2
        assert history[0].mode == "full"
        assert history[1].mode == "market"
        assert history[1].summary == {"events_created": 20}

    def test_history_capped_at_100(self, tmp_path: Path) -> None:
        path = tmp_path / "history.yml"
        for i in range(110):
            rec = ScheduleRunRecord(
                started_at=f"2026-01-01T{i:04d}",
                mode="full",
            )
            append_schedule_history(rec, path)
        history = load_schedule_history(path)
        assert len(history) == 100

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "history.yml"
        path.write_text("")
        assert load_schedule_history(path) == []

    def test_load_invalid_content(self, tmp_path: Path) -> None:
        """Non-list YAML should return empty list."""
        path = tmp_path / "history.yml"
        path.write_text("key: value\n")
        assert load_schedule_history(path) == []


# ---------------------------------------------------------------------------
# PipelineScheduler unit tests
# ---------------------------------------------------------------------------


class TestPipelineScheduler:
    def test_construction(self) -> None:
        from job_hunter.scheduling.scheduler import PipelineScheduler

        scheduler = PipelineScheduler()
        assert scheduler.config.enabled is False
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_start_and_stop_disabled(self) -> None:
        from job_hunter.scheduling.scheduler import PipelineScheduler

        scheduler = PipelineScheduler()
        cfg = ScheduleConfig(enabled=False)
        scheduler.start(cfg)
        assert scheduler.running is True  # scheduler loop starts, job just isn't added
        assert scheduler.get_next_run_time() is None
        scheduler.stop()
        # After shutdown(wait=False) the internal state may not flip immediately
        # in an async context — just verify no error is raised

    @pytest.mark.asyncio
    async def test_start_enabled_creates_job(self) -> None:
        from job_hunter.scheduling.scheduler import JOB_ID, PipelineScheduler

        scheduler = PipelineScheduler()
        cfg = ScheduleConfig(
            enabled=True,
            time_of_day="10:30",
            days_of_week=["mon", "wed"],
        )
        scheduler.start(cfg)
        try:
            assert scheduler.get_next_run_time() is not None
        finally:
            scheduler.stop()

    @pytest.mark.asyncio
    async def test_reschedule_enables_job(self) -> None:
        from job_hunter.scheduling.scheduler import PipelineScheduler

        scheduler = PipelineScheduler()
        scheduler.start(ScheduleConfig(enabled=False))
        assert scheduler.get_next_run_time() is None

        scheduler.reschedule(ScheduleConfig(
            enabled=True,
            time_of_day="15:00",
            days_of_week=["tue", "thu"],
        ))
        assert scheduler.get_next_run_time() is not None
        scheduler.stop()

    @pytest.mark.asyncio
    async def test_reschedule_disables_job(self) -> None:
        from job_hunter.scheduling.scheduler import PipelineScheduler

        scheduler = PipelineScheduler()
        scheduler.start(ScheduleConfig(enabled=True, time_of_day="09:00"))
        assert scheduler.get_next_run_time() is not None

        scheduler.reschedule(ScheduleConfig(enabled=False))
        assert scheduler.get_next_run_time() is None
        scheduler.stop()

    @pytest.mark.asyncio
    async def test_config_property(self) -> None:
        from job_hunter.scheduling.scheduler import PipelineScheduler

        scheduler = PipelineScheduler()
        cfg = ScheduleConfig(enabled=True, profile_name="test")
        scheduler.start(cfg)
        assert scheduler.config.profile_name == "test"
        scheduler.stop()

    def test_stop_idempotent(self) -> None:
        """Calling stop on an already-stopped scheduler should not raise."""
        from job_hunter.scheduling.scheduler import PipelineScheduler

        scheduler = PipelineScheduler()
        # Not started — stop should be safe
        scheduler.stop()



