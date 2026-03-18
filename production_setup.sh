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
GPU_MEMORY_LIMIT_GB=10
ALERT_LOG=/var/log/compliance_alert.log
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
step_start "[1/11] 环境检查（生产模式）"

if ! nvidia-smi &>/dev/null; then
    step_fail 1 "nvidia-smi 不可用" "确认GPU驱动已安装"
fi
nvidia-smi
CUDA_VERSION=$(nvidia-smi | grep -oP 'CUDA Version: \K[\d.]+' | head -1)
echo "  GPU 检查通过，CUDA 版本: $CUDA_VERSION"

# 检查可用显存
GPU_FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
GPU_FREE_GB=$(echo "scale=1; $GPU_FREE_MB / 1024" | bc)
if (( $(echo "$GPU_FREE_GB < $GPU_MEMORY_LIMIT_GB" | bc -l) )); then
    echo "  ⚠️  可用显存 ${GPU_FREE_GB}GB 低于要求 ${GPU_MEMORY_LIMIT_GB}GB"
    read -p "  是否继续？(y/N): " CONFIRM
    if [ "${CONFIRM,,}" != "y" ]; then
        echo "  已取消部署"
        exit 1
    fi
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    step_fail 1 "Python 版本 $PYTHON_VERSION 低于要求 3.10" "升级 Python"
fi
echo "  Python $PYTHON_VERSION ✅"

DISK_AVAIL=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
if [ "${DISK_AVAIL:-0}" -lt 30 ]; then
    step_fail 1 "磁盘可用空间 ${DISK_AVAIL}GB 不足" "清理磁盘空间"
fi
echo "  磁盘可用 ${DISK_AVAIL}GB ✅"

if ! docker --version &>/dev/null; then
    step_fail 1 "docker 未安装" "安装 Docker：curl -fsSL https://get.docker.com | bash"
fi
echo "  Docker ✅"

# 检查端口冲突
for PORT in $EMBED_PORT $VLLM_PORT $BACKEND_PORT $QDRANT_PORT; do
    if ss -tlnp | grep -q ":${PORT} "; then
        step_fail 1 "端口 $PORT 已被占用" "修改配置区对应端口，或停止占用该端口的进程"
    fi
done
echo "  端口检查通过 ✅"

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

pip install -r "$PROJECT_DIR/compliance_service/requirements.txt" --quiet

if ! python -c "import vllm" &>/dev/null; then
    pip install vllm --quiet
else
    echo "  vLLM 已安装，跳过"
fi

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
        --restart always \
        -p "${QDRANT_PORT}:6333" \
        -v "$PROJECT_DIR/qdrant_storage:/qdrant/storage" \
        qdrant/qdrant:v1.9.7

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
step_start "[6/11] 启动嵌入服务（systemd）"

cat > /etc/systemd/system/embed_server.service << EOF
[Unit]
Description=Compliance Embed Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/embed_server.py
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/embed_server.log
StandardError=append:$LOG_DIR/embed_server.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable embed_server
systemctl restart embed_server

echo "  等待嵌入服务启动（最多60s）..."
for i in $(seq 1 12); do
    sleep 5
    if curl -sf "http://localhost:${EMBED_PORT}/health" &>/dev/null; then
        echo "  嵌入服务已就绪"
        break
    fi
    echo "  等待中... (${i}/12)"
    if [ "$i" -eq 12 ]; then
        step_fail 6 "嵌入服务启动超时" "查看日志：cat $LOG_DIR/embed_server.log"
    fi
done

step_end

# ----------------------------------------------------------
step_start "[7/11] 启动 vLLM 服务（systemd）"

cat > /etc/systemd/system/vllm_server.service << EOF
[Unit]
Description=Compliance vLLM Server
After=network.target embed_server.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python -m vllm.entrypoints.openai.api_server \
    --model $MODEL_DIR/Qwen2.5-7B-Instruct-GPTQ-Int4 \
    --port $VLLM_PORT \
    --gpu-memory-utilization $GPU_MEMORY_UTILIZATION \
    --max-model-len 4096 \
    --quantization gptq
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/vllm_server.log
StandardError=append:$LOG_DIR/vllm_server.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vllm_server
systemctl restart vllm_server

echo "  等待 vLLM 启动（最多300s）..."
for i in $(seq 1 30); do
    sleep 10
    if curl -sf "http://localhost:${VLLM_PORT}/health" &>/dev/null; then
        echo "  vLLM 已就绪"
        break
    fi
    echo "  等待中... (${i}/30)"
    if [ "$i" -eq 30 ]; then
        step_fail 7 "vLLM 启动超时" "GPU显存不足 → 降低 GPU_MEMORY_UTILIZATION；查看日志：cat $LOG_DIR/vllm_server.log"
    fi
done

step_end

# ----------------------------------------------------------
step_start "[8/11] 导入数据"

source "$VENV_DIR/bin/activate"

WORD_COUNT=$(curl -s "http://localhost:${QDRANT_PORT}/collections/sensitive_words" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('points_count',0))" 2>/dev/null || echo "0")

if [ "${WORD_COUNT:-0}" -gt 0 ]; then
    echo "  sensitive_words 已有 $WORD_COUNT 条数据，跳过导入"
else
    if [ -f "$DATA_DIR/敏感词表.xlsx" ] && [ -f "$DATA_DIR/平台规则表.xlsx" ]; then
        cd "$PROJECT_DIR/compliance_service/backend"
        python scripts/ingest.py 2>&1 | tee "$LOG_DIR/ingest.log"
    else
        echo "  ⚠️  未找到 Excel 数据文件，跳过导入"
    fi
fi

step_end

# ----------------------------------------------------------
step_start "[9/11] 启动后端服务（systemd）"

cat > /etc/systemd/system/compliance_backend.service << EOF
[Unit]
Description=Compliance Backend Service
After=network.target embed_server.service vllm_server.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR/compliance_service/backend
ExecStart=$VENV_DIR/bin/python -m uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/backend.log
StandardError=append:$LOG_DIR/backend.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable compliance_backend
systemctl restart compliance_backend

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

step_end

# ----------------------------------------------------------
step_start "[10/11] 配置监控"

chmod +x "$PROJECT_DIR/monitor.sh"

# 配置 crontab 每60秒执行一次
CRON_JOB="* * * * * $PROJECT_DIR/monitor.sh >> $ALERT_LOG 2>&1"
( crontab -l 2>/dev/null | grep -v "monitor.sh" ; echo "$CRON_JOB" ) | crontab -
echo "  监控定时任务已配置（每分钟执行）"
touch "$ALERT_LOG"
chmod 644 "$ALERT_LOG"

step_end

# ----------------------------------------------------------
step_start "[11/11] 部署完成汇总（生产模式）"

echo ""
systemctl status embed_server --no-pager -l | head -5 || true
systemctl status vllm_server --no-pager -l | head -5 || true
systemctl status compliance_backend --no-pager -l | head -5 || true
echo ""
echo "  ✅ 嵌入服务:  http://localhost:${EMBED_PORT}"
echo "  ✅ 生成服务:  http://localhost:${VLLM_PORT}"
echo "  ✅ 后端服务:  http://localhost:${BACKEND_PORT}"
echo "  ✅ 前端页面:  用浏览器打开 $PROJECT_DIR/compliance_service/frontend/index.html"
echo "  📋 告警日志:  tail -f $ALERT_LOG"
echo ""
echo "  服务管理："
echo "    systemctl status|restart|stop embed_server"
echo "    systemctl status|restart|stop vllm_server"
echo "    systemctl status|restart|stop compliance_backend"

step_end
