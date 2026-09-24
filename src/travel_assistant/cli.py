"""命令行界面。

用法::

    python -m travel_assistant.cli                # 交互式对话
    python -m travel_assistant.cli --image a.jpg  # 先发一张图片
    python -m travel_assistant.cli -m "帮我规划杭州3天"   # 单次提问后退出
    python -m travel_assistant.cli --memory       # 查看长期记忆
    python -m travel_assistant.cli --trips        # 查看历史行程归档
    python -m travel_assistant.cli --sessions     # 查看历史会话
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .agent import TravelAssistant
from .config import get_settings
from .memory import PROFILE_KEYS
from .tools.attractions_data import ATTRACTIONS, BY_CITY

console = Console()

BANNER = """[bold cyan]旅行出行助手[/bold cyan] [dim]· 基于 DeepSeek 的旅游规划智能体[/dim]

[dim]我能做：出行方案规划 · 游玩措施推荐 · 旅行费用规划 · 景点图片识别[/dim]
[dim]命令：/help 帮助  /new 新会话  /memory 长期记忆  /clear 清空记忆  /exit 退出[/dim]
[dim]发图片：/img <图片路径> [可选说明][/dim]
"""

HELP = """[bold]可用命令[/bold]
  /help            显示本帮助
  /new             开始一个新会话（保留长期记忆）
  /memory          查看长期记忆（用户画像）
  /forget <字段>   删除某条长期记忆，例如 /forget home_city
  /clear           清空全部长期记忆
  /trips           查看历史行程归档
  /cities          查看内置收录的城市
  /img <路径> [说明]  发送图片进行景点识别
  /exit 或 /quit   退出

