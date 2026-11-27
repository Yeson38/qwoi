"""
语言配置模块
定义各语言的编译命令、运行命令、文件扩展名、默认资源限制。

配置优先级（高 → 低）：
  1. 题目配置  problems/<TASK>/meta.yml
  2. 语言配置  本文件 SUPPORTED[lang]["limits"]
  3. 代码保底  本文件末尾的 DEFAULT_* 常量

compile_cmd 使用模板占位符 {src} 和 {bin}，由 run_tests.py 替换。
limits 中的每一项都可以被 meta.yml 覆盖；数值 0 表示"不设置该限制"。
"""

SUPPORTED = {
    "cpp": {
        "ext": ".cpp",
        "compile_cmd": [
            "g++",
            "-O2",
            "-std=c++17",
            "-DONLINE_JUDGE",
            "-fno-asm",
            "-fno-inline",
            "-Wall",
            "{src}",
            "-o", "{bin}",
        ],
        "run_cmd": ["{bin}"],    # 运行命令模板
        # ---- 语言级默认限制（第 2 层配置）----
        "limits": {
            "tle_ms": 1000,      # 时间限制（毫秒，按 wall clock 判定）
            "ml_mb": 256,        # 内存限制（MB，RLIMIT_AS）
            "compile_tle": 30,   # 编译超时（秒）
            "fsize_mb": 0,       # 单文件写入上限（MB），0 = 不限制
            "nproc": 0,          # 进程/线程数上限，0 = 不限制
        },
    },
    "py": {
        "ext": ".py",
        "compile_cmd": None,     # Python 不需要编译
        # -S: 不加载 site 模块
        # -E: 忽略 PYTHON* 环境变量
        # -s: 不加载用户 site-packages
        # -B: 不写 .pyc 文件
        "run_cmd": [
            "python3",
            "-S", "-E", "-s", "-B",
            "{src}",
        ],
        # ---- 语言级默认限制（第 2 层配置）----
        "limits": {
            "tle_ms": 1000,
            "ml_mb": 256,
            "compile_tle": 30,
            "fsize_mb": 0,
            "nproc": 0,
        },
    },
}

# ==================== 第 3 层：代码保底 ====================
DEFAULT_TLE_MS = 1000       # 1 秒
DEFAULT_ML_MB = 256         # 256 MB
DEFAULT_POINTS = 100
DEFAULT_COMPARE = "normalize"
DEFAULT_COMPILE_TLE = 30    # 编译超时秒数
DEFAULT_FSIZE_MB = 0        # 0 = 不限制单文件写入
DEFAULT_NPROC = 0           # 0 = 不限制进程/线程数
