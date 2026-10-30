from collections import OrderedDict


class LRUCache:
    """LRUCache(capacity): capacity >= 1 else ValueError.
    get(key, default=None): return value and mark as most recently used; default if missing.
    put(key, value): insert/update, mark most recent; evict least recently used when over capacity.
    __len__, __contains__ (does NOT change recency), keys(): from least to most recently used.
    """

    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity, self._d = capacity, OrderedDict()

    def get(self, key, default=None):
        if key not in self._d:
            return default
        self._d.move_to_end(key)
        return self._d[key]

    def put(self, key, value):
        self._d[key] = value
        self._d.move_to_end(key)
        if len(self._d) > self.capacity:
            self._d.popitem(last=False)

    def __len__(self):
        return len(self._d)

    def __contains__(self, key):
        return key in self._d

    def keys(self):
        return list(self._d)
