# 微信群消息统计工具

本项目提供本机微信聊天数据库备份，以及按群和日期范围导出消息、生成活动统计的命令行工具。读取操作在本机完成；程序不会连接微信服务、发送消息或修改微信数据库。

## 能做什么

- 按群名查找会话，或列出指定日期内有消息的群。
- 按完整日期范围导出消息，起止日期均包含。
- 周末复盘时快捷选择周一至周五。
- 输出 JSONL 或 CSV 明细，以及 Markdown 统计摘要。
- 统计消息量、活跃日期、消息类型、消息内可识别的发送者 username 和文本关键词。

发送者只从群消息自身携带的 username 前缀解析。没有可靠 username 的系统消息和媒体消息会留空，不会按通讯录好友关系推断群友身份。图片、语音、链接卡片等未解码内容不会作为普通文本分析。摘要里的关键词仅为词频线索，不是完整的语义结论。

## 环境要求

- macOS 微信桌面版，数据位于默认沙盒目录：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/`
- Python 3.9 或更新版本。
- SQLCipher 4 动态库。Apple Silicon Homebrew 默认位置为 `/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib`；Intel Homebrew 常见位置为 `/usr/local/opt/sqlcipher/lib/libsqlcipher.dylib`。也可通过 `SQLCIPHER_LIBRARY` 指定动态库路径。
- 本机私有 SQLCipher 密钥清单。默认使用 `~/wechat-export/key-capture/keys.json`；若该文件不存在且只找到一份旧式 `~/wechat-export/key-capture-*/keys.json`，工具会自动复用它。也可以用 `--key-file` 指定路径。密钥文件必须只允许当前用户读取（权限 `600`）。

密钥获取脚本会把清单写到仓库以外的私有目录。请不要把密钥、清单、数据库副本或聊天记录复制进 Git 仓库或发给他人。项目不会打印密钥值。

## 首次获取密钥

已有密钥时先复用并验证，不要每次使用都重新获取。没有可用密钥时，按下面流程为本人账号初始化一次。

### 1. 备份并选定账号数据库

```bash
./0_backup.sh
find "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files" \
  -maxdepth 3 -type d -name db_storage -print
```

从输出中确认本人账号的 `db_storage` 路径，并在终端设置变量：

```bash
WX_DB="$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/本人账号目录/db_storage"
KEYS="$HOME/wechat-export/key-capture/keys.json"
```

多账号时必须手动选对 `WX_DB`。不要混用其他账号或其他数据库副本的密钥。
若已有密钥保存在旧式带日期目录（例如 `~/wechat-export/key-capture-<日期>/keys.json`），把上面的 `KEYS` 改成那份文件的完整路径。

### 2. 检查现有密钥

如果手上已有密钥，先针对该账号的消息库做只读验证：

```bash
python3 sqlcipher_probe.py verify \
  --db "$WX_DB/message/message_0.db" \
  --key-file "$KEYS"
```

看到 `verified: true` 后再运行 `python3 group_digest.py --list` 检查联系人库和消息分片是否可读。如果密钥有效，就跳过下一步。新建的消息分片可能尚未包含在旧密钥清单中；若工具报告该分片缺少密钥，应核对账号和数据库路径，再考虑重新获取。

### 3. 没有有效密钥时运行获取脚本

密钥获取需要 Apple Silicon（arm64）Mac、可用的 Xcode Command Line Tools/LLDB，以及本人能登录的微信账号。先保存工作并从微信菜单**正常退出微信**。脚本会拒绝以 `sudo` 运行，也会拒绝在原版微信仍运行时启动。

```bash
python3 bootstrap_keys.py \
  --db-root "$WX_DB" \
  --state-dir "$HOME/wechat-export/key-capture" \
  --acknowledge-debug-copy
```

脚本会复制微信到私有状态目录，只对临时副本做 ad-hoc 签名，再由 LLDB 启动这个副本并观察数据库密钥派生调用。你需要在临时微信窗口里登录本人账号，必要时用手机确认或扫码；不要在临时副本里继续聊天。捕获到的候选密钥会按数据库盐值逐库验证，通过 HMAC 检查后才以 `0600` 权限写入 `keys.json`。流程结束会关闭临时进程、禁用副本并请求恢复原版微信；请亲自确认原版可以正常打开和登录。

成功后验证刚写入的清单并列出群：

```bash
python3 sqlcipher_probe.py verify --db "$WX_DB/message/message_0.db" --key-file "$KEYS"
python3 group_digest.py --key-file "$KEYS" --list
```

`bootstrap_keys.py` 默认拒绝覆盖已有密钥、临时副本或禁用副本。如果上一次中断留下状态，不要直接删除；先确认临时微信已退出，再检查状态目录。确实需要重做时，为 `--state-dir` 选择一个新的私有目录，并随后用对应的 `--key-file`。

**兼容性边界：**捕获核心只支持 Apple Silicon 上通过 LLDB 启动的微信进程。它依赖具体微信版本中的 PBKDF 调用；随附方法的公开实测说明覆盖微信 4.1.15，其他版本需单独验证。若目标版本没有触发断点，脚本会超时/失败，不应把失败解释为数据损坏。已经验证可用的密钥应继续复用。副本需要使用真实账号数据，可能触发登录验证；此操作会中断微信并改变临时副本签名状态，不能保证服务端对登录行为无感知。不要关闭 SIP、不要使用 `sudo`，也不要把密钥发给他人。

## 快速开始

在仓库目录运行：

```bash
# 查看所有有本地记录的群
python3 group_digest.py --list

