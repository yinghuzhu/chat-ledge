#!/bin/bash
# 第 0 步：备份微信数据库。
#
# 只做复制，不删不改任何东西，对微信零影响。
# 备份必须在抓密钥之前做——数据先在手，后面怎么折腾都不慌。
#
# 用法：
#   ./0_backup.sh                     # 只备份 db_storage（约 1.5G，够用）
#   ./0_backup.sh --full              # 连同图片视频一起备份（约 66G）
#   ./0_backup.sh --dest /Volumes/移动硬盘/wx   # 指定备份位置

set -uo pipefail

SRC="$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
MODE="db"
DEST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) MODE="full"; shift ;;
    --dest) DEST="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "未知参数：$1"; exit 1 ;;
  esac
done

if [[ ! -d "$SRC" ]]; then
  echo "[x] 找不到微信数据目录：$SRC"
  echo "    微信是否装在了别处？或从未登录过？"
  exit 1
fi

# --- 1. 检查微信是否退出 ---
if pgrep -x WeChat > /dev/null 2>&1; then
  echo "[!] 微信正在运行。"
  echo "    运行中复制可能拿到「写了一半」的数据库（WAL 未落盘）。"
  echo "    虽然多半也能用，但既然是备份，就别留这个隐患。"
  echo
  read -r -p "    现在退出微信再继续？[y/N] " ans
  if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
    osascript -e 'tell application "WeChat" to quit' 2>/dev/null || true
    echo "    等待微信退出..."
    for _ in $(seq 1 15); do
      pgrep -x WeChat > /dev/null 2>&1 || break
      sleep 1
    done
    if pgrep -x WeChat > /dev/null 2>&1; then
      echo "[x] 微信仍未退出，请手动退出后重跑本脚本。"
      exit 1
    fi
    echo "    [✓] 微信已退出"
  else
    echo "    [!] 继续复制，但不保证数据库处于一致状态。"
  fi
fi

# --- 2. 准备目标目录 ---
if [[ -z "$DEST" ]]; then
  DEST="$HOME/wechat-db-backup-$(date +%Y%m%d-%H%M)"
fi
mkdir -p "$DEST"
echo
echo "[*] 备份位置：$DEST"

# --- 3. 列出账号 ---
accounts=()
while IFS= read -r d; do
  accounts+=("$(basename "$d")")
done < <(find "$SRC" -maxdepth 1 -mindepth 1 -type d | sort)

if [[ ${#accounts[@]} -eq 0 ]]; then
  echo "[x] 没找到任何账号目录"
  exit 1
fi
echo "[*] 发现 ${#accounts[@]} 个账号：${accounts[*]}"

# --- 4. 复制 ---
total_src=0
for acc in "${accounts[@]}"; do
  if [[ "$MODE" == "db" ]]; then
    from="$SRC/$acc/db_storage"
    [[ -d "$from" ]] || { echo "    [!] $acc 没有 db_storage，跳过"; continue; }
    to="$DEST/$acc/db_storage"
    mkdir -p "$DEST/$acc"
    echo
    echo "[*] 复制 $acc/db_storage ..."
    cp -R "$from" "$to"
    total_src=$(( total_src + $(find "$from" -type f | wc -l | tr -d ' ') ))
  else
    from="$SRC/$acc"
    to="$DEST/$acc"
    echo
    echo "[*] 复制 $acc（完整，含图片视频）..."
    cp -R "$from" "$to"
    total_src=$(( total_src + $(find "$from" -type f | wc -l | tr -d ' ') ))
  fi
done

# --- 5. 验证 ---
echo
echo "===== 验证 ====="
ok=1
for acc in "${accounts[@]}"; do
  if [[ "$MODE" == "db" ]]; then
    s="$SRC/$acc/db_storage"; d="$DEST/$acc/db_storage"
  else
    s="$SRC/$acc"; d="$DEST/$acc"
  fi
  [[ -d "$s" ]] || continue
  ns=$(find "$s" -type f 2>/dev/null | wc -l | tr -d ' ')
  nd=$(find "$d" -type f 2>/dev/null | wc -l | tr -d ' ')
  ss=$(du -sk "$s" 2>/dev/null | awk '{print $1}')
  sd=$(du -sk "$d" 2>/dev/null | awk '{print $1}')
  if [[ "$ns" == "$nd" ]]; then
    printf "  [✓] %-24s 文件数 %s = %s，大小 %sM = %sM\n" \
      "$acc" "$ns" "$nd" $(( ss / 1024 )) $(( sd / 1024 ))
  else
    printf "  [✗] %-24s 文件数不一致！源 %s，备份 %s\n" "$acc" "$ns" "$nd"
    ok=0
  fi
done

echo
if [[ $ok -eq 1 ]]; then
  echo "[✓] 备份完成且校验一致：$DEST"
  echo
  echo "下一步："
  echo "  运行群聊统计： python3 group_digest.py --list"
  echo "  密钥清单应由你本机单独保管，并通过 --key-file 指定；不要放入备份或 Git 仓库。"
else
  echo "[x] 校验未通过，请检查磁盘空间后重跑。"
  exit 1
fi
