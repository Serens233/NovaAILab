"""增量正文过滤：显式思考标签属于内部字段，不进入聊天文本。"""


class AnswerFilter:
    """只识别明确的成对标签，不尝试从普通文字猜测模型的内部思考。"""

    _names = ("think", "thinking", "analysis", "reasoning")
    _tokens = {
        token: (name, closing)
        for name in _names
        for closing, token in ((False, f"<{name}>"), (True, f"</{name}>"))
    }

    def __init__(self):
        self._pending = ""
        self._hidden = []

    def feed(self, text):
        self._pending += text
        visible = []
        while self._pending:
            marker = self._pending.find("<")
            if marker == -1:
                if not self._hidden:
                    visible.append(self._pending)
                self._pending = ""
                break
            if marker:
                if not self._hidden:
                    visible.append(self._pending[:marker])
                self._pending = self._pending[marker:]
            folded = self._pending.lower()
            matched = next((token for token in self._tokens if folded.startswith(token)), None)
            if matched:
                name, closing = self._tokens[matched]
                if closing:
                    if not self._hidden or self._hidden[-1] != name:
                        raise ValueError("unbalanced_reasoning_marker")
                    self._hidden.pop()
                else:
                    self._hidden.append(name)
                self._pending = self._pending[len(matched) :]
            elif any(token.startswith(folded) for token in self._tokens):
                # 标签可能在下一块才结束；此处最多保留一个短标签前缀。
                break
            else:
                if not self._hidden:
                    visible.append("<")
                self._pending = self._pending[1:]
        return "".join(visible)

    def finish(self):
        if self._hidden or self._pending:
            raise ValueError("unfinished_reasoning_marker")


def final_answer(text):
    filtered = AnswerFilter()
    visible = filtered.feed(text)
    filtered.finish()
    return visible
