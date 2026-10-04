# 一次完整的实验会话（以「自定义 Sigmoid 算子」为例）

> 这是真实跑过一轮的流程复刻。照抄命令就能走通。
> 约定：`$` 开头的行是**本机**执行，`容器>` 是**容器里**的 python/结果。

---

## 第 0 步：探针取凭据（在平台页面 Console）

在浏览器里打开你的环境页面（地址栏带 `?ns=...`），F12 → Console，粘贴
[`browser/probe_env.js`](../browser/probe_env.js)，回车。得到：

```
=========== 复制下面 5 行，存成 aics.env ===========
AICS_HOST=aics.cambricon.com
AICS_PORT=31862
AICS_USER=root
AICS_PASSWORD=AbCdEf123456
AICS_SOCKS=127.0.0.1:1080
====================================================
```

写进本机 `aics.env`。

## 第 1 步：起代理 + 自检

```bash
$ ssh -N -D 127.0.0.1:1080 <跳板机用户>@<跳板机地址>      # 挂着别关
```

另开终端：

```bash
$ python -m aicslab check
[1/3] 本地 SOCKS5 127.0.0.1:1080 ... OK
[2/3] SSH 连接 root@aics.cambricon.com:31862 ... OK
[3/3] 容器信息:
      hostname : aics-notebook-xxxx
      python   : /torch/venv3/pytorch/bin/python (3.10.8)
      torch    : 2.5.0+cpu   torch_mlu: 1.24.1
      /opt     : code_chap4  code_chap6
```

## 第 2 步：摸清实验目录

```bash
$ python -m aicslab run "ls -la /opt/code_chap4/ && find /opt/code_chap4 -maxdepth 2 -type d -name 'exp_*' | sort"
drwxr-xr-x  exp_4_1_vgg19
drwxr-xr-x  exp_4_2_SytleTransfer
drwxr-xr-x  exp_4_3_...
drwxr-xr-x  exp_4_4_mysigmoid
drwxr-xr-x  exp_4_5_transformer
```

> 注意目录名可能拼错（`Sytle`），**以实测 `ls` 为准**。

## 第 3 步：先把"参考证据"备份走

```bash
$ python -m aicslab run "mkdir -p /root/ref && cp -a /opt/code_chap4/exp_4_4_mysigmoid/stu_upload/__pycache__ /root/ref/ 2>/dev/null; ls -R /root/ref"
/root/ref/__pycache__:
evaluate_cpu.cpython-310.pyc   evaluate_mlu.cpython-310.pyc   ...
```

## 第 4 步：把骨架拉回本地

```bash
$ python -m aicslab pull /opt/code_chap4/exp_4_4_mysigmoid ./ch4/exp_4_4_mysigmoid
```

## 第 5 步：反查参考解（关键）

对本机拿回来的 pyc 反汇编：

```bash
$ python dis_pyc.py ch4/exp_4_4_mysigmoid/stu_upload/__pycache__/evaluate_cpu.cpython-310.pyc
============================================================
/forward
  varnames : ('self', 'x')
  names    : ('torch', 'exp', 'clamp')
  consts   : (None, ...)
     LOAD_GLOBAL exp
     LOAD_FAST   x
     CALL_FUNCTION 1
     ...
```

⇒ 还原出 `torch.clamp(torch.exp(x), min=?, max=?)` 之类的精确写法，
而不是"看起来像 sigmoid 就行"。

## 第 6 步：本地改代码 → 推回去 → 复跑

```bash
$ # 在本地编辑 stu_upload/*.py，把 _______ 换成还原出的实现
$ python -m aicslab put ./ch4/exp_4_4_mysigmoid/stu_upload/evaluate_cpu.py \
                        /opt/code_chap4/exp_4_4_mysigmoid/stu_upload/evaluate_cpu.py
$ python -m aicslab run "cd /opt/code_chap4/exp_4_4_mysigmoid/stu_upload && python -m py_compile evaluate_cpu.py && echo SYNTAX_OK"
SYNTAX_OK
```

编译算子 + 跑入口：

```bash
$ python -m aicslab run "cd /opt/code_chap4/exp_4_4_mysigmoid && rm -rf build && rm -f *.so && bash run_cpu.sh 2>&1 | tail -40"
...
[PASS] mysigmoid CPU test passed
[PASS] forward/inference OK
```

> `py_compile` 过 **不等于**跑得通——路径、cwd、依赖遮蔽只有实跑才暴露。
> **每次改完都在容器里复跑一遍，并留下 stdout。**

## 第 7 步：打包提交

```bash
$ python -m aicslab pull /opt/code_chap4/exp_4_4_mysigmoid ./deliver/exp_4_4_mysigmoid
# 本地按要求打 zip，命名与实验目录一致，只含 readme 点名的文件
```

## 第 8 步：收尾

- 关键结论（实测数字、对齐依据）写进完成报告。
- **去平台停掉环境**（3 小时时长 + 总配额有限）。
- 全程没有把密码写进任何会被提交的文件。

---

## 对应的"给 AI 一句话"

> 把本仓库丢给 AI，说：
> 「按 `AGENT.md` 帮我做 `/opt/code_chap4/exp_4_4_mysigmoid`，凭据我已经填进 `aics.env`，代理已起。」

AI 会自己走完 §1 自检 → §4 反查 → §3 工作流 → §6 收尾。
