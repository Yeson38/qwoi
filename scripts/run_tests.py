#!/usr/bin/env python3
"""
评测脚本：编译/运行选手代码，比对输出，输出 JSON 结果。

修复要点：
  1. 题目路径 problems/<lang>/<task>/（补 lang 层级）
  2. 读取 meta.yml（非 config.yaml），limits 按语言嵌套取值
  3. 测试点从 <task>/data/ 目录收集
  4. 沙箱改为生成 wrapper 脚本注入，不再用 -c + exec(open())
  5. 不再禁用 input()（评测依赖 stdin 重定向）
  6. 编译/运行命令统一由 lang_config.SUPPORTED 驱动
  7. 支持 meta.yml 的 compare 策略与 points 计分
  8. SIGSEGV 判 RE（不再误判 MLE），超时显式 killpg
"""

import argparse
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# 允许作为脚本直接运行时导入同目录/上级包的模块
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lang_config import SUPPORTED  # noqa: E402

# ---------- 默认限制（meta.yml 缺失或未配置对应语言时使用） ----------
DEFAULT_TLE_MS = 2000
DEFAULT_ML_MB = 256
DEFAULT_COMPILE_TLE = 30
DEFAULT_POINTS = 100
DEFAULT_COMPARE = "line_by_line"

# 沙箱模块所在目录（scripts/）
SANDBOX_DIR = _THIS_DIR


