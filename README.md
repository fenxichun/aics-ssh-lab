# aics-ssh-lab

> 用 **SSH** 打通「智算平台云端容器」的远程实验工作流。
> 读文件、改代码、跑实验、取结果，全部走一条 SSH 通道——
> **不需要浏览器自动化，不需要网页版 VS Code，不需要点剪贴板。**

面向的场景：「智能计算系统」这类寒武纪 MLU370 云实验环境，
代码在容器里、只能从平台页面进容器，网页 IDE 的终端还时不时起不来。
本仓库把它变成一套可脚本化、可交给 AI 自动完成的流程。

---

## 为什么是 SSH

网页版 VS Code 那条路有三个躲不开的坑：Workspace Trust 弹窗、终端的 profile 下拉、
终端 pty host 挂掉（挂掉就只能重建环境）。而平台其实给每个环境都开了一个 **SSH 端口**，
只要建环境时勾了 SSH，就能用 `paramiko` 直连容器 root：

```
本机 ──SOCKS5(127.0.0.1:1080)──► aics.cambricon.com:<NodePort> ──► 容器 root
         \_____ ssh -N -D 起的动态转发 _____/
```

之后：**读 = SFTP get，写 = SFTP put，跑 = exec_command**，全在 Python 里，稳且可复现。

---

## 三步接入

### 1. 取本环境的 SSH 端口与密码

打开平台，进你的**开发环境页面**（地址栏带 `?ns=...`），`F12 → Console`，
把 [`browser/probe_env.js`](browser/probe_env.js) 整个粘进去回车。
它会打印这样一段：

```
=========== 复制下面 5 行，存成 aics.env ===========
AICS_HOST=aics.cambricon.com
AICS_PORT=31862
AICS_USER=root
AICS_PASSWORD=xxxxxxxxxxxx
AICS_SOCKS=127.0.0.1:1080
====================================================
```

为什么必须在页面里跑：鉴权用的是页面 `localStorage` 里的令牌（放进 `X-Auth-Token` 头），
**这个令牌拿不出页面**。脚本只读接口，不改任何东西。

> 脚本会顺手检查 `spec.enableSSH`。如果它是 `false`，说明这个环境建的时候没勾 SSH，
> 那条路走不通（一般无法补开），只能退回网页 IDE。

### 2. 起本地 SOCKS5 代理

容器端口不直连，要经代理。跳板机账号问课程助教：

```bash
ssh -N -D 127.0.0.1:1080 <跳板机用户>@<跳板机地址>
# 这一行会一直挂着，别关；另开一个终端干别的
```

### 3. 装依赖 & 自检

```bash
pip install -r requirements.txt          # 只有一个 paramiko
cp aics.env.example aics.env             # 把第 1 步那 5 行填进去

python -m aicslab check
```

`check` 通过时你会看到本地代理可达、SSH 已连通、容器主机名、
以及里面的 `torch` / `torch_mlu` 版本和 `/opt` 目录列表。**这一步不过，后面全是白费。**

---

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `python -m aicslab env` | 查看生效配置（密码自动打码），并显示每个值来自哪里 |
| `python -m aicslab env --reveal` | 打印可直接 `export` 的真实值（只在本机用） |
| `python -m aicslab check` | 连通性自检：代理 → SSH → 容器信息 |
| `python -m aicslab run "<cmd>"` | 在容器里执行命令 |
| `python -m aicslab sh "<cmd>"` | 同上，但带 pty（进度条 / 交互式输出更正常） |
| `python -m aicslab get <remote> <local>` | 下载单个文件 |
| `python -m aicslab put <local> <remote>` | 上传单个文件 |
| `python -m aicslab pull <remote_dir> <local_dir>` | 递归拉目录（**默认跳过** `data/models/out` 和 `.pth/.zip/.so` 等大对象） |
| `python -m aicslab push <local_dir> <remote_dir>` | 递归推目录 |

典型一轮：

```bash
python -m aicslab run  "ls -la /opt/ && find /opt -maxdepth 3 -type d -name 'exp_*' | sort"
python -m aicslab pull /opt/code_chap4/exp_4_4_mysigmoid ./exp_4_4_mysigmoid
# ... 在本地改 stu_upload/ 里的文件 ...
python -m aicslab put  ./exp_4_4_mysigmoid/stu_upload/evaluate_cpu.py \
                       /opt/code_chap4/exp_4_4_mysigmoid/stu_upload/evaluate_cpu.py
python -m aicslab run  "cd /opt/code_chap4/exp_4_4_mysigmoid && bash run_cpu.sh 2>&1 | tail -40"
```

命令行参数 `--host / --port / --user / --password / --key / --socks` 可临时覆盖配置；
优先级是 **命令行 > 环境变量 `AICS_*` > `./aics.env` 或 `~/.aics.env` > 默认值**。

> 大文件千万别用 `pull --all`：数据集和权重动辄几 GB，而骨架代码总共才几十 KB。

---

## 把整个仓库交给 AI

这个仓库设计成**可以直接丢给 AI 助手**：让 AI 读 [`AGENT.md`](AGENT.md) 就能接手。

`AGENT.md` 里写清了三件 AI 最容易做错的事：

1. **补 TODO 不要猜** —— 容器里往往残留着老师跑通参考实现时的 `__pycache__/*.pyc`，
   反汇编（`marshal` + `dis`）能把每条 TODO 的精确写法还原出来；
   预置 `.pth` 的 `state_dict()` 键名与形状能锁死网络结构；
   `output.txt` / `logs/` 里的历史数字是金标准。
2. **路径不要硬编码** —— 骨架里的相对路径经常自相矛盾，要用 `__file__` 反推 + 候选回退。
3. **凭据绝不进仓库** —— 端口和密码必须由人类从页面探针里取。

更多细节见 [`docs/reverse-engineering.md`](docs/reverse-engineering.md) 和
[`docs/troubleshooting.md`](docs/troubleshooting.md)。

---

## 目录结构

```
aicslab/
  config.py     配置解析（命令行 > 环境变量 > 配置文件），无任何硬编码凭据
  tunnel.py     SOCKS5 握手 + paramiko SSH + SFTP 读写/目录同步
  cli.py        python -m aicslab 的命令行
browser/
  probe_env.js  【在平台页面控制台里跑】取 SSH NodePort 与密码
docs/
  reverse-engineering.md   反查参考解的三种取证方法
  troubleshooting.md       排障总表
AGENT.md        给 AI 助手的操作契约
aics.env.example  配置模板（真实配置请写进 aics.env，已被 .gitignore 忽略）
```

## 安全

- 仓库内**不含任何账号、密码、令牌**，`aics.env` / `*.pem` / `id_rsa*` 都在 `.gitignore` 里。
- 端口和密码是**每个环境各一份**的，环境重建后会变——别忘了重跑探针。
- 用完请去平台**停掉环境**释放配额。

## 许可

MIT
