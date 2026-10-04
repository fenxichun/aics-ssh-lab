# -*- coding: utf-8 -*-
"""配置解析。**仓库里绝不存放任何真实密码/令牌。**

优先级（高 → 低）：
  1) 函数参数 / 命令行参数
  2) 环境变量 ``AICS_*``
  3) 配置文件（默认 ``./aics.env``，其次 ``~/.aics.env``，可用 ``AICS_ENV_FILE`` 指定）
  4) 内置默认值

配置项
------
==============  ==============================================  ====================
键              含义                                            默认
==============  ==============================================  ====================
``AICS_HOST``   平台域名                                        ``aics.cambricon.com``
``AICS_PORT``   SSH NodePort（从平台 API 取，见 probe_env.js）  **必填**
``AICS_USER``   SSH 用户名                                       ``root``
``AICS_PASSWORD`` 容器 root 密码                                **必填**（或用 AICS_KEY）
``AICS_KEY``    私钥路径（设置后走公钥认证，优先于密码）         空
``AICS_SOCKS``  本地 SOCKS5 代理 ``host:port`` 或只写端口        ``127.0.0.1:1080``
``AICS_SOCKS_USER`` / ``AICS_SOCKS_PASSWORD``  SOCKS5 认证（一般不用）  空
==============  ==============================================  ====================

配置文件是 ``KEY=VALUE`` 的纯文本（``#`` 开头为注释），例如::

    AICS_PORT=31862
    AICS_PASSWORD=换成你自己的
"""

import os

DEFAULTS = {
    "AICS_HOST": "aics.cambricon.com",
    "AICS_PORT": "",
    "AICS_USER": "root",
    "AICS_PASSWORD": "",
    "AICS_KEY": "",
    "AICS_SOCKS": "127.0.0.1:1080",
    "AICS_SOCKS_USER": "",
    "AICS_SOCKS_PASSWORD": "",
}

KEYS = tuple(DEFAULTS)


class ConfigError(Exception):
    """配置缺失或格式错误。"""


def _env_file_path():
    explicit = os.environ.get("AICS_ENV_FILE")
    if explicit:
        return explicit
    for cand in ("aics.env", os.path.join(os.path.expanduser("~"), ".aics.env")):
        if os.path.isfile(cand):
            return cand
    return None


def parse_env_file(path):
    """读 ``KEY=VALUE`` 文本；键名统一大写并补上 ``AICS_`` 前缀。"""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ConfigError("%s:%d 不是 KEY=VALUE 形式: %r" % (path, lineno, line))
            key, val = line.split("=", 1)
            key = key.strip().upper()
            if not key.startswith("AICS_"):
                key = "AICS_" + key
            out[key] = val.strip().strip('"').strip("'")
    return out


def _split_socks(value):
    value = (value or "").strip()
    if not value:
        value = DEFAULTS["AICS_SOCKS"]
    if ":" in value:
        host, _, port = value.rpartition(":")
    else:  # 只给了端口
        host, port = "127.0.0.1", value
    try:
        port = int(port)
    except ValueError:
        raise ConfigError("AICS_SOCKS 端口不是整数: %r" % value)
    return host or "127.0.0.1", port


class Config(object):
    """一份生效的配置。``repr`` 会自动隐藏密码。"""

    def __init__(self, values, source=None):
        self._v = dict(values)
        self.source = source or {}

    # ---- 只读属性 ----
    @property
    def host(self):
        return self._v["AICS_HOST"]

    @property
    def port(self):
        return int(self._v["AICS_PORT"])

    @property
    def user(self):
        return self._v["AICS_USER"]

    @property
    def password(self):
        return self._v["AICS_PASSWORD"]

    @property
    def key(self):
        return self._v["AICS_KEY"] or None

    @property
    def socks(self):
        return _split_socks(self._v["AICS_SOCKS"])

    @property
    def socks_user(self):
        return self._v.get("AICS_SOCKS_USER") or None

    @property
    def socks_password(self):
        return self._v.get("AICS_SOCKS_PASSWORD") or None

    def get(self, key, default=None):
        return self._v.get(key, default)

    def masked(self):
        """给人和给 AI 看的安全视图。"""
        data = dict(self._v)
        for secret in ("AICS_PASSWORD", "AICS_SOCKS_PASSWORD"):
            if data.get(secret):
                data[secret] = "<已设置，共 %d 字符>" % len(data[secret])
        data["AICS_SOCKS"] = "%s:%d" % self.socks
        return data

    def __repr__(self):
        return "Config(%r)" % (self.masked(),)

    # 常用组合
    def ssh_url(self):
        return "%s@%s:%d" % (self.user, self.host, self.port)

    def to_env_block(self):
        """输出可直接 ``export`` 的片段（含真实密码，只给本机用）。"""
        lines = [
            "AICS_HOST=%s" % self.host,
            "AICS_PORT=%d" % self.port,
            "AICS_USER=%s" % self.user,
            "AICS_PASSWORD=%s" % self.password,
            "AICS_SOCKS=%s:%d" % self.socks,
        ]
        return "\n".join(lines)


def load(**overrides):
    """按优先级合并出最终配置。``overrides`` 的键形如 ``AICS_PORT``。"""
    values = dict(DEFAULTS)
    source = {}

    path = _env_file_path()
    if path:
        for key, val in parse_env_file(path).items():
            if key in DEFAULTS and val != "":
                values[key] = val
                source[key] = path

    for key in KEYS:
        val = os.environ.get(key)
        if val:
            values[key] = val
            source[key] = "env"

    for key, val in overrides.items():
        if val is None or val == "":
            continue
        values[key] = str(val)
        source[key] = "cli"

    missing = []
    if not values["AICS_PORT"]:
        missing.append("AICS_PORT")
    if not values["AICS_PASSWORD"] and not values["AICS_KEY"]:
        missing.append("AICS_PASSWORD（或 AICS_KEY）")

    if missing:
        raise ConfigError(
            "缺少配置：%s\n"
            "取法见 README 的「三步接入」：先在平台页面跑 browser/probe_env.js 拿到 SSH 端口与密码，\n"
            "然后写进 aics.env（可复制 aics.env.example）或导出环境变量。"
            % "、".join(missing)
        )

    try:
        int(values["AICS_PORT"])
    except ValueError:
        raise ConfigError("AICS_PORT 不是整数: %r" % values["AICS_PORT"])

    return Config(values, source)
