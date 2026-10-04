# 反查参考解：三种取证方法

> 骨架代码只给你一句注释（"执行卷积""做实例归一化"），靠猜必错。
> 好在容器里通常留着**老师跑通过参考实现之后的残留物**——它们就是规格书。
> 本文按可信度从高到低列出三种取证方法，全部**只读**，不改容器里任何东西。

---

## 方法一：`__pycache__/*.pyc` 反汇编（最强）

### 为什么会有参考解的字节码

Python 在**编译成功后、执行之前**就会把字节码写进 `__pycache__/`。
所以"骨架也产 pyc"——但骨架在 import 期就会因为未定义名（`_______`）抛 `NameError`，
**根本执行不到 `forward`**，留下的也不会是"整批同版本、能完整反汇编"的 pyc。

### 判定"这份 pyc 是参考解"的指纹

| 判据 | 说明 |
|---|---|
| **入口脚本没有 pyc** | `python eval.py` 这种**主模块不产 pyc**（`__main__` 不写缓存）。 |
| 它 import 的模块**都有** pyc | 说明这些模块被真实 import 过。 |
| pyc 里**没有** `_______` 占位名 | 骨架的 pyc 会在 `co_names` 里带 `_______`。 |
| 同一目录下**整批**同版本 | 零散一两个可能是误触，整批才说明跑过。 |

三条凑齐，基本可以断定：**这份 pyc 就是老师跑通的参考实现编译出来的。**

### 反汇编

```python
import marshal, dis

PYC = '__pycache__/modules.cpython-310.pyc'   # 版本号按容器里的实际文件名来
with open(PYC, 'rb') as f:
    f.read(16)                  # 跳过 16 字节头：magic(4)+flags(4)+mtime(4)+size(4)
    code = marshal.load(f)

def walk(c, path=''):
    yield path + '/' + c.co_name, c
    for k in c.co_consts:
        if hasattr(k, 'co_name'):        # 函数、类、推导式都是独立 code object
            yield from walk(k, path + '/' + c.co_name)

for full, c in walk(code):
    print('=' * 60)
    print(full)
    print('  varnames :', c.co_varnames)
    print('  names    :', c.co_names)
    print('  consts   :', c.co_consts)
    dis.dis(c)
```

### 怎么读

- **`co_names`** → 这个函数用到哪些**全局名 / 属性名 / 方法名**，以及调用顺序。
  例如看到 `conv2d`、`InstanceNorm2d`、`relu`，顺序就是执行顺序。
- **`co_varnames`** → 局部变量名，通常直接对应骨架里的变量（`content_features`、`style_features`…）。
- **`co_consts`** → 常量。**关键技巧**：关键字调用（`f(a, b, key=1)`）的 **kwargs 名会以元组出现在 `co_consts`**。
  例如：
  ```
  consts: (..., ('num_units', 'num_heads', 'dropout_rate', 'causality'), ...)
  ```
  直接告诉你 `MultiHeadAttention(...)` 该用哪四个关键字、按什么顺序传。
- **递归**：列表推导式、生成器表达式、嵌套函数都是**独立的 code object**，
  要从 `co_consts` 里递归取出来（上面的 `walk` 已处理）。
- **字节码顺序 = 语义顺序**。`dis` 输出的顺序就是执行顺序。举例：
  - `BINARY_SUBTRACT` 出现在 `BINARY_MULTIPLY` **之前** ⇒ 是 `gamma * (x - mean)`，
    而不是 `gamma * x - mean`。
  - 先 `LOAD_FAST mean` 再 `LOAD_FAST var` 再 `BINARY_ADD` ⇒ `mean + var`。

> ⚠️ **自己上传的文件会覆盖同名 pyc。动手改代码之前，先把 `__pycache__` 整个 `cp -a` 到 `/root/` 另存。**
> ```bash
> python -m aicslab run "mkdir -p /root/ref && cp -a /opt/code_chap4/exp_4_4_mysigmoid/stu_upload/__pycache__ /root/ref/ 2>/dev/null; ls -R /root/ref"
> ```

### 附带收获：`SourceFile`

`code.co_filename` 会告诉你这个模块的**原始路径**，
能帮你确认它到底是从 `stu_upload/` 还是别处编译来的：

```python
print(code.co_filename)   # 例如 /opt/code_chap4/exp_4_4_mysigmoid/modules.py
```

---

## 方法二：预置权重 / 编译产物的结构（锁死网络）

### 2.1 `.pth` 的 `state_dict()`

如果实验给了预训练权重，**键名 + 形状**就是网络结构的硬约束：

```python
import torch
sd = torch.load('model.pth', map_location='cpu')
if isinstance(sd, dict) and 'state_dict' in sd:
    sd = sd['state_dict']
for k, v in sd.items():
    print(f'{k:40s} {tuple(v.shape)}')
```