[bold]直接输入文字即可对话。[/bold]
"""


def _mask(key: str) -> str:
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="travel", description="旅行出行助手 —— 旅游规划智能体"
    )
    p.add_argument("-m", "--message", help="单次提问后退出（非交互模式）")
    p.add_argument("-i", "--image", action="append", default=[], help="随消息发送的图片路径，可多次指定")
    p.add_argument("--hint", default="", help="给图片识别补充的线索")
    p.add_argument("--session", help="指定会话 id（继续之前的会话）")
    p.add_argument("--memory", action="store_true", help="查看长期记忆后退出")
    p.add_argument("--clear-memory", action="store_true", help="清空长期记忆后退出")
    p.add_argument("--trips", action="store_true", help="查看行程归档后退出")
    p.add_argument("--sessions", action="store_true", help="查看会话列表后退出")
    p.add_argument("--cities", action="store_true", help="查看内置城市后退出")
    p.add_argument("--tools", action="store_true", help="查看可用工具后退出")
    p.add_argument("--no-long-term", action="store_true", help="本次不写入长期记忆（调试用）")
    return p


# ---------------------------------------------------------------------- #
# 展示辅助
# ---------------------------------------------------------------------- #
def show_memory(assistant: TravelAssistant) -> None:
    prof = assistant.memory.get_profile()
    if not prof:
        console.print("[dim]长期记忆为空。随着对话进行，我会自动记住你的偏好。[/dim]")
        return
    lines = [f"[bold]{PROFILE_KEYS.get(k, k)}[/bold]：{v}" for k, v in prof.items()]
    console.print(Panel("\n".join(lines), title="长期记忆（跨会话保留）", border_style="cyan"))


def show_trips(assistant: TravelAssistant) -> None:
    trips = assistant.memory.list_trips()
    if not trips:
        console.print("[dim]暂无行程归档。[/dim]")
        return
    lines = [
        f"[bold]#{t['id']}[/bold] {t['city']} · {t['days']}天 · {t['people']}人"
        + (f" · {t['tier']}" if t["tier"] else "")
        + f" [dim]{t['created_at']}[/dim]"
        for t in trips
    ]
    console.print(Panel("\n".join(lines), title="历史行程", border_style="cyan"))


def show_sessions(assistant: TravelAssistant) -> None:
    sessions = assistant.memory.list_sessions()
    lines = [
        f"[bold]{s['id']}[/bold] · {s['title']} [dim]{s['updated_at']}[/dim]"
        for s in sessions
    ]
    console.print(Panel("\n".join(lines) or "无", title="会话列表", border_style="cyan"))


def show_cities() -> None:
    lines = []
    for city, items in sorted(BY_CITY.items(), key=lambda x: -len(x[1])):
        names = "、".join(a["name"] for a in items)
        lines.append(f"[bold]{city}[/bold]（{len(items)}）：{names}")
    console.print(Panel("\n".join(lines), title=f"内置景点库（{len(ATTRACTIONS)} 个景点）", border_style="cyan"))


def show_tools(assistant: TravelAssistant) -> None:
    lines = []
    for spec in assistant.registry.specs():
        lines.append(f"[bold cyan]{spec.name}[/bold cyan]\n  {spec.description}")
    console.print(Panel("\n".join(lines), title="可用工具", border_style="cyan"))


# ---------------------------------------------------------------------- #
# 对话渲染
# ---------------------------------------------------------------------- #
async def run_turn(
    assistant: TravelAssistant,
    text: str,
    images: list[Path] | None = None,
    hint: str = "",
) -> None:
    """执行一轮对话并实时渲染事件。"""
    try:
        async for ev in assistant.stream(text, image_paths=images or [], image_hint=hint):
            et = ev["type"]
            if et == "status":
                console.print(f"[dim]· {ev['text']}…[/dim]")
            elif et == "vision":
                r = ev["result"]
                if r.get("is_travel_image"):
                    body = (
                        f"场景：{r['scene_type']}\n"
                        f"地标：{'、'.join(r['landmarks']) or '未确定'}\n"
                        f"地区：{r.get('country') or '未知'}"
                        + (f" · {r['region']}" if r.get("region") else "")
                        + (f" · {r['city']}" if r.get("city") else "")
                        + f"\n置信度：{r['confidence']}\n{r['description']}"
                    )
                else:
                    body = f"未识别出景点。\n{r['description']}"
                console.print(Panel(body, title="图片识别", border_style="magenta"))
            elif et == "tool_call":
                args = ev["args"]
                arg_s = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "无参数"
                console.print(f"[yellow]→ 调用工具[/yellow] [bold]{ev['name']}[/bold]({arg_s})")
            elif et == "tool_result":
                mark = "[green]✓[/green]" if ev["ok"] else "[red]✗[/red]"
                first = (ev["summary"] or "").strip().splitlines()
                head = first[0] if first else ""
                console.print(f"  {mark} [dim]{head[:160]}[/dim]")
            elif et == "final":
                console.print()
                console.print(Markdown(ev["content"]))
                console.print()
            elif et == "error":
                console.print(f"[red]错误：{ev['message']}[/red]")
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断本轮对话[/yellow]")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]运行异常：{type(e).__name__}: {e}[/red]")


async def interactive(assistant: TravelAssistant) -> None:
    console.print(BANNER)
    console.print(
        f"[dim]会话 id：{assistant.session_id}　"
        f"模型：{assistant.settings.deepseek_model}　"
        f"密钥：{_mask(assistant.settings.deepseek_api_key)}[/dim]\n"
    )

    while True:
        try:
            raw = console.input("[bold green]你 ▸[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见！[/dim]")
            break

        if not raw:
            continue

        # ---- 命令处理 ----
        if raw.startswith("/"):
            parts = raw.split(maxsplit=2)
            cmd = parts[0].lower()
            if cmd in ("/exit", "/quit"):
                console.print("[dim]再见！[/dim]")
                break
            if cmd == "/help":
                console.print(HELP)
                continue
            if cmd == "/new":
                assistant.session_id = assistant.memory.ensure_session(None, "新对话")
                console.print(f"[green]已开始新会话：{assistant.session_id}[/green]")
                continue
            if cmd == "/memory":
                show_memory(assistant)
                continue
            if cmd == "/clear":
                assistant.memory.clear_profile()
                console.print("[green]长期记忆已清空[/green]")
                continue
            if cmd == "/forget":
                if len(parts) < 2:
                    console.print("[red]用法：/forget <字段名>[/red]")
                else:
                    assistant.memory.delete_profile(parts[1])
                    console.print(f"[green]已删除：{parts[1]}[/green]")
                continue
            if cmd == "/trips":
                show_trips(assistant)
                continue
            if cmd == "/cities":
                show_cities()
                continue
            if cmd == "/img":
                if len(parts) < 2:
                    console.print("[red]用法：/img <图片路径> [说明][/red]")
                    continue
                path = Path(parts[1].strip('"').strip("'"))
                if not path.exists():
                    console.print(f"[red]图片不存在：{path}[/red]")
                    continue
                hint = parts[2] if len(parts) > 2 else ""
                console.print(f"[dim]发送图片：{path.name}[/dim]")
                await run_turn(assistant, "", [path], hint)
                continue
            console.print(f"[red]未知命令：{cmd}，输入 /help 查看帮助[/red]")
            continue

        # ---- 普通对话 ----
        await run_turn(assistant, raw)


async def amain(args: argparse.Namespace) -> int:
    settings = get_settings()

    try:
        assistant = TravelAssistant(
            settings,
            session_id=args.session,
            enable_long_term_memory=not args.no_long_term,
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return 2

    try:
        # 一次性查询类参数
        if args.memory:
            show_memory(assistant)
            return 0
        if args.clear_memory:
            assistant.memory.clear_profile()
            console.print("[green]长期记忆已清空[/green]")
            return 0
        if args.trips:
            show_trips(assistant)
            return 0
        if args.sessions:
            show_sessions(assistant)
            return 0
        if args.cities:
            show_cities()
            return 0
        if args.tools:
            show_tools(assistant)
            return 0

        images = [Path(p) for p in args.image]
        for p in images:
            if not p.exists():
                console.print(f"[red]图片不存在：{p}[/red]")
                return 2

        # 单次提问模式
        if args.message or images:
            await run_turn(assistant, args.message or "", images, args.hint)
            return 0

        # 交互模式
        await interactive(assistant)
        return 0
    finally:
        await assistant.aclose()


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
