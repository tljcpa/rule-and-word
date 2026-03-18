#!/bin/bash
# 合规服务健康监控脚本
# 由 production_setup.sh 配置为每分钟执行一次

PROJECT_DIR=/root/compliance_project
LOG_DIR=$PROJECT_DIR/logs
ALERT_LOG=/var/log/compliance_alert.log

EMBED_PORT=8001
VLLM_PORT=8002
BACKEND_PORT=8000
QDRANT_PORT=6333

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
ALERT=0

check_http() {
    local name=$1
    local url=$2
    local service=$3

    if ! curl -sf "$url" &>/dev/null; then
        echo "[$TIMESTAMP] ALERT: $name 无响应 ($url)" >> "$ALERT_LOG"
        ALERT=1
        if [ -n "$service" ]; then
            systemctl restart "$service" 2>/dev/null && \
                echo "[$TIMESTAMP] INFO: 已自动重启 $service" >> "$ALERT_LOG"
        fi
    fi
}

check_gpu() {
    local mem_used
    mem_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
    local mem_total
    mem_total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    if [ -n "$mem_used" ] && [ -n "$mem_total" ] && [ "$mem_total" -gt 0 ]; then
        local usage_pct=$(( mem_used * 100 / mem_total ))
        if [ "$usage_pct" -gt 95 ]; then
            echo "[$TIMESTAMP] WARN: GPU显存使用率 ${usage_pct}% (${mem_used}MB/${mem_total}MB)" >> "$ALERT_LOG"
        fi
    fi
}

check_disk() {
    local avail
    avail=$(df -BG "$PROJECT_DIR" | awk 'NR==2{print $4}' | tr -d 'G')
    if [ "${avail:-999}" -lt 5 ]; then
        echo "[$TIMESTAMP] ALERT: 磁盘可用空间不足 ${avail}GB" >> "$ALERT_LOG"
        ALERT=1
    fi
}

check_log_size() {
    local log_path="$LOG_DIR/requests.jsonl"
    if [ -f "$log_path" ]; then
        local size_mb
        size_mb=$(du -m "$log_path" | cut -f1)
        if [ "${size_mb:-0}" -gt 500 ]; then
            echo "[$TIMESTAMP] WARN: 请求日志文件过大 ${size_mb}MB，建议归档" >> "$ALERT_LOG"
        fi
    fi
}

# 执行检查
check_http "嵌入服务" "http://localhost:${EMBED_PORT}/health" "embed_server"
check_http "vLLM服务" "http://localhost:${VLLM_PORT}/health" "vllm_server"
check_http "后端服务" "http://localhost:${BACKEND_PORT}/health" "compliance_backend"
check_http "Qdrant" "http://localhost:${QDRANT_PORT}/healthz" ""

check_gpu
check_disk
check_log_size

if [ "$ALERT" -eq 0 ]; then
    # 每10分钟记录一次正常状态（避免日志过多）
    MINUTE=$(date +%M)
    if [ "$((10#$MINUTE % 10))" -eq 0 ]; then
        echo "[$TIMESTAMP] OK: 所有服务正常" >> "$ALERT_LOG"
    fi
fi
