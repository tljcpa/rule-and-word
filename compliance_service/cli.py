#!/usr/bin/env python3
import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from vector_store import init_collections, get_all_sensitive_words
from detector import build_automaton
from pipeline import process


async def init():
    await init_collections()
    words = await get_all_sensitive_words()
    build_automaton(words)
    print(f"初始化完成，已加载 {len(words)} 个敏感词")


async def interactive_mode():
    await init()
    print("输入文本进行检测，输入 q 退出\n")
    while True:
        try:
            text = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() == "q":
            break
        if not text:
            continue
        result = await process(text)
        violations = result["violations"]
        detail = result["latency_detail"]
        violation_str = " ".join(
            f"{v['word']}({v['reason']})" for v in violations
        ) or "无"
        print(f"原文：{text}")
        print(f"结果：{result['result']}")
        print(f"命中：{'是' if result['hit'] else '否'}")
        print(f"违规词：{violation_str}")
        print(
            f"耗时：normalize={detail['normalize_ms']}ms "
            f"embed={detail['embed_ms']}ms "
            f"detect={detail['detect_ms']}ms "
            f"rewrite={detail['rewrite_ms']}ms "
            f"total={result['latency_ms']}ms"
        )
        model_used = result.get("model_used", "")
        used_fallback = result.get("used_fallback", False)
        print(f"模型：{model_used} fallback={'是' if used_fallback else '否'}")
        print("-" * 40)


async def file_mode(input_path: str, output_path: str):
    await init()
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    results = []
    for i, line in enumerate(lines):
        r = await process(line, request_id=str(i))
        results.append(r["result"])
        print(f"[{i+1}/{len(lines)}] 处理完成")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(results))
    print(f"完成，结果已写入 {output_path}")


def main():
    parser = argparse.ArgumentParser(description="内容合规CLI")
    parser.add_argument("--input", help="输入文件路径")
    parser.add_argument("--output", help="输出文件路径")
    args = parser.parse_args()

    if args.input and args.output:
        asyncio.run(file_mode(args.input, args.output))
    elif args.input or args.output:
        parser.error("--input 和 --output 必须同时指定")
    else:
        asyncio.run(interactive_mode())


if __name__ == "__main__":
    main()
