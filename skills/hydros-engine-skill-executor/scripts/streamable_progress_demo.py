#!/usr/bin/env python3
"""
流式进度条演示脚本

演示两种进度条实现方式：
1. Polling（轮询）模式 - 当前 hydros-engine-skill 使用的方式
2. Streamable（流式）模式 - 更高频的连续输出方式

使用方法：
    python streamable_progress_demo.py --mode polling
    python streamable_progress_demo.py --mode streamable
"""

import sys
import time
import argparse


def format_progress_bar(current: int, total: int, width: int = 10) -> str:
    """
    格式化进度条

    Args:
        current: 当前进度
        total: 总进度
        width: 进度条宽度（字符数）

    Returns:
        格式化的进度条字符串，如 "███░░░░░░░15.4% | 185/1200"
    """
    percentage = (current / total) * 100
    filled = int((current / total) * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar}{percentage:5.1f}% | {current}/{total}"


def polling_mode_demo(total_steps: int = 1200, poll_interval: float = 5.0):
    """
    轮询模式演示

    模拟当前 hydros-engine-skill 的实现方式：
    - 客户端定时查询进度
    - 每次查询间隔 5-10 秒
    - 进度跳跃式更新

    Args:
        total_steps: 总步数
        poll_interval: 轮询间隔（秒）
    """
    print("=" * 60)
    print("轮询模式（Polling）演示")
    print("=" * 60)
    print(f"总步数: {total_steps}")
    print(f"轮询间隔: {poll_interval} 秒")
    print()

    # 模拟仿真进度（每秒完成约 12 步）
    steps_per_second = 12
    current_step = 0

    print("开始监测...")
    print()

    while current_step < total_steps:
        # 模拟轮询：调用 get_task_step
        print(f"[轮询] 查询当前步数...")

        # 模拟在轮询间隔内，仿真继续推进
        current_step = min(current_step + int(steps_per_second * poll_interval), total_steps)

        # 输出进度条
        progress_bar = format_progress_bar(current_step, total_steps)
        print(f"进度: {progress_bar}")
        print()

        if current_step < total_steps:
            print(f"[等待 {poll_interval} 秒...]")
            print()
            time.sleep(poll_interval)

    print("✓ 任务完成")
    print()


def streamable_mode_demo(total_steps: int = 1200, update_interval: float = 0.5):
    """
    流式模式演示

    模拟服务端主动推送进度的方式：
    - 服务端每完成一定步数就立即推送
    - 更新频率高（毫秒级到秒级）
    - 进度平滑更新

    Args:
        total_steps: 总步数
        update_interval: 更新间隔（秒）
    """
    print("=" * 60)
    print("流式模式（Streamable）演示")
    print("=" * 60)
    print(f"总步数: {total_steps}")
    print(f"更新间隔: {update_interval} 秒")
    print()

    # 模拟仿真进度（每秒完成约 12 步）
    steps_per_second = 12
    steps_per_update = max(1, int(steps_per_second * update_interval))

    print("开始监测...")
    print()

    current_step = 0
    while current_step < total_steps:
        # 模拟服务端推送进度更新
        current_step = min(current_step + steps_per_update, total_steps)

        # 输出进度条（流式输出）
        progress_bar = format_progress_bar(current_step, total_steps)

        # 使用 \r 实现原地刷新（仅在终端环境有效）
        if sys.stdout.isatty():
            print(f"\r进度: {progress_bar}", end="", flush=True)
        else:
            # 非终端环境，追加输出
            print(f"进度: {progress_bar}")

        if current_step < total_steps:
            time.sleep(update_interval)

    # 确保最后一行输出完整
    if sys.stdout.isatty():
        print()

    print()
    print("✓ 任务完成")
    print()

def comparison_demo():
    """
    对比演示

    并排展示轮询模式和流式模式的区别
    """
    print("=" * 60)
    print("轮询 vs 流式 对比演示")
    print("=" * 60)
    print()

    print("【轮询模式特点】")
    print("- 客户端主动查询")
    print("- 更新间隔：5-10 秒")
    print("- 进度跳跃式更新")
    print("- 实时性：取决于轮询间隔")
    print()

    print("【流式模式特点】")
    print("- 服务端主动推送")
    print("- 更新间隔：毫秒到秒级")
    print("- 进度平滑更新")
    print("- 实时性：高")
    print()

    input("按 Enter 键开始轮询模式演示...")
    polling_mode_demo(total_steps=300, poll_interval=2.0)

    input("按 Enter 键开始流式模式演示...")
    streamable_mode_demo(total_steps=300, update_interval=0.2)


def main():
    parser = argparse.ArgumentParser(
        description="流式进度条演示脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  %(prog)s --mode polling       # 演示轮询模式
  %(prog)s --mode streamable    # 演示流式模式
  %(prog)s --mode comparison    # 对比演示
        """
    )

    parser.add_argument(
        "--mode",
        choices=["polling", "streamable", "comparison"],
        default="comparison",
        help="演示模式（默认：comparison）"
    )

    parser.add_argument(
        "--steps",
        type=int,
        default=1200,
        help="总步数（默认：1200）"
    )

    args = parser.parse_args()

    if args.mode == "polling":
        polling_mode_demo(total_steps=args.steps)
    elif args.mode == "streamable":
        streamable_mode_demo(total_steps=args.steps)
    elif args.mode == "comparison":
        comparison_demo()


if __name__ == "__main__":
    main()
