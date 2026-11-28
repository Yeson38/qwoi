# OJ 评测系统

基于 GitHub Actions 的在线评测（Online Judge）。提交通过 `repository_dispatch` 触发，
在干净的 Ubuntu runner 上编译、断网运行、比对输出，并把结构化结果写进 `result.json`。

支持语言：**C++17**、**Python 3**。

---

## 目录结构

```
.
├── .github/workflows/judge.yml   # CI 工作流：准备源码 → 启用隔离 → 评测 → 上传/评论/摘要
├── problems/
│   ├── cpp/                      # 语言目录（与 lang_spec.SUPPORTED 的 key 一致）
│   │   └── <TASK>/               # 题目目录，目录名即 task 名（[A-Za-z0-9_-]）
│   │       ├── meta.yml          # 题目配置（可选）
│   │       └── tests/
│   │           ├── 1.in          # 输入
│   │           └── 1.ans         # 期望输出（缺失 = 空答案）
│   ├── py/
│   │   └── <TASK>/               # 同上
│   └── <TASK>/                   # 可选：多语言共用同一套数据的题，优先级低于语言目录
├── scripts/
│   ├── run_tests.py              # 评测器 CLI
│   └── lang_spec.py              # 语言配置：编译/运行命令 + 语言级默认限制
└── out/result.json               # 本地运行时的默认结果路径（已被 .gitignore 忽略）
```

---

## 工作原理

`scripts/run_tests.py` 每次评测执行以下步骤：

