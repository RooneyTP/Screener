# (Validation helper — run from project directory)
if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    from telegram_notifier import (
        format_signal, send_telegram, send_scan_summary, _get_config
    )
    import yaml

    # 1. format_signal
    msg = format_signal("BBCA", 9875, 78.5, "STRONG_BUY", 9500, 10200, 2.33, "BULL")
    assert len(msg) <= 4000, f"Too long: {len(msg)}"
    assert "BBCA" in msg and "Rp" in msg
    print(f"format_signal(): OK ({len(msg)} chars)")

    # 2. format_signal — minimal
    msg2 = format_signal("TLKM", 3980, 55.0, "WEAK_BUY")
    assert len(msg2) <= 4000
    print(f"format_signal(min): OK ({len(msg2)} chars)")

    # 3. send_telegram disabled
    cfg = _get_config()
    assert cfg["enabled"] is False
    assert send_telegram("test") is False
    print("send_telegram(): disabled correctly")

    # 4. send_scan_summary disabled
    dummy = [
        {"ticker":"BBCA", "price":9875, "score":78.5, "signal":"STRONG_BUY",
         "regime":"BULL", "stop_loss":9500, "take_profit":10200, "rrr":2.33,
         "rsi":62.5, "adx":35.0, "vol_ratio":1.8, "ret_20d":3.2},
        {"ticker":"BBRI", "price":5450, "score":72.0, "signal":"BUY",
         "regime":"BULL", "stop_loss":5200, "take_profit":5800, "rrr":2.0,
         "rsi":58.0, "adx":32.0, "vol_ratio":1.5, "ret_20d":2.1},
    ]
    assert send_scan_summary(dummy) == 0
    print("send_scan_summary(): OK, returns 0 when disabled")

    # 5. YAML config
    with open(os.path.join(os.path.dirname(__file__), "config.yaml")) as f:
        pc = yaml.safe_load(f)
    assert "telegram" in pc
    assert pc["telegram"]["enabled"] is False
    assert pc["telegram"]["send_only"] == ["STRONG_BUY", "BUY"]
    print("config.yaml telegram section: OK")

    print("\n=== ALL CHECKS PASSED ===")
