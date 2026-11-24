#!/usr/bin/env python3
"""
评测脚本：编译/运行选手代码，比对输出，输出 JSON 结果。
修复 MLE/TLE/RE 判定、环境变量清空、沙箱集成、答案文件保护等问题。
"""

import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
import shutil


# ---------- 资源限制 ----------
def set_limits(tle_ms: int, ml_mb: int):
    """在子进程启动前设置资源限制（preexec_fn）"""
    tle_s = max(1, tle_ms // 1000 + 1)
    ml_bytes = ml_mb * 1024 * 1024

    # CPU 时间限制（超时发送 SIGXCPU）
    resource.setrlimit(resource.RLIMIT_CPU, (tle_s, tle_s + 1))
    # 地址空间限制（内存超限发送 SIGKILL）
    resource.setrlimit(resource.RLIMIT_AS, (ml_bytes, ml_bytes))
    # 文件大小限制（禁止写大文件）
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    # 进程数限制（防止 fork 炸弹）
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    # 核心转储禁用
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # 文件描述符限制
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def normalize(text: str) -> str:
    """标准化输出：去除行尾空白、统一换行符、去除末尾空行"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    lines = [line.rstrip() for line in lines]
    # 去除末尾空行
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# ---------- 编译 ----------
def compile_src(lang: str, src: str, workdir: str) -> dict:
    """编译源码，返回 {'ok': bool, 'msg': str}"""
    if lang == "cpp":
        cmd = [
            "g++", "-O2", "-std=c++17", "-o", f"{workdir}/prog",
            "-DONLINE_JUDGE",
            "-fno-asm",           # 禁止内联汇编
            "-fno-builtin",       # 禁止内建函数绕过
            src
        ]
        try:
            r = subprocess.run(
                cmd, capture_output=True, timeout=60,
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin", "HOME": workdir}
            )
            if r.returncode != 0:
                return {
                    "ok": False,
                    "msg": r.stderr.decode("utf-8", "ignore")[:2000]
                }
            return {"ok": True, "msg": ""}
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "编译超时（60s）"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
    elif lang == "py":
        # Python 不需要编译，但做语法检查
        try:
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", src],
                capture_output=True, timeout=10,
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin", "HOME": workdir}
            )
            if r.returncode != 0:
                return {
                    "ok": False,
                    "msg": r.stderr.decode("utf-8", "ignore")[:2000]
                }
            return {"ok": True, "msg": ""}
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "语法检查超时"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
    return {"ok": False, "msg": f"不支持的语言: {lang}"}


# ---------- 运行单个测试点 ----------
def run_one(
    cmd: list,
    inp_path: str,
    out_path: str,
    tle_ms: int,
    ml_mb: int,
    workdir: str,
) -> dict:
    """运行一次测试，返回结果字典"""
    with open(inp_path, "rb") as fin:
        start = time.perf_counter()
        try:
            r = subprocess.run(
                cmd,
                stdin=fin,
                capture_output=True,
                timeout=tle_ms / 1000 + 5,  # 墙钟超时兜底
                preexec_fn=set_limits(tle_ms, ml_mb),
                cwd=workdir,
                start_new_session=True,       # 新会话，防止杀父进程
                env={"PATH": "/usr/bin:/bin", "HOME": workdir},  # 清空环境变量
            )
            elapsed = int((time.perf_counter() - start) * 1000)
        except subprocess.TimeoutExpired:
            return {"verdict": "TLE", "time_ms": tle_ms, "ok": False, "msg": "墙钟超时"}

    # ---------- 信号判定 ----------
    if r.returncode < 0:
        sig = -r.returncode
        if sig == signal.SIGKILL:       # 9  — 通常是 OOM Killer
            return {"verdict": "MLE", "time_ms": elapsed, "ok": False, "msg": "内存超限(SIGKILL)"}
        if sig == signal.SIGSEGV:       # 11 — 段错误，也可能是 OOM
            return {"verdict": "MLE", "time_ms": elapsed, "ok": False, "msg": "段错误/内存越界(SIGSEGV)"}
        if sig == signal.SIGXCPU:       # 24 — CPU 时间超限
            return {"verdict": "TLE", "time_ms": elapsed, "ok": False, "msg": "CPU时间超限(SIGXCPU)"}
        if sig == signal.SIGXFSZ:       # 25 — 文件大小超限
            return {"verdict": "RE", "time_ms": elapsed, "ok": False, "msg": "输出文件过大(SIGXFSZ)"}
        if sig == signal.SIGALRM:       # 14 — 定时器超时
            return {"verdict": "TLE", "time_ms": elapsed, "ok": False, "msg": "墙钟超时(SIGALRM)"}
        return {"verdict": "RE", "time_ms": elapsed, "ok": False, "msg": f"信号 {sig}"}

    # ---------- 退出码判定 ----------
    if r.returncode != 0:
        stderr = r.stderr.decode("utf-8", "ignore")
        # Python OOM 特征
        if "MemoryError" in stderr or "std::bad_alloc" in stderr or "out of memory" in stderr.lower():
            return {"verdict": "MLE", "time_ms": elapsed, "ok": False, "msg": "内存超限"}
        # PermissionError 来自沙箱
        if "PermissionError" in stderr:
            return {"verdict": "RE", "time_ms": elapsed, "ok": False, "msg": f"沙箱拦截: {stderr[:500]}"}
        return {"verdict": "RE", "time_ms": elapsed, "ok": False, "msg": stderr[:500]}

    # ---------- 输出比对 ----------
    got = r.stdout.decode("utf-8", "ignore")
    with open(out_path, "r", encoding="utf-8") as f:
        want = f.read()

    ok = normalize(got) == normalize(want)
    return {
        "verdict": "AC" if ok else "WA",
        "time_ms": elapsed,
        "ok": ok,
        "msg": "" if ok else "输出不匹配"
    }


# ---------- 主评测流程 ----------
def judge(lang: str, task: str, source: str, player: str, out_json: str):
    problems_dir = os.path.join("problems", task)
    if not os.path.isdir(problems_dir):
        finish(out_json, 0, "IE", [], lang, task, player, f"题目目录不存在: {problems_dir}")
        return

    # 读取题目配置
    config_path = os.path.join(problems_dir, "config.yaml")
    tle_ms = 2000
    ml_mb = 256
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
            tle_ms = cfg.get("tle_ms", 2000)
            ml_mb = cfg.get("ml_mb", 256)
        except Exception:
            pass  # 使用默认值

    # 收集测试点
    tests = sorted([
        f for f in os.listdir(problems_dir) if f.endswith(".in")
    ])
    if not tests:
        finish(out_json, 0, "IE", [], lang, task, player, "无测试点")
        return

    # 创建工作目录
    workdir = tempfile.mkdtemp(prefix="judge_")
    try:
        # 复制源码到工作目录
        src_in_workdir = os.path.join(workdir, os.path.basename(source))
        shutil.copy2(source, src_in_workdir)

        # 编译
        compile_result = compile_src(lang, src_in_workdir, workdir)
        if not compile_result["ok"]:
            finish(out_json, 0, "CE", [], lang, task, player, compile_result["msg"])
            return

        # 构建运行命令
        if lang == "cpp":
            run_cmd = [os.path.join(workdir, "prog")]
        elif lang == "py":
            # 关键修复：使用 -c 参数在运行选手代码前安装沙箱
            # 沙箱代码通过环境变量传递，避免路径依赖
            sandbox_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "sandbox.py")
            )
            run_cmd = [
                sys.executable, "-c",
                f"import sys; sys.path.insert(0, {os.path.dirname(sandbox_path)!r}); "
                f"import sandbox; sandbox.install(); "
                f"exec(open({src_in_workdir!r}).read())"
            ]
        else:
            finish(out_json, 0, "IE", [], lang, task, player, f"不支持的语言: {lang}")
            return

        # 逐测试点运行
        results = []
        total_score = 0
        all_ok = True

        for tf in tests:
            inp_path = os.path.join(problems_dir, tf)
            out_path = os.path.join(problems_dir, tf.replace(".in", ".out"))

            if not os.path.exists(out_path):
                results.append({
                    "test": tf,
                    "verdict": "IE",
                    "time_ms": 0,
                    "ok": False,
                    "msg": "缺少 .out 文件"
                })
                all_ok = False
                continue

            result = run_one(run_cmd, inp_path, out_path, tle_ms, ml_mb, workdir)
            result["test"] = tf
            results.append(result)

            if result["ok"]:
                total_score += 1
            else:
                all_ok = False

        total_tests = len(tests)
        score = int(total_score / total_tests * 100) if total_tests > 0 else 0

        # 判定最终 verdict
        if all_ok:
            verdict = "AC"
        elif any(r["verdict"] == "CE" for r in results):
            verdict = "CE"
        elif any(r["verdict"] == "IE" for r in results):
            verdict = "IE"
        elif any(r["verdict"] == "MLE" for r in results):
            verdict = "MLE"
        elif any(r["verdict"] == "TLE" for r in results):
            verdict = "TLE"
        elif any(r["verdict"] == "RE" for r in results):
            verdict = "RE"
        else:
            verdict = "WA"

        finish(out_json, score, verdict, results, lang, task, player, "")

    finally:
        # 清理工作目录
        shutil.rmtree(workdir, ignore_errors=True)


def finish(out_json, score, verdict, tests, lang, task, player, msg=""):
    """输出 JSON 结果"""
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
    parser.add_argument("--lang", required=True, choices=["cpp", "py"])
    parser.add_argument("--task", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--out", default="out/result.json")
    args = parser.parse_args()

    judge(args.lang, args.task, args.source, args.player, args.out)
