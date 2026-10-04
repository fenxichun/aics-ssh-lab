# -*- coding: utf-8 -*-
"""SSH over SOCKS5 通道：连接、执行命令、SFTP 读写、目录同步。

为什么不用 ``ssh -D`` + ``ProxyCommand``？
  能连上平台的 SOCKS5 代理之后，直接在 Python 里做 SOCKS5 握手最省事：
  不依赖本机 ssh / nc / connect.exe，Windows 上也不会有引号地狱。
  前提：本机已经有一个可用的 SOCKS5（例如 ``ssh -N -D 127.0.0.1:1080 跳板机``）。
"""

import io
import os
import socket
import stat as statmod
import struct
import threading
import time

DEFAULT_TIMEOUT = 25

# pull 时默认跳过的大目录 / 大文件阈值，避免把几十 GB 的数据集拉下来
SKIP_DIRS = {
    "data", "dataset", "datasets", "models", "out", "output",
    "ckpt_model", "corpora", "__pycache__", ".git", ".cache",
}
SKIP_EXT = {".pth", ".pt", ".ckpt", ".tar", ".tgz", ".zip", ".onnx", ".bin", ".so", ".egg"}
MAX_BYTES = 3 * 1024 * 1024


class TunnelError(Exception):
    """连接层出错。"""


# --------------------------------------------------------------------------
# SOCKS5
# --------------------------------------------------------------------------
def socks5_connect(proxy, dst, timeout=DEFAULT_TIMEOUT,
                   username=None, password=None):
    """经 ``proxy=(host, port)`` 连到 ``dst=(host, port)``，返回已连接的 socket。"""
    phost, pport = proxy
    try:
        sock = socket.create_connection((phost, pport), timeout=timeout)
    except OSError as exc:
        raise TunnelError(
            "连不上本地 SOCKS5 代理 %s:%d（%s）。\n"
            "请先起一个：ssh -N -D 127.0.0.1:%d <跳板机>"
            % (phost, pport, exc, pport)
        )
    sock.settimeout(timeout)
    try:
        if username:
            sock.sendall(b"\x05\x02\x00\x02")          # 匿名 + 用户名密码
        else:
            sock.sendall(b"\x05\x01\x00")              # 只支持匿名
        resp = sock.recv(2)
        if len(resp) < 2 or resp[0] != 5:
            raise TunnelError("SOCKS5 握手响应异常: %r" % (resp,))
        method = resp[1]
        if method == 0x02:                             # 需要用户名密码
            if not username:
                sock.close()
                raise TunnelError(
                    "SOCKS5 代理要求用户名/密码认证，请设置 AICS_SOCKS_USER / AICS_SOCKS_PASSWORD"
                )
            u = (username or "").encode()
            p = (password or "").encode()
            sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
            if sock.recv(2)[1] != 0:
                sock.close()
                raise TunnelError("SOCKS5 用户名/密码认证失败")
        elif method != 0x00:
            sock.close()
            raise TunnelError("SOCKS5 代理不接受匿名，也不接受用户名密码（method=%d）" % method)

        host = dst[0].encode()
        port = int(dst[1])
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack(">H", port))
        head = sock.recv(4)
        if len(head) < 4 or head[1] != 0:
            sock.close()
            raise TunnelError(
                "SOCKS5 无法连到 %s:%d（rc=%s）。端口可能是旧环境的，"
                "重新跑一次 browser/probe_env.js 取新 NodePort。"
                % (dst[0], dst[1], head[1] if len(head) > 1 else "?")
            )
        atyp = head[3]
        if atyp == 1:
            sock.recv(4)
        elif atyp == 3:
            sock.recv(sock.recv(1)[0])
        elif atyp == 4:
            sock.recv(16)
        sock.recv(2)
        return sock
    except TunnelError:
        raise
    except OSError as exc:
        try:
            sock.close()
        except OSError:
            pass
        raise TunnelError("SOCKS5 通信失败: %s" % exc)


