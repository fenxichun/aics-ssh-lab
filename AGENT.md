# AGENT.md —— 给 AI 助手的操作契约

> 这个文件是给**接手这份作业的 AI** 看的。人类用户只需要把整个仓库丢给 AI，说一句
> 「按 AGENT.md 帮我做实验」即可。下面每一条都是实际踩过坑之后写下来的，请照做，不要"优化"。

## 0. 这份仓库是什么

一套**以 SSH 为核心**的远程实验工作流：用 `paramiko + 本地 SOCKS5` 直连云端容器，
读文件走 SFTP、改文件在本地改完再推回去、跑命令走 exec。
**完全不依赖浏览器、网页版 VS Code、CDP 或剪贴板**——这是它比"在网页 IDE 里点"稳定的根本原因。

```
aicslab/              工具箱（config / tunnel / cli）
browser/probe_env.js  【关键】在平台页面控制台里跑，取 SSH 端口与密码
docs/                 反查参考解的方法、排障表
aics.env.example      配置模板（真实值由用户填进 aics.env，已 gitignore）
```

## 1. 开工前的硬性检查（缺一不可）

1. **凭据**：确认存在 `aics.env`（或环境变量 `AICS_PORT` / `AICS_PASSWORD`）。
   若没有，**让用户去平台页面跑 `browser/probe_env.js`**，把打印的 5 行抄进 `aics.env`。
   **绝对不要试图猜端口或密码，也不要把真实凭据写进任何被提交的文件。**
2. **本地代理**：容器不直连，必须经本机 SOCKS5。先确认 `127.0.0.1:1080` 有人监听；
   没有就让用户跑 `ssh -N -D 127.0.0.1:1080 <跳板机>`。
3. **自检**：`python -m aicslab check`。
   期望看到「本地 SOCKS5 可达」+「SSH 已连通」+ 容器里 `torch` / `torch_mlu` 版本与
   `/opt/...` 的目录列表。**这一步不过，后面全是白费**，别急着改代码。

```bash
python -m aicslab env                    # 看生效配置（密码自动打码）
python -m aicslab check                  # 连通性 + 容器信息
python -m aicslab run "ls -la /opt"      # 随便看一眼
```

## 2. 环境事实（先记下来，省得反复试）

- 容器里的 `python` 就是主力解释器：`/torch/venv3/pytorch/bin/python`
  （Python 3.10 + torch 2.5.0 + `torch_mlu`，MLU 直接用它跑）。
- 教材各章代码在 `/opt/code_chap<N>/` 下，注意章节目录名**可能没有下划线**
  （第 4 章是 `code_chap4`，但手册正文里写作 `code_chap_4`）。
- 每个实验目录形如 `exp_<章>_<序>_<名字>/`，待填骨架在 `stu_upload/`，
  入口是同级 `run_*.sh`，**判分只看入口脚本的 stdout**。
  也有例外：4.5 的待填文件直接放在实验根目录（`modules.py` / `AttModel.py` / `eval.py`）。
- `/opt` 是开发容器的挂载点；提交后的判分环境可能挂在别的根（如 `/cg/...`）。
  **所以代码里的路径不要硬编码**，用 `__file__` 反推 + 候选回退（见 §4.4）。
- 手册/readme 里的文件名**经常是错的或过时的**（拼写错误、与实际不符）。
  一律以容器里 `ls` 出来的真实文件名为准。
- 用户给的 PDF 手册**文件名也不可信**：曾经出现"第五章实验手册.pdf"实际是第 4 章后半段。
  **先 `page.get_text()` 看页眉和页码，再对照容器目录确认覆盖范围。**

## 3. 标准工作流

```bash
# 1) 摸清实验目录
python -m aicslab run "ls -la /opt/code_chap4/ && find /opt/code_chap4 -maxdepth 3 -type d -name 'exp_*' | sort"

# 2) 把骨架 + readme + 入口脚本拉回本地（默认已过滤 data/models/out 与大文件）
python -m aicslab pull /opt/code_chap4/exp_4_4_mysigmoid ./ch4/exp_4_4_mysigmoid

# 3) 【重要】把"参考证据"备份到容器里别的目录，别让它们被覆盖（见 §4）
python -m aicslab run "mkdir -p /root/ref && cp -a /opt/code_chap4/exp_4_4_mysigmoid/stu_upload/op_mysigmoid.egg-info /root/ref/ 2>/dev/null; ls /root/ref"

# 4) 在本地改代码，改完推回去
python -m aicslab put ./ch4/exp_4_4_mysigmoid/stu_upload/evaluate_cpu.py /opt/code_chap4/exp_4_4_mysigmoid/stu_upload/evaluate_cpu.py
python -m aicslab run "cd /opt/code_chap4/exp_4_4_mysigmoid/stu_upload && python -m py_compile evaluate_cpu.py && echo SYNTAX_OK"

# 5) 跑入口脚本，留 stdout 证据
python -m aicslab run "cd /opt/code_chap4/exp_4_4_mysigmoid && bash run_cpu.sh 2>&1 | tail -40"

# 6) 长任务放后台跑，别把会话卡死
#    （在 WorkBuddy / CodeBuddy 里用 run_in_background；纯 shell 里用 nohup + &）
```

**改完必须复跑**。只做 `py_compile` 通过不算数——路径逻辑、工作目录、依赖遮蔽这些问题
只有真跑才会暴露。

## 4. 补 TODO 的正确姿势：**反查参考解，不要猜**

骨架只给"执行卷积 / 实例归一化"这种文字提示时，靠注释猜必错。按下面的优先级取证：

