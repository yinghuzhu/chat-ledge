#!/usr/bin/env python3
"""按群名和日期范围读取本机微信消息库，输出 JSONL/CSV 与统计摘要。

读取方式为只读 SQLCipher C API，支持仍留在 WAL 中的已提交消息。
普通群消息发送者从消息内容前缀解析；绝不通过联系人好友表映射群成员。
"""

import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_CONTAINER = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
DEFAULT_KEY_FILE = Path.home() / "wechat-export/key-capture/keys.json"
DEFAULT_OUT = Path.home() / "wechat-export/exports"
TZ = ZoneInfo("Asia/Shanghai")
MSG_HASH = re.compile(r"^Msg_([0-9a-fA-F]{32})$")
SENDER_BODY = re.compile(r"^([^:\r\n]{1,128}):\r?\n(.*)$", re.S)
BAD_TEXT = re.compile("\ufffd")


def parser():
    p = argparse.ArgumentParser(description="指定微信群与日期范围，导出消息并生成活动统计")
    p.add_argument("--chat", help="群名称关键词；若匹配多个群会列出候选并退出")
    p.add_argument("--start", help="起始日期，含当天，格式 YYYY-MM-DD")
    p.add_argument("--end", help="结束日期，含当天，格式 YYYY-MM-DD")
    p.add_argument("--weekdays", action="store_true", help="快捷窗口：指定日期所在周的周一至周五")
    p.add_argument("--week-of", help="与 --weekdays 连用，指定该周任意日期 YYYY-MM-DD")
    p.add_argument("--list", action="store_true", help="列出可识别群组")
    p.add_argument("--account", help="微信账号目录名，默认自动选择数据最大的账号")
    p.add_argument("--container", default=str(DEFAULT_CONTAINER), help="微信 xwechat_files 路径")
    p.add_argument("--key-file", default=None, help="私有 JSON 密钥清单路径")
    p.add_argument("--outdir", default=str(DEFAULT_OUT), help="导出根目录")
    p.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    p.add_argument("--limit", type=int, default=0, help="仅取前 N 条，默认全部")
    return p


def get_crypto_db_class():
    """Load the bundled SQLCipher read-only ctypes reader."""
    import importlib.util
    path = Path(__file__).with_name("sqlcipher_probe.py")
    spec = importlib.util.spec_from_file_location("wechat_sqlcipher_probe", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载 SQLCipher reader：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DB, module.load_key


def choose_account(container, requested):
    roots = [p for p in container.iterdir() if p.is_dir() and (p / "db_storage").is_dir()]
    if requested:
        roots = [p for p in roots if p.name == requested]
        if not roots:
            raise ValueError(f"找不到账号 {requested}，可用账号目录：{', '.join(p.name for p in container.iterdir() if p.is_dir())}")
        return roots[0]
    if not roots:
        raise ValueError(f"没有找到含 db_storage 的账号目录：{container}")
    return max(roots, key=lambda p: sum(f.stat().st_size for f in (p / "db_storage").rglob("*.db") if f.is_file()))


def open_db(db_class, load_key, key_file, path):
    return db_class(path, load_key(key_file, path))


def query_contacts(db_class, load_key, key_file, account):
    db = open_db(db_class, load_key, key_file, account / "db_storage/contact/contact.db")
    try:
        rows = db.query("SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom'")
        result = {}
        for row in rows:
            uid = row["username"]
            title = (row.get("remark") or "").strip() or (row.get("nick_name") or "").strip()
            # The prior fallback displayed the internal room ID as if it were a group name.
            if not title or title == uid or title == uid.removesuffix("@chatroom"):
                title = "未命名群"
            result[uid] = title
        return result
    finally:
        db.close()


def find_group_tables(db_class, load_key, key_file, account, contacts):
    dbdir = account / "db_storage/message"
    chats_by_hash = {hashlib.md5(uid.encode()).hexdigest(): (uid, title)
                     for uid, title in contacts.items()}
    found = []
    for path in sorted(dbdir.glob("message_*.db")):
        db = open_db(db_class, load_key, key_file, path)
        try:
            names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in names:
                match = MSG_HASH.fullmatch(name)
                if match and match.group(1).lower() in chats_by_hash:
                    uid, title = chats_by_hash[match.group(1).lower()]
                    found.append((uid, title, path, name))
        finally:
            db.close()
    return found


def parse_window(args):
    if args.list and not any([args.start, args.end, args.weekdays, args.week_of]):
        return None, None
    if args.weekdays:
        reference = dt.date.fromisoformat(args.week_of or args.start or dt.date.today().isoformat())
        monday = reference - dt.timedelta(days=reference.weekday())
        if not args.week_of and not args.start and reference.weekday() < 5:
            monday -= dt.timedelta(days=7)
        return monday, monday + dt.timedelta(days=4)
    if args.week_of:
        raise ValueError("--week-of 需要与 --weekdays 同时使用")
    if not args.start and not args.end:
        raise ValueError("请指定 --start/--end，或使用 --weekdays [--week-of YYYY-MM-DD]")
    start = dt.date.fromisoformat(args.start) if args.start else dt.date(1970, 1, 1)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    if end < start:
        raise ValueError("结束日期不能早于起始日期")
    return start, end


def date_bounds(start, end):
    if start is None or end is None:
        return None, None
    return int(dt.datetime.combine(start, dt.time.min, TZ).timestamp()), int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, TZ).timestamp())


