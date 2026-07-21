# FIX: Kill switch implementation — mandatory before live trading (SKILL.md §⑤)
# ── STRATEGY ASSUMPTIONS ─────────────────────────────────────────
# Risk hierarchy enforced in order:
#   Level 1 — Per Trade:    Max 1–2% account equity at risk
#   Level 2 — Per Session:  Max 5% intraday loss → halt scalping
#   Level 3 — Per Week:     Max 8% weekly drawdown → reduce size 50%
#   Level 4 — Per Month:    Max 15% monthly drawdown → full strategy review
#   Level 5 — Account Floor: 20% from peak → kill switch, stop all trading
# ─────────────────────────────────────────────────────────────────

import logging
from datetime import datetime

logger = logging.getLogger("kill_switch")


class KillSwitch:
    """Hard halt logic — your job is not to make money, it's to survive."""

    def __init__(
        self,
        max_daily_loss_pct: float = 0.05,
        max_weekly_loss_pct: float = 0.08,
        max_monthly_loss_pct: float = 0.15,
        max_drawdown_pct: float = 0.20,
    ):
        self.max_daily_loss = max_daily_loss_pct
        self.max_weekly_loss = max_weekly_loss_pct
        self.max_monthly_loss = max_monthly_loss_pct
        self.max_drawdown = max_drawdown_pct
        self.triggered = False
        self.trigger_reason = ""
        self.peak_equity = 0.0
        # Track weekly / monthly starting equity for drawdown windows
        self.week_start_equity: dict[str, float] = {}
        self.month_start_equity: dict[str, float] = {}

    def check(
        self,
        current_equity: float,
        peak_equity: float,
        session_start_equity: float,
    ) -> tuple[bool, str]:
        """
        Returns (True, "") if trading is allowed; (False, reason) if kill switch triggered.

        Parameters
        ----------
        current_equity : float — total account value right now
        peak_equity : float — highest equity ever recorded
        session_start_equity : float — equity at start of this trading session
        """
        if self.triggered:
            return False, f"ALREADY TRIGGERED: {self.trigger_reason}"
    
        # Level 5: Account floor (peak drawdown) — checked FIRST (highest priority)
        if peak_equity > 0:
            total_dd = (peak_equity - current_equity) / peak_equity
            if total_dd >= self.max_drawdown:
                self.triggered = True
                self.trigger_reason = f"Total drawdown {total_dd*100:.1f}% >= {self.max_drawdown*100:.0f}% from peak"
                self._halt()
                return False, self.trigger_reason
    
        # Level 2: Daily loss check — only if drawdown floor hasn't triggered
        if session_start_equity > 0:
            daily_loss = (session_start_equity - current_equity) / session_start_equity
            if daily_loss >= self.max_daily_loss:
                self.triggered = True
                self.trigger_reason = f"Daily loss {daily_loss*100:.1f}% >= {self.max_daily_loss*100:.0f}%"
                self._halt()
                return False, self.trigger_reason

        # Update peak
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        return True, ""

    def check_weekly(self, current_equity: float, week_label: str) -> tuple[bool, str]:
        """Check weekly drawdown limit (Level 3)."""
        if self.triggered:
            return False, f"ALREADY TRIGGERED: {self.trigger_reason}"

        if week_label not in self.week_start_equity:
            self.week_start_equity[week_label] = current_equity

        week_start = self.week_start_equity[week_label]
        if week_start > 0:
            week_dd = (week_start - current_equity) / week_start
            if week_dd >= self.max_weekly_loss:
                self.triggered = True
                self.trigger_reason = f"Weekly drawdown {week_dd*100:.1f}% >= {self.max_weekly_loss*100:.0f}%"
                self._halt()
                return False, self.trigger_reason

        return True, ""

    def check_monthly(self, current_equity: float, month_label: str) -> tuple[bool, str]:
        """Check monthly drawdown limit (Level 4)."""
        if self.triggered:
            return False, f"ALREADY TRIGGERED: {self.trigger_reason}"

        if month_label not in self.month_start_equity:
            self.month_start_equity[month_label] = current_equity

        month_start = self.month_start_equity[month_label]
        if month_start > 0:
            month_dd = (month_start - current_equity) / month_start
            if month_dd >= self.max_monthly_loss:
                self.triggered = True
                self.trigger_reason = f"Monthly drawdown {month_dd*100:.1f}% >= {self.max_monthly_loss*100:.0f}%"
                self._halt()
                return False, self.trigger_reason

        return True, ""

    def _halt(self) -> None:
        """Hard halt — log and prepare for system shutdown."""
        logger.critical(
            "KILL SWITCH TRIGGERED — %s | Time: %s",
            self.trigger_reason,
            datetime.now().isoformat(),
        )
        # In production: send urgent alert to all channels (Telegram, Discord, Email)

    def reset(self) -> None:
        """Manual reset — only after review."""
        logger.warning("Kill switch manually reset.")
        self.triggered = False
        self.trigger_reason = ""
        self.peak_equity = 0.0
        self.week_start_equity.clear()
        self.month_start_equity.clear()
