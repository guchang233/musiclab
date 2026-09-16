"""musiclab 命令行入口。

子命令：
- ``backends``  列出后端可用性
- ``separate``  多轨分离
- ``transcribe`` 单文件转谱
- ``pipeline``  端到端：分离 + 逐轨转谱
- ``serve``     启动核心服务进程（HTTP API，供 UI 壳调用）

退出码：0 成功；2 参数/输入错误；3 后端不可用；4 未知内部错误。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from musiclab.config import EngineConfig, default_cache_dir
from musiclab.engine.errors import (
    AudioLoadError,
    BackendNotAvailableError,
    MusicLabError,
)
from musiclab.types import STEM_PRESETS

logger = logging.getLogger("musiclab")

_EXIT_USAGE = 2
_EXIT_BACKEND = 3
_EXIT_INTERNAL = 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musiclab",
        description="音乐工作站内核 CLI：音源分离 + WAV→MIDI 转录",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- backends ----
    sub.add_parser("backends", help="列出全部后端及其可用性")

    # ---- separate ----
    p_sep = sub.add_parser("separate", help="多轨分离")
    p_sep.add_argument("input", help="输入音频文件")
    p_sep.add_argument("-o", "--output", required=True, help="输出目录")
    p_sep.add_argument(
        "--stems", default="4", choices=sorted(STEM_PRESETS), help="分轨预设（默认 4）"
    )
    p_sep.add_argument("--backend", default=None, help="指定分离后端（默认自动）")
    p_sep.add_argument("--no-cache", action="store_true", help="禁用产物缓存")

    # ---- transcribe ----
    p_tr = sub.add_parser("transcribe", help="单文件 WAV→MIDI 转录")
    p_tr.add_argument("input", help="输入音频文件")
    p_tr.add_argument("-o", "--output", required=True, help="输出 .mid 路径")
    p_tr.add_argument(
        "--instrument", default="auto", help="乐器标签（bass/vocals/drums/…，默认 auto）"
    )
    p_tr.add_argument(
        "--polyphonic",
        action="store_true",
        help="内容为多声部/和弦时指定（选择多音轨能力的后端，避免单音后端输出垃圾）",
    )
    p_tr.add_argument("--backend", default=None, help="指定转录后端（默认自动）")
    p_tr.add_argument("--tempo", type=float, default=120.0, help="MIDI tempo（默认 120）")

    # ---- pipeline ----
    p_pl = sub.add_parser("pipeline", help="端到端：分离 + 逐轨转谱")
    p_pl.add_argument("input", help="输入音频文件")
    p_pl.add_argument("-o", "--output", required=True, help="输出目录")
    p_pl.add_argument(
        "--stems", default="4", choices=sorted(STEM_PRESETS), help="分轨预设（默认 4）"
    )
    p_pl.add_argument("--skip", nargs="*", default=[], help="跳过转谱的分轨名列表")
    p_pl.add_argument("--tempo", type=float, default=120.0, help="MIDI tempo（默认 120）")
    p_pl.add_argument("--no-cache", action="store_true", help="禁用产物缓存")
    p_pl.add_argument("--json", action="store_true", help="以 JSON 输出执行报告")

    # ---- serve ----
    p_sv = sub.add_parser(
        "serve", help="启动核心服务进程（HTTP JSON API + SSE 进度流，供 UI 壳调用）"
    )
    p_sv.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p_sv.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    p_sv.add_argument("--workers", type=int, default=1, help="worker 线程数（默认 1）")
    return parser


def _config_from(args: argparse.Namespace) -> EngineConfig:
    """从 CLI 参数构建引擎配置。

    缓存目录走默认（XDG）；``--no-cache`` 通过 cache_dir=None 表达。
    ``--backend`` 仅在 separate/transcribe 子命令里作为后端选择器使用。
    """
    cache_dir = None if getattr(args, "no_cache", False) else default_cache_dir()
    return EngineConfig(cache_dir=cache_dir)


def _ensure_input(path: str) -> None:
    """输入文件前置校验：缺文件时报文件错误而非后端错误。"""
    from pathlib import Path

    if not Path(path).is_file():
        print(f"错误：音频文件不存在 {path}", file=sys.stderr)
        raise SystemExit(_EXIT_USAGE)


def cmd_backends(args: argparse.Namespace) -> int:
    from musiclab.engine import get_default_registry

    registry = get_default_registry()
    rows = registry.diagnostics()
    print(f"{'后端':<16}{'类型':<12}{'层级':<6}{'状态':<18}{'说明'}")
    print("-" * 88)
    for row in rows:
        status = "可用" if row["available"] else f"不可用：{row['reason']}"
        hint = f"（{row['install_hint']}）" if not row["available"] and row.get("install_hint") else ""
        print(
            f"{row['name']:<16}{row['kind']:<12}{row['tier']:<6}{status}{hint}  {row['description']}"
        )
    return 0


def cmd_separate(args: argparse.Namespace) -> int:
    from musiclab.audio import load_audio, write_stems
    from musiclab.engine import get_default_registry
    from musiclab.types import STEM_PRESETS

    schema = STEM_PRESETS[args.stems]
    config = _config_from(args)
    _ensure_input(args.input)
    registry = get_default_registry()
    separator = registry.separator(schema, name=args.backend)
    print(f"分离后端：{separator.name}（schema={list(schema)}）")

    audio = load_audio(args.input, sample_rate=config.sample_rate)
    stems = separator.separate(audio, schema)
    paths = write_stems(args.output, stems)
    for stem, path in zip(stems, paths):
        print(f"  {stem.name:<12} → {path}")
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    from musiclab.audio import load_audio
    from musiclab.engine import get_default_registry
    from musiclab.midi import write_midi

    is_drums = args.instrument == "drums"
    _ensure_input(args.input)
    registry = get_default_registry()
    # 单文件转谱默认接受单音后端（用户清楚自己的内容）；
    # 内容是多声部时用 --polyphonic 显式声明，避免单音后端输出垃圾
    transcriber = registry.transcriber(
        args.instrument, polyphonic=args.polyphonic, name=args.backend
    )
    print(f"转谱后端：{transcriber.name}（instrument={transcriber.instrument}）")

    audio = load_audio(args.input)
    result = transcriber.transcribe(audio)
    path = write_midi(args.output, result, tempo_bpm=args.tempo)
    print(f"音符数：{len(result.notes)} → {path}")
    for w in result.warnings:
        print(f"  警告：{w}", file=sys.stderr)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    from musiclab.pipeline import TranscriptionPipeline

    _ensure_input(args.input)
    config = _config_from(args)
    routing = {name: "skip" for name in args.skip}
    pipeline = TranscriptionPipeline(config=config)
    report = pipeline.run(
        args.input, args.output, stems=args.stems, routing=routing, tempo_bpm=args.tempo
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"分轨：{', '.join(report.schema)}")
        for stem, path in report.midi_files.items():
            print(f"  {stem:<12} {report.notes_count.get(stem, 0):>4} 音符 → {path}")
        for w in report.warnings:
            print(f"  警告：{w}", file=sys.stderr)
        print(f"总耗时：{report.total_seconds:.1f}s")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from musiclab.service import serve as run_service

    print(f"musiclab 服务已启动：http://{args.host}:{args.port}")
    print("API：GET /api/health | GET /api/backends | POST /api/tasks")
    try:
        server, _thread = run_service(
            host=args.host, port=args.port, workers=args.workers, background=False
        )
        server.serve_forever()  # 主线程接管事件循环，Ctrl-C 退出
    except KeyboardInterrupt:
        print("已停止", file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    handlers = {
        "backends": cmd_backends,
        "separate": cmd_separate,
        "transcribe": cmd_transcribe,
        "pipeline": cmd_pipeline,
        "serve": cmd_serve,
    }
    try:
        return handlers[args.command](args)
    except SystemExit as exc:  # _ensure_input 等子命令内的主动退出
        return int(exc.code or 0)
    except BackendNotAvailableError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return _EXIT_BACKEND
    except (AudioLoadError, MusicLabError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return _EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"错误：文件不存在 {exc}", file=sys.stderr)
        return _EXIT_USAGE
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 —— CLI 边界的最后防线
        logger.exception("未预期的错误")
        print(f"内部错误：{exc}", file=sys.stderr)
        return _EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