def probe_proxy(cfg, timeout=8):
    """只测本地代理通不通，不碰容器。返回 (ok, 说明)。"""
    phost, pport = cfg.socks
    try:
        s = socket.create_connection((phost, pport), timeout=timeout)
        s.close()
        return True, "本地 SOCKS5 %s:%d 可达" % (phost, pport)
    except OSError as exc:
        return False, "本地 SOCKS5 %s:%d 不可达（%s）" % (phost, pport, exc)


# --------------------------------------------------------------------------
# SSH
# --------------------------------------------------------------------------
def connect(cfg, timeout=DEFAULT_TIMEOUT):
    """建立到容器的 paramiko SSHClient。调用方负责 ``close()``。"""
    try:
        import paramiko
    except ImportError:
        raise TunnelError("缺少依赖：pip install paramiko")

    sock = socks5_connect(cfg.socks, (cfg.host, cfg.port), timeout=timeout,
                          username=cfg.socks_user, password=cfg.socks_password)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        port=cfg.port, username=cfg.user, sock=sock,
        allow_agent=False, timeout=timeout,
        banner_timeout=60, auth_timeout=60,
    )
    if cfg.key:
        kwargs["key_filename"] = cfg.key
        kwargs["look_for_keys"] = False
        kwargs["password"] = None
    else:
        kwargs["password"] = cfg.password
        kwargs["look_for_keys"] = False
    try:
        client.connect(cfg.host, **kwargs)
    except Exception as exc:
        try:
            sock.close()
        except OSError:
            pass
        raise TunnelError(
            "SSH 认证/握手失败: %s: %s\n"
            "常见原因：NodePort 变了、密码是旧环境的、或建环境时没勾 SSH。"
            % (type(exc).__name__, exc)
        )
    return client


def run(cfg, cmd, timeout=1800, client=None, get_pty=False):
    """在容器里执行命令，返回 ``(exit_code, stdout, stderr)``。

    stdout / stderr 用两个线程分别读，避免「一个流写满窗口、另一个流还在读」造成死锁。
    """
    own = client is None
    if own:
        client = connect(cfg)
    try:
        chan = client.get_transport().open_session(timeout=timeout)
        chan.settimeout(timeout)
        if get_pty:
            chan.get_pty()
        chan.exec_command(cmd)

        buffers = {"out": b"", "err": b""}

        def pump(reader, key):
            while True:
                chunk = reader.read(65536)
                if not chunk:
                    break
                buffers[key] += chunk

        threads = [
            threading.Thread(target=pump, args=(chan.makefile("rb", -1), "out")),
            threading.Thread(target=pump, args=(chan.makefile_stderr("rb", -1), "err")),
        ]
        for th in threads:
            th.daemon = True
            th.start()
        rc = chan.recv_exit_status()          # 阻塞到远端退出
        for th in threads:
            th.join(timeout=30)
        chan.close()
        return (rc,
                buffers["out"].decode("utf-8", "replace"),
                buffers["err"].decode("utf-8", "replace"))
    finally:
        if own:
            client.close()


def check(cfg):
    """连通性自检，返回一段即可读的文本。"""
    lines = []
    ok, msg = probe_proxy(cfg)
    lines.append(("[OK] " if ok else "[FAIL] ") + msg)
    if not ok:
        return "\n".join(lines)
    try:
        client = connect(cfg)
    except TunnelError as exc:
        lines.append("[FAIL] " + str(exc))
        return "\n".join(lines)
    try:
        probe = (
            "echo \"host=$(hostname)\"; "
            "echo \"user=$(whoami)\"; "
            "echo \"kernel=$(uname -r)\"; "
            "echo \"python=$(python -V 2>&1)\"; "
            "echo \"cwd=$(pwd)\"; "
            "python -c 'import torch;print(\"torch=\"+torch.__version__)' 2>/dev/null; "
            "python -c 'import torch_mlu;print(\"torch_mlu=\"+torch_mlu.__version__)' 2>/dev/null; "
            "ls -d /opt/* /cg/* 2>/dev/null | head -20"
        )
        rc, out, err = run(cfg, probe, timeout=120, client=client)
        lines.append("[OK] SSH 已连通 %s（exit=%d）" % (cfg.ssh_url(), rc))
        lines.append(out.rstrip())
        if err.strip():
            lines.append("--- stderr ---\n" + err.rstrip())
    finally:
        client.close()
    return "\n".join(lines)


