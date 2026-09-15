"""The census has to actually happen, on a machine that gets restarted.

An interval job's first run is one whole interval after the scheduler starts.
The dashboard restarts on every deploy and every logon, so with a thirty
minute interval a service restarted every twenty minutes would take a census
exactly never -- and the failure is invisible, because nothing errors. The job
is simply always about to run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import scheduler as scheduler_module


class FakeScheduler:
    def __init__(self, *_args, **_kwargs):
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func, **kwargs})

    def start(self):
        self.started = True

    def job(self, job_id):
        return next((job for job in self.jobs if job.get("id") == job_id), None)


def build(monkeypatch, settings, **overrides):
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value, raising=False)
    monkeypatch.setattr(scheduler_module, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", FakeScheduler)
    return scheduler_module.start_scheduler()


def test_the_first_census_is_taken_at_startup_not_one_interval_later(monkeypatch, settings):
    scheduler = build(monkeypatch, settings, roblox_charts_enabled=True,
                      market_sample_minutes=30)

    job = scheduler.job("market_pulse_sample")
    assert job, "the sampler was never scheduled"
    first = job["next_run_time"]
    assert first is not None, "the first run is one full interval away"
    delay = first - datetime.now(settings.tz)
    assert delay < timedelta(minutes=1), (
        f"the first census is {delay} away; a restart would push it back again"
    )


def test_the_sampler_repeats_on_the_configured_interval(monkeypatch, settings):
    scheduler = build(monkeypatch, settings, roblox_charts_enabled=True,
                      market_sample_minutes=45)

    job = scheduler.job("market_pulse_sample")
    assert job["minutes"] == 45
    assert job["func"] is scheduler_module.sample_market


def test_samples_never_overlap(monkeypatch, settings):
    """A slow sample must not have a second one running underneath it, or two
    censuses land with near-identical timestamps and the interval between
    observations collapses."""
    scheduler = build(monkeypatch, settings, roblox_charts_enabled=True,
                      market_sample_minutes=30)

    assert scheduler.job("market_pulse_sample")["max_instances"] == 1


def test_disabling_charts_removes_the_job_rather_than_running_it_disabled(monkeypatch,
                                                                         settings):
    scheduler = build(monkeypatch, settings, roblox_charts_enabled=False,
                      market_sample_minutes=30)

    assert scheduler.job("market_pulse_sample") is None
    assert scheduler.job("daily_metric_snapshot"), "the daily snapshot is unrelated"


def test_the_daily_snapshot_is_untouched_by_the_sampler(monkeypatch, settings):
    scheduler = build(monkeypatch, settings, roblox_charts_enabled=True,
                      market_sample_minutes=30)

    daily = scheduler.job("daily_metric_snapshot")
    assert daily["trigger"] == "cron"
    assert daily["func"] is scheduler_module.snapshot_all
