# tests/test_risk.py
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from risk.kill_switch import KillSwitch


class TestKillSwitch:
    """Test the kill switch — mandatory safety component (SKILL.md ✓)."""

    def test_not_triggered_initially(self):
        ks = KillSwitch()
        ok, _ = ks.check(100, 120, 100)
        assert ok is True

    def test_daily_loss_triggers(self):
        ks = KillSwitch(max_daily_loss_pct=0.05)
        ok, reason = ks.check(91, 100, 100)  # 9% loss
        assert ok is False
        assert "Daily loss" in reason

    def test_drawdown_triggers(self):
        ks = KillSwitch(max_drawdown_pct=0.20)
        ok, reason = ks.check(75, 100, 100)  # 25% from peak
        assert ok is False
        assert "drawdown" in reason

    def test_normal_equity_passes(self):
        ks = KillSwitch()
        ok, _ = ks.check(105, 100, 100)  # profit
        assert ok is True

    def test_weekly_check(self):
        ks = KillSwitch(max_weekly_loss_pct=0.08)
        ok, _ = ks.check_weekly(95, "2026-W20")
        assert ok is True

    def test_monthly_check(self):
        ks = KillSwitch(max_monthly_loss_pct=0.15)
        ok, _ = ks.check_monthly(90, "2026-05")
        assert ok is True

    def test_already_triggered_blocks(self):
        ks = KillSwitch(max_daily_loss_pct=0.01)
        ks.check(98, 100, 100)  # 2% > 1%
        assert ks.triggered is True
        ok, reason = ks.check(100, 100, 100)
        assert ok is False
        assert "ALREADY TRIGGERED" in reason

    def test_reset_clears(self):
        ks = KillSwitch(max_daily_loss_pct=0.01)
        ks.check(98, 100, 100)
        assert ks.triggered is True
        ks.reset()
        assert ks.triggered is False
