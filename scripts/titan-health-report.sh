#!/usr/bin/env bash
set -euo pipefail

REPORT="/tmp/titan-health-report.md"
: > "$REPORT"

WARN=0
CRIT=0

now_utc() {
  date -u "+%Y-%m-%d %H:%M:%S UTC"
}

write() {
  printf "%s\n" "$*" | tee -a "$REPORT"
}

ssh_run() {
  local host="$1"; shift
  timeout 5s ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$host" "$@"
}

mark_warn() {
  WARN=1
}

mark_crit() {
  CRIT=1
}

fmt_host() {
  local host="$1"
  if [[ -n "$host" ]]; then
    printf " (%s)" "$host"
  fi
}

percent_used_root() {
  ssh_run "$1" "df -P / | awk 'NR==2 {gsub(/%/, \"\", \$5); print \$5}'" 2>/dev/null || echo ""
}

ollama_status() {
  ssh_run "$1" "command -v ollama >/dev/null 2>&1 && (systemctl is-active ollama 2>/dev/null || true)" 2>/dev/null || true
}

ollama_models() {
  ssh_run "$1" "command -v ollama >/dev/null 2>&1 && ollama ps 2>/dev/null | tail -n +2 | awk '{print \$1}' | xargs" 2>/dev/null || true
}

gpu_vram() {
  ssh_run "$1" "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits; elif command -v rocm-smi >/dev/null 2>&1; then rocm-smi --showmeminfo vram --json 2>/dev/null | head -c 200; else echo ''; fi" 2>/dev/null || true
}

service_active() {
  local host="$1" svc="$2"
  ssh_run "$host" "systemctl is-active --quiet '$svc' && echo active || echo inactive" 2>/dev/null || echo "inactive"
}

docker_names() {
  ssh_run "$1" "command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}'" 2>/dev/null || true
}

recent_restarts() {
  local host="$1" svc="$2"
  ssh_run "$host" "journalctl -u '$svc' --since '24 hours ago' -o cat 2>/dev/null | grep -E 'Started|Starting|Stopped|Restart' | wc -l" 2>/dev/null || echo "0"
}

write "# TitanArray Health Report v1.0"
write "Generated: $(now_utc)"
write ""

check_node() {
  local name="$1" host="$2" role="$3"
  if [[ -z "$host" ]]; then
    write "## ${name} — Unknown"
    write "- Host not configured"
    write ""
    mark_warn
    return
  fi

  if ! ssh_run "$host" "echo ok" >/dev/null 2>&1; then
    write "## ${name}$(fmt_host "$host") — Offline"
    write "- SSH unavailable"
    write ""
    mark_crit
    return
  fi

  write "## ${name}$(fmt_host "$host") — Online"

  local used
  used="$(percent_used_root "$host")"
  if [[ -n "$used" ]]; then
    write "- Disk: ${used}% used (/)"
    if (( used > 85 )); then
      write "  - Warning: disk usage above 85%"
      mark_warn
    fi
  else
    write "- Disk: unknown"
    mark_warn
  fi

  case "$role" in
    sarge)
      local ollama
      ollama="$(ollama_status "$host")"
      if [[ "$ollama" == "active" ]]; then
        write "- Ollama: active"
      else
        write "- Ollama: inactive"
        mark_warn
      fi

      local models
      models="$(ollama_models "$host")"
      if [[ -n "$models" ]]; then
        write "- Models loaded: ${models}"
      else
        write "- Models loaded: none"
      fi
      write "- Tokens/sec (last): n/a"

      local vram
      vram="$(gpu_vram "$host")"
      if [[ -n "$vram" ]]; then
        write "- GPU VRAM: ${vram}"
      else
        write "- GPU VRAM: unknown"
        mark_warn
      fi

      local restarts
      restarts="$(recent_restarts "$host" "ollama")"
      if (( restarts > 0 )); then
        write "- Restarts (last 24h): ollama=${restarts}"
        mark_warn
      else
        write "- Restarts (last 24h): none"
      fi
      ;;

    shadow)
      local names
      names="$(docker_names "$host")"
      if [[ -n "$names" ]]; then
        write "- Docker: running"
        for svc in qdrant milvus glance vikunja; do
          if echo "$names" | grep -iq "$svc"; then
            write "  - ${svc}: running"
          else
            write "  - ${svc}: not detected"
            mark_warn
          fi
        done
      else
        write "- Docker: not available"
        mark_warn
      fi
      ;;

    stream)
      local tech adg
      tech="$(service_active "$host" "technitium")"
      adg="$(service_active "$host" "adguardhome")"
      write "- Technitium: ${tech}"
      write "- AdGuard: ${adg}"
      if [[ "$tech" != "active" ]] || [[ "$adg" != "active" ]]; then
        mark_warn
      fi

      local ollama
      ollama="$(ollama_status "$host")"
      if [[ -n "$ollama" ]]; then
        write "- Ollama: ${ollama}"
      else
        write "- Ollama: not detected"
      fi
      ;;

    shark)
      local ollama
      ollama="$(ollama_status "$host")"
      if [[ "$ollama" == "active" ]]; then
        write "- Ollama: active"
      else
        write "- Ollama: inactive"
        mark_warn
      fi
      local models
      models="$(ollama_models "$host")"
      if [[ -n "$models" ]]; then
        write "- Models loaded: ${models}"
      else
        write "- Models loaded: none"
      fi
      write "- Tokens/sec (last): n/a"
      ;;

    share)
      write "- Unraid array: check not configured"
      mark_warn
      ;;
  esac

  write ""
}

SARGE_HOST="${TITAN_SARGE_HOST:-}"
SHADOW_HOST="${TITAN_SHADOW_HOST:-}"
STREAM_HOST="${TITAN_STREAM_HOST:-}"
SHARK_HOST="${TITAN_SHARK_HOST:-}"
SHARE_HOST="${TITAN_SHARE_HOST:-}"

check_node "TitanSarge" "$SARGE_HOST" "sarge"
check_node "TitanShadow" "$SHADOW_HOST" "shadow"
check_node "TitanStream" "$STREAM_HOST" "stream"
check_node "TitanShark" "$SHARK_HOST" "shark"
check_node "TitanShare" "$SHARE_HOST" "share"

if (( CRIT == 1 )); then
  exit 2
fi
if (( WARN == 1 )); then
  exit 1
fi
exit 0
