"""每日简报 AI 助手 Agent — 入口

用法：
    python main.py            # 进入对话模式
    python main.py --send     # 立即生成简报并发送邮件（单次）
    python main.py --daemon   # 常驻守护进程，到点自动发送邮件
    # TODO F9（P1）：也可配合 Windows 任务计划程序每天定时调用 --send
"""

import argparse
import time
from datetime import datetime

from agent import EverydayAgent
from mailer import send_email
from tools.config import GetLocationTool, SetLocationTool, _load_config
from tools.news import NewsTool
from tools.recipient import (
    AddRecipientTool,
    ListRecipientsTool,
    RemoveRecipientTool,
    SetSendTimeTool,
)
from tools.registry import ToolRegistry
from tools.todo import (
    AddTodoTool,
    CompleteTodoTool,
    DeleteTodoTool,
    ListTodoTool,
)
from tools.weather import WeatherTool


def build_registry() -> ToolRegistry:
    """注册全部 12 个工具，供 LLM 调用"""
    registry = ToolRegistry()
    # 天气与新闻
    registry.register(WeatherTool())
    registry.register(NewsTool())
    # 待办管理
    registry.register(ListTodoTool())
    registry.register(AddTodoTool())
    registry.register(CompleteTodoTool())
    registry.register(DeleteTodoTool())
    # 地址配置
    registry.register(GetLocationTool())
    registry.register(SetLocationTool())
    # 邮件收件人与发送时间
    registry.register(ListRecipientsTool())
    registry.register(AddRecipientTool())
    registry.register(RemoveRecipientTool())
    registry.register(SetSendTimeTool())
    return registry


def run_send(agent: EverydayAgent):
    """--send 模式：生成一次简报并发送邮件，单次执行后退出"""
    config = _load_config()
    recipients = config.get("recipients", [])
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"📧 收件人：{', '.join(recipients) if recipients else '（无）'}")
    print("生成简报中...\n")

    # agent.run 返回最终简报文本（Markdown），作为邮件正文
    report = agent.run("请生成今日简报")

    if not report:
        print("\n简报生成失败，未发送邮件")
        return

    subject = f"每日简报 — {today}"
    result = send_email(subject, report, recipients)
    print(f"\n📧 邮件发送结果：{result}")


def run_daemon(agent: EverydayAgent):
    """--daemon 模式：常驻循环，每天到 send_time 自动生成简报并发送邮件"""
    print("=" * 50)
    print("🔁 守护进程已启动")
    print("   程序将常驻运行，每天到点自动发送简报邮件")
    print("   按 Ctrl+C 退出")
    print("=" * 50)

    # last_sent_date 记录今天已发过的日期，避免同一分钟内重复发送
    last_sent_date = None

    while True:
        try:
            now = datetime.now()
            config = _load_config()
            send_time = config.get("send_time", "08:00")
            recipients = config.get("recipients", [])
            today_str = now.strftime("%Y-%m-%d")
            current_hm = now.strftime("%H:%M")

            # 命中发送时间 + 今天还没发过 + 有收件人
            if current_hm == send_time and last_sent_date != today_str and recipients:
                print(f"\n[{now.strftime('%Y-%m-%d %H:%M')}] 到达发送时间 {send_time}，开始生成简报...")
                report = agent.run("请生成今日简报")
                if report:
                    subject = f"每日简报 — {today_str}"
                    result = send_email(subject, report, recipients)
                    print(f"📧 邮件：{result}")
                else:
                    print("⚠️ 简报生成失败，本次跳过")
                # 标记今天已发，防止下一轮（同分钟内）重复触发
                last_sent_date = today_str
        except KeyboardInterrupt:
            print("\n守护进程已停止 👋")
            break
        except Exception as e:
            # 守护进程不能因单次异常退出，记录后继续
            print(f"⚠️ 守护循环异常：{type(e).__name__}: {e}")

        # 每 30 秒检查一次时间，平衡及时性与 CPU 占用
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description="每日简报 AI 助手 Agent")
    parser.add_argument("--send", action="store_true", help="立即生成简报并发送邮件（单次）")
    parser.add_argument("--daemon", action="store_true", help="常驻守护进程，到点自动发送邮件")
    args = parser.parse_args()

    registry = build_registry()
    agent = EverydayAgent(registry)

    if args.send:
        run_send(agent)
    elif args.daemon:
        run_daemon(agent)
    else:
        # 默认对话模式
        print("=" * 50)
        print("📅 每日简报助手已就绪")
        print("   试试输入：出简报 / 把地址改成上海 / 加一条 todo 下午开会")
        print("   邮件相关：加收件人 a@b.com / 发送时间改 8点 / 查看收件人")
        print("   输入 exit 或 退出 结束对话")
        print("=" * 50)

        while True:
            try:
                user_input = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见 👋")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "退出"):
                print("再见 👋")
                break

            agent.run(user_input)


if __name__ == "__main__":
    main()