def count_group_messages(db_class, load_key, key_file, matches, start_ts, end_ts):
    total = 0
    for _uid, _title, path, table in matches:
        db = open_db(db_class, load_key, key_file, path)
        try:
            cols = {r["name"] for r in db.query(f'PRAGMA table_info("{table}")')}
            time_col = "create_time" if "create_time" in cols else "CreateTime" if "CreateTime" in cols else None
            if start_ts is None:
                sql = f'SELECT COUNT(*) AS n FROM "{table}"'
            elif time_col:
                sql = f'SELECT COUNT(*) AS n FROM "{table}" WHERE "{time_col}" >= {start_ts} AND "{time_col}" < {end_ts}'
            else:
                continue
            total += int(db.query(sql)[0]["n"] or 0)
        finally:
            db.close()
    return total


def safe_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = str(value).replace("\x00", "")
    if BAD_TEXT.search(value):
        return "[系统/富媒体负载未解码]"
    return "".join(ch for ch in value if ch in "\n\r\t" or ord(ch) >= 32)


def decode_content(raw, typ):
    text = safe_text(raw)
    if text.startswith("[系统/富媒体负载未解码]"):
        return None, text
    match = SENDER_BODY.match(text)
    if match:
        return match.group(1).strip(), match.group(2)
    return None, text


