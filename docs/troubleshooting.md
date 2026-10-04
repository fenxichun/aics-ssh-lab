# 排障总表

> 从**下往上**读：先确认最底层（配置）对，再往上排连接、再排容器内。
> 每一步都给了**一条**诊断命令，别一次改一堆东西。

---

## 分层模型

```
第 5 层  容器内的 Python / 编译 / import     ← 最后的怪问题都在这
第 4 层  exec_command 跑命令
第 3 层  SSH 认证（密码 / 端口）
第 2 层  SOCKS5 代理可达
第 1 层  配置（aics.env / 环境变量）
```

**永远从第 1 层开始。** 90% 的"连不上"是端口或密码过期了。

---

## 第 1 层：配置

| 现象 | 原因 | 处理 |
|---|---|---|
| `ConfigError: 缺少 AICS_PORT` | `aics.env` 没建 / 没填 | `cp aics.env.example aics.env` 然后填第 1 步的 5 行 |
| 配置读到了但值是占位符 | 忘了替换 `替换成...` | `python -m aicslab env` 看生效值；带占位符会打码成 `***` |
| 命令行覆盖不生效 | 优先级记反了 | 优先级：**命令行 > `AICS_*` 环境变量 > `./aics.env` 或 `~/.aics.env` > 默认值** |
| 想确认每个值来自哪 | —— | `python -m aicslab env`（会显示 source 一列） |

诊断：

```bash
python -m aicslab env
```

---

## 第 2 层：SOCKS5 代理

| 现象 | 原因 | 处理 |
|---|---|---|
| `Connection refused` 到 `127.0.0.1:1080` | 代理没起 | 另开终端：`ssh -N -D 127.0.0.1:1080 <跳板机>`，**这个终端要一直挂着** |
| 代理起了但连目标超时 | 代理端到目标网络不通 | 确认跳板机地址/账号；问助教要正确的跳板机 |
| `SOCKS5 认证失败` | 代理需要账号密码 | 在 `aics.env` 里设 `AICS_SOCKS_USER` / `AICS_SOCKS_PASSWORD` |
| Windows 上 socks 名解析炸 | host 用了不可解析的节点名 | **host 一律用 `aics.cambricon.com` + NodePort**，别用 `aics-dmz-xxx` |

诊断（本地端口是否有人听）：

```bash
# Linux/macOS
ss -lntp | grep 1080
# Windows PowerShell
Get-NetTCPConnection -LocalPort 1080 -State Listen
```

用 aicslab 自带的探针：

```bash
python -m aicslab check     # 第一步就是"本地 SOCKS5 可达"
```

---

## 第 3 层：SSH 认证

| 现象 | 原因 | 处理 |
|---|---|---|
| `Authentication failed` | **密码或端口是旧环境的**（最常见） | 环境重建后 NodePort 和密码都会变 → **重跑 `browser/probe_env.js`** |
| `Authentication failed` 且 `spec.enableSSH=false` | 建环境时没勾 SSH | 一般**无法补开**，只能重建环境（重建时勾上 SSH），否则退回网页 IDE |
| 端口连不上（不是拒绝，是超时） | NodePort 写错了 | 对着探针输出逐位核对 |
| `Unable to connect to port` | 端口号是别的环境的 | 一个 NodePort 只对**一个**环境有效 |

诊断：

```bash
# 直接看探针当前读到什么（在平台页面 Console 里跑 browser/probe_env.js）
python -m aicslab check
```

---

## 第 4 层：exec / 文件传输

