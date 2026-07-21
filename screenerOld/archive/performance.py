"""
performance.py — Performance Benchmarking
Benchmark berbagai metode/teknik dan bandingkan kecepatan.
Gunakan timeit untuk mengukur waktu eksekusi.
"""

import time
import timeit
import logging

logger = logging.getLogger(__name__)

def benchmark_func(func, *args, iterations=10, **kwargs):
    """
    Benchmark satu function call.
    
    Args:
        func: function to benchmark
        *args: positional arguments
        iterations: number of runs
        **kwargs: keyword arguments
    
    Returns:
        dict with avg_time, min_time, max_time, calls_per_sec
    """
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    
    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)
    calls_per_sec = 1.0 / avg_time if avg_time > 0 else float('inf')
    
    return {
        "avg_time_ms": round(avg_time * 1000, 2),
        "min_time_ms": round(min_time * 1000, 2),
        "max_time_ms": round(max_time * 1000, 2),
        "calls_per_sec": round(calls_per_sec, 1)
    }


def benchmark_screener_cycle(screener_module, iterations=3):
    """
    Benchmark cycle penuh screener (fetch + analyze for all tickers).
    
    Returns dict with breakdown per fase.
    """
    import time
    
    results = {}
    
    # Time full cycle without parallel
    start = time.perf_counter()
    for _ in range(iterations):
        screener_module.jalankan_screener_async()
    total = (time.perf_counter() - start) / iterations
    
    results["full_cycle_avg_seconds"] = round(total, 2)
    
    return results


def benchmark_ai_inference(model, X, iterations=100):
    """Benchmark AI inference in batches."""
    import torch
    
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        with torch.no_grad():
            _ = model(X)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    
    avg = sum(times) / len(times)
    calls_per_sec = 1.0 / avg if avg > 0 else float('inf')
    
    return {
        "avg_inference_ms": round(avg * 1000, 2),
        "inferences_per_sec": round(calls_per_sec, 1),
        "batch_size": X.size(0)
    }


def time_import(module_name: str) -> float:
    """Measure import time of a module."""
    import subprocess, sys
    
    script = f"import time; t=time.time(); import {module_name}; print(time.time()-t)"
    result = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True)
    return float(result.stdout.strip())
