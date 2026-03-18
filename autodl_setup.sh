#!/bin/bash
set -e

# ==================== 配置区（按需修改）====================
PROJECT_DIR=/root/compliance_project
VENV_DIR=$PROJECT_DIR/venv
MODEL_DIR=$PROJECT_DIR/models
DATA_DIR=$PROJECT_DIR/data
LOG_DIR=$PROJECT_DIR/logs
EMBED_PORT=8001
VLLM_PORT=8002
BACKEND_PORT=8000
QDRANT_PORT=6333
GPU_MEMORY_UTILIZATION=0.85
# =========================================================

STEP_START_TIME=0

step_start() {
    STEP_START_TIME=$(date +%s)
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

step_end() {
    local elapsed=$(( $(date +%s) - STEP_START_TIME ))
    echo "  ✅ 完成，耗时 ${elapsed}s"
}

step_fail() {
    echo "  ❌ 第${1}步失败：${2}"
    echo "  排查建议：${3}"
    exit 1
}

# ----------------------------------------------------------
step_start "[1/11] 环境检查"

if ! nvidia-smi &>/dev/null; then
    step_fail 1 "nvidia-smi 不可用" "确认GPU驱动已安装并且是NVIDIA GPU"
fi
nvidia-smi
CUDA_VERSION=$(nvidia-smi | grep -oP 'CUDA Version: \K[\d.]+' | head -1)
echo "  GPU 检查通过，CUDA 版本: $CUDA_VERSION"

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    step_fail 1 "Python 版本 $PYTHON_VERSION 低于要求 3.10" "升级 Python：sudo apt install python3.10"
fi
echo "  Python $PYTHON_VERSION ✅"

DISK_AVAIL=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
if [ "${DISK_AVAIL:-0}" -lt 30 ]; then
    step_fail 1 "磁盘可用空间 ${DISK_AVAIL}GB，低于要求 30GB" "清理磁盘空间"
fi
echo "  磁盘可用 ${DISK_AVAIL}GB ✅"

if ! docker --version &>/dev/null; then
    step_fail 1 "docker 未安装" "安装 Docker：curl -fsSL https://get.docker.com | bash"
fi
echo "  Docker ✅"

step_end

# ----------------------------------------------------------
step_start "[2/11] 创建目录和虚拟环境"

mkdir -p "$LOG_DIR" "$DATA_DIR" "$MODEL_DIR"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  虚拟环境已创建: $VENV_DIR"
else
    echo "  虚拟环境已存在，跳过创建"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

step_end

# ----------------------------------------------------------
step_start "[3/11] 安装 Python 依赖"

source "$VENV_DIR/bin/activate"

# 根据 CUDA 版本选择 torch 安装命令
CUDA_MAJOR=$(echo "$CUDA_VERSION" | cut -d. -f1)
CUDA_MINOR=$(echo "$CUDA_VERSION" | cut -d. -f2)

if ! python -c "import torch" &>/dev/null; then
    if [ "$CUDA_MAJOR" -eq 11 ]; then
        TORCH_CUDA="cu118"
    elif [ "$CUDA_MAJOR" -eq 12 ] && [ "$CUDA_MINOR" -le 3 ]; then
        TORCH_CUDA="cu121"
    else
        TORCH_CUDA="cu124"
    fi
    echo "  安装 torch (CUDA $TORCH_CUDA)..."
    pip install torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/${TORCH_CUDA}" --quiet
else
    echo "  torch 已安装，跳过"
fi

echo "  安装其余依赖..."
pip install -r "$PROJECT_DIR/compliance_service/requirements.txt" --quiet

# vLLM 单独安装（版本敏感）
if ! python -c "import vllm" &>/dev/null; then
    echo "  安装 vLLM..."
    pip install vllm --quiet
else
    echo "  vLLM 已安装，跳过"
fi

# modelscope + huggingface_hub 用于模型下载
pip install modelscope huggingface_hub --quiet

step_end

# ----------------------------------------------------------
step_start "[4/11] 下载模型"

source "$VENV_DIR/bin/activate"

BGE_DIR="$MODEL_DIR/bge-m3"
QWEN_DIR="$MODEL_DIR/Qwen2.5-7B-Instruct-GPTQ-Int4"

if [ -d "$BGE_DIR" ] && [ "$(ls -A $BGE_DIR)" ] && \
   [ -d "$QWEN_DIR" ] && [ "$(ls -A $QWEN_DIR)" ]; then
    echo "  模型已存在，跳过下载"
else
    echo "  开始下载模型（可能需要较长时间）..."
    python "$PROJECT_DIR/model_download.py" || \
        step_fail 4 "模型下载失败" "检查网络连接，或手动下载后放入 $MODEL_DIR"
fi

step_end

# ----------------------------------------------------------
step_start "[5/11] 启动 Qdrant"

QDRANT_CONTAINER="qdrant_compliance"

if docker ps --format '{{.Names}}' | grep -q "^${QDRANT_CONTAINER}$"; then
    echo "  Qdrant 容器已在运行，跳过"
else
    docker run -d \
        --name "$QDRANT_CONTAINER" \
        --restart unless-stopped \
        -p "${QDRANT_PORT}:6333" \
        -v "$PROJECT_DIR/qdrant_storage:/qdrant/storage" \
        qdrant/qdrant:v1.9.7

    echo "  等待 Qdrant 健康检查..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:${QDRANT_PORT}/healthz" &>/dev/null; then
            echo "  Qdrant 已就绪（${i}s）"
            break
        fi
        if [ "$i" -eq 30 ]; then
            step_fail 5 "Qdrant 启动超时" "检查 Docker 日志：docker logs $QDRANT_CONTAINER"
        fi
        sleep 1
    done
fi

step_end

# ----------------------------------------------------------
step_start "[6/11] 启动嵌入服务"

source "$VENV_DIR/bin/activate"

if ! curl -sf "http://localhost:${EMBED_PORT}/health" &>/dev/null; then
    screen -dmS embed bash -c "
        source $VENV_DIR/bin/activate
        cd $PROJECT_DIR
        python embed_server.py 2>&1 | tee $LOG_DIR/embed_server.log
    "
    echo "  等待嵌入服务启动（最多60s）..."
    for i in $(seq 1 12); do
        sleep 5
        if curl -sf "http://localhost:${EMBED_PORT}/health" &>/dev/null; then
            LATENCY=$(curl -s -o /dev/null -w "%{time_total}" "http://localhost:${EMBED_PORT}/health")
            echo "  嵌入服务已就绪，延迟 ${LATENCY}s"
            break
        fi
        echo "  等待中... (${i}/12)"
        if [ "$i" -eq 12 ]; then
            step_fail 6 "嵌入服务启动超时" "查看日志：cat $LOG_DIR/embed_server.log"
        fi
    done
else
    echo "  嵌入服务已在运行，跳过"
fi

step_end

# ----------------------------------------------------------
step_start "[7/11] 启动 vLLM 生成服务"

source "$VENV_DIR/bin/activate"

if ! curl -sf "http://localhost:${VLLM_PORT}/health" &>/dev/null; then
    screen -dmS vllm bash -c "
        source $VENV_DIR/bin/activate
        cd $PROJECT_DIR
        python -m vllm.entrypoints.openai.api_server \
            --model $MODEL_DIR/Qwen2.5-7B-Instruct-GPTQ-Int4 \
            --port $VLLM_PORT \
            --gpu-memory-utilization $GPU_MEMORY_UTILIZATION \
            --max-model-len 4096 \
            --quantization gptq \
            2>&1 | tee $LOG_DIR/vllm_server.log
    "
    echo "  等待 vLLM 启动（最多300s，模型加载较慢）..."
    for i in $(seq 1 30); do
        sleep 10
        if curl -sf "http://localhost:${VLLM_PORT}/health" &>/dev/null; then
            LATENCY=$(curl -s -o /dev/null -w "%{time_total}" "http://localhost:${VLLM_PORT}/health")
            echo "  vLLM 已就绪，延迟 ${LATENCY}s"
            break
        fi
        echo "  等待中... (${i}/30)"
        if [ "$i" -eq 30 ]; then
            step_fail 7 "vLLM 启动超时" "GPU显存不足 → 降低 GPU_MEMORY_UTILIZATION；CUDA不兼容 → 检查 torch 版本；查看日志：cat $LOG_DIR/vllm_server.log"
        fi
    done
else
    echo "  vLLM 已在运行，跳过"
fi

step_end

# ----------------------------------------------------------
step_start "[8/11] 导入数据"

source "$VENV_DIR/bin/activate"

# 检查 Qdrant 是否已有数据
WORD_COUNT=$(curl -s "http://localhost:${QDRANT_PORT}/collections/sensitive_words" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('points_count',0))" 2>/dev/null || echo "0")

if [ "${WORD_COUNT:-0}" -gt 0 ]; then
    echo "  sensitive_words 已有 $WORD_COUNT 条数据，跳过导入"
else
    if [ -f "$DATA_DIR/敏感词表.xlsx" ] && [ -f "$DATA_DIR/平台规则表.xlsx" ]; then
        echo "  开始导入数据..."
        cd "$PROJECT_DIR/compliance_service/backend"
        python scripts/ingest.py 2>&1 | tee "$LOG_DIR/ingest.log"
    else
        echo "  ⚠️  未找到 Excel 数据文件，跳过导入"
        echo "     请上传后手动执行: cd $PROJECT_DIR/compliance_service/backend && python scripts/ingest.py"
    fi
fi

step_end

# ----------------------------------------------------------
step_start "[9/11] 启动后端服务"

source "$VENV_DIR/bin/activate"

if ! curl -sf "http://localhost:${BACKEND_PORT}/health" &>/dev/null; then
    screen -dmS backend bash -c "
        source $VENV_DIR/bin/activate
        cd $PROJECT_DIR/compliance_service/backend
        python -m uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT 2>&1 | tee $LOG_DIR/backend.log
    "
    echo "  等待后端服务启动（最多30s）..."
    for i in $(seq 1 30); do
        sleep 1
        if curl -sf "http://localhost:${BACKEND_PORT}/health" &>/dev/null; then
            echo "  后端服务已就绪（${i}s）"
            break
        fi
        if [ "$i" -eq 30 ]; then
            step_fail 9 "后端服务启动超时" "端口冲突 → 修改 BACKEND_PORT；查看日志：cat $LOG_DIR/backend.log"
        fi
    done
else
    echo "  后端服务已在运行，跳过"
fi

step_end

# ----------------------------------------------------------
step_start "[10/11] 运行 benchmark"

source "$VENV_DIR/bin/activate"
cd "$PROJECT_DIR"
echo "  执行性能测试..."
python benchmark.py 2>&1 | tee "$LOG_DIR/benchmark_output.log" || \
    echo "  ⚠️  benchmark 执行出错，查看日志：cat $LOG_DIR/benchmark_output.log"

step_end

# ----------------------------------------------------------
step_start "[11/11] 部署完成汇总"

echo ""
echo "  ✅ 嵌入服务:  http://localhost:${EMBED_PORT}"
echo "  ✅ 生成服务:  http://localhost:${VLLM_PORT}"
echo "  ✅ 后端服务:  http://localhost:${BACKEND_PORT}"
echo "  ✅ 前端页面:  用浏览器打开 $PROJECT_DIR/compliance_service/frontend/index.html"
echo "   性能报告:  cat $LOG_DIR/benchmark_report.txt"
echo ""
echo "  Screen 会话："
echo "    screen -r embed    # 嵌入服务日志"
echo "    screen -r vllm     # vLLM 日志"
echo "    screen -r backend  # 后端日志"

step_end
