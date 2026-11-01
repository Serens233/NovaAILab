"""启用无白名单与压力保护后，复用原真实Ollama/鉴权/模式回合验收。"""

import os
import sys

from verify_gpu_live import main

if __name__ == "__main__":
    # 只传给本轮验收子进程，不写.env或全局Windows环境。
    os.environ["NOVA_GPU_AUTO_GAME_ENABLED"] = "true"
    os.environ["NOVA_GPU_GAME_DETECTION_ENABLED"] = "false"
    os.environ["NOVA_GPU_MEMORY_PRESSURE_ENABLED"] = "true"
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