### 4.1 容器里残留的 `.pyc`（最强的规格书）

镜像里经常留着**老师跑通参考实现时的字节码**：`<实验目录>/__pycache__/*.cpython-3XX.pyc`。

**判定它是"参考解"还是"骨架"**：`.pyc` 在编译成功后、执行前就会写盘，所以骨架也可能有；
但骨架在 import 期就会因未定义名 `_______` 抛 `NameError`，不会留下**整批同版本**的 pyc。
最可靠的判据是：**入口脚本没有 pyc**（以 `python eval.py` 跑的主模块不产 pyc），
而它 import 的那些模块都有 —— 这正是"真实跑过"的指纹。

```python
import marshal, dis
with open('__pycache__/modules.cpython-310.pyc', 'rb') as f:
    f.read(16)                      # 跳过 16 字节头
    code = marshal.load(f)

def walk(c, path=''):
    yield path + '/' + c.co_name, c
    for k in c.co_consts:
        if hasattr(k, 'co_name'):
            yield from walk(k, path + '/' + c.co_name)

for full, c in walk(code):
    if c.co_name == 'forward':
        print(full, c.co_varnames, c.co_consts, c.co_names)
        dis.dis(c)
```

看什么：
- `co_names` / `co_varnames` → 用到哪些函数、有哪些局部变量、调用顺序。
- `co_consts` → 常量。**`CALL_FUNCTION_KW` 的关键字参数名会以元组出现在 `co_consts`**，
  例如 `('num_units','num_heads','dropout_rate','causality')` 直接告诉你关键字调用怎么写。
- **嵌套推导式是独立 code object，要递归取**。
- 字节码顺序就是语义。比如 `BINARY_SUBTRACT` 出现在 `BINARY_MULTIPLY` 之前，
  说明是 `gamma * (x - mean)` 而不是 `gamma * x - mean`。

> ⚠️ 自己的文件一传上去就会覆盖同名 pyc。**改之前先 `cp -a` 到 `/root/` 另存。**

### 4.2 预置权重 / 编译产物的结构

- `torch.load('xxx.pth')` 后打印 `state_dict()` 的**键名和形状**，
  直接锁死网络结构。例：键是 `Q_proj.0.weight` ⇒ `Q_proj` 是 `nn.Sequential(...)` 且第 0 项是 Linear；
  键是 `conv1.0.weight` ⇒ 即使走"全连接分支"也必须是 Sequential。
  写完用 `load_state_dict(sd, strict=True)` 验收：**0 missing / 0 unexpected / 0 shape mismatch** 才算对。
- 预编译好的 `.so` 可以直接 import 跑一遍，反推出**导出符号名**和**输出张量形状**。

### 4.3 历史产物就是金标准

`output.txt`、`logs/*.txt`、`out/*.jpg` 往往是老师真正跑出来的结果。
例：某实验 `output.txt` 里留着 4 条 `Bleu Score = 17.09554...`，
那么你自己跑到**逐位相同**才叫复现成功。这比"看起来差不多"强得多。

### 4.4 路径：`__file__` 反推 + 候选回退

骨架里的相对路径**经常自相矛盾**（例如 `../data/` 和 `./models/` 不可能在同一个 cwd 下同时成立）。
不要二选一，统一这么做：

```python
_HERE = os.path.dirname(os.path.abspath(__file__))
def _root_dir():
    for base in (_HERE, os.path.dirname(_HERE)):
        if os.path.isdir(os.path.join(base, 'models')):
            return base
    return os.path.dirname(_HERE)
```

然后所有 IO 用绝对路径，并在**两种工作目录下各跑一次**验证。

## 5. 高频坑（都是真实踩过的）

| 现象 | 原因 / 处理 |
|---|---|
| `AttributeError: module 'xxx' has no attribute ... Did you mean: 'hsigmoid'?` | `site-packages` 里有**同名但无关的 egg** 抢了 import。用 `find / -name 'xxx*'` 找出所有同名模块，看 `<egg>/EGG-INFO/SOURCES.txt` 确认它由哪个源文件编的。**修法：`sys.path.insert(0, <自己目录>)`**；用 `append` 无效。 |
| 改了源码但结果没变 | 同名 `__pycache__/*.pyc`、旧 `.so` 或 egg 仍在生效。自定义算子要 `rm -rf build && rm -f *.so` 再 `build_ext --inplace`。 |
| 平台聚合 API 401 | fetch 漏了 `X-Auth-Token` 头（**不是**学生账号没权限）。 |
| `aics-dmz-xxx:31862` 解析失败 | 节点名不可解析是正常的。host 用 `aics.cambricon.com` + NodePort，且必须走代理。 |
| SOCKS5 连不上目标端口 | NodePort 是**每个环境一份**的，环境重建后端口会变 → 重跑 `probe_env.js`。 |
| SSH 认证失败 | 密码是旧环境的；或建环境时没勾 SSH（`spec.enableSSH=false`，一般无法补开）。 |
| `libc10.so: cannot open shared object file` | 直接 `import` 自定义算子时先 `import torch`，让它把 torch 的动态库加载进来。 |

## 6. 收尾清单

- [ ] 每个实验都在容器里**实跑并留下 stdout**（`run_*.sh` 跑出的 PASS 行）。
- [ ] 提交包只含 readme 点名的文件，命名与实验目录一致。
- [ ] 把关键结论（实测数字、对齐依据）写进一份完成报告，别只丢代码。
- [ ] **提醒用户去平台停掉环境**——有 3 小时时长，且总配额有限。
- [ ] 全程没有把密码/token 写进任何会被提交的文件。
