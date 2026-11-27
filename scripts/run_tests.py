#!/usr/bin/env python3
"""
OJ 评测脚本
支持 C++ 和 Python，含资源限制、断网、信号判定。

断网方案：使用 unshare -n -r 创建独立网络命名空间。
如果 unshare 不可用，会降级为无断网模式并在结果中记录警告。
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

from lang_config import (
    SUPPORTED,
    DEFAULT_TLE_MS,
    DEFAULT_ML_MB,
    DEFAULT_POINTS,
    DEFAULT_COMPARE,
    DEFAULT_COMPILE_TLE,
)

# 安全字符校验正则
_SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')

# 检测 unshare 是否可用（用于断网）
_HAS_UNSHARE = None


def _check_unshare() -> bool:
    """检测 unshare -n -r 是否可用"""
    global _HAS_UNSHARE
    if _HAS_UNSHARE is not None:
        return _HAS_UNSHARE
    try:
        r = subprocess.run(
            ["unshare", "-n", "-r", "true"],
            capture_output=True,
            timeout=5,
        )
        _HAS_UNSHARE = (r.returncode == 0)
    except Exception:
        _HAS_UNSHARE = False
    return _HAS_UNSHARE


# ==================== 资源限制 ====================
def set_limits(tle_ms: int, ml_mb: int):
    """在子进程 exec 前设置资源限制（通过 preexec_fn 调用）"""
    import resource

    # CPU 时间限制（秒），向上取整
    cpu_sec = (tle_ms + 999) // 1000
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec + 1))

    # 地址空间限制
    ml_bytes = ml_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (ml_bytes, ml_bytes))

    # 文件大小限制（禁止写大文件）
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))

    # 禁止创建子进程（防止 fork 炸弹）
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

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
      - normalize:  去掉行尾空格和末尾空行后比较
      - line_by_line: 逐行比较，行内连续空白折叠为单个空格
      - token:      按空白分词后逐 token 比较
    """
    if mode == "full":
        return got == want

    if mode == "normalize":
        g = "\n".join(line.rstrip() for line in _split_lines(got))
        w = "\n".join(line.rstrip() for line in _split_lines(want))
        return g == w

    if mode == "line_by_line":
        g = [" ".join(line.split()) for line in _split_lines(got)]
        w = [" ".join(line.split()) for line in _split_lines(want)]
        return g == w

    if mode == "token":
        return got.split() == want.split()

    # 默认 normalize
    g = "\n".join(line.rstrip() for line in _split_lines(got))
    w = "\n".join(line.rstrip() for line in _split_lines(want))
    return g == w


# ==================== meta.yml 加载 ====================
def load_meta(problems_dir: str, lang: str) -> dict:
    """
    读取 meta.yml，返回限制配置。
    支持语言级别覆盖：
      tle_ms / ml_mb / points / compare 可以是标量或 dict（按语言分）。
    """
    meta = {
        "tle_ms": DEFAULT_TLE_MS,
        "ml_mb": DEFAULT_ML_MB,
        "points": DEFAULT_POINTS,
        "compare": DEFAULT_COMPARE,
    }

    meta_path = os.path.join(problems_dir, "meta.yml")
    if not os.path.exists(meta_path):
        return meta

    try:
        import yaml
        with open(meta_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[WARN] 读取 meta.yml 失败: {e}", file=sys.stderr)
        return meta

    def _resolve(key, default):
        val = cfg.get(key, default)
        if isinstance(val, dict):
            val = val.get(lang, default)
        return val

    try:
        meta["tle_ms"] = int(_resolve("tle_ms", DEFAULT_TLE_MS))
    except (ValueError, TypeError):
        pass
    try:
        meta["ml_mb"] = int(_resolve("ml_mb", DEFAULT_ML_MB))
    except (ValueError, TypeError):
        pass
    try:
        meta["points"] = int(_resolve("points", DEFAULT_POINTS))
    except (ValueError, TypeError):
        pass

    cmp_val = _resolve("compare", DEFAULT_COMPARE)
    if isinstance(cmp_val, str) and cmp_val in ("full", "normalize", "line_by_line", "token"):
        meta["compare"] = cmp_val

    return meta


# ==================== 编译 ====================
def compile_src(lang: str, src: str, workdir: str, binary: str) -> dict:
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
                timeout=DEFAULT_COMPILE_TLE,
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

---
刚才的回答被截断了，需要我继续输出 `scripts/judge.py` 的剩余部分和 `requirements.txt` 吗？
