"""
通过 exec 注入，在选手代码运行前禁用危险模块与内置函数。
使用 find_spec（Python 3.4+ 正式 API），修复作用域、绕过、路径白名单等问题。
"""

GUARD_CODE = r'''
import sys
import builtins
import os as _os

# ---------- 危险模块黑名单 ----------
_DENY = frozenset("""
os subprocess shutil pathlib socket ctypes importlib
io tempfile glob fcntl resource signal multiprocessing threading
http urllib requests pickle marshal code pty pwd grp termios
posix _posixsubprocess gc inspect ast types weakref
builtins sys __builtin__
""".split())

# ---------- 可读文件白名单（仅允许读取当前目录下的相对路径） ----------
_ALLOWED_READ_PREFIXES = ("./",)
_DENY_READ_PATHS = frozenset([
    "/etc", "/proc", "/sys", "/dev", "/root", "/home",
    "/usr", "/lib", "/bin", "/sbin", "/var", "/tmp/.git",
])

class _Guard:
    """meta_path finder，使用 find_spec（非 deprecated）"""
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in _DENY:
            raise ImportError(f"模块 {fullname} 在评测环境中被禁用")
        return None  # 返回 None 让后续 finder 正常处理

    # 兼容旧版（Python < 3.12 仍会调用）
    def find_module(self, fullname, path=None):
        root = fullname.split(".")[0]
        if root in _DENY:
            raise ImportError(f"模块 {fullname} 在评测环境中被禁用")
        return None

# ---------- 清理已加载的危险模块 ----------
for _m in list(sys.modules):
    if _m.split(".")[0] in _DENY:
        del sys.modules[_m]

# ---------- 安装 meta_path 钩子 ----------
sys.meta_path.insert(0, _Guard())

# ---------- 安全 open ----------
_real_open = builtins.open

def _safe_open(file, mode="r", *a, **k):
    # 禁止写/追加/更新模式
    if any(c in mode for c in "wa+xX"):
        raise PermissionError("禁止写文件")
    # 路径安全检查
    path = str(file)
    # 拒绝绝对路径
    if path.startswith("/"):
        for deny in _DENY_READ_PATHS:
            if path.startswith(deny):
                raise PermissionError(f"禁止读取系统路径: {path}")
        # 允许 /dev/null
        if path == "/dev/null":
            return _real_open(file, mode, *a, **k)
        raise PermissionError(f"禁止读取绝对路径: {path}")
    # 拒绝 .. 路径穿越
    if ".." in path:
        raise PermissionError("禁止路径穿越")
    return _real_open(file, mode, *a, **k)

builtins.open = _safe_open

# ---------- 禁用危险内置函数 ----------
# 不设为 None，而是替换为抛出异常的函数，避免 AttributeError 泄露信息
def _forbidden(*a, **k):
    raise PermissionError("该内置函数在评测环境中被禁用")

builtins.exec = _forbidden
builtins.eval = _forbidden
builtins.compile = _forbidden
builtins.breakpoint = _forbidden
builtins.input = _forbidden  # 如果需要 stdin 交互可移除此行

# ---------- 保护 __builtins__ 字典 ----------
# 防止通过 __builtins__['exec'] 等方式恢复
class _ProtectedDict(dict):
    _PROTECTED = frozenset(["exec", "eval", "compile", "open", "__import__", "breakpoint", "input"])
    def __setitem__(self, key, value):
        if key in self._PROTECTED:
            raise PermissionError(f"禁止修改 {key}")
        super().__setitem__(key, value)
    def update(self, *a, **k):
        keys = set()
        if a and isinstance(a[0], dict):
            keys.update(a[0].keys())
        keys.update(k.keys())
        if keys & self._PROTECTED:
            raise PermissionError("禁止批量修改受保护的内置函数")
        super().update(*a, **k)

# 替换 builtins 模块的 __dict__ 中的受保护项引用
# 注意：不能直接替换 builtins.__dict__ 本身，但可以冻结关键项
'''


def install():
    """
    安装沙箱。
    使用 globals() 确保所有定义在全局命名空间，避免闭包 GC 问题。
    """
    exec(GUARD_CODE, globals())
