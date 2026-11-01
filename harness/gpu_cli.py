"""受控 GPU 接口的本机命令行客户端；令牌只从进程环境读取。"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "mode", "unload", "wake", "generate"])
    parser.add_argument("value", nargs="?", help="mode命令的 AUTO 或 GAMING")
    parser.add_argument("--prompt", help="仅用于合成测试；省略则从标准输入读取")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    token = os.environ.get("NOVA_GPU_TOKEN", "")
    if not token:
        parser.error("请在当前进程设置 NOVA_GPU_TOKEN，不要把令牌放在命令行参数中")
    url = urlsplit(args.url)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        parser.error("仅允许本机127.0.0.1 HTTP服务根地址")
    payload = None
    if args.command == "mode":
        if args.value not in {"AUTO", "GAMING"}:
            parser.error("mode 后必须指定 AUTO 或 GAMING")
        payload = {"mode": args.value}
    if args.command == "generate":
        payload = {"prompt": args.prompt if args.prompt is not None else input("问题：")}
    request = Request(
        args.url.rstrip("/") + "/api/gpu/" + args.command,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        method="GET" if args.command == "status" else "POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=180) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except HTTPError as error:
        print(f"请求被拒绝，HTTP {error.code}；请查询状态并核对模式与认证。", file=sys.stderr)
    except URLError, OSError, ValueError:
        print("无法完成本机管理请求，请检查服务和配置。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
