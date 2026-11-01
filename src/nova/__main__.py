"""Windows 与本地开发启动器，环境中的 HOST / PORT 实际用于服务配置。"""

import argparse
import logging
import sys

import uvicorn

from nova.config import ConfigurationError, load_settings
from nova.logging_config import configure_logging
from nova.main import create_app
from nova.storage.database import Database, DatabaseError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova 后端基础架构")
    parser.add_argument("--init-db", action="store_true", help="只执行幂等数据库初始化后退出")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.secret_values())
    if args.init_db:
        database = Database(settings.database_path, timeout=settings.db_timeout)
        try:
            database.initialize()
        except DatabaseError:
            logging.getLogger("nova.bootstrap").error("database_initialization_failed")
            return 1
        finally:
            database.close()
        print("数据库初始化完成；无业务表。")
        return 0
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