1. **参数校验**：`lang` 必须已注册；`task` / `player` 只允许 `[A-Za-z0-9_-]`（防路径穿越）。
2. **读取配置**：按「题目 > 语言 > 代码保底」解析时间/内存等限制（见 [配置系统](#配置系统)）。
3. **断网检测**：`unshare -n -r true` 是否可用；不可用则降级并写入 `warnings`。
4. **编译**：C++ 用 `g++ -O2 -std=c++17 ...`；Python 用 `py_compile` 做语法检查。
5. **逐用例运行**：把 `tests/*.in` 按名字顺序跑，直到第一个非 AC。
6. **判定**：退出码 / 信号 / 实测耗时 / 内存报错特征 → `AC / WA / TLE / MLE / RE / CE / IE`。
7. **输出**：写 `result.json` 并打印到 stdout。

---

## 快速开始

### 本地评测

```bash
# 1) 依赖（仅 C++ 之外的配置解析需要 pyyaml；缺了会忽略 meta.yml）
pip install pyyaml

# 2) 准备一份源码
cat > /tmp/main.cpp <<'EOF'
#include <iostream>
int main() {
    int n; std::cin >> n;
    long long s = 0, x;
    for (int i = 0; i < n; i++) { std::cin >> x; s += x; }
    std::cout << s << "\n";
}
EOF

# 3) 评测
python scripts/run_tests.py \
  --lang cpp \
  --task TEST \
  --player alice \
  --source /tmp/main.cpp \
  --out out/result.json
```

结果同时写入 `out/result.json` 并打印到终端。

### Python 提交

```bash
python scripts/run_tests.py --lang py --task TEST --player alice --source /tmp/main.py
```

### 允许联网（调试用）

默认断网。调试需要联网时：

```bash
python scripts/run_tests.py --lang cpp --task TEST --player alice \
  --source /tmp/main.cpp --allow-network
```

---

## 命令行参数

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--lang` | ✅ | — | `cpp` 或 `py`（取自 `lang_spec.SUPPORTED`） |
| `--task` | ✅ | — | 题目名，`[A-Za-z0-9_-]`；解析为 `problems/<lang>/<task>`，不存在时回退到 `problems/<task>` |
| `--player` | ✅ | — | 选手名，`[A-Za-z0-9_-]` |
| `--source` | ✅ | — | 提交源码路径 |
| `--out` | ❌ | `out/result.json` | 结果 JSON 输出路径（目录会自动创建） |
| `--problems` | ❌ | `problems` | 题目根目录 |
| `--allow-network` | ❌ | 关闭 | 允许网络访问；不加则用 `unshare -n -r` 断网 |

查看帮助：

```bash
python scripts/run_tests.py --help
```

---

## 配置系统

### 优先级

同一个配置项（例如 `tle_ms`）按下面的顺序取第一个存在的值：

| 层级 | 位置 | 说明 |
| --- | --- | --- |
| 1（最高） | `problems/<lang>/<TASK>/meta.yml`（回退 `problems/<TASK>/meta.yml`） | 题目配置。语言专属写法 > 全局写法 |
| 2 | `scripts/lang_spec.py` → `SUPPORTED[lang]["limits"]` | 语言级默认值 |
| 3（最低） | `scripts/lang_spec.py` 末尾 `DEFAULT_*` | 代码保底，缺失 `meta.yml` 或未安装 pyyaml 时生效 |

在题目配置内部，语言的解析顺序是：

1. `limits.<lang>.<key>`（最具体）
2. `<key>: {<lang>: 值}`
3. `<key>: 值`（对没有单独指定的语言生效）

### meta.yml 支持的键

| 键 | 类型 | 含义 | 代码保底 |
| --- | --- | --- | --- |
| `title` | str | 题目标题（仅信息性） | — |
| `points` | int | 该题总分 | `DEFAULT_POINTS = 100` |
| `tle_ms` | int | 时间限制（毫秒，按 wall clock 判定） | `DEFAULT_TLE_MS = 1000` |
| `ml_mb` | int | 内存限制（MB，`RLIMIT_AS`） | `DEFAULT_ML_MB = 256` |
| `compile_tle` | int | 编译超时（秒） | `DEFAULT_COMPILE_TLE = 30` |
| `fsize_mb` | int | 单文件写入上限（MB），**0 = 不限制** | `DEFAULT_FSIZE_MB = 0` |
| `nproc` | int | 进程/线程数上限，**0 = 不限制** | `DEFAULT_NPROC = 0` |
| `compare` | str | 比对模式：`full` / `normalize` / `line_by_line` / `token` | `DEFAULT_COMPARE = "normalize"` |
| `limits` | dict | 按语言分组的上述数值键 | — |

> 数值项都不接受负数语义上的特殊含义，`0` 只对 `fsize_mb` / `nproc` 表示“不设限”；
> `tle_ms` / `ml_mb` / `compile_tle` 为 0 时等价于去掉该限制（`ml_mb <= 0` 时不设置
> `RLIMIT_AS`）。

### 示例 1：按语言分别限制（推荐）

```yaml
title: 序列求和
points: 100
limits:
  cpp:
    tle_ms: 1000
    ml_mb: 512
  py:
    tle_ms: 2000
    ml_mb: 256
compare: line_by_line
```

### 示例 2：全局 + 个别语言覆盖

```yaml
points: 100
tle_ms: 2000          # 两种语言都给 2 秒
ml_mb: 256
limits:
  cpp:
    ml_mb: 512        # C++ 单独放宽到 512 MB（优先级高于上面的全局值）
```

### 示例 3：字典写法（等价于 `limits`）

```yaml
tle_ms:
  cpp: 1000
  py: 2000
points: 100
```

### 示例 4：不加任何限制（调试 / 特殊题）

```yaml
points: 100
tle_ms: 10000
ml_mb: 0        # 不限制内存
fsize_mb: 0     # 不限制写盘
nproc: 0        # 不限制进程/线程
```

### 语言配置（`scripts/lang_spec.py`）

每种语言包含：

```python
"cpp": {
    "ext": ".cpp",                 # 源文件扩展名
    "compile_cmd": [...],          # 编译命令模板，{src} 源文件、{bin} 产物
    "run_cmd": ["{bin}"],          # 运行命令模板，{src} 源文件、{bin} 产物
    "limits": {                    # 第 2 层配置（题目未指定时生效）
        "tle_ms": 1000,
        "ml_mb": 256,
        "compile_tle": 30,
        "fsize_mb": 0,
        "nproc": 0,
    },
}
```

`compile_cmd = None` 表示该语言不需要编译（如 Python，只做语法检查）。

### 新增一门语言

1. 在 `SUPPORTED` 里加一项，填 `ext` / `compile_cmd` / `run_cmd` / `limits`。
2. 在 `.github/workflows/judge.yml` 的 `Prepare source` 步骤里，`case "$LANG_TYPE"` 增加分支，
   把扩展名对应上（`--lang` 的取值直接用 `SUPPORTED` 的键）。
3. 跑一次评测验证。

---

## 新增一道题

下面以题目 `SUM`（读入 `n` 和 `n` 个整数，输出它们的和）为例，走一遍完整流程。

### 1. 建目录

题目必须放在 **语言目录** 下：`problems/<lang>/<TASK>/`，`<lang>` 取 `lang_spec.SUPPORTED` 的键
（当前为 `cpp` / `py`），`<TASK>` 只能用 `[A-Za-z0-9_-]`。

```bash
mkdir -p problems/cpp/SUM/tests
mkdir -p problems/py/SUM/tests     # Python 要单独评就建；不需要就跳过
```

两套目录里的用例可以完全一样（内容重复很少），也可以分别定制。
不想重复时把用例放共用的 `problems/SUM/tests/`，评测器在两处都找不到语言目录时会自动回退，
并在 `warnings` 里提示“已回退到共用目录”。

### 2. 放用例

```
problems/cpp/SUM/tests/
├── 1.in      # 输入
├── 1.ans     # 期望输出
├── 2.in
└── 2.ans
```

```bash
cat > problems/cpp/SUM/tests/1.in <<'EOF'
5
1 2 3 4 5
EOF
cat > problems/cpp/SUM/tests/1.ans <<'EOF'
15
EOF

printf '3\n-1 0 1\n'   > problems/cpp/SUM/tests/2.in
printf '0\n'            > problems/cpp/SUM/tests/2.ans
```

规则：

- 只看 `*.in`，同名 `.ans` 是期望输出（**缺失 = 空答案**，容易误判 `AC`，正式题务必建）。
- 用例顺序是 **文件名字典序**（`sorted()`），所以 `1, 10, 2, 3` 会按 `1, 10, 2, 3` 执行。
  想按数字顺序就补零：`01.in` `02.in` `10.in`；文件后缀不是 `.in` 的会被忽略。
- 默认遇到第一个非 `AC` 用例就停，后面的不跑、不计分。
- 每题至少一个 `.in`，否则 `IE: 没有找到测试用例`。

### 3. 写 `meta.yml`（可选）

最小可用版本是什么都不写（全靠语言默认：C++ 1s / 256MB，Python 2s / 256MB）。推荐至少写清限制：

```yaml
# problems/cpp/SUM/meta.yml
title: 序列求和
points: 100
compare: line_by_line
limits:
  cpp:
    tle_ms: 1000
    ml_mb: 512
```

各键含义见 [meta.yml 支持的键](#metayml-支持的键)，优先级见 [配置系统](#配置系统)。
两道常见选择：

- 输出可能有多种合法写法 → 用 `token`（按空白切词比较）或 `line_by_line`。
- 想卡 Python 常数 → 给 `py` 单独放大 `tle_ms`，别把全局值调成两者都不合理。

### 4. 本地验证

```bash
# 选手答案
cat > /tmp/sum.cpp <<'EOF'
#include <bits/stdc++.h>
int main() {
    long long n, x, s = 0;
    std::cin >> n;
    for (int i = 0; i < n; i++) { std::cin >> x; s += x; }
    std::cout << s << "\n";
}
EOF

# 跑一次
python scripts/run_tests.py --lang cpp --task SUM --player alice \
  --source /tmp/sum.cpp --out out/sum.json
```

看三件事：`verdict` 是不是 `AC`；`limits` 回显的是不是你写的值（优先级没写错）；
`tests[*].time_ms` 离 `tle_ms` 远不远。

Python 也走一遍（同一题得在 `problems/py/SUM/` 也有目录）：

```bash
printf 'import sys\nd=sys.stdin.read().split()\nn=int(d[0])\nprint(sum(int(x) for x in d[1:1+n]))\n' > /tmp/sum.py
python scripts/run_tests.py --lang py --task SUM --player alice \
  --source /tmp/sum.py --out out/sum_py.json
```

再用错答案确认用例真能判错（否则可能是 `.ans` 没写或比对过宽）：

```bash
echo 'int main(){}' > /tmp/wrong.cpp
python scripts/run_tests.py --lang cpp --task SUM --player bob \
  --source /tmp/wrong.cpp --out out/wrong.json   # 期望 WA
```

### 5. 提交到线上触发一次

```bash
SRC=$(base64 -w0 /tmp/sum.cpp)
cat > p.json <<EOF
{"event_type":"judge","client_payload":{"lang":"cpp","task":"SUM","player":"alice","source_content":"$SRC"}}
EOF
gh api repos/<owner>/qwoi/dispatches --method POST --input p.json
gh run list --repo <owner>/qwoi --workflow judge.yml --limit 1
```

下载产物并核对 `result.json`，见 [获取评测结果的所有方式](#获取评测结果的所有方式)。

### 检查清单

- [ ] 目录是 `problems/<lang>/<TASK>/tests/`，不是 `problems/<TASK>/data/`
- [ ] 每个 `N.in` 都有 `N.ans`（空答案会误判 `AC`）
- [ ] 用例名位数统一（`01/02/10`），避免字典序打乱顺序
- [ ] `meta.yml` 里的语言键与 `SUPPORTED` 一致（`cpp` / `py`，不是 `c++`）
- [ ] 本地用错误答案跑出过 `WA`，确认判题真的在检查输出
- [ ] 本地 `limits` 回显与预期一致；CI 上再跑一次确认（本地没有 `unshare` 时是降级模式）

---

## 判定结果

### verdict 一览

| 判定 | 含义 | 触发条件 |
| --- | --- | --- |
| `AC` | 通过 | 所有用例输出匹配 |
| `WA` | 答案错误 | 程序正常退出，但输出与 `.ans` 不匹配 |
| `TLE` | 超时 | CPU 时间超限（`SIGXCPU`/`SIGALRM`）**或** wall clock 超限 |
| `MLE` | 内存超限 | `SIGKILL`，或 stderr 命中内存报错特征（`bad_alloc` / `MemoryError` / …） |
| `RE` | 运行错误 | 非零退出码 / 其他信号（段错误、`SIGABRT` 等） |
| `CE` | 编译错误 | 编译失败或语法错误，`compile_msg` 带编译器输出 |
| `IE` | 内部错误 | 语言不支持、参数非法、题目/用例目录缺失等 |

### 与洛谷状态对照

洛谷（以及多数 OJ）的状态全集是 AC / CE / PC / WA / RE / TLE / MLE / OLE / UKE，
本评测器的对应关系如下：

| 洛谷状态 | 本评测器 | 说明 |
| --- | --- | --- |
| AC | `AC` | 所有用例输出匹配 |
| CE | `CE` | 编译失败，或编译用时超 `compile_tle`（`compile_msg` 写“编译超时”） |
| WA | `WA` | 输出不匹配；输出不是合法 UTF-8 也走 `WA`（附 `warnings`），不会被吞成空串误判 `AC` |
| RE | `RE` | 非零退出，或除 `SIGXCPU`/`SIGALRM`/`SIGKILL` 之外的信号（段错误、`SIGABRT`、`SIGXFSZ` 等） |
| TLE | `TLE` | `SIGXCPU`/`SIGALRM`，或 wall clock 超过 `tle_ms` |
| MLE | `MLE` | `SIGKILL`，或非正常退出且 stderr 命中 `bad_alloc`/`MemoryError` 等特征 |
| OLE | **无** | 没有输出上限：写 64 MB 也只会判 `WA`（实测 90 ms）。需要 OLE 时要自行改读循环加计数 |
| PC | **无** | 部分分只体现在 `score`（如 4 例 90 分错 1 例 → `score=22.5`），`verdict` 仍是 `WA`；且默认遇首个失败用例即停，要跑满需把 `break` 改成 `continue` |
| UKE | **无** | 用 `IE` 代替：评测侧故障（题目/用例目录缺失、语言不支持、编译流程异常等）一律 `IE` |

反过来，`IE` 是洛谷没有的状态，用来区分“选手的错”和“评测器/题目配置的错”。
另：部分 OJ 的 `PE`（格式错误）这里也没有，空白与换行差异已由 `normalize`/`line_by_line`/`token` 吸收。

### 时间判定细节

- **CPU 时间**：`RLIMIT_CPU = ceil(tle_ms / 1000)` 秒，超限收到 `SIGXCPU` → `TLE`。
- **Wall clock**：`subprocess` 超时设为 `tle_ms / 1000` 秒，到点强杀 → `TLE`；
  程序正常结束但实测 `time_ms > tle_ms` 也判 `TLE`（覆盖 `sleep`、阻塞 I/O 这类不耗 CPU 的等待）。
- 因此 `time_ms` 不会出现“超过限制但判 AC”的情况。

### 内存判定细节

`RLIMIT_AS` 超限时进程通常拿到 `std::bad_alloc`（C++）或 `MemoryError`（Python），
表现为 `SIGABRT` 或非零退出，**不是** `SIGKILL`。评测器对这两种情况都会判成 `MLE`，
不会误报成 `RE`。

### 输出比对

| 模式 | 规则 |
| --- | --- |
| `full` | 逐字节一致 |
| `normalize`（默认） | 去掉每行行尾空白 + 去掉末尾空行 |
| `line_by_line` | 逐行比较，行内连续空白折叠为一个空格，去掉末尾空行 |
| `token` | 按空白切词后逐 token 比较（忽略所有换行/空格差异） |

匹配是忽略 `\r\n` 与 `\n` 差异的（按行切分后再比较）。编码统一按 UTF-8 处理：
答案或输出含非法字节时按替换字符比较并写入 `warnings`，既不会崩评测，也不会把乱码当成空输出。

---

## 资源限制与网络隔离

所有限制都在子进程 `exec` 前通过 `preexec_fn` 设置，随题目配置变化：

| 限制 | 实现 | 默认 | 不限制的方式 |
| --- | --- | --- | --- |
| CPU 时间 | `RLIMIT_CPU` | `tle_ms` | 设很大的 `tle_ms` |
| 内存 | `RLIMIT_AS` | 256 MB | `ml_mb: 0` |
| 单文件写入 | `RLIMIT_FSIZE` | 不限制 | `fsize_mb: 0`（默认） |
| 进程/线程数 | `RLIMIT_NPROC` | 不限制 | `nproc: 0`（默认） |
| 核心转储 | `RLIMIT_CORE` | 0 | 固定关闭 |

> 系统默认不额外限制进程数，因此 `std::thread`、`multiprocessing`、`fork` 都能正常使用；
> 需要防 fork 炸弹时在题目配置里设 `nproc`（例如 `nproc: 64`）。

### 断网

- 运行命令会被包成 `unshare -n -r <cmd>`：新建无网络接口的 network namespace。
- 可用性在评测开始时检测；不可用则降级运行，并在 `result.json` 的 `warnings` 里给出原因，
  `network_disabled` 为 `false`。
- Ubuntu 24.04 默认用 AppArmor 限制非特权 user namespace，会让 `unshare -n -r` 直接失败。
  `.github/workflows/judge.yml` 里因此有一步：

  ```bash
  sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 || true
  unshare -n -r true     # 失败则整个 job 失败，不会带着网络去评测
  ```

- 本地/其他机器上如果同样被限制，可手动执行上面的 `sysctl`（需要 root）。

### 本地 Windows 说明

`preexec_fn` 与 `resource` 是 POSIX 专有接口，Windows 上 `run_one` 会返回 `IE`
（`preexec_fn is not supported on Windows platforms`）。Windows 本地只适合验证参数解析/
配置解析/输出比对；完整评测请在 Linux（或 GitHub Actions）上跑。

---

## 触发评测的所有方式

工作流监听 `repository_dispatch`，`event_type` 固定为 `judge`。

### client_payload 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `lang` | ✅ | `cpp` / `py` |
| `task` | ✅ | 题目目录名 |
| `player` | ✅ | 选手名 |
| `source_content` | ✅ | **base64 编码**的源码（注意：不是 `source`） |
| `submission_id` | ❌ | 用于并发分组，缺省用 `run_id` |
| `issue_number` | ❌ | 填了就在该 Issue 下评论评测结果 |

### 方式 1：GitHub CLI

```bash
B64=$(base64 -w0 main.cpp)
gh api repos/<owner>/<repo>/dispatches --method POST \
  -f event_type=judge \
  -f client_payload[lang]=cpp \
  -f client_payload[task]=TEST \
  -f client_payload[player]=alice \
  -f client_payload[source_content]="$B64" \
  -f client_payload[submission_id]=sub_001
```

（`gh api --input file.json` 也可以，避免特殊字符转义问题。）

### 方式 2：curl

```bash
B64=$(base64 -w0 main.cpp)
curl -X POST \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/<owner>/<repo>/dispatches \
  -d "{\"event_type\":\"judge\",\"client_payload\":{
        \"lang\":\"cpp\",\"task\":\"TEST\",\"player\":\"alice\",
        \"source_content\":\"$B64\",\"submission_id\":\"sub_001\"}}"
```

成功返回 `204 No Content`。

### 方式 3：PowerShell

```powershell
$token  = "github_pat_xxx"
$repo   = "Yeson38/qwoi"
$code   = Get-Content -Raw -Encoding UTF8 .\main.cpp
$b64    = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))