能直接读出来的东西：

| 键名形态 | 结论 |
|---|---|
| `Q_proj.0.weight` | `Q_proj` 是 `nn.Sequential`，第 0 项是 `Linear` |
| `conv1.0.weight` / `conv1.1.weight` | 即使走"全连接分支"，`conv1` 也必须是 `Sequential` |
| `norm1.weight` + `norm1.bias` + `norm1.running_mean` | `BatchNorm`（若只有 weight/bias 则是 `LayerNorm`/`InstanceNorm`） |
| `in_proj_weight`（单个大矩阵） | 是 `nn.MultiheadAttention` 的官方实现，不是手写的 QKV 三个 Linear |

**验收标准**：写完模型后

```python
model.load_state_dict(torch.load('model.pth', map_location='cpu'), strict=True)
```

必须 **0 missing / 0 unexpected / 0 shape mismatch**。任何一条不为零都说明结构还没对上。

### 2.2 预编译的 `.so`（自定义算子）

实验会预置一个编好的扩展（例如 `op_exp.cpython-310-x86_64-linux-gnu.so`）。
直接 import 跑一遍就能反推：

- **导出符号名**：`python -c "import op_exp; print(dir(op_exp))"`
- **接口签名**：丢一个已知形状的 `torch.Tensor` 进去，看输出形状 / dtype / 数值。
- 用 `nm -D xxx.so | grep PyInit` 确认它注册的模块名。

> ⚠️ **同名 egg 抢占陷阱**：镜像 `site-packages` 里可能有个同名但无关的 `.egg`
> （由别的源文件编的，例如 `hsigmoid.cpp` 而不是你的 `mysigmoid.cpp`）。
> `import op_exp` 会命中它，报
> `AttributeError: module 'op_exp' has no attribute 'mysigmoid'. Did you mean: 'hsigmoid'?`
>
> 排查：
> ```bash
> python -m aicslab run "python -c 'import op_exp, inspect; print(op_exp.__file__)'; find / -name 'op_exp*' 2>/dev/null"
> python -m aicslab run "cat \$(python -c 'import os,op_exp;print(os.path.dirname(op_exp.__file__))')/../EGG-INFO/SOURCES.txt 2>/dev/null"
> ```
> `EGG-INFO/SOURCES.txt` 会写明这个 egg 由哪个 `.cpp` 编出来——一眼看出是不是你的实验。
>
> **修法**：把**你自己的目录**插到 `sys.path` **最前面**：
> ```python
> import sys, os
> sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'op_mysigmoid'))
> #   ↑ insert(0, ...) 才有效；用 append 会被 egg 盖掉
> import op_exp
> ```

---

## 方法三：历史产物就是金标准

老师真正跑过实验后，工作目录里常留下**结果文件**：

| 文件 | 用途 |
|---|---|
| `output.txt` | 打印出来的指标（loss / BLEU / accuracy） |
| `logs/*.txt` | 训练日志，可能含多轮 loss 曲线 |
| `out/*.jpg` / `*.png` | 风格迁移、生成的图像 |
| `*.npy` / `*.pt` | 中间张量，可 `torch.load` 比对数值 |

**用法**：把它们当作**验收基准**。

- 例：某 Transformer 实验的 `output.txt` 里留着 4 条
  `Bleu Score = 17.095544451082745`。
  那么你自己跑到**逐位相同**（`==`，不是 `np.allclose`）才叫复现成功——
  这比"看起来差不多"强得多，也说明随机种子、数据顺序、`eval` 模式全对上了。
- 图像类可以比 size 和 mean/std，必要时做像素级 diff。

> 这些数字往往**出现在脚手架打印的固定格式**里。
> 先 `python -m aicslab run "cat output.txt"` 看原始文本，再决定用什么精度去比对。

---

## 建议的取证顺序

1. **先列目录**，找 `__pycache__`、`*.pth`、`*.so`、`output.txt`、`logs/`、`out/`。
2. **`cp -a` 把参考证据备份到 `/root/ref/`**（防止被自己的上传覆盖）。
3. 有 pyc → 反汇编，逐函数还原（方法一）。
4. 有权重 / 算子 → dump 结构（方法二）。
5. 有历史输出 → 记下数字当验收线（方法三）。
6. **动手写代码**，写完在容器里实跑，对照验收线。
7. 没用上证据、纯靠猜的 TODO，**在报告里标注为"推断"**，不要假装是还原的。

---

## 边界与诚实

- 反查是**为了少猜**，不是抄答案。还原出来的实现要**能解释**：
  为什么是这个顺序、这个 kwargs、这个结构。
- 如果某条 TODO 三种方法都取不到证据，就**如实说明"这里按 XX 推断"**，
  并把可验证的中间结果（形状、loss 量级）贴出来。
