"""应用工厂：资源初始化只发生在 lifespan，不发生在模块导入时。"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from nova.api.middleware import RequestContextMiddleware
from nova.api.router import register_routes
from nova.chat.registry import experimental_policy, stable_policies
from nova.chat.service import ChatService, idle_monitor
from nova.config import Settings, load_settings
from nova.errors import register_exception_handlers
from nova.gpu.backend import OllamaBackend
from nova.gpu.detection import GameDetector
from nova.gpu.manager import GpuManager
from nova.gpu.observation import WindowsSignalSource
from nova.gpu.pressure import MemoryPressureGuard
from nova.logging_config import configure_logging
from nova.storage.chat import ChatStore
from nova.storage.database import Database, DatabaseError

logger = logging.getLogger("nova.lifecycle")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    database = Database(settings.database_path, timeout=settings.db_timeout)
    experimental = experimental_policy(settings)
    gpu_manager = (
        GpuManager(
            OllamaBackend(
                settings.gpu_ollama_url, settings.gpu_model, settings.gpu_request_timeout
            ),
            settings.data_dir / "gpu-mode.json",
            drain_timeout=settings.gpu_drain_timeout,
            detection_enabled=settings.gpu_game_detection_enabled or settings.gpu_auto_game_enabled,
            pressure_enabled=settings.gpu_memory_pressure_enabled,
            model_policies={**stable_policies(), experimental["model"]: experimental},
            # 与注册表共享同一模板，wake/generate/chat 都受实验开关、RAM和显存门禁。
            experimental_policy=experimental,
        )
        if settings.gpu_enabled
        else None
    )
    chat_store = ChatStore(database) if settings.chat_enabled else None
    chat_service = ChatService(chat_store, gpu_manager, settings) if settings.chat_enabled else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level, settings.secret_values())
        app.state.ready = False
        try:
            if gpu_manager:
                await gpu_manager.start()
                if settings.gpu_auto_game_enabled or settings.gpu_memory_pressure_enabled:
                    app.state.signal_source = WindowsSignalSource()
                if settings.gpu_auto_game_enabled:
                    app.state.game_detector = GameDetector(
                        gpu_manager,
                        [],
                        scan=app.state.signal_source.scan,
                        interval=settings.gpu_game_poll_seconds,
                        automatic=True,
                    )
                    await app.state.game_detector.start()
                elif settings.gpu_game_detection_enabled:
                    app.state.game_detector = GameDetector(
                        gpu_manager,
                        settings.gpu_game_processes,
                        interval=settings.gpu_game_poll_seconds,
                    )
                    await app.state.game_detector.start()
                if settings.gpu_memory_pressure_enabled:
                    app.state.pressure_guard = MemoryPressureGuard(
                        gpu_manager,
                        app.state.signal_source,
                        interval=settings.gpu_game_poll_seconds,
                    )
                    await app.state.pressure_guard.start()
            try:
                await asyncio.to_thread(database.initialize)
            except DatabaseError:
                logger.error("database_initialization_failed")
                raise RuntimeError("数据库初始化失败，请检查数据库配置和文件状态") from None
            if chat_store:
                await asyncio.to_thread(chat_store.initialize)
                app.state.idle_task = asyncio.create_task(
                    idle_monitor(gpu_manager, settings.chat_idle_seconds)
                )
            app.state.ready = True
            logger.info("application_started")
            yield
        finally:
            app.state.ready = False
            try:
                try:
                    try:
                        if app.state.idle_task:
                            app.state.idle_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await app.state.idle_task
                    finally:
                        if chat_service:
                            await chat_service.close()
                finally:
                    try:
                        if app.state.game_detector:
                            await app.state.game_detector.stop()
                    finally:
                        try:
                            if app.state.pressure_guard:
                                await app.state.pressure_guard.stop()
                        finally:
                            try:
                                if app.state.signal_source:
                                    await app.state.signal_source.close()
                            finally:
                                if gpu_manager:
                                    await gpu_manager.close()
            finally:
                await asyncio.to_thread(database.close)
                logger.info("application_stopped")

    app = FastAPI(title="Nova", version="0.0.0", lifespan=lifespan, debug=False)
    app.state.settings = settings
    app.state.database = database
    app.state.gpu_manager = gpu_manager
    app.state.game_detector = None
    app.state.signal_source = None
    app.state.pressure_guard = None
    app.state.idle_task = None
    app.state.chat_service = chat_service
    app.state.ready = False
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    register_routes(app)
    return app
