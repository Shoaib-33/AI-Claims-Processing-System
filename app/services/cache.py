from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np


def stable_hash(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class LRUCache:
    def __init__(self, max_size: int = 256) -> None:
        self.max_size = max_size
        self._items: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def set(self, key: str, value: Any) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        if len(self._items) > self.max_size:
            self._items.popitem(last=False)


@dataclass
class SemanticCacheEntry:
    vector: list[float]
    value: Any


class SemanticCache:
    def __init__(self, threshold: float = 0.92, max_size: int = 128) -> None:
        self.threshold = threshold
        self.max_size = max_size
        self._items: list[SemanticCacheEntry] = []

    def get(self, vector: list[float]) -> Any | None:
        if not self._items:
            return None
        query = np.array(vector, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return None

        best_score = -1.0
        best_value = None
        for entry in self._items:
            candidate = np.array(entry.vector, dtype=np.float32)
            denom = query_norm * np.linalg.norm(candidate)
            score = float(np.dot(query, candidate) / denom) if denom else 0.0
            if score > best_score:
                best_score = score
                best_value = entry.value

        return best_value if best_score >= self.threshold else None

    def set(self, vector: list[float], value: Any) -> None:
        self._items.append(SemanticCacheEntry(vector=vector, value=value))
        if len(self._items) > self.max_size:
            self._items.pop(0)


class RedisSemanticCache:
    def __init__(self, namespace: str, threshold: float = 0.92, max_size: int = 256) -> None:
        self.namespace = namespace
        self.threshold = threshold
        self.max_size = max_size
        self.enabled = False
        self._redis = None

        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return

        try:
            import redis

            self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()
            self.enabled = True
        except Exception:
            self._redis = None
            self.enabled = False

    def get(self, vector: list[float]) -> Any | None:
        if not self.enabled or self._redis is None:
            return None

        query = np.array(vector, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return None

        best_score = -1.0
        best_value = None
        index_key = f"{self.namespace}:index"
        for item_key in self._redis.lrange(index_key, 0, -1):
            raw = self._redis.get(item_key)
            if not raw:
                continue
            item = json.loads(raw)
            candidate = np.array(item["vector"], dtype=np.float32)
            denom = query_norm * np.linalg.norm(candidate)
            score = float(np.dot(query, candidate) / denom) if denom else 0.0
            if score > best_score:
                best_score = score
                best_value = item["value"]

        return best_value if best_score >= self.threshold else None

    def set(self, vector: list[float], value: Any) -> None:
        if not self.enabled or self._redis is None:
            return

        payload = {"vector": vector, "value": value}
        item_key = f"{self.namespace}:item:{stable_hash(payload)}"
        index_key = f"{self.namespace}:index"
        self._redis.set(item_key, json.dumps(payload, default=str))
        self._redis.lpush(index_key, item_key)
        self._redis.ltrim(index_key, 0, self.max_size - 1)