# ==================== 资源限制 ====================
def set_limits(tle_ms: int, ml_mb: int):
    """在子进程启动前设置资源限制（preexec_fn，仅子进程生效）"""
    tle_s = max(1, int(tle_ms / 1000) + 1)
    ml_bytes = ml_mb * 1024 * 1024

    resource.setrlimit(resource.RLIMIT_CPU, (tle_s, tle_s + 1))
    resource.setrlimit(resource.RLIMIT_AS, (ml_bytes, ml_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


# ==================== 输出比对 ====================
def _split_lines(text: str):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    lines = [line.rstrip() for line in lines]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def normalize(text: str) -> str:
    """标准化输出：统一换行、去行尾空白、去末尾空行"""
    return "\n".join(_split_lines(text))


def compare_output(got: str, want: str, mode: str) -> bool:
    """
    按 compare 策略比对输出。
      full         : 原样比较
      normalize    : 去行尾空白/末尾空行后整体比较
      line_by_line : 逐行比较（行内多空格折叠）
      token        : 按空白切分为 token 序列比较
    """
    if mode == "full":
        return got == want
    if mode == "token":
        return normalize(got).split() == normalize(want).split()
    if mode == "line_by_line":
        g = [" ".join(line.split()) for line in _split_lines(got)]
        w = [" ".join(line.split()) for line in _split_lines(want)]
        return g == w
    # 默认 normalize
    return normalize(got) == normalize(want)


# ==================== 题目配置 ====================
def load_meta(problems_dir: str, lang: str) -> dict:
    """
    读取 meta.yml，按语言解析 limits。
    返回 {tle_ms, ml_mb, points, compare, title}
    """
    meta = {
        "tle_ms": DEFAULT_TLE_MS,
        "ml_mb": DEFAULT_ML_MB,
        "points": DEFAULT_POINTS,
        "compare": DEFAULT_COMPARE,
        "title": "",
    }

    meta_path = os.path.join(problems_dir, "meta.yml")
    if not os.path.exists(meta_path):
        return meta

    try:
        import yaml
        with open(meta_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return meta

    if not isinstance(cfg, dict):
        return meta

    meta["title"] = str(cfg.get("title", ""))
    meta["points"] = cfg.get("points", DEFAULT_POINTS)
    meta["compare"] = cfg.get("compare", DEFAULT_COMPARE)

    # limits 按语言分组：limits.<lang>.{tle_ms, ml_mb}
    limits = cfg.get("limits") or {}
    if isinstance(limits, dict):
        lang_limits = limits.get(lang) or {}
        if isinstance(lang_limits, dict):
            meta["tle_ms"] = lang_limits.get("tle_ms", DEFAULT_TLE_MS)
            meta["ml_mb"] = lang_limits.get("ml_mb", DEFAULT_ML_MB)

    return meta


# ==================== 编译 ====================
def compile_src(lang: str, src: str, workdir: str, binary: str) -> dict:
    """编译源码，返回 {'ok': bool, 'msg': str}"""
    conf = SUPPORTED.get(lang)
    if conf is None:
        return {"ok": False, "msg": f"不支持的语言: {lang}"}

    if lang == "cpp":
        cmd = [
            conf["compile_cmd"],
            "-O2",
            "-std=c++17",
            "-DONLINE_JUDGE",
            "-fno-asm",
            src,
            "-o", binary,
        ]
    else:
        # Python 无需编译，仅做语法检查
        cmd = [sys.executable, "-m", "py_compile", src]

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=conf.get("compile_tle", DEFAULT_COMPILE_TLE),
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
        return {"ok": False, "msg": str(e)}


def _clean_env(workdir: str) -> dict:
    """最小化环境变量，避免泄漏 CI token 等敏感信息"""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": workdir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }


# ==================== 沙箱 wrapper ====================
def write_py_wrapper(workdir: str, src_in_workdir: str) -> str:
    """
    生成 Python 运行入口 wrapper：先安装沙箱，再执行选手源码。
    相比 -c 内联，wrapper 文件方式路径稳定、可调试、不依赖 __file__ 推断。
    """
    wrapper_path = os.path.join(workdir, "__runner__.py")
    content = (
        "# -*- coding: utf-8 -*-\n"
        "import sys, os\n"
        f"sys.path.insert(0, {SANDBOX_DIR!r})\n"
        "import sandbox\n"
        "sandbox.install()\n"
        f"_SRC = {src_in_workdir!r}\n"
        "with open(_SRC, 'r', encoding='utf-8') as _f:\n"
        "    _code = _f.read()\n"
        "_g = {'__name__': '__main__', '__file__': _SRC}\n"
        "exec(compile(_code, _SRC, 'exec'), _g)\n"
    )
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(content)
    return wrapper_path


# ==================== 运行单个测试点 ====================
def run_one(cmd, inp_path, out_path, tle_ms, ml_mb, workdir, compare_mode):
    """运行一次测试，返回结果字典"""
    with open(inp_path, "rb") as fin:
        start = time.perf_counter()
        try:
            r = subprocess.run(
                cmd,
                stdin=fin,
                capture_output=True,
                timeout=tle_ms / 1000 + 5,  # 墙钟兜底
                preexec_fn=lambda: set_limits(tle_ms, ml_mb),
                cwd=workdir,
                start_new_session=True,
                env=_clean_env(workdir),
            )
            elapsed = int((time.perf_counter() - start) * 1000)
        except subprocess.TimeoutExpired as e:
            # 显式清理可能残留的进程组
            _killpg_of(e)
            return {
                "verdict": "TLE",
                "time_ms": tle_ms,
                "ok": False,
                "msg": "墙钟超时",
            }

    # ---------- 信号判定 ----------
    if r.returncode < 0:
        sig = -r.returncode
        if sig == signal.SIGKILL:
            return {"verdict": "MLE", "time_ms": elapsed, "ok": False,
                    "msg": "内存超限(SIGKILL)"}
        if sig == signal.SIGXCPU:
            return {"verdict": "TLE", "time_ms": elapsed, "ok": False,
                    "msg": "CPU时间超限(SIGXCPU)"}
        if sig == signal.SIGALRM:
            return {"verdict": "TLE", "time_ms": elapsed, "ok": False,
                    "msg": "墙钟超时(SIGALRM)"}
        if sig == signal.SIGXFSZ:
            return {"verdict": "RE", "time_ms": elapsed, "ok": False,
                    "msg": "输出文件过大(SIGXFSZ)"}
        if sig == signal.SIGSEGV:
            # 段错误更常见于越界/空指针，归为 RE；OOM 通常由 SIGKILL 捕获
            return {"verdict": "RE", "time_ms": elapsed, "ok": False,
                    "msg": "段错误/内存越界(SIGSEGV)"}
        return {"verdict": "RE", "time_ms": elapsed, "ok": False,
                "msg": f"信号 {sig}"}

    # ---------- 退出码判定 ----------
    if r.returncode != 0:
        stderr = r.stderr.decode("utf-8", "ignore")
        low = stderr.lower()
        if ("MemoryError" in stderr
                or "std::bad_alloc" in stderr
                or "out of memory" in low):
            return {"verdict": "MLE", "time_ms": elapsed, "ok": False,
                    "msg": "内存超限"}
        if "PermissionError" in stderr or "SandboxViolation" in stderr:
            return {"verdict": "RE", "time_ms": elapsed, "ok": False,
                    "msg": f"沙箱拦截: {stderr[:500]}"}
        return {"verdict": "RE", "time_ms": elapsed, "ok": False,
                "msg": stderr[:500]}

    # ---------- 输出比对 ----------
    got = r.stdout.decode("utf-8", "ignore")
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            want = f.read()
    except Exception as e:
        return {"verdict": "IE", "time_ms": elapsed, "ok": False,
                "msg": f"读取答案失败: {e}"}

    ok = compare_output(got, want, compare_mode)
    return {
        "verdict": "AC" if ok else "WA",
        "time_ms": elapsed,
        "ok": ok,
        "msg": "" if ok else "输出不匹配",
    }


def _killpg_of(exc):
    """尽力杀掉超时残留的子进程组"""
    proc = getattr(exc, "process", None)
    pid = getattr(proc, "pid", None)
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ==================== 主评测流程 ====================
def judge(lang, task, source, player, out_json):
    conf = SUPPORTED.get(lang)
    if conf is None:
        finish(out_json, 0, "IE", [], lang, task, player,
               f"不支持的语言: {lang}")
        return

    # 关键修复：题目目录含语言层级 problems/<lang>/<task>/
    problems_dir = os.path.join("problems", lang, task)
    if not os.path.isdir(problems_dir):
        finish(out_json, 0, "IE", [], lang, task, player,
               f"题目目录不存在: {problems_dir}")
        return

    # 读取 meta.yml（按语言解析 limits）
    meta = load_meta(problems_dir, lang)
    tle_ms = meta["tle_ms"]
    ml_mb = meta["ml_mb"]
    compare_mode = meta["compare"]
    total_points = meta["points"]

    # 关键修复：测试点位于 data/ 子目录
    data_dir = os.path.join(problems_dir, "data")
    if not os.path.isdir(data_dir):
        finish(out_json, 0, "IE", [], lang, task, player,
               f"测试数据目录不存在: {data_dir}")
        return

    tests = sorted(
        f for f in os.listdir(data_dir) if f.endswith(".in")
    )
    if not tests:
        finish(out_json, 0, "IE", [], lang, task, player, "无测试点")
        return

    if not os.path.exists(source):
        finish(out_json, 0, "IE", [], lang, task, player,
               f"源文件不存在: {source}")
        return

    workdir = tempfile.mkdtemp(prefix="judge_")
    try:
        src_in_workdir = os.path.join(workdir, f"main{conf['ext']}")
        shutil.copy2(source, src_in_workdir)

        # ---------- 编译 ----------
        binary = os.path.join(workdir, "prog")
        compile_result = compile_src(lang, src_in_workdir, workdir, binary)
        if not compile_result["ok"]:
            finish(out_json, 0, "CE", [], lang, task, player,
                   compile_result["msg"])
            return

        # ---------- 构建运行命令 ----------
        if lang == "cpp":
            run_cmd = [binary]
        elif lang == "py":
            wrapper = write_py_wrapper(workdir, src_in_workdir)
            run_cmd = [sys.executable, wrapper]
        else:
            finish(out_json, 0, "IE", [], lang, task, player,
                   f"不支持的语言: {lang}")
            return

        # ---------- 逐测试点运行 ----------
        results = []
        earned = 0.0
        all_ok = True
        per_test_points = float(total_points) / len(tests)

        for tf in tests:
            inp_path = os.path.join(data_dir, tf)
            out_path = os.path.join(data_dir, tf[:-3] + ".out")

            if not os.path.exists(out_path):
                results.append({
                    "test": tf,
                    "verdict": "IE",
                    "time_ms": 0,
                    "ok": False,
                    "msg": "缺少 .out 文件",
                })
                all_ok = False
                continue

            result = run_one(run_cmd, inp_path, out_path,
                             tle_ms, ml_mb, workdir, compare_mode)
            result["test"] = tf
            results.append(result)

            if result["ok"]:
                earned += per_test_points
            else:
                all_ok = False

        score = int(round(earned)) if total_points else 0

        # ---------- 最终 verdict ----------
        if all_ok:
            verdict = "AC"
        else:
            order = ["CE", "IE", "MLE", "TLE", "RE"]
            verdict = "WA"
            for v in order:
                if any(r["verdict"] == v for r in results):
                    verdict = v
                    break

        finish(out_json, score, verdict, results, lang, task, player, "")

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ==================== 结果输出 ====================
def finish(out_json, score, verdict, tests, lang, task, player, msg=""):
    result = {
        "score": score,
        "verdict": verdict,
        "tests": tests,
        "lang": lang,
        "task": task,
        "player": player,
    }
    if msg:
        result["msg"] = msg

    out_dir = os.path.dirname(out_json)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OJ Judge")
    parser.add_argument("--lang", required=True, choices=list(SUPPORTED.keys()))
    parser.add_argument("--task", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--out", default="out/result.json")
    args = parser.parse_args()

    judge(args.lang, args.task, args.source, args.player, args.out)
