"""Command line entry point for the standalone benchmark."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .cases import load_cases
from .config import (
    DEFAULT_CASES_PATH,
    DEFAULT_MODELS,
    DEFAULT_OUTPUT_DIR,
    REPO_ROOT,
    BenchmarkConfig,
)
from .environment import collect_environment, iso_now
from .nova_policy import NovaPolicyError, load_policy
from .ollama_client import OllamaClient, OllamaError
from .report import write_inventory
from .runner import BenchmarkRunner, describe_models
from .safety import SafetyError, check_preflight
from .sanitize import sanitize_config, sanitize_environment
from .suites import list_suites


def _json_print(document: object) -> None:
    print(json.dumps(document, ensure_ascii=False, indent=2, default=str))


def _parse_models(value: str) -> tuple[str, ...]:
    models = tuple(item.strip() for item in value.split(",") if item.strip())
    if not models:
        raise argparse.ArgumentTypeError("at least one model is required")
    return models


def _build_config(args: argparse.Namespace) -> BenchmarkConfig:
    output_dir = Path(args.output).resolve() if args.output else DEFAULT_OUTPUT_DIR
    cases_path = Path(args.cases).resolve() if args.cases else DEFAULT_CASES_PATH
    lease_path = Path(args.lease).resolve() if getattr(args, "lease", None) else None
    nova_state = Path(args.nova_state).resolve() if getattr(args, "nova_state", None) else None
    lock_path = Path(args.lock).resolve() if getattr(args, "lock", None) else None
    game_processes = tuple(args.game_process) if getattr(args, "game_process", None) else ()
    baseline = getattr(args, "baseline_commit", None) or os.environ.get(
        "NOVA_BENCH_BASELINE_COMMIT"
    )
    return BenchmarkConfig(
        ollama_url=args.ollama_url,
        models=args.models,
        cases_path=cases_path,
        case_ids=tuple(getattr(args, "case", []) or ()),
        suite=args.suite,
        warm_repetitions=getattr(args, "warm_reps", 3),
        cold_repetitions=getattr(args, "cold_reps", 1),
        output_dir=output_dir,
        timeout_s=args.timeout,
        settle_s=args.settle,
        sample_interval_s=args.sample_interval,
        max_duration_s=args.max_seconds,
        game_processes=game_processes,
        allow_experimental=args.allow_experimental,
        allow_unload_loaded=getattr(args, "allow_unload_loaded", False),
        baseline_commit=baseline,
        lease_path=lease_path,
        nova_state_path=nova_state,
        lock_path=lock_path,
        include_preview=bool(getattr(args, "preview", False))
        and not bool(getattr(args, "no_preview", False)),
        execute=getattr(args, "execute", False),
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--models", type=_parse_models, default=DEFAULT_MODELS)
    parser.add_argument("--cases", default=None, help="fixed case JSON file")
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    parser.add_argument("--suite", default="performance")
    parser.add_argument("--output", default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument("--baseline-commit", default=None)
    parser.add_argument(
        "--nova-state",
        default=None,
        help="Nova gpu-mode.json; defaults to the path from Nova settings",
    )
    parser.add_argument("--game-process", action="append", default=None)
    parser.add_argument("--lock", default=None)
    parser.add_argument(
        "--preview", action="store_true", help="store a short reply preview (off by default)"
    )
    parser.add_argument("--no-preview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--allow-experimental", action="store_true")
    parser.add_argument(
        "--allow-unload-loaded",
        action="store_true",
        help=(
            "allow evicting models already resident in Ollama; requires a lease that "
            "explicitly lists them and --execute"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.model_benchmark",
        description="Independent Nova local Ollama benchmark; never modifies production code.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    suites = subparsers.add_parser("suites", help="list implemented and reserved suites")
    suites.set_defaults(func=cmd_suites)

    inventory = subparsers.add_parser(
        "inventory",
        help="read Ollama tags/show and hardware metadata without loading a model",
    )
    _add_common(inventory)
    inventory.set_defaults(func=cmd_inventory, execute=False)

    preflight = subparsers.add_parser(
        "preflight",
        help="check GPU, RAM, games, Ollama and an optional GPU lease",
    )
    _add_common(preflight)
    preflight.add_argument("--execute", action="store_true")
    preflight.add_argument("--lease", default=None)
    preflight.set_defaults(func=cmd_preflight)

    run = subparsers.add_parser("run", help="run the cold/warm benchmark")
    _add_common(run)
    run.add_argument("--cold-reps", type=int, default=1)
    run.add_argument("--warm-reps", type=int, default=3)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--lease", default=None)
    run.set_defaults(func=cmd_run)
    return parser


def cmd_suites(args: argparse.Namespace) -> int:
    _json_print({"suites": list_suites()})
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    config = _build_config(args)
    config.validate()
    client = OllamaClient(config.ollama_url, timeout_s=min(config.timeout_s, 60.0))
    environment = collect_environment(Path.cwd())
    try:
        policy = load_policy(REPO_ROOT)
        environment["ollama"] = client.version()
        models = describe_models(client, config, environment, policy)
    except (OllamaError, NovaPolicyError, ValueError) as exc:
        _json_print({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 2
    cases = load_cases(config.cases_path, config.case_ids)
    output_dir = (
        Path(args.output).resolve()
        if args.output
        else config.output_dir / f"inventory-{iso_now().replace(':', '')}"
    )
    paths = write_inventory(output_dir, environment, models, cases)
    _json_print(
        {
            "status": "ok",
            "output_dir": str(output_dir),
            "files": [str(path) for path in paths],
            "models": [
                {
                    "id": model["id"],
                    "installed": model["installed"],
                    "quantization": (model.get("details") or {}).get("quantization_level"),
                    "admission": model["admission"],
                }
                for model in models
            ],
        }
    )
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    config = _build_config(args)
    config.validate()
    client = OllamaClient(config.ollama_url, timeout_s=min(config.timeout_s, 60.0))
    environment = collect_environment(Path.cwd())
    try:
        environment["ollama"] = client.version()
    except (OllamaError, ValueError) as exc:
        environment["ollama"] = {"version": None, "error": str(exc)}
    report = check_preflight(config, client, environment)
    _json_print(
        {
            "ok": report["ok"],
            "config": sanitize_config(config.as_dict()),
            "environment": sanitize_environment(environment),
            "checks": report["checks"],
            "warnings": report["warnings"],
            "loaded_models": report["loaded_models"],
            "lease": report["lease"],
        }
    )
    return 0 if report["ok"] else 2


def cmd_run(args: argparse.Namespace) -> int:
    config = _build_config(args)
    config.validate()
    client = OllamaClient(config.ollama_url, timeout_s=min(config.timeout_s, 60.0))
    environment = collect_environment(Path.cwd())
    try:
        environment["ollama"] = client.version()
    except (OllamaError, ValueError) as exc:
        environment["ollama"] = {"version": None, "error": str(exc)}
    runner = BenchmarkRunner(config, client, environment)
    if not args.execute:
        plan = runner.plan()
        preflight = check_preflight(config, client, environment)
        _json_print({"status": "dry-run", "plan": plan, "preflight": preflight})
        return 0 if preflight["ok"] else 2
    try:
        payload = runner.run()
    except (SafetyError, ValueError, OllamaError) as exc:
        blocked_dir = config.output_dir / f"blocked-{iso_now().replace(':', '')}"
        blocked_dir.mkdir(parents=True, exist_ok=True)
        document = {
            "status": "blocked",
            "error": f"{type(exc).__name__}: {exc}",
            "at": iso_now(),
            "config": sanitize_config(config.as_dict()),
            "environment": sanitize_environment(environment),
        }
        (blocked_dir / "blocked.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _json_print({**document, "output_dir": str(blocked_dir)})
        return 2
    _json_print(
        {
            "status": payload["status"],
            "run_id": payload["run_id"],
            "output_dir": str(config.output_dir / str(payload["run_id"])),
            "models": {
                model["id"]: {
                    "status": model.get("status"),
                    "admission": model.get("admission"),
                }
                for model in payload["models"]
            },
        }
    )
    if payload["status"] == "completed":
        return 0
    if payload["status"] == "partial":
        return 4
    return 5


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(
            json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
