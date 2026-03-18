#!/usr/bin/env python3
"""
下载 bge-m3 和 Qwen2.5-7B-Instruct-GPTQ-Int4 到 models/ 目录。
优先用 ModelScope，失败自动切换 HuggingFace。
"""
import os
import sys

MODEL_DIR = "/root/compliance_project/models"
os.makedirs(MODEL_DIR, exist_ok=True)

MODELS = [
    {
        "name": "bge-m3",
        "modelscope_id": "BAAI/bge-m3",
        "huggingface_id": "BAAI/bge-m3",
        "local_dir": os.path.join(MODEL_DIR, "bge-m3"),
    },
    {
        "name": "Qwen2.5-7B-Instruct-GPTQ-Int4",
        "modelscope_id": "Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4",
        "huggingface_id": "Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4",
        "local_dir": os.path.join(MODEL_DIR, "Qwen2.5-7B-Instruct-GPTQ-Int4"),
    },
]


def download_modelscope(model_id: str, local_dir: str) -> bool:
    try:
        from modelscope import snapshot_download
        import shutil
        print(f"  [ModelScope] 下载 {model_id} ...")
        # 新版 modelscope 支持 local_dir，直接下载到目标目录
        # 旧版只有 cache_dir（会嵌套一层 org/model），需要再挪路径
        try:
            snapshot_download(model_id, local_dir=local_dir)
        except TypeError:
            # 旧版 modelscope 不支持 local_dir 参数
            actual_path = snapshot_download(model_id)
            if os.path.abspath(actual_path) != os.path.abspath(local_dir):
                if os.path.exists(local_dir):
                    shutil.rmtree(local_dir)
                shutil.copytree(actual_path, local_dir)
        return True
    except Exception as e:
        print(f"  [ModelScope] 失败: {e}")
        return False


def download_huggingface(model_id: str, local_dir: str) -> bool:
    try:
        from huggingface_hub import snapshot_download
        print(f"  [HuggingFace] 下载 {model_id} ...")
        snapshot_download(repo_id=model_id, local_dir=local_dir)
        return True
    except Exception as e:
        print(f"  [HuggingFace] 失败: {e}")
        return False


def main():
    for m in MODELS:
        local_dir = m["local_dir"]
        if os.path.exists(local_dir) and os.listdir(local_dir):
            print(f"[跳过] {m['name']} 已存在于 {local_dir}")
            continue

        print(f"\n开始下载: {m['name']}")
        ok = download_modelscope(m["modelscope_id"], local_dir)
        if not ok:
            ok = download_huggingface(m["huggingface_id"], local_dir)
        if not ok:
            print(f"ERROR: {m['name']} 下载失败，请检查网络或手动下载")
            sys.exit(1)
        print(f"  {m['name']} 下载完成 -> {local_dir}")

    print("\n所有模型下载完成")


if __name__ == "__main__":
    main()