| 现象 | 原因 | 处理 |
|---|---|---|
| 命令跑了但看不到输出 | 没合并 stderr | 用 `2>&1`：`python -m aicslab run "cmd 2>&1"` |
| 进度条乱码 / 输出卡住 | 没 pty | 用 `python -m aicslab sh "cmd"`（带 pty） |
| 长时间任务把会话卡死 | 前台阻塞 | 长任务放后台：在 WorkBuddy 里用 `run_in_background`；纯 shell 用 `nohup ... &`，或加 `--timeout` |
| `pull` 拉了几百 MB | 没过滤大文件 | 默认已跳过 `data/models/out` 与 `.pth/.zip/.so`；**别加 `--all`** |
| 上传后容器里文件没变 | 路径写错 / 上传到了别处 | `python -m aicslab run "ls -l <remote_path>"` 核对时间戳 |
| 中文文件名乱码 | 编码 | 容器里尽量用 ASCII 文件名 |

诊断：

```bash
python -m aicslab run "pwd; whoami; echo '---'; ls -la /opt | head"
```

---

## 第 5 层：容器内 Python / 编译

| 现象 | 原因 | 处理 |
|---|---|---|
| `AttributeError: module 'xxx' has no attribute ... Did you mean: 'hsigmoid'?` | `site-packages` 里有**同名但无关的 egg** 抢了 `import` | `sys.path.insert(0, <自己目录>)`；**用 `append` 无效**。详见 [`reverse-engineering.md`](reverse-engineering.md) §2.2 |
| `libc10.so: cannot open shared object file` | import 自定义算子时 torch 的动态库还没加载 | 先 `import torch`，再 import 你的算子 |
| 改了源码但结果没变 | 旧 `__pycache__/*.pyc` / 旧 `.so` / egg 仍在生效 | 自定义算子：`rm -rf build && rm -f *.so && python setup.py build_ext --inplace`；纯 py：删 `__pycache__` |
| `NameError: name '_______' is not defined` | 骨架占位符**还没填** | 这就是要做的实验；参考 `reverse-engineering.md` 的取证方法 |
| `FileNotFoundError: ../data/xxx` | 骨架相对路径**自相矛盾** | 别改 cwd，用 `__file__` 反推 + 候选回退（AGENT.md §4.4） |
| 用的是系统 python 而不是容器 python | PATH 里 `python` 指向别的 | 显式用 `/torch/venv3/pytorch/bin/python`，它 = 容器里的 `python`（3.10 + torch 2.5 + torch_mlu） |
| MLU 相关报错 | 跑到了 CPU 分支 / 设备号不对 | 确认 `torch_mlu` 已 import，`device='mlu'` 且 MLU 可见：`python -c "import torch_mlu, torch; print(torch.mlu.is_available())"` |
| `Permission denied` 写 `/opt` | 权限 | 容器内是 root，一般不会；若真遇到，写到 `/root/` 下再 `cp` |

诊断：

```bash
python -m aicslab run "python -c 'import sys; print(sys.executable, sys.version)'; which python; python -c 'import torch, torch_mlu; print(torch.__version__, torch.mlu.is_available())'"
```

---

## 一页速查（按报错关键字）

| 报错里出现 | 直奔 |
|---|---|
| `ConfigError` / 缺 PORT / PASSWORD | 第 1 层 |
| `Connection refused` / `1080` | 第 2 层 |
| `Authentication failed` | 第 3 层（**先重跑探针**） |
| `enableSSH` / `false` | 第 3 层（重建环境） |
| `Did you mean` / `hsigmoid` / `egg` | 第 5 层（`sys.path.insert(0,...)`） |
| `libc10.so` | 第 5 层（先 `import torch`） |
| `NameError` / `_______` | 骨架未填，去 `reverse-engineering.md` |
| `FileNotFoundError` / `../` | AGENT.md §4.4 路径反推 |
| 结果没变化 / 旧版本 | 清 `__pycache__` / `build` / `.so` |

---

## 兜底

如果五层都排完还是不行：

1. 把 `python -m aicslab env`（打码版）、`python -m aicslab check` 的**完整输出**贴出来。
2. 在平台页面 Console 重跑一次 `browser/probe_env.js`，确认端口/密码是不是变了。
3. 确认环境的 **SSH 开关**在建的时候是勾上的、**时长没过期**。
4. 实在不行就重建环境（**记得勾 SSH**），再重跑探针。
