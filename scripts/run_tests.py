#!/usr/bin/env python3
"""
OJ 评测脚本
支持 C++ 和 Python，含资源限制、断网、信号判定。

断网方案：使用 unshare -n -r 创建独立网络命名空间。
如果 unshare 不可用，会降级为无断网模式并在结果中记录警告。

配置优先级（高 → 低）：
  1. 题目配置：problems/<TASK>/meta.yml
        limits.<lang>.<key>  →  <key>.<lang>  →  <key>
  2. 语言配置：scripts/lang_spec.py 中 SUPPORTED[lang]["limits"]
  3. 代码保底：lang_spec.py 末尾的 DEFAULT_* 常量

资源限制中的 0 表示"不设置该限制"（沿用系统行为），不会额外限制系统。
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# 确保能导入同目录模块
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lang_spec import (  # noqa: E402
    SUPPORTED,
    DEFAULT_TLE_MS,
    DEFAULT_ML_MB,
    DEFAULT_POINTS,
    DEFAULT_COMPARE,
    DEFAULT_COMPILE_TLE,
    DEFAULT_FSIZE_MB,
    DEFAULT_NPROC,
)

# 安全字符校验正则
_SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')

# 支持的输出比对方言
COMPARE_MODES = ("full", "normalize", "line_by_line", "token")

# 判定为内存超限的报错特征（RLIMIT_AS 触发时不会走 SIGKILL 路径）
_OOM_MARKERS = (
    b"MemoryError",
    b"bad_alloc",
    b"std::bad_alloc",
    b"Cannot allocate memory",
    b"cannot allocate memory",
    b"Out of memory",
)

# 检测 unshare 是否可用（用于断网）
_HAS_UNSHARE = None
_UNSHARE_ERR = ""


def _check_unshare() -> bool:
    """检测 unshare -n -r 是否可用"""
    global _HAS_UNSHARE, _UNSHARE_ERR
    if _HAS_UNSHARE is not None:
        return _HAS_UNSHARE
    try:
        r = subprocess.run(
            ["unshare", "-n", "-r", "true"],
            capture_output=True,
            timeout=5,
        )
        _HAS_UNSHARE = (r.returncode == 0)
        if not _HAS_UNSHARE:
            _UNSHARE_ERR = (r.stderr or b"").decode("utf-8", "ignore").strip()[:200]
    except Exception as e:
        _HAS_UNSHARE = False
        _UNSHARE_ERR = str(e)[:200]
    return _HAS_UNSHARE


# ==================== 资源限制 ====================
def set_limits(tle_ms: int, ml_mb: int, fsize_mb: int = 0, nproc: int = 0):
    """
    在子进程 exec 前设置资源限制（通过 preexec_fn 调用）。

    每一项 <= 0 时跳过，不额外限制系统：
      ml_mb   <= 0 → 不设置 RLIMIT_AS
      fsize_mb<= 0 → 不设置 RLIMIT_FSIZE
      nproc   <= 0 → 不设置 RLIMIT_NPROC（线程/多进程求解可用）
    """
    import resource

    # CPU 时间限制（秒），向上取整
    cpu_sec = max(1, (tle_ms + 999) // 1000)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec + 1))

    # 地址空间限制
    if ml_mb and ml_mb > 0:
        ml_bytes = ml_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (ml_bytes, ml_bytes))

    # 单文件写入大小限制
    if fsize_mb and fsize_mb > 0:
        fsize_bytes = fsize_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))

    # 进程/线程数限制
    if nproc and nproc > 0:
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))

    # 核心转储关闭
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _clean_env(workdir: str, for_compile: bool = False) -> dict:
    """构造干净的环境变量，防止泄漏敏感信息"""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": "0",
    }
    if for_compile:
        # 编译时可能需要更多环境
        env["CC"] = "gcc"
        env["CXX"] = "g++"
    return env


# ==================== 输出比对 ====================
def _split_lines(s: str):
    """按行分割，去掉末尾空行"""
    lines = s.splitlines()
    while lines and lines[-1].strip() == "":
        lines.pop()
    return lines


def compare_output(got: str, want: str, mode: str) -> bool:
    """
    比对输出。
    模式：
      - full:       完全一致（含空格换行）
      - normalize:  去掉行尾空格和末尾空行后比较（默认）
      - line_by_line: 逐行比较，行内连续空白折叠为单个空格
      - token:      按空白分词后逐 token 比较
    """
    if mode == "full":
        return got == want

    if mode == "line_by_line":
        g = [" ".join(line.split()) for line in _split_lines(got)]
        w = [" ".join(line.split()) for line in _split_lines(want)]
        return g == w

    if mode == "token":
        return got.split() == want.split()

    # full / normalize / 未知模式都退化为 normalize
    g = "\n".join(line.rstrip() for line in _split_lines(got))
    w = "\n".join(line.rstrip() for line in _split_lines(want))
    return g == w


# ==================== meta.yml 加载 ====================
_INT_KEYS = ("tle_ms", "ml_mb", "points", "compile_tle", "fsize_mb", "nproc")


def _language_default(lang: str, key: str, fallback):
    """第 2 层：lang_spec.py 中该语言的默认值"""
    conf = SUPPORTED.get(lang) or {}
    limits = conf.get("limits")
    if isinstance(limits, dict) and key in limits:
        return limits[key]
    return fallback


def _resolve(cfg: dict, lang: str, key: str, fallback):
    """
    按“题目 > 语言 > 代码保底”解析单个配置项。

    题目配置支持两种写法（同时存在时 <key>.<lang> 优先于 limits.<lang>.<key>？否：
    limits.<lang>.<key> 更具体，优先）：
      limits:
        cpp:
          tle_ms: 2000
      tle_ms:
        cpp: 2000
      tle_ms: 1000          # 全局，对未单独指定该语言的情况生效
    """
    if isinstance(cfg, dict):
        limits = cfg.get("limits")
        if isinstance(limits, dict):
            per_lang = limits.get(lang)
            if isinstance(per_lang, dict) and key in per_lang:
                return per_lang[key]

        val = cfg.get(key)
        if isinstance(val, dict):
            if lang in val:
                return val[lang]
        elif val is not None:
            return val

    return _language_default(lang, key, fallback)


def load_meta(problem_dir: str, lang: str) -> dict:
    """读取 meta.yml，按优先级返回生效的限制与评分配置。"""
    meta = {
        "tle_ms": DEFAULT_TLE_MS,
        "ml_mb": DEFAULT_ML_MB,
        "points": DEFAULT_POINTS,
        "compare": DEFAULT_COMPARE,
        "compile_tle": DEFAULT_COMPILE_TLE,
        "fsize_mb": DEFAULT_FSIZE_MB,
        "nproc": DEFAULT_NPROC,
    }

    cfg = {}
    meta_path = os.path.join(problem_dir, "meta.yml")
    if os.path.exists(meta_path):
        try:
            import yaml
            with open(meta_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except ImportError:
            print("[WARN] 未安装 pyyaml，忽略 meta.yml", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] 读取 meta.yml 失败: {e}", file=sys.stderr)
    if not isinstance(cfg, dict):
        cfg = {}

    for key in _INT_KEYS:
        try:
            meta[key] = int(_resolve(cfg, lang, key, meta[key]))
        except (TypeError, ValueError):
            pass

    cmp_val = _resolve(cfg, lang, "compare", DEFAULT_COMPARE)
    if isinstance(cmp_val, str) and cmp_val in COMPARE_MODES:
        meta["compare"] = cmp_val

    return meta


# ==================== 编译 ====================
def compile_src(lang: str, src: str, workdir: str, binary: str,
                compile_tle: int = DEFAULT_COMPILE_TLE) -> dict:
    """编译源文件，返回 {ok, msg}"""
    conf = SUPPORTED.get(lang)
    if conf is None:
        return {"ok": False, "msg": f"不支持的语言: {lang}"}

    # Python 不需要编译，做语法检查即可
    if conf["compile_cmd"] is None:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", src],
                capture_output=True,
                timeout=compile_tle,
                cwd=workdir,
                env=_clean_env(workdir),
            )
            if r.returncode != 0:
                return {
                    "ok": False,
                    "msg": r.stderr.decode("utf-8", "ignore")[:2000],
                }
            return {"ok": True, "msg": ""}
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "编译超时"}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:2000]}

    # C++ 编译
    cmd = [arg.format(src=src, bin=binary) for arg in conf["compile_cmd"]]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=compile_tle,
            cwd=workdir,
            env=_clean_env(workdir, for_compile=True),
        )
        if r.returncode != 0:
            return {
                "ok": False,
                "msg": r.stderr.decode("utf-8", "ignore")[:2000],
            }
        return {"ok": True, "msg": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "编译超时"}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:2000]}


# ==================== 运行 ====================
def _looks_like_oom(stderr: bytes) -> bool:
    """判断进程是否因内存上限而失败（RLIMIT_AS 不会触发 SIGKILL）"""
    return any(marker in stderr for marker in _OOM_MARKERS)


def run_one(
    lang: str,
    binary: str,
    src: str,
    stdin_data: bytes,
    tle_ms: int,
    ml_mb: int,
    workdir: str,
    use_network: bool = False,
    fsize_mb: int = 0,
    nproc: int = 0,
) -> dict:
    """
    运行一次测试。
    返回 {stdout, stderr, exit_code, signal, time_ms, verdict}
    verdict: OK / TLE / MLE / RE / IE
    """
    conf = SUPPORTED.get(lang)
    if conf is None:
        return {
            "stdout": b"",
            "stderr": "不支持的语言".encode("utf-8"),
            "exit_code": -1,
            "signal": 0,
            "time_ms": 0,
            "verdict": "IE",
        }

    # 构造运行命令
    if lang == "cpp":
        cmd = [binary]
    elif lang == "py":
        cmd = [arg.format(src=src) for arg in conf["run_cmd"]]
    else:
        cmd = [binary]

    # 断网：用 unshare -n -r 包裹
    # -n: 新的网络命名空间（无网络接口）
    # -r: 映射当前用户为 root（避免权限问题）
    use_unshare = (not use_network) and _check_unshare()
    if use_unshare:
        cmd = ["unshare", "-n", "-r"] + cmd

    start = time.perf_counter()
    try:
        r = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            # wall clock 上限就是 tle_ms 本身，不再额外放宽
            timeout=tle_ms / 1000.0,
            cwd=workdir,
            env=_clean_env(workdir),
            preexec_fn=lambda: set_limits(tle_ms, ml_mb, fsize_mb, nproc),
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # 判断是否被信号杀死
        sig = 0
        exit_code = r.returncode
        if exit_code < 0:
            sig = -exit_code

        # 判定顺序：CPU 超时信号 > OOM > 其他信号 > 非零退出 > wall 超时
        if sig in (signal.SIGXCPU, signal.SIGALRM):
            verdict = "TLE"
        elif sig == signal.SIGKILL:
            # 被 OOM killer 杀掉时只可能是内存问题
            verdict = "MLE"
        elif _looks_like_oom(r.stderr):
            # RLIMIT_AS 触发的是 bad_alloc / MemoryError / SIGABRT，不是 SIGKILL
            verdict = "MLE"
        elif sig != 0:
            verdict = "RE"
        elif exit_code != 0:
            verdict = "RE"
        elif elapsed_ms > tle_ms:
            # 0 CPU 的等待（sleep、阻塞 IO）只能靠 wall clock 抓
            verdict = "TLE"
        else:
            verdict = "OK"

        return {
            "stdout": r.stdout,
            "stderr": r.stderr,
            "exit_code": exit_code,
            "signal": sig,
            "time_ms": elapsed_ms,
            "verdict": verdict,
        }

    except subprocess.TimeoutExpired as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "stdout": e.stdout or b"",
            "stderr": e.stderr or b"",
            "exit_code": -1,
            "signal": 0,
            "time_ms": elapsed_ms,
            "verdict": "TLE",
        }
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "stdout": b"",
            "stderr": str(e).encode("utf-8", "ignore"),
            "exit_code": -1,
            "signal": 0,
            "time_ms": elapsed_ms,
            "verdict": "IE",
        }


# ==================== 主评测流程 ====================
def judge(
    lang: str,
    task: str,
    player: str,
    source_path: str,
    out_path: str,
    problems_dir: str = "problems",
    use_network: bool = False,
):
    """执行完整评测流程"""

    result = {
        "player": player,
        "task": task,
        "lang": lang,
        "verdict": "IE",
        "score": 0,
        "total_score": 0,
        "tests": [],
        "compile_msg": "",
        "time_ms": 0,
        "max_time_ms": 0,
        "network_disabled": False,
        "warnings": [],
        "limits": {},
    }

    # 参数校验
    if lang not in SUPPORTED:
        result["verdict"] = "IE"
        result["compile_msg"] = f"不支持的语言: {lang}"
        _write_result(result, out_path)
        return

    if not _SAFE_NAME_RE.match(task) or not _SAFE_NAME_RE.match(player):
        result["verdict"] = "IE"
        result["compile_msg"] = "非法 task 或 player 名"
        _write_result(result, out_path)
        return

    if not os.path.exists(source_path):
        result["verdict"] = "IE"
        result["compile_msg"] = "源文件不存在"
        _write_result(result, out_path)
        return

    # 题目目录
    task_dir = os.path.join(problems_dir, task)
    if not os.path.isdir(task_dir):
        result["verdict"] = "IE"
        result["compile_msg"] = f"题目目录不存在: {task_dir}"
        _write_result(result, out_path)
        return

    # 读取 meta（题目 > 语言 > 代码保底）
    meta = load_meta(task_dir, lang)
    tle_ms = meta["tle_ms"]
    ml_mb = meta["ml_mb"]
    points = meta["points"]
    compare_mode = meta["compare"]
    compile_tle = meta["compile_tle"]
    fsize_mb = meta["fsize_mb"]
    nproc = meta["nproc"]
    result["total_score"] = points
    result["limits"] = {
        "tle_ms": tle_ms,
        "ml_mb": ml_mb,
        "compile_tle": compile_tle,
        "fsize_mb": fsize_mb,
        "nproc": nproc,
        "compare": compare_mode,
    }

    # 断网检测
    if not use_network:
        if _check_unshare():
            result["network_disabled"] = True
        else:
            detail = f": {_UNSHARE_ERR}" if _UNSHARE_ERR else ""
            result["warnings"].append(f"unshare 不可用，无法断网，已降级运行{detail}")

    # 创建临时工作目录
    with tempfile.TemporaryDirectory(prefix="judge_") as workdir:
        # 复制源文件到工作目录
        ext = SUPPORTED[lang]["ext"]
        src_in_work = os.path.join(workdir, f"main{ext}")
        shutil.copy2(source_path, src_in_work)

        # 编译
        binary = os.path.join(workdir, "main")
        compile_result = compile_src(lang, src_in_work, workdir, binary, compile_tle)
        if not compile_result["ok"]:
            result["verdict"] = "CE"
            result["compile_msg"] = compile_result["msg"]
            _write_result(result, out_path)
            return

        # 查找测试用例
        tests_dir = os.path.join(task_dir, "tests")
        if not os.path.isdir(tests_dir):
            result["verdict"] = "IE"
            result["compile_msg"] = "测试用例目录不存在"
            _write_result(result, out_path)
            return

        # 收集 .in 文件
        test_files = sorted([
            f[:-3] for f in os.listdir(tests_dir)
            if f.endswith(".in")
        ])

        if not test_files:
            result["verdict"] = "IE"
            result["compile_msg"] = "没有找到测试用例"
            _write_result(result, out_path)
            return

        # 逐个测试
        per_test_score = points / len(test_files)
        total_time = 0
        max_time = 0

        for test_name in test_files:
            in_path = os.path.join(tests_dir, f"{test_name}.in")
            ans_path = os.path.join(tests_dir, f"{test_name}.ans")

            # 读取输入
            with open(in_path, "rb") as f:
                stdin_data = f.read()

            # 读取期望输出（不存在则视为空答案）
            want = ""
            if os.path.exists(ans_path):
                with open(ans_path, "r", encoding="utf-8") as f:
                    want = f.read()

            # 运行
            run_result = run_one(
                lang=lang,
                binary=binary,
                src=src_in_work,
                stdin_data=stdin_data,
                tle_ms=tle_ms,
                ml_mb=ml_mb,
                workdir=workdir,
                use_network=use_network,
                fsize_mb=fsize_mb,
                nproc=nproc,
            )

            total_time += run_result["time_ms"]
            max_time = max(max_time, run_result["time_ms"])

            test_verdict = run_result["verdict"]
            test_score = 0.0

            if test_verdict == "OK":
                got = run_result["stdout"].decode("utf-8", "ignore")
                if compare_output(got, want, compare_mode):
                    test_verdict = "AC"
                    test_score = per_test_score
                else:
                    test_verdict = "WA"
            # TLE / MLE / RE / IE 直接沿用

            result["tests"].append({
                "name": test_name,
                "verdict": test_verdict,
                "score": round(test_score, 2),
                "time_ms": run_result["time_ms"],
                "signal": run_result["signal"],
            })

            # 非 AC 则停止（改为 continue 可跑完所有测试）
            if test_verdict != "AC":
                break

        # 计算总分
        result["score"] = round(sum(t["score"] for t in result["tests"]), 2)
        result["time_ms"] = total_time
        result["max_time_ms"] = max_time

        # 最终判定
        if result["tests"] and all(t["verdict"] == "AC" for t in result["tests"]):
            result["verdict"] = "AC"
        else:
            # 取第一个非 AC 的 verdict
            for t in result["tests"]:
                if t["verdict"] != "AC":
                    result["verdict"] = t["verdict"]
                    break

    _write_result(result, out_path)


def _write_result(result: dict, out_path: str):
    """写出结果 JSON（同时打印到标准输出）"""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ==================== CLI ====================
def main():
    parser = argparse.ArgumentParser(description="OJ 评测脚本")
    parser.add_argument("--lang", required=True, choices=list(SUPPORTED.keys()),
                        help="语言: cpp / py")
    parser.add_argument("--task", required=True, help="题目名（problems/ 下的目录名）")
    parser.add_argument("--player", required=True, help="选手名（仅字母数字下划线短横线）")
    parser.add_argument("--source", required=True, help="源文件路径")
    parser.add_argument("--out", default="out/result.json", help="结果输出路径")
    parser.add_argument("--problems", default="problems", help="题目根目录")
    parser.add_argument("--allow-network", action="store_true",
                        help="允许网络访问（默认断网）")

    args = parser.parse_args()

    judge(
        lang=args.lang,
        task=args.task,
        player=args.player,
        source_path=args.source,
        out_path=args.out,
        problems_dir=args.problems,
        use_network=args.allow_network,
    )


if __name__ == "__main__":
    main()