# --------------------------------------------------------------------------
# SFTP
# --------------------------------------------------------------------------
def sftp(cfg, client=None):
    own = client is None
    if own:
        client = connect(cfg)
    return client.open_sftp(), client if own else None


def get(cfg, remote, local, client=None):
    s, own_client = sftp(cfg, client)
    try:
        parent = os.path.dirname(os.path.abspath(local))
        if parent:
            os.makedirs(parent, exist_ok=True)
        s.get(remote, local)
        return local
    finally:
        s.close()
        if own_client:
            own_client.close()


def put(cfg, local, remote, client=None):
    s, own_client = sftp(cfg, client)
    try:
        s.put(local, remote)
        return remote
    finally:
        s.close()
        if own_client:
            own_client.close()


def _walk_remote(s, path, acc, skip_dirs, skip_ext, max_bytes):
    for entry in s.listdir_attr(path):
        full = path.rstrip("/") + "/" + entry.filename
        if statmod.S_ISDIR(entry.st_mode):
            if entry.filename in skip_dirs:
                continue
            acc.append((full, True))
            _walk_remote(s, full, acc, skip_dirs, skip_ext, max_bytes)
        else:
            ext = os.path.splitext(entry.filename)[1].lower()
            if ext in skip_ext or entry.st_size > max_bytes:
                continue
            acc.append((full, False))


def pull(cfg, remote_dir, local_dir, skip_dirs=None, skip_ext=None,
         max_bytes=MAX_BYTES, client=None, on_skip=None):
    """递归把容器目录拉到本地。返回拉取的文件数。

    默认跳过 ``data/models/out/...`` 与 ``.pth/.zip/.so`` 这类大对象——
    骨架代码只需要几十 KB，别把几 GB 数据集搬回家。
    """
    skip_dirs = SKIP_DIRS if skip_dirs is None else skip_dirs
    skip_ext = SKIP_EXT if skip_ext is None else skip_ext
    remote_dir = remote_dir.rstrip("/")
    s, own_client = sftp(cfg, client)
    try:
        acc = []
        _walk_remote(s, remote_dir, acc, skip_dirs, skip_ext, max_bytes)
        count = 0
        for full, is_dir in acc:
            rel = full[len(remote_dir):].lstrip("/")
            target = os.path.join(local_dir, rel.replace("/", os.sep))
            if is_dir:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                s.get(full, target)
                count += 1
            except Exception as exc:
                if on_skip:
                    on_skip(full, exc)
        return count
    finally:
        s.close()
        if own_client:
            own_client.close()


def push(cfg, local_dir, remote_dir, client=None):
    """把本地目录（递归）推回容器。返回上传的文件数。"""
    remote_dir = remote_dir.rstrip("/")
    s, own_client = sftp(cfg, client)
    try:
        count = 0
        for root, _dirs, files in os.walk(local_dir):
            rel_root = os.path.relpath(root, local_dir)
            rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/")
            target_dir = remote_dir + ("/" + rel_root if rel_root else "")
            try:
                s.stat(target_dir)
            except IOError:
                s.mkdir(target_dir)
            for name in files:
                if name.endswith(".pyc"):
                    continue
                s.put(os.path.join(root, name), target_dir + "/" + name)
                count += 1
        return count
    finally:
        s.close()
        if own_client:
            own_client.close()
