#!/usr/bin/env python3
"""为私有群导出目录创建指向本机媒体目录的相对符号链接。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional

DEFAULT_CONTAINER = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
DEFAULT_EXPORTS = Path.home() / "wechat-export/exports"
MEDIA_DIRS = {
    "images": Path("msg/attach"),
    "videos": Path("msg/video"),
    "files": Path("msg/file"),
}
APP_ATTACHMENT_TYPE = "6"


def local_file_name(metadata: dict) -> Optional[str]:
    title = metadata.get("title")
    ext = metadata.get("file_extension")
    if not isinstance(title, str) or not title.strip():
        return None
    title = title.strip()
    if Path(title).name != title or "/" in title or "\\" in title:
        return None
    if ext is not None:
        if not isinstance(ext, str):
            return None
        ext = ext.strip().lstrip(".")
        if ext and not re.fullmatch(r"[A-Za-z0-9]{1,12}", ext):
            return None
        if ext and Path(title).suffix.lower() != ("." + ext).lower():
            title += "." + ext
    return title


def add_exact_file_links(group_dir: Path, account: Path) -> dict:
    """Link only attachment messages with a unique exact name+size match."""
    source_dir = account / MEDIA_DIRS["files"]
    candidates = {}
    for path in source_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        candidates.setdefault((path.name, size), []).append(path)

    linked = unmatched = 0
    linked_dir = group_dir / "media" / "linked_files"
    for data_path in group_dir.glob("*.jsonl"):
        temp_path = data_path.with_name("." + data_path.name + ".media.tmp")
        try:
            with data_path.open("r", encoding="utf-8") as source, temp_path.open("w", encoding="utf-8") as target:
                os.chmod(temp_path, 0o600)
                for line in source:
                    row = json.loads(line)
                    metadata = row.get("metadata") or {}
                    existing_links = row.get("media_links") or []
                    if str(metadata.get("app_type", "")) == APP_ATTACHMENT_TYPE:
                        name = local_file_name(metadata)
                        try:
                            size = int(metadata.get("file_size_bytes"))
                        except (TypeError, ValueError):
                            size = -1
                        matches = candidates.get((name, size), []) if name and size >= 0 else []
                        if len(matches) == 1:
                            source_path = matches[0]
                            rel_source = source_path.relative_to(account).as_posix()
                            digest = hashlib.sha256(rel_source.encode("utf-8")).hexdigest()[:20]
                            suffix = source_path.suffix.lower()
                            link_name = "file-" + digest + suffix
                            link_path = linked_dir / link_name
                            linked_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                            os.chmod(linked_dir, 0o700)
                            target_text = os.path.relpath(source_path, linked_dir)
                            if os.path.isabs(target_text):
                                raise ValueError("无法生成相对附件链接")
                            if link_path.is_symlink():
                                if os.readlink(link_path) != target_text:
                                    raise ValueError("已有附件链接指向其他位置")
                            elif link_path.exists():
                                raise ValueError("附件链接目标已存在且不是符号链接")
                            else:
                                link_path.symlink_to(target_text)
                            relative_link = (Path("media") / "linked_files" / link_name).as_posix()
                            if relative_link not in existing_links:
                                existing_links.append(relative_link)
                            row["media_links"] = existing_links
                            linked += 1
                        else:
                            unmatched += 1
                    target.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.chmod(temp_path, 0o600)
            temp_path.replace(data_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    return {"linked_file_attachments": linked, "unmatched_file_attachments": unmatched}


def choose_account(container: Path, requested: Optional[str]) -> Path:
    roots = [p for p in container.iterdir() if p.is_dir() and (p / "db_storage").is_dir()]
    if requested:
        roots = [p for p in roots if p.name == requested]
        if not roots:
            raise ValueError("指定的微信账号目录不存在")
        return roots[0]
    if not roots:
        raise ValueError("没有找到含 db_storage 的微信账号目录")
    return max(roots, key=lambda p: sum(f.stat().st_size for f in (p / "db_storage").rglob("*.db") if f.is_file()))


def safe_group_dir(exports_dir: Path, name: str) -> Path:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("群目录必须是导出根目录下的单个目录名")
    candidate = exports_dir / name
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("群导出目录不存在或不是普通目录")
    base = exports_dir.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    if resolved.parent != base:
        raise ValueError("群导出目录必须直接位于导出根目录下")
    return candidate


def add_links(account: Path, group_dir: Path) -> dict:
    os.chmod(group_dir, 0o700)
    media_dir = group_dir / "media"
    if media_dir.is_symlink():
        raise ValueError("media 路径不能是符号链接")
    media_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(media_dir, 0o700)

    relative_paths = {}
    for category, source_rel in MEDIA_DIRS.items():
        source = account / source_rel
        if not source.is_dir():
            raise ValueError(f"本机媒体目录缺失：{source_rel.as_posix()}")
        link = media_dir / category
        target = os.path.relpath(source, media_dir)
        if os.path.isabs(target):
            raise ValueError("无法生成相对媒体链接")
        if link.is_symlink():
            if os.readlink(link) != target:
                raise ValueError(f"已有链接指向其他位置：media/{category}")
        elif link.exists():
            raise ValueError(f"目标已存在且不是符号链接：media/{category}")
        else:
            link.symlink_to(target, target_is_directory=True)
        relative_paths[category] = (Path("media") / category).as_posix()

    file_results = add_exact_file_links(group_dir, account)
    manifest = {
        "version": 1,
        "storage": "relative_symlinks",
        "message_file_mapping": "unique_exact_name_and_size_for_file_attachments",
        "media_paths": relative_paths,
        **file_results,
        "voice": "stored as database blobs; no standalone local file link",
    }
    manifest_path = group_dir / "media_links.json"
    tmp_path = group_dir / ".media_links.json.tmp"
    tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(manifest_path)
    os.chmod(manifest_path, 0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="在现有群导出目录中建立相对媒体目录链接，不复制媒体内容")
    parser.add_argument("--group", action="append", required=True, help="导出根目录下的群目录名；可重复指定")
    parser.add_argument("--container", default=str(DEFAULT_CONTAINER), help="微信 xwechat_files 路径")
    parser.add_argument("--account", help="微信账号目录名；多个账号时必填")
    parser.add_argument("--exports-dir", default=str(DEFAULT_EXPORTS), help="群导出根目录")
    args = parser.parse_args()
    try:
        container = Path(args.container).expanduser()
        account = choose_account(container, args.account)
        exports_dir = Path(args.exports_dir).expanduser()
        for name in args.group:
            folder = safe_group_dir(exports_dir, name)
            manifest = add_links(account, folder)
            print(f"已建立 {len(manifest['media_paths'])} 组相对媒体目录链接；逐条匹配文件附件 {manifest['linked_file_attachments']} 条：{name}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
