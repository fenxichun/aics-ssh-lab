# -*- coding: utf-8 -*-
"""命令行入口。

    python -m aicslab env                      查看生效配置（密码自动打码）
    python -m aicslab check                    连通性自检（代理 → SSH → 容器信息）
    python -m aicslab run "ls /opt"            在容器里执行命令
    python -m aicslab sh  "bash run_cpu.sh"    带 pty 执行（进度条类输出更正常）
    python -m aicslab get /remote/a.py a.py    下载单文件
    python -m aicslab put a.py /remote/a.py    上传单文件
    python -m aicslab pull /opt/exp_1 ./exp_1  递归拉目录（默认过滤大文件）
    python -m aicslab push ./stu_upload /opt/x 递归推目录
"""

import argparse
import os
import sys

from . import __version__, config as config_mod, tunnel


def _add_common(parser):
    parser.add_argument("--host", help="平台域名，默认 aics.cambricon.com")
    parser.add_argument("--port", type=int, help="SSH NodePort（必填，见 browser/probe_env.js）")
    parser.add_argument("--user", help="SSH 用户名，默认 root")
    parser.add_argument("--password", help="容器 root 密码（建议用 aics.env 或环境变量）")
    parser.add_argument("--key", help="私钥路径；给了就用公钥认证")
    parser.add_argument("--socks", help="本地 SOCKS5 代理 host:port，默认 127.0.0.1:1080")
    parser.add_argument("--timeout", type=int, default=1800, help="单条命令超时秒数，默认 1800")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="aicslab",
        description="AICS 智算平台远程实验工具箱（SSH over SOCKS5 为核心）",
    )
    parser.add_argument("--version", action="version", version="aicslab %s" % __version__)
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("env", help="查看生效配置（密码打码）")
    _add_common(p)
    p.add_argument("--reveal", action="store_true",
                   help="打印可直接 export 的真实值（只在本机用）")

    p = sub.add_parser("check", help="连通性自检")
    _add_common(p)

    for name, help_text in (("run", "执行命令"), ("sh", "带 pty 执行命令")):
        p = sub.add_parser(name, help=help_text)
        _add_common(p)
        p.add_argument("command", help="要在容器里执行的命令")

    p = sub.add_parser("get", help="下载单个文件")
    _add_common(p)
    p.add_argument("remote")
    p.add_argument("local")

    p = sub.add_parser("put", help="上传单个文件")
    _add_common(p)
    p.add_argument("local")
    p.add_argument("remote")

    p = sub.add_parser("pull", help="递归拉取目录（默认跳过 data/models/out 与大文件）")
    _add_common(p)
    p.add_argument("remote_dir")
    p.add_argument("local_dir")
    p.add_argument("--all", action="store_true", help="不过滤，全部拉取（小心几 GB 数据集）")
    p.add_argument("--max-bytes", type=int, default=tunnel.MAX_BYTES,
                   help="单文件大小上限，默认 %d" % tunnel.MAX_BYTES)
    p.add_argument("--skip", action="append", default=[],
                   help="额外跳过的目录名，可重复")

    p = sub.add_parser("push", help="递归推送本地目录到容器")
    _add_common(p)
    p.add_argument("local_dir")
    p.add_argument("remote_dir")

    return parser


def _load(args):
    overrides = {
        "AICS_HOST": args.host,
        "AICS_PORT": args.port,
        "AICS_USER": args.user,
        "AICS_PASSWORD": args.password,
        "AICS_KEY": args.key,
        "AICS_SOCKS": args.socks,
    }
    return config_mod.load(**overrides)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2

    try:
        cfg = _load(args)
    except config_mod.ConfigError as exc:
        print("[配置错误] %s" % exc, file=sys.stderr)
        return 2

    try:
        if args.cmd == "env":
            if args.reveal:
                print(cfg.to_env_block())
                return 0
            print("生效配置：")
            for key, val in sorted(cfg.masked().items()):
                if val == "":
                    continue
                origin = cfg.source.get(key, "default")
                print("  %-22s %-34s (%s)" % (key, val, origin))
            print("\n目标：%s" % cfg.ssh_url())
            print("本地代理：%s:%d" % cfg.socks)
            return 0

        if args.cmd == "check":
            print(tunnel.check(cfg))
            return 0

        if args.cmd == "run":
            rc, out, err = tunnel.run(cfg, args.command, timeout=args.timeout)
            sys.stdout.write(out)
            if err.strip():
                sys.stdout.write("\n--- STDERR ---\n" + err)
            print("\n[exit %d]" % rc)
            return rc

        if args.cmd == "sh":
            rc, out, err = tunnel.run(cfg, args.command, timeout=args.timeout, get_pty=True)
            sys.stdout.write(out)
            if err.strip():
                sys.stdout.write("\n--- STDERR ---\n" + err)
            print("\n[exit %d]" % rc)
            return rc

        if args.cmd == "get":
            tunnel.get(cfg, args.remote, args.local)
            print("get ok -> %s" % args.local)
            return 0

        if args.cmd == "put":
            tunnel.put(cfg, args.local, args.remote)
            print("put ok -> %s" % args.remote)
            return 0

        if args.cmd == "pull":
            skip_dirs = None if args.all else (tunnel.SKIP_DIRS | set(args.skip))
            max_bytes = float("inf") if args.all else args.max_bytes
            count = tunnel.pull(
                cfg, args.remote_dir, args.local_dir,
                skip_dirs=skip_dirs, skip_ext=set() if args.all else tunnel.SKIP_EXT,
                max_bytes=max_bytes,
                on_skip=lambda path, exc: print("  skip %s (%s)" % (path, exc)),
            )
            print("pulled %d files -> %s" % (count, args.local_dir))
            return 0

        if args.cmd == "push":
            count = tunnel.push(cfg, args.local_dir, args.remote_dir)
            print("pushed %d files -> %s" % (count, args.remote_dir))
            return 0
    except tunnel.TunnelError as exc:
        print("[连接错误] %s" % exc, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    return 2
