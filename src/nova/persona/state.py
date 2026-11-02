"""有限、会话隔离的进程内临时状态，不持久化或自动晋升人格事实。"""

import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class DynamicState:
    session_id: str
    topic: str
    expression: str
    expires_at: float

    def to_prompt_data(self):
        return asdict(self)


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("临时状态会话标识无效")
    return value


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("临时状态文本无效或超长")
    return value


class DynamicStateStore:
    """expires_at是进程单调时钟值；不作为用户事实或跨进程时间戳。"""

    def __init__(self, *, ttl_seconds=900, max_sessions=256, clock=time.monotonic):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("临时状态TTL必须为1至3600秒整数")
        if type(max_sessions) is not int or not 1 <= max_sessions <= 4096:
            raise ValueError("临时状态容量必须为1至4096整数")
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._states = OrderedDict()
        self._lock = threading.RLock()

    def _now(self):
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("临时状态时钟无效")
        return now

    def get(self, session_id):
        session_id = _identity(session_id)
        with self._lock:
            state = self._states.get(session_id)
            if state is not None and state.expires_at <= self._now():
                self._states.pop(session_id, None)
                return None
            return state

    def clear(self):
        """应用关闭时丢弃临时情境；不触碰持久人格或聊天历史。"""
        with self._lock:
            self._states.clear()

    def update(self, session_id, *, topic="", expression="neutral"):
        session_id = _identity(session_id)
        topic, expression = _text(topic, 200), _text(expression, 80)
        with self._lock:
            now = self._now()
            for key, state in tuple(self._states.items()):
                if state.expires_at <= now:
                    self._states.pop(key, None)
            if session_id not in self._states and len(self._states) >= self.max_sessions:
                self._states.popitem(last=False)
            state = DynamicState(session_id, topic, expression, now + self.ttl_seconds)
            self._states[session_id] = state
            self._states.move_to_end(session_id)
            return state

    def reset(self, session_id):
        with self._lock:
            return self._states.pop(_identity(session_id), None) is not None