$body = @{
  event_type     = "judge"
  client_payload = @{
    lang           = "cpp"
    task           = "TEST"
    player         = "alice"
    source_content = $b64
    submission_id  = "sub_001"
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/dispatches" `
  -Method Post -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer $token"; Accept = "application/vnd.github+json" } `
  -Body $body
```

### 方式 4：Python

```python
import base64, json, os, urllib.request

src = open("main.cpp", encoding="utf-8").read()
payload = {
    "event_type": "judge",
    "client_payload": {
        "lang": "cpp",
        "task": "TEST",
        "player": "alice",
        "source_content": base64.b64encode(src.encode()).decode(),
        "submission_id": "sub_001",
    },
}
req = urllib.request.Request(
    "https://api.github.com/repos/<owner>/<repo>/dispatches",
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    },
    method="POST",
)
print(urllib.request.urlopen(req).status)   # 204
```

### 方式 5：任意 HTTP 客户端

Postman / Insomnia / 自建后端 / 另一个 Actions 工作流，只要能发 HTTPS POST 都可以，
请求体同上。GitHub 网页界面**不支持**手动触发 `repository_dispatch`，
网页上只能在 Actions 列表里查看和重跑已有的运行。

### Token 权限

| Token 类型 | 需要的权限 |
| --- | --- |
| Fine-grained PAT | **Contents: Read and write**（触发 dispatch） |
| Fine-grained PAT（要推送改 workflow） | 额外 **Workflows: Read and write** |
| Classic PAT / OAuth | `repo` |

> 注意：`git push` 包含 `.github/workflows/` 下文件的改动时，GitHub 要求 token 带
> `Workflows` 权限，否则整个 push 会被拒绝。

### 触发后如何定位这次运行

- `run-name`：`评测 <player> - <task> (#<run_number>)`
- 并发组：`judge-<submission_id>`（不同 `submission_id` 之间并行，相同则排队）
- 列表：`gh run list --workflow judge.yml --limit 5`

---

## 获取评测结果的所有方式

### 1. 本地 `result.json`

`--out` 指定的路径（默认 `out/result.json`），同时打印到 stdout。

### 2. 工作流 Artifact（推荐）

每次运行都会上传 `out/result.json`，名称：

```
result-<player>-<task>-<run_number>
```

获取方式：

```bash
# 找到最近一次运行
gh run list --workflow judge.yml --limit 1 --json databaseId,displayTitle

# 下载该运行的全部 artifact 到目录
gh run download <run_id> -D ./artifacts

# 只下载指定名字的 artifact
gh run download <run_id> -n result-alice-TEST-22 -D ./artifacts
```

网页：Actions → 选中运行 → 页面底部 **Artifacts** → 下载 zip。

API：

```bash
# 列出 artifact
curl -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/<owner>/<repo>/actions/runs/<run_id>/artifacts

# 下载（返回 302，需要跟随重定向）
curl -L -H "Authorization: Bearer $TOKEN" -o result.zip \
  https://api.github.com/repos/<owner>/<repo>/actions/artifacts/<artifact_id>/zip
```

> artifact 保留 7 天（`retention-days: 7`）。

### 3. Job Summary

每次运行会在页面的 **Summary** 标签里贴出完整 `result.json`（```json 代码块），
不需要下载任何东西。也可用 API 读取：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/<owner>/<repo>/actions/runs/<run_id>
# 然后在网页上看 summary，或读日志中的 "Summary" 步骤
```

### 4. 运行日志

```bash
gh run view <run_id> --log                 # 全部日志
gh run view <run_id> --log-failed          # 只失败步骤
gh run view <run_id> --log | grep 评测
```

评测器会把最终 JSON 也 `print` 到 stdout，所以日志里一定有完整结果。

### 5. 评论结果

若 `client_payload` 带 `issue_number`，工作流会用 `GITHUB_TOKEN` 在该 Issue 下评论：

```
✅ **alice** - TEST: AC (100.0 分) | Run #24
```

`VERDICT` 的 emoji 映射见 `judge.yml` 的 `Comment result` 步骤。

### 6. Actions API / Webhook / 第三方

```bash
# 运行状态与结论
curl -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/<owner>/<repo>/actions/runs/<run_id>

# 某次运行的所有 job / step
curl -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/<owner>/<repo>/actions/runs/<run_id>/jobs

# 最近的工作流运行
gh api repos/<owner>/<repo>/actions/workflows/judge.yml/runs
```

可以在仓库 Settings → Webhooks 里订阅 `workflow_run` 事件，把结果回调到自己的服务。

---

## result.json 字段

```json
{
  "player": "alice",
  "task": "TEST",
  "lang": "cpp",
  "verdict": "AC",
  "score": 100.0,
  "total_score": 100,
  "tests": [
    {
      "name": "1",
      "verdict": "AC",
      "score": 100.0,
      "time_ms": 3,
      "signal": 0
    }
  ],
  "compile_msg": "",
  "time_ms": 3,
  "max_time_ms": 3,
  "network_disabled": true,
  "warnings": [],
  "limits": {
    "tle_ms": 1000,
    "ml_mb": 512,
    "compile_tle": 30,
    "fsize_mb": 0,
    "nproc": 0,
    "compare": "line_by_line"
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `verdict` | 最终判定（第一个非 AC 用例的判定，全 AC 则为 `AC`） |
| `score` / `total_score` | 已得分 / 满分 |
| `tests[]` | 已运行的用例（每个 `points / 用例数`） |
| `tests[].signal` | 终止信号编号，0 表示正常退出 |
| `compile_msg` | 编译/语法错误输出（`CE` 时） |
| `time_ms` / `max_time_ms` | 所有已运行用例耗时之和 / 最大单个用例耗时 |
| `network_disabled` | 本次是否真的断网 |
| `warnings` | 降级信息（如 `unshare` 不可用原因） |
| `limits` | **本次实际生效**的限制与比对模式，便于核对配置优先级 |

计分规则：`per_test_score = points / 用例数`；遇到第一个非 AC 用例即停止，
`score` 为已通过用例的分数之和（要跑完全部用例，把 `run_tests.py` 里的 `break` 改成 `continue`）。

---

## 边界与已知行为

| 现象 | 原因 / 说明 |
| --- | --- |
| 空 `.ans` + 不输出的程序 → `AC` | `line_by_line`/`normalize` 会把空答案和空输出都规整为空列表。写调试用例时故意为之即可，正式题务必填答案 |
| `.ans` 缺失 → 按空答案比对 | 不报错，避免用例未准备好时整体 IE |
| 一个用例失败即中止 | 见上，`break` 可改 `continue` |
| `sleep` 也会 `TLE` | wall clock 判定生效，不再只看 CPU |
| 内存超限是 `MLE` 而非 `RE` | 通过 stderr 特征识别 `bad_alloc`/`MemoryError`，且要求进程非正常退出；正常退出只在 stderr 里提到这些词不会被误判 |
| 答案/输出不是合法 UTF-8 | 按替换字符比较并写进 `warnings`，既不会让评测崩溃，也不会把乱码吞成空串而误判 `AC` |
| 程序可以开线程/子进程 | 默认 `nproc: 0` 不限制；需要时在题目里设，注意下面的 per-UID 说明 |
| 写盘默认不限制 | `fsize_mb: 0`；共享 runner 资源有限，正式题建议设一个上限 |
| Windows 本地跑出 `IE` | `preexec_fn` 不可用（POSIX only） |
| 中文警告在 Windows 控制台乱码 | 控制台代码页问题，不影响 `result.json`（UTF-8） |
| `unshare` 不可用时只警告 | `network_disabled: false` + `warnings` 说明；CI 里已用前置步骤强制可断网 |

---

## 实测能力边界

下面的数字都是在 `ubuntu-latest`（4 vCPU / 16 GB）上跑 `repository_dispatch` 实测得到的，
不是理论值。题目侧的限制都可以按 [配置优先级](#配置系统) 覆盖。

### 时间

| 场景 | 结果 |
| --- | --- |
| `sleep(0.95s)`，限制 1000 ms | `AC`，`time_ms = 954` |
| `sleep(0.99s)`，限制 1000 ms | `AC`，`time_ms = 1000`（判定用 `elapsed > tle_ms`，恰好等于限制算过） |
| `sleep(1.0s)`，限制 1000 ms | `TLE`，`time_ms ≈ 1004~1009` |
| 每秒约 1e9 次空循环，限制 1000 ms | `TLE`，`time_ms ≈ 1064` |
| 每个用例的计时 | 从 `fork/exec` 到进程退出的 wall clock，含 `unshare` 启动开销约 5~10 ms |

### 内存

`ml_mb` 是 **虚拟地址空间** 上限（`RLIMIT_AS`），程序本身的代码段和动态库都算在内，
所以实际可用的堆比 `ml_mb` 略小。

| 场景（`ml_mb: 512`） | 结果 |
| --- | --- |
| `vector` 申请 480 MB 并逐页触碰 | 通过（`WA` 只是因为输出不对），`time_ms ≈ 244` |
| `vector` 申请 512 MB 并逐页触碰 | `MLE`（`SIGABRT`，stderr 有 `bad_alloc`） |
| `vector` 申请 600 MB | `MLE`，`time_ms ≈ 50` |
| Python `bytearray(600 MB)` | `MLE`（`MemoryError` 或 `SIGKILL` 都能识别） |
| 多线程 | **线程栈也计入**：默认 8 MB/线程，`ml_mb: 512` 时约 50 个线程会撞上限；要开很多线程请调大 `ml_mb` 或直接用 `ml_mb: 0` |

### 进程与文件

| 场景 | 结果 |
| --- | --- |
| `nproc: 0`（默认）+ `std::thread` / `fork` | 正常，可以随意开 |
| `nproc: 8` + 50 线程 | **一个都建不起来**：`RLIMIT_NPROC` 是 **整个 UID 的进程/线程总数**，runner 上该 UID 已有进程，余量不够 |
| `nproc: 512` + 50 线程（`ml_mb: 0`） | 正常，50 个线程全建成 |
| `fsize_mb: 0`（默认）写 30 MB 文件 | 成功 |
| `fsize_mb: 1` 写 2 MB 文件 | `SIGXFSZ`（25）→ `RE`，`time_ms ≈ 97` |

> 结论：`nproc` 要限制就设一个明显大于 runner 已有进程数的值（如 256/512），
> 设成个位数会把求解程序一起锁死。

### 输入输出与比对

| 场景 | 结果 |
| --- | --- |
| 108 KB 的 `1.in`（两万个整数） | `AC`，`time_ms ≈ 7`（`subprocess` 并发读写，不会死锁） |
| 8.4 MB stdout | 正常比对，`WA`，`time_ms ≈ 21`（输出全量捕获在内存里） |
| 64 MB stdout | `WA`，`time_ms ≈ 90` |
| 100 字节 `0xFF` 的非法 UTF-8 输出 | `WA` + `warnings`（旧版本会 `ignore` 成空串而误判 `AC`） |
| `.ans` 是非 UTF-8（如 GBK） | 按替换字符比对 + `warnings`（旧版本会直接崩掉整个评测，连 `result.json` 都不产出） |
| 输出 `3\r\n`、答案 `3\n` | `line_by_line` / `normalize` 都判 `AC`（`splitlines` 吃掉 `\r`） |
| 输出前后带多余空格/空行 | `line_by_line`/`normalize`/`token` 都容忍；`full` 不容忍 |
| `.ans` 缺失 | 按空答案；程序无输出 → `AC`，有输出 → `WA` |
| 空 `.ans` + 空输出 | `AC`（故意留空样例时就会这样） |

### 状态与信号

| 程序行为 | 判定 | `signal` |
| --- | --- | --- |
| 空指针解引用 | `RE` | 11 |
| 无限递归爆栈 | `RE` | 11 |
| `exit(42)` | `RE` | 0（`exit_code = 42`） |
| `raise(SIGKILL)` / OOM killer | `MLE` | 9 |
| `SIGXCPU`（CPU 时间到） | `TLE` | 24 |
| 输出里带 `bad_alloc` 但正常 `return 0` | `AC`（不再误判 `MLE`） |

### 多用例与编译

| 场景 | 结果 |
| --- | --- |
| 4 个用例、`points: 90` | 每个 22.5 分，`score` 累加，`total_score` 仍是 90 |
| 第 2 个用例答案错 | `WA`，`score = 22.5`，`tests` 里只有前 2 条（遇到失败即停） |
| 用例文件 `1.in 2.in 3.in 10.in` | 执行顺序是 **字典序** `1, 10, 2, 3`（按文件名排序，不是按数值） |
| `compile_tle: 1` + `#include <bits/stdc++.h>` | `CE`，`compile_msg: 编译超时` |
| `#include <bits/stdc++.h>` 正常编译 | 约 1.5~2 s，默认 `compile_tle: 30` 很宽裕 |

### 进程树

| 场景 | 结果 |
| --- | --- |
| 程序 `fork` 后父进程立刻退出，子进程 `sleep(120)` 且持有 stdout | 评测仍在 `time_ms ≈ 1008` 返回 `TLE`，**不会被孤儿进程拖住** |
| 说明 | 超时只对直接子进程发 `SIGKILL`（也不建进程组），孤儿进程不会回收但已无法影响本次判定；正式题请避免依赖子进程行为 |

---

## 排错

| 症状 | 排查 |
| --- | --- |
| `IE: 题目目录不存在` | 检查 `problems/<lang>/<TASK>/`（或共用的 `problems/<TASK>/`），大小写敏感；报错信息里有完整路径 |
| `IE: 测试用例目录不存在` | 需要 `problems/<lang>/<TASK>/tests/`，不是 `data/` |
| 警告“已回退到共用目录” | 该语言目录下没有这个题，用的是 `problems/<TASK>` 的共用数据 |
| `IE: 没有找到测试用例` | `tests/` 里没有 `*.in` |
| `CE` 但本地能编译 | 线上用 `g++ -std=c++17 -O2`；检查是否用了本地扩展/更高标准 |
| 结果里 `limits` 和预期不符 | 按 [优先级](#优先级) 检查：`limits.<lang>` → 全局键 → 语言默认 |
| `network_disabled: false` | `unshare` 不可用，看 `warnings`；CI 需确认 `Enable network isolation` 步骤通过 |
| push 被拒 `without workflow scope` | token 需要 **Workflows: Read and write** 权限 |
| 触发后没有运行 | 检查 `event_type` 是否 `judge`、token 是否有 Contents 写权限 |
| `source_content 为空` | 字段名必须是 `source_content`，且值必须是 base64 |