# 查看指定日期范围内有消息的群及消息数
python3 group_digest.py --start 2025-01-06 --end 2025-01-10 --list

# 导出指定群在一个日期范围内的消息，并生成统计摘要
python3 group_digest.py \
  --chat "示例交流群" \
  --start 2025-01-06 \
  --end 2025-01-10

# 周末复盘最近结束的一周（周一至周五）
python3 group_digest.py --chat "示例交流群" --weekdays

# 指定某周中的任意日期，统计该周周一至周五
python3 group_digest.py \
  --chat "示例交流群" \
  --weekdays \
  --week-of 2025-01-08

# 输出 CSV 明细
python3 group_digest.py \
  --chat "示例交流群" \
  --start 2025-01-06 \
  --end 2025-01-10 \
  --format csv
```

群名支持关键词匹配。如果匹配到多个不同群，程序会列出候选项并退出；请用更具体的群名重新运行。`--list` 每个会话只显示一行，消息数为 0 的群会隐藏。输出中的“微信会话 ID”（形如 `123456789@chatroom`）是微信内部标识，用于区分群，不是群名称。无法从联系人资料取得名称时，群名会显示为“未命名群”。

可选参数：

| 参数 | 作用 |
|---|---|
| `--account NAME` | 选择微信账号目录；默认选择数据库总量最大的账号 |
| `--container PATH` | 指定 `xwechat_files` 数据目录 |
| `--key-file PATH` | 指定本机私有密钥清单 |
| `--outdir PATH` | 指定导出根目录，默认 `~/wechat-export/exports` |
| `--format jsonl\|csv` | 选择明细格式，默认 JSONL |
| `--limit N` | 限制导出条数；默认导出范围内的全部消息 |

日期按 `Asia/Shanghai` 计算，起止日期均包含。仅给出一个边界时，另一边界默认取 1970-01-01 或今天。`--weekdays` 不带参考日期时，建议在周末运行；需要指定某一周时使用 `--week-of YYYY-MM-DD`。

## 输出文件

每次运行会在 `~/wechat-export/exports/<群名>/` 下创建；若有多个同名群，目录名会附加微信会话 ID 以避免覆盖：

- `<开始日期>_<结束日期>.jsonl` 或 `.csv`：范围内的逐条消息。
- `<开始日期>_<结束日期>_summary.md`：总量、每日/月消息数、发送者统计和可读文本关键词。

JSONL 每行是一条消息，字段包括群名、内部群 ID、时间、群消息内发送者 username、消息类型、正文、消息 ID 和来源分片。私有数据文件会设置为仅当前用户可读写（`600`）；目录为仅当前用户访问（`700`）。

## 脚本说明

### `bootstrap_keys.py`

首次取钥的流程编排器。检查平台、架构、权限、微信进程和状态目录；复制指定微信 App，仅对副本 ad-hoc 签名；调用捕获脚本，并在结束时关闭/禁用副本、检查原版签名未变化，再请求恢复原版。不要跳过 README 的前置说明，不要使用 `sudo`。

### `capture_keys.py`

由 `bootstrap_keys.py` 调用的 LLDB 捕获核心。只启动指定的微信副本，不 attach 任意进程；在 Apple Silicon 上观察 PBKDF 参数，只处理与所选数据库盐值匹配的候选，并在输出密钥前验证数据库页 HMAC。密钥只写私有 JSON 文件，不打印到终端。版本兼容边界和捕获失败时的含义见“首次获取密钥”。

### `group_digest.py`

主命令行工具。只读当前微信 SQLCipher 数据库，并读取相邻 WAL 文件中已提交的记录。它从联系人库取得群名和内部群 ID，再按 `Msg_<MD5(群 ID)>` 匹配分布在多个 `message_*.db` 中的消息表，按时间合并分片并导出。

消息正文中的群 username 用作发送者标识；本地联系人昵称不会覆盖它。无法可靠解码的系统或富媒体负载会替换为占位说明。关键词摘要只处理可读普通文本。

### `sqlcipher_probe.py`

供 `group_digest.py` 使用的 SQLCipher C API 只读适配层。负责从权限受限的密钥清单中按数据库路径/盐值查找密钥，以只读、query-only 模式打开加密库。也提供 `verify` 子命令检查一份数据库和密钥是否匹配，以及 `self-test` 在临时目录中验证适配层行为；不会修改真实微信数据库。

### `0_backup.sh`

备份微信账号的 `db_storage`，默认只备数据库；`--full` 会复制整个账号目录（含媒体文件，可能占用大量空间）；`--dest PATH` 指定备份位置。程序会检查微信是否运行，并在复制后核对文件数量。运行中的数据库备份可能包含不一致的 WAL 状态，因此建议按脚本提示先退出微信。

```bash
./0_backup.sh
./0_backup.sh --full --dest /Volumes/Backup/wechat
```

`bootstrap_keys.py` 与 `capture_keys.py` 来源于 [wechat-key-research](https://gist.github.com/0a01249ef72970d07a2603dbb629e80f)，按随附 MIT 许可保留版权和许可声明。

## 隐私与 Git

仓库的 `.gitignore` 会忽略 SQLCipher/SQLite 数据库、密钥清单、导出目录、备份、解密副本、环境变量文件和 Python 缓存。提交前仍应检查 Git 暂存清单，确认没有个人聊天内容或密钥。

```bash
git status --short
git diff --cached --name-only
```