def fetch_chat(db_class, load_key, key_file, matches, start_ts, end_ts, limit):
    rows = []
    def append_rows(batch, title, uid, shard):
        for raw in batch:
            ts = int(raw.get(time_col) or 0)
            if ts > 10_000_000_000:
                ts //= 1000
            typ = raw.get(type_col, "") if type_col else ""
            content = safe_text(raw.get(content_col)) if content_col else ""
            sender, content = decode_content(content, typ)
            rows.append({"chat": title, "chat_id": uid, "time": dt.datetime.fromtimestamp(ts, TZ).isoformat(sep=" ", timespec="seconds"),
                         "timestamp": ts, "sender": sender, "type": str(typ), "content": content,
                         "local_id": raw.get(id_col) if id_col else None,
                         "server_id": raw.get(server_id_col) if server_id_col else None,
                         "source_shard": shard})

    for uid, title, path, table in matches:
        db = open_db(db_class, load_key, key_file, path)
        try:
            cols = {r["name"] for r in db.query(f'PRAGMA table_info("{table}")')}
            time_col = "create_time" if "create_time" in cols else "CreateTime" if "CreateTime" in cols else None
            content_col = "message_content" if "message_content" in cols else "StrContent" if "StrContent" in cols else None
            type_col = "local_type" if "local_type" in cols else "type" if "type" in cols else None
            id_col = "local_id" if "local_id" in cols else "MsgSvrID" if "MsgSvrID" in cols else None
            server_id_col = "server_id" if "server_id" in cols else "MsgSvrID" if "MsgSvrID" in cols else None
            sender_id_col = "real_sender_id" if "real_sender_id" in cols else None
            if not time_col:
                raise RuntimeError(f"{path.name}:{table} 没有识别时间列")
            selected = list(dict.fromkeys(c for c in [id_col, server_id_col, time_col, type_col, sender_id_col, content_col] if c))
            # Stream rows in bounded batches; weekly reports can span hundreds of thousands of events.
            sql = f'SELECT {", ".join(chr(34)+c+chr(34) for c in selected)} FROM "{table}" WHERE "{time_col}" >= {start_ts} AND "{time_col}" < {end_ts} ORDER BY "{time_col}"'
            batch_size = 2000
            offset = 0
            while True:
                batch = db.query(sql + f" LIMIT {batch_size} OFFSET {offset}")
                if not batch:
                    break
                append_rows(batch, title, uid, path.name)
                offset += len(batch)
        finally:
            db.close()
    rows.sort(key=lambda r: (r["timestamp"], str(r["local_id"] or "")))
    unique, seen = [], set()
    for row in rows:
        server_id = row.get("server_id")
        key = ((row["chat_id"], "server", server_id) if server_id not in (None, "", "0") else
               (row["chat_id"], "local", row.get("source_shard"), row.get("local_id")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique[:limit] if limit else unique


def summarize(rows, title, start, end):
    types = collections.Counter(r["type"] for r in rows)
    senders = collections.Counter(r["sender"] for r in rows if r["sender"])
    daily = collections.Counter(r["time"][:10] for r in rows)
    monthly = collections.Counter(r["time"][:7] for r in rows)
    plain_count = sum(1 for r in rows if r["type"] == "1")
    lines = [f"# {title}：{start} 至 {end} 消息统计", "", f"- 消息总数：{len(rows):,}",
             f"- 普通文本消息（type=1）：{plain_count:,}", f"- 可解析群成员发送者：{sum(1 for r in rows if r['sender']):,} 条，{len(senders):,} 个 username",
             f"- 无法确认发送者：{sum(1 for r in rows if not r['sender']):,} 条",
             f"- 活跃日期：{len(daily):,} 天", f"- 消息类型数：{len(types):,}", "",
             "## 每日消息量", "", "| 日期 | 条数 |", "|---|---:|"]
    lines += [f"| {day} | {count:,} |" for day, count in sorted(daily.items())]
    lines += ["", "## 每月消息量", "", "| 月份 | 条数 |", "|---|---:|"]
    lines += [f"| {month} | {count:,} |" for month, count in sorted(monthly.items())]
    lines += ["", "## 活跃发送者（消息内 username）", "", "| Username | 条数 |", "|---|---:|"]
    lines += [f"| `{name}` | {count:,} |" for name, count in senders.most_common(30)]
    lines += ["", "## 内容简析", "", "以下仅对可读纯文本做启发式关键词统计，不代表语义分类；建议阅读导出文本后再归纳主题。", ""]
    words = collections.Counter()
    stop = set("我们 你们 他们 这个 那个 可以 不是 没有 一个 真的 感觉 现在 今天 时候 因为 所以 怎么 什么 如果 还是 但是 而且 大家 一下 这里 那里".split())
    for row in rows:
        if row["type"] != "1" or row["content"].startswith("["):
            continue
        for word in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", row["content"]):
            if word not in stop and len(word) < 25:
                words[word.lower()] += 1
    lines += ["关键词（原文词频，不等同主题结论）：" + ("、".join(f"{w}（{n}）" for w, n in words.most_common(30)) or "无可统计纯文本"), "",
              "> 发送者只从群消息内容的 username 前缀提取。没有此前缀时留空；不会依据本地好友/联系人昵称推断。图片、语音、链接卡片等未解码内容不进入文本关键词统计。"]
    return "\n".join(lines) + "\n"


def write_rows(rows, path, fmt):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["chat", "chat_id", "time", "timestamp", "sender", "type", "content", "local_id", "source_shard"])
            writer.writeheader()
            writer.writerows(rows)
    os.chmod(path, 0o600)


def main():
    args = parser().parse_args()
    try:
        start, end = parse_window(args)
        container = Path(args.container).expanduser()
        account = choose_account(container, args.account)
        db_class, load_key = get_crypto_db_class()
        key_file = Path(args.key_file).expanduser() if args.key_file else DEFAULT_KEY_FILE
        if not args.key_file and not key_file.exists():
            legacy = sorted((Path.home() / "wechat-export").glob("key-capture-*/keys.json"))
            if len(legacy) == 1:
                key_file = legacy[0]
            elif len(legacy) > 1:
                raise ValueError("发现多份历史密钥清单，请用 --key-file 明确指定正确文件")
        contacts = query_contacts(db_class, load_key, key_file, account)
        tables = find_group_tables(db_class, load_key, key_file, account, contacts)
        if args.list:
            start_ts, end_ts = date_bounds(start, end) if start else (None, None)
            grouped = {}
            for uid, title, path, table in tables:
                grouped.setdefault(uid, {"title": title, "matches": []})["matches"].append((uid, title, path, table))
            period = f"{start} 至 {end}" if start else "全部时间"
            print(f"群名\t消息数\t微信会话 ID\t日期范围")
            for uid, group in sorted(grouped.items(), key=lambda item: (item[1]["title"].casefold(), item[0])):
                count = count_group_messages(db_class, load_key, key_file, group["matches"], start_ts, end_ts)
                if count == 0:
                    continue
                print(f"{group['title']}\t{count:,} 条\t{uid}\t{period}")
            return 0
        if not args.chat:
            raise ValueError("请提供 --chat 群名关键词，或用 --list 查看群组")
        matched = [m for m in tables if args.chat.casefold() in m[1].casefold() or args.chat.casefold() in m[0].casefold()]
        # Collapse the same conversation table across message shards to one conversation.
        groups = {}
        for uid, title, path, table in matched:
            groups.setdefault(uid, {"title": title, "matches": []})["matches"].append((uid, title, path, table))
        if not groups:
            raise ValueError(f"没有匹配到群「{args.chat}」，先运行 --list")
        if len(groups) > 1:
            print("匹配到多个群，请用更精确的关键词：", file=sys.stderr)
            for uid, group in groups.items():
                print(f"  {group['title']}\t{uid}", file=sys.stderr)
            return 2
        group = next(iter(groups.values()))
        start_ts, end_ts = date_bounds(start, end)
        rows = fetch_chat(db_class, load_key, key_file, group["matches"], start_ts, end_ts, args.limit)
        folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", group["title"]).strip(" .") or "未命名群"
        same_title_count = sum(1 for title in contacts.values() if title == group["title"])
        if same_title_count > 1:
            folder_name += "-" + group["matches"][0][0].removesuffix("@chatroom")
        folder = Path(args.outdir).expanduser() / folder_name
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        data_path = folder / f"{start.isoformat()}_{end.isoformat()}.{args.format}"
        summary_path = folder / f"{start.isoformat()}_{end.isoformat()}_summary.md"
        write_rows(rows, data_path, args.format)
        summary_path.write_text(summarize(rows, group["title"], start, end), encoding="utf-8")
        os.chmod(summary_path, 0o600)
        print(f"群组：{group['title']}\n区间：{start} 至 {end}\n消息：{len(rows):,}\n明细：{data_path}\n统计：{summary_path}")
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
