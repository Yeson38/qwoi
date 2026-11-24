"""
通过 exec 注入，在选手代码运行前禁用危险模块与内置函数。
"""

GUARD_CODE = '''
import sys

_DENY = set("""os subprocess shutil pathlib socket ctypes importlib
io tempfile glob fcntl resource signal multiprocessing threading
http urllib requests pickle marshal code pty pwd grp termios
posix _posixsubprocess""".split())

class _Guard:
    def find_module(self, name, path=None):
        root = name.split(".")[0]
        if root in _DENY:
            raise ImportError(f"模块 {name} 在评测环境中被禁用")
        return None

for _m in list(sys.modules):
    if _m.split(".")[0] in _DENY and _m not in ("sys", "builtins"):
        del sys.modules[_m]

sys.meta_path.insert(0, _Guard())

import builtins
def _safe_open(file, mode="r", *a, **k):
    if any(c in mode for c in "wa+"):
        raise PermissionError("禁止写文件")
    return builtins.open(file, mode, *a, **k)

builtins.open = _safe_open
builtins.exec = None
builtins.eval = None
builtins.compile = None
builtins.__import__ = None
'''


def install():
    exec(GUARD_CODE)
