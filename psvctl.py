#!/usr/bin/env python3
"""Small, deliberately restricted VitaShell FTP client."""

from __future__ import annotations

import argparse
import ftplib
import io
import json
import os
import posixpath
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
READ_ROOTS = ("ux0:/app", "ux0:/patch", "ux0:/rePatch")
WRITE_ROOT = "ux0:/rePatch"


class PsvCtlError(RuntimeError):
    pass


HANDLED_ERRORS = (PsvCtlError,) + ftplib.all_errors


def canonical_remote(raw: str) -> str:
    value = raw.strip().replace("\\", "/")
    if not value:
        raise PsvCtlError("远程路径不能为空")
    if "\x00" in value:
        raise PsvCtlError("远程路径包含非法字符")

    # Accept both VitaShell spellings: ux0:app/foo and ux0:/app/foo.
    if ":" not in value:
        raise PsvCtlError("远程路径必须以分区名开头，例如 ux0:app/")
    volume, rest = value.split(":", 1)
    volume = volume.lower()
    if volume != "ux0":
        raise PsvCtlError("第一版只允许访问 ux0: 分区")
    parts = rest.split("/")
    if any(part == ".." for part in parts):
        raise PsvCtlError("远程路径不允许包含 '..'")
    cleaned = posixpath.normpath("/" + rest.lstrip("/"))
    return f"{volume}:{cleaned}"


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def ftp_path(path: str) -> str:
    """Convert a validated Vita path to FTPVita's absolute path spelling."""
    return "/" + path


def require_read_path(raw: str) -> str:
    path = canonical_remote(raw)
    if not any(_within(path, root) for root in READ_ROOTS):
        allowed = ", ".join(READ_ROOTS)
        raise PsvCtlError(f"拒绝读取 {path}；允许的目录：{allowed}")
    return path


def require_write_path(raw: str) -> str:
    path = canonical_remote(raw)
    if not _within(path, WRITE_ROOT):
        raise PsvCtlError(
            f"拒绝写入 {path}；为保护游戏本体，只允许写入 {WRITE_ROOT}/"
        )
    if path == WRITE_ROOT:
        raise PsvCtlError("push 的目标必须是文件路径，不能是 rePatch 根目录")
    return path


def load_config(path: Path) -> dict:
    if not path.exists():
        raise PsvCtlError(
            f"找不到配置文件 {path.name}。先运行：./psvctl configure <PSV_IP> [端口]"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        host = str(data["host"]).strip()
        port = int(data.get("port", 1337))
        timeout = float(data.get("timeout", 10))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PsvCtlError(f"配置文件格式无效：{exc}") from exc
    if not host or not 1 <= port <= 65535 or timeout <= 0:
        raise PsvCtlError("配置中的 host、port 或 timeout 无效")
    return {"host": host, "port": port, "timeout": timeout}


def connect(config: dict) -> ftplib.FTP:
    ftp = ftplib.FTP()
    ftp.connect(config["host"], config["port"], timeout=config["timeout"])
    ftp.login()
    ftp.set_pasv(True)
    return ftp


def mkdirs(ftp: ftplib.FTP, remote_dir: str) -> None:
    volume, rest = remote_dir.split(":", 1)
    current = "/" + volume + ":"
    for part in (p for p in rest.split("/") if p):
        current += "/" + part
        try:
            ftp.mkd(current)
        except ftplib.error_perm as exc:
            # VitaShell commonly reports 550 when the directory already exists.
            if not str(exc).startswith("550"):
                raise


def parse_sfo(data: bytes) -> dict[str, object]:
    if len(data) < 20 or data[:4] != b"\x00PSF":
        raise PsvCtlError("不是有效的 param.sfo")
    _, _, key_start, data_start, count = struct.unpack_from("<4sIIII", data)
    result: dict[str, object] = {}
    for index in range(count):
        offset = 20 + index * 16
        if offset + 16 > len(data):
            raise PsvCtlError("param.sfo 索引越界")
        key_offset, value_format, value_len, _, value_offset = struct.unpack_from(
            "<HHIII", data, offset
        )
        key_pos = key_start + key_offset
        key_end = data.find(b"\0", key_pos)
        value_pos = data_start + value_offset
        value_end = value_pos + value_len
        if key_end < 0 or value_end > len(data):
            raise PsvCtlError("param.sfo 数据越界")
        key = data[key_pos:key_end].decode("utf-8", errors="replace")
        raw = data[value_pos:value_end]
        if value_format == 0x0404 and len(raw) >= 4:
            value: object = struct.unpack_from("<I", raw)[0]
        else:
            value = raw.rstrip(b"\0").decode("utf-8", errors="replace")
        result[key] = value
    return result


def cmd_configure(args: argparse.Namespace) -> None:
    payload = {"host": args.host, "port": args.port, "timeout": args.timeout}
    args.config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"已保存连接配置：{args.host}:{args.port}")


