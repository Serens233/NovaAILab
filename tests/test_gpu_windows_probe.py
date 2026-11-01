"""几何信号只证明窗口状态，不单独赋予游戏标签。"""

import pytest

from nova.gpu.windows_probe import window_features


@pytest.mark.parametrize(
    "rect,monitor,style,visible,minimized,expected",
    [
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), 0, True, False, (True, True)),
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), 0x00C00000, True, False, (True, False)),
        ((1920, 0, 3840, 1080), (1920, 0, 3840, 1080), 0, True, False, (True, True)),
        ((0, 0, 1280, 720), (0, 0, 1920, 1080), 0, True, False, (False, False)),
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), 0, True, True, (False, False)),
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), 0, False, False, (False, False)),
        (None, (0, 0, 1920, 1080), 0, True, False, (False, False)),
    ],
)
def test_window_features(rect, monitor, style, visible, minimized, expected):
    result = window_features(rect, monitor, style, visible=visible, minimized=minimized)
    assert (result["fullscreen"], result["borderless"]) == expected
