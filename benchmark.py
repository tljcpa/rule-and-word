#!/usr/bin/env python3
"""
性能基准测试脚本
自动生成测试文本，无需外部文件
"""
import asyncio
import json
import os
import subprocess
import time
from datetime import datetime

import httpx

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
LOG_DIR = os.getenv("LOG_DIR", "/root/compliance_project/logs")
REPORT_PATH = os.path.join(LOG_DIR, "benchmark_report.txt")

# 内置测试文本（5条含违规词 + 5条正常文本）
TEST_TEXTS = [
    "这款产品绝对治愈您的疾病，100%有效，无任何副作用，立即购买！",
    "国家领导人亲自批示，本项目获得最高级别政府支持，稳赚不赔。",
    "限时特卖，原价9999元现在只需99元，数量有限先到先得，不买后悔！",
    "我们的投资回报率高达500%，保证收益，绝无风险，欢迎加盟！",
    "本品含有违禁成分，效果超过同类药品十倍，专治各种疑难杂症。",
    "这款手机电池续航时间为12小时，支持快速充电，屏幕分辨率1080P。",
    "今天天气晴朗，适合出门散步，公园里的花都开了，景色很美。",
    "新款笔记本电脑搭载最新处理器，运行速度提升30%，价格实惠。",
    "我们的客服团队24小时在线，随时解答您的疑问，欢迎咨询。",
    "这部电影讲述了一个普通家庭的日常生活，情节温馨，值得一看。",
]


async def single_request(client: httpx.AsyncClient, text: str, idx: int) -> dict:
    payload = {"text": text, "request_id": f"bench_{idx}"}
    t0 = time.time()
    try:
        resp = await client.post(f"{BACKEND_URL}/process", json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        wall_ms = round((time.time() - t0) * 1000)
        return {
            "ok": True,
            "wall_ms": wall_ms,
            "total_ms": data.get("latency_ms", wall_ms),
            "detail": data.get("latency_detail", {}),
            "hit": data.get("hit", False),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "wall_ms": round((time.time() - t0) * 1000)}


async def run_serial(client: httpx.AsyncClient) -> list[dict]:
    results = []
    for i, text in enumerate(TEST_TEXTS):
        r = await single_request(client, text, i)
        results.append(r)
    return results


async def run_concurrent(client: httpx.AsyncClient, concurrency: int, rounds: int = 3) -> list[dict]:
    all_results = []
    for _ in range(rounds):
        tasks = [
            single_request(client, TEST_TEXTS[i % len(TEST_TEXTS)], i)
            for i in range(concurrency)
        ]
        results = await asyncio.gather(*tasks)
        all_results.extend(results)
    return all_results


def pct(values: list[float], p: int) -> int:
    if not values:
        return 0
    s = sorted(values)
    idx = max(0, int(len(s) * p / 100) - 1)
    return round(s[idx])


def avg(values: list[float]) -> int:
    return round(sum(values) / len(values)) if values else 0


def get_gpu_stats() -> dict:
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            text=True,
        ).strip()
        parts = [p.strip() for p in out.split(",")]
        return {
            "used_mb": int(parts[0]),
            "total_mb": int(parts[1]),
            "util_pct": int(parts[2]),
        }
    except Exception:
        return {"used_mb": 0, "total_mb": 24576, "util_pct": 0}


def build_report(
    serial: list[dict],
    conc10: list[dict],
    conc50: list[dict],
    gpu_before: dict,
    gpu_after: dict,
) -> str:
    ok_serial = [r for r in serial if r.get("ok")]

    def field(results, key):
        return [r["detail"].get(key, 0) for r in results if r.get("ok") and "detail" in r]

    def total_field(results):
        return [r["total_ms"] for r in results if r.get("ok")]

    embed_vals = field(ok_serial, "embed_ms")
    detect_vals = field(ok_serial, "detect_ms")
    rewrite_vals = field(ok_serial, "rewrite_ms")
    norm_vals = field(ok_serial, "normalize_ms")
    total_vals = total_field(ok_serial)

    hit_vals = [r["total_ms"] for r in ok_serial if r.get("hit")]
    nohit_vals = [r["total_ms"] for r in ok_serial if not r.get("hit")]

    c10_total = total_field(conc10)
    c50_total = total_field(conc50)

    gpu_used_gb = gpu_after["used_mb"] / 1024
    p50_ok = pct(total_vals, 50) < 1000
    p99_c50_ok = pct(c50_total, 99) < 1000

    bottleneck = "LLM生成" if avg(rewrite_vals) > avg(embed_vals) else "嵌入检索"

    lines = [
        "=====================================",
        "性能测试报告",
        f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "硬件: RTX 3090 24GB",
        "嵌入模型: bge-m3 (本地)",
        "生成模型: Qwen2.5-7B-Instruct-GPTQ-Int4 (本地vLLM)",
        "=====================================",
        "",
        "【单次延迟 (ms)】",
        f"{'':12}{'平均':>6}{'P50':>6}{'P99':>6}{'最大':>6}",
        f"{'嵌入:':12}{avg(embed_vals):>6}{pct(embed_vals,50):>6}{pct(embed_vals,99):>6}{max(embed_vals or [0]):>6}",
        f"{'检测:':12}{avg(detect_vals):>6}{pct(detect_vals,50):>6}{pct(detect_vals,99):>6}{max(detect_vals or [0]):>6}",
        f"{'生成:':12}{avg(rewrite_vals):>6}{pct(rewrite_vals,50):>6}{pct(rewrite_vals,99):>6}{max(rewrite_vals or [0]):>6}",
        f"{'总计:':12}{avg(total_vals):>6}{pct(total_vals,50):>6}{pct(total_vals,99):>6}{max(total_vals or [0]):>6}",
        "",
        "【有命中 vs 无命中】",
        f"有命中平均: {avg(hit_vals)}ms",
        f"无命中平均: {avg(nohit_vals)}ms（已跳过LLM）",
        "",
        "【并发性能 (ms)】",
        f"{'':12}{'并发10':^16}{'并发50':^16}",
        f"{'':12}{'平均':>6}{'P99':>6}    {'平均':>6}{'P99':>6}",
        f"{'总延迟:':12}{avg(c10_total):>6}{pct(c10_total,99):>6}    {avg(c50_total):>6}{pct(c50_total,99):>6}",
        "",
        "【GPU资源占用】",
        f"显存使用: {gpu_used_gb:.1f}/{gpu_after['total_mb']//1024}GB",
        f"GPU利用率峰值: {gpu_after['util_pct']}%",
        "",
        "【结论】",
        f"P50总延迟满足<1秒: {'是' if p50_ok else '否'}",
        f"P99并发50满足<1秒: {'是' if p99_c50_ok else '否'}",
        f"主要瓶颈: {bottleneck}",
        "=====================================",
    ]
    return "\n".join(lines)


async def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    print("开始性能测试...")

    gpu_before = get_gpu_stats()

    async with httpx.AsyncClient() as client:
        # 预热
        print("预热中...")
        await single_request(client, TEST_TEXTS[0], -1)

        print("单次延迟测试（10条）...")
        serial_results = await run_serial(client)

        print("并发10测试（3轮）...")
        conc10_results = await run_concurrent(client, 10, rounds=3)

        print("并发50测试（3轮）...")
        conc50_results = await run_concurrent(client, 50, rounds=3)

    gpu_after = get_gpu_stats()

    report = build_report(serial_results, conc10_results, conc50_results, gpu_before, gpu_after)
    print("\n" + report)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
