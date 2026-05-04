import os
from concurrent.futures import ThreadPoolExecutor

# Redis for caching (open-source version) - LAZY LOADED, NO BLOCKING
redis_client = None
_redis_checked = False

def _init_redis():
    """Lazy initialize Redis connection only when first cache operation is used"""
    global redis_client, _redis_checked
    if _redis_checked:
        return redis_client
    
    _redis_checked = True
    try:
        import redis
        # Fast timeout - 0.5s only
        conn = redis.Redis(
            host='localhost',
            port=6379,
            db=0,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_keepalive=False,
            socket_keepalive_options=None,
        )
        conn.ping()  # Test connection
        redis_client = conn
    except:
        redis_client = None
    
    return redis_client

def cache_get(key: str):
    """Get value from cache (Redis or None)"""
    conn = _init_redis()
    if not conn:
        return None
    try:
        return conn.get(key)
    except:
        return None

def cache_set(key: str, value: str, expire: int = 3600):
    """Set value in cache (Redis or skip if unavailable)"""
    conn = _init_redis()
    if not conn:
        return
    try:
        conn.set(key, value, ex=expire)
    except:
        pass

def cache_delete(key: str):
    """Delete key from cache (Redis or skip if unavailable)"""
    conn = _init_redis()
    if not conn:
        return
    try:
        conn.delete(key)
    except:
        pass

# Thread executor for parallel tasks
executor = ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4))

def run_in_executor_sync(func, items: list):
    """Run function on multiple items in parallel"""
    from concurrent.futures import as_completed
    results = []
    futures = {executor.submit(func, item): item for item in items}
    for future in as_completed(futures):
        try:
            results.append(future.result())
        except Exception as e:
            print(f"Error in executor: {e}")
    return results

# Batch processing for multiple items
def process_batch_sync(func, items: list, batch_size: int = 10):
    """Process items in batches synchronously"""
    results = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        batch_results = run_in_executor_sync(func, batch)
        results.extend(batch_results)
    return results

# Memory optimization: Use generators for large datasets
def lazy_data_processor(data_generator):
    for data in data_generator:
        yield process_data(data)

def process_data(data):
    # Placeholder for data processing
    return data