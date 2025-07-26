# FILE: services/cache_service.py

import json
from pathlib import Path
from gi.repository import GLib
import threading # Import threading

# --- Data Caching ---
CACHE_DIR = Path(GLib.get_user_cache_dir()) / 'pipman'
CACHE_FILE = CACHE_DIR / 'sizes.json'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_cache_save_lock = threading.Lock() # Create a lock specifically for saving the cache file

def load_size_cache():
    """Loads the package size cache from a JSON file."""
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        # If file is corrupted or unreadable, treat as empty cache
        return {}

def save_size_cache(cache_data):
    """Saves the package size cache to a JSON file in a thread-safe manner."""
    with _cache_save_lock: # Acquire the lock before writing
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except IOError as e:
            # In a real app, you might log this to your app's main log
            print(f"Error saving size cache: {e}")