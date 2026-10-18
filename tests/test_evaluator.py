"""Unit tests for token_monitor.evaluator."""

from token_monitor.config import MonitorConfig
from token_monitor.evaluator import evaluate_usage
from token_monitor.ledger import UsageRecord


def test_no_warning_when_below_threshold():
    cfg = MonitorConfig(limit_total_tokens=1000, warning_levels=[0.5, 0.8, 0.95])
    rec = UsageRecord(total_tokens=499)
    res = evaluate_usage(rec, cfg)
    assert not res.should_warn
    assert not res.new_warning_levels
    assert not res.should_freeze


def test_triggers_step_warning():
    cfg = MonitorConfig(limit_total_tokens=1000, warning_levels=[0.5, 0.8, 0.95])
    rec = UsageRecord(total_tokens=500)
    res = evaluate_usage(rec, cfg)
    assert res.should_warn
    assert res.new_warning_levels == [0.5]
    assert not res.should_freeze
    assert "50%" in res.message


def test_does_not_repeat_already_warned_levels():
    cfg = MonitorConfig(limit_total_tokens=1000, warning_levels=[0.5, 0.8, 0.95])
    # Already warned 0.5
    rec = UsageRecord(total_tokens=600, warned_levels=[0.5])
    res = evaluate_usage(rec, cfg)
    assert not res.should_warn
    assert res.new_warning_levels == []

    # Crosses 0.8 threshold
    rec.total_tokens = 850
    res2 = evaluate_usage(rec, cfg)
    assert res2.should_warn
    assert res2.new_warning_levels == [0.8]
    assert "80%" in res2.message


def test_freeze_when_limit_reached():
    cfg = MonitorConfig(limit_total_tokens=1000, freeze_on_limit=True)
    rec = UsageRecord(total_tokens=1000)
    res = evaluate_usage(rec, cfg)
    assert res.should_freeze
    assert "Limit reached" in res.freeze_reason


def test_no_freeze_when_freeze_disabled():
    cfg = MonitorConfig(limit_total_tokens=1000, freeze_on_limit=False)
    rec = UsageRecord(total_tokens=1200)
    res = evaluate_usage(rec, cfg)
    assert not res.should_freeze