def cmd_status(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    with connect(cfg) as ftp:
        print(f"已连接 PSV：{cfg['host']}:{cfg['port']}")
        print(f"服务器：{ftp.getwelcome()}")
        print(f"当前位置：{ftp.pwd()}")
        try:
            entries = ftp.nlst()
        except ftplib.error_perm:
            entries = []
        if entries:
            print("根目录：" + ", ".join(entries))
        probes = ("ux0:", "/ux0:", "ux0:/", "/ux0:/")
        for candidate in probes:
            try:
                ftp.cwd("/")
                ftp.cwd(candidate)
                print(f"可用的 ux0 路径：{candidate}（当前位置 {ftp.pwd()}）")
                break
            except ftplib.error_perm:
                continue


def cmd_ls(args: argparse.Namespace) -> None:
    remote = require_read_path(args.remote)
    wire_path = ftp_path(remote)
    cfg = load_config(args.config)
    with connect(cfg) as ftp:
        ftp.cwd(wire_path)
        try:
            for name, facts in ftp.mlsd():
                kind = facts.get("type", "?")
                size = facts.get("size", "-")
                print(f"{kind:4} {size:>12}  {name}")
        except (ftplib.error_perm, NotImplementedError):
            ftp.retrlines("LIST", print)


def cmd_titles(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    with connect(cfg) as ftp:
        ftp.cwd("/ux0:/app")
        lines: list[str] = []
        ftp.retrlines("LIST", lines.append)
        title_ids = sorted(
            line.rsplit(maxsplit=1)[-1]
            for line in lines
            if line.startswith("d") and line.rsplit(maxsplit=1)[-1] not in (".", "..")
        )
        for folder in title_ids:
            stream = io.BytesIO()
            try:
                ftp.retrbinary(
                    f"RETR /ux0:/app/{folder}/sce_sys/param.sfo", stream.write
                )
                fields = parse_sfo(stream.getvalue())
                title_id = fields.get("TITLE_ID", folder)
                title = fields.get("TITLE", "（未知标题）")
                print(f"{title_id}\t{title}")
            except (ftplib.error_perm, PsvCtlError):
                print(f"{folder}\t（无法读取标题）")


def cmd_pull(args: argparse.Namespace) -> None:
    remote = require_read_path(args.remote)
    local = args.local or Path(posixpath.basename(remote))
    if local.exists() and not args.force:
        raise PsvCtlError(f"本地文件已存在：{local}（如需覆盖，加 --force）")
    local.parent.mkdir(parents=True, exist_ok=True)
    temp = local.with_name(local.name + ".part")
    cfg = load_config(args.config)
    try:
        with connect(cfg) as ftp, temp.open("wb") as stream:
            ftp.retrbinary(f"RETR {ftp_path(remote)}", stream.write)
        os.replace(temp, local)
    finally:
        if temp.exists():
            temp.unlink()
    print(f"已拉取：{remote} -> {local} ({local.stat().st_size} bytes)")


def cmd_push(args: argparse.Namespace) -> None:
    local = args.local
    if not local.is_file():
        raise PsvCtlError(f"本地文件不存在：{local}")
    remote = require_write_path(args.remote)
    wire_path = ftp_path(remote)
    cfg = load_config(args.config)
    with connect(cfg) as ftp, local.open("rb") as stream:
        mkdirs(ftp, posixpath.dirname(remote))
        ftp.storbinary(f"STOR {wire_path}", stream)
        try:
            remote_size = ftp.size(wire_path)
        except ftplib.all_errors:
            remote_size = None
    if remote_size is not None and remote_size != local.stat().st_size:
        raise PsvCtlError(
            f"上传后的大小校验失败：本地 {local.stat().st_size}，PSV {remote_size}"
        )
    print(f"已上传并校验：{local} -> {remote}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psvctl", description="通过 VitaShell FTP 安全读取 PSV，并只向 ux0:rePatch/ 写入"
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="连接配置文件（默认 config.json）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser("configure", help="保存 VitaShell FTP 地址")
    configure.add_argument("host", help="VitaShell 显示的 IP 地址")
    configure.add_argument("port", nargs="?", type=int, default=1337)
    configure.add_argument("--timeout", type=float, default=10)
    configure.set_defaults(func=cmd_configure)

    status = sub.add_parser("status", help="测试 FTP 连接")
    status.set_defaults(func=cmd_status)

    ls_cmd = sub.add_parser("ls", help="列出只读/补丁目录")
    ls_cmd.add_argument("remote")
    ls_cmd.set_defaults(func=cmd_ls)

    titles = sub.add_parser("titles", help="只读扫描已安装游戏的标题")
    titles.set_defaults(func=cmd_titles)

    pull = sub.add_parser("pull", help="从允许的目录拉取一个文件")
    pull.add_argument("remote")
    pull.add_argument("local", nargs="?", type=Path)
    pull.add_argument("--force", action="store_true")
    pull.set_defaults(func=cmd_pull)

    push = sub.add_parser("push", help="向 ux0:rePatch/ 上传一个文件")
    push.add_argument("local", type=Path)
    push.add_argument("remote")
    push.set_defaults(func=cmd_push)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except HANDLED_ERRORS as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
