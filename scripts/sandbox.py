"""
Python 沙箱模块
通过限制 builtins 和模块导入来降低风险。

⚠️ 注意：这只能防误操作，不能防恶意逃逸。
真正安全的 Python 沙箱需要 Docker/gVisor/Firecracker 等强隔离。
当前方案配合 unshare -n 断网 + RLIMIT 资源限制，
可以应对大多数非恶意场景。
"""

import builtins
import sys
import os

# 禁止的模块前缀
_BLOCKED_MODULES = frozenset([
    "os", "sys", "subprocess", "shutil", "pathlib",
    "socket", "http", "urllib", "requests", "ftplib",
    "ctypes", "cffi", "multiprocessing", "threading",
    "importlib", "builtins", "gc", "inspect",
    "pdb", "trace", "profile", "cProfile",
    "signal", "resource", "pwd", "grp", "termios",
    "pty", "fcntl", "mmap", "tempfile",
    "webbrowser", "smtplib", "poplib", "imaplib",
    "telnetlib", "xmlrpc", "asyncio",
])

# 禁止的 builtins
_BLOCKED_BUILTINS = frozenset([
    "exec", "eval", "compile",
    "open", "input",
    "__import__",
    "breakpoint",
    "exit", "quit",
    "help",
])

# 保留的安全 builtins（白名单模式更可靠）
_SAFE_BUILTINS = frozenset([
    # 类型
    "bool", "int", "float", "complex", "str", "bytes",
    "bytearray", "list", "tuple", "dict", "set", "frozenset",
    "range", "slice", "type", "object",
    # 数学
    "abs", "divmod", "pow", "round", "min", "max", "sum",
    # 转换
    "bin", "oct", "hex", "ord", "chr", "repr", "ascii",
    "format", "hash",
    # 序列操作
    "len", "sorted", "reversed", "enumerate", "zip", "map", "filter",
    "all", "any",
    # 判断
    "isinstance", "issubclass", "callable", "hasattr", "getattr", "setattr", "delattr",
    # 迭代
    "iter", "next",
    # 异常
    "Exception", "BaseException", "ArithmeticError", "AssertionError",
    "AttributeError", "EOFError", "IndexError", "KeyError",
    "LookupError", "MemoryError", "NameError", "NotImplementedError",
    "OverflowError", "RecursionError", "RuntimeError", "StopIteration",
    "SyntaxError", "SystemError", "TypeError", "ValueError", "ZeroDivisionError",
    "Warning", "UserWarning", "DeprecationWarning",
    # 常量
    "True", "False", "None", "NotImplemented", "Ellipsis",
    # 打印
    "print",
    # 变量
    "globals", "locals", "vars", "dir", "id",
    # 函数
    "staticmethod", "classmethod", "property", "super",
])


def _forbidden(*args, **kwargs):
    raise PermissionError("沙箱禁止此操作")


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """受限的 __import__：只允许安全模块"""
    root = name.split(".")[0]
    if root in _BLOCKED_MODULES:
        raise ImportError(f"沙箱禁止导入模块: {name}")
    # 允许标准库中的安全模块
    return builtins.__import__(name, globals, locals, fromlist, level)


def _safe_open(*args, **kwargs):
    """受限的 open：只允许读写当前工作目录下的文件"""
    if len(args) >= 1:
        path = str(args[0])
    elif "file" in kwargs:
        path = str(kwargs["file"])
    else:
        raise PermissionError("沙箱: open 需要文件路径参数")

    # 禁止绝对路径和上级目录
    if path.startswith("/") or ".." in path:
        raise PermissionError(f"沙箱禁止访问路径: {path}")

    # 禁止写模式（选手不应该写文件）
    mode = kwargs.get("mode", args[1] if len(args) >= 2 else "r")
    if any(c in str(mode) for c in ("w", "a", "x", "+")):
        raise PermissionError(f"沙箱禁止写文件: {path}")

    return builtins.open(path, *args[1:], **kwargs)


def install():
    """安装沙箱：替换危险 builtins"""
    # 禁用危险函数
    for name in _BLOCKED_BUILTINS:
        if hasattr(builtins, name):
            setattr(builtins, name, _forbidden)

    # 用受限版本替换
    builtins.__import__ = _safe_import
    builtins.open = _safe_open

    # 禁止通过 sys 模块逃逸
    if hasattr(sys, "_getframe"):
        sys._getframe = _forbidden

    # 限制 sys.path，防止加载任意模块
    # 只保留当前目录和最小必要路径
    safe_path = []
    for p in sys.path:
        real = os.path.realpath(p) if p else os.getcwd()
        # 保留标准库路径，去掉用户目录和 site-packages
        if "site-packages" not in real and "dist-packages" not in real:
            if "user" not in real:
                safe_path.append(p)
    sys.path[:] = safe_path

    # 限制 sys.modules 中已有的危险模块
    for mod_name in list(sys.modules.keys()):
        root = mod_name.split(".")[0]
        if root in _BLOCKED_MODULES:
            del sys.modules[mod_name]
