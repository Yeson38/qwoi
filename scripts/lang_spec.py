"""
语言配置模块
定义各语言的编译命令、运行命令、文件扩展名等。
compile_cmd 使用模板占位符 {src} 和 {bin}，由 judge.py 替换。
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
        "compile_tle": 30,       # 编译超时（秒）
        "run_cmd": ["{bin}"],    # 运行命令模板
    },
    "py": {
        "ext": ".py",
        "compile_cmd": None,     # Python 不需要编译
        "compile_tle": 0,
        # -S: 不加载 site 模块
        # -E: 忽略 PYTHON* 环境变量
        # -s: 不加载用户 site-packages
        # -B: 不写 .pyc 文件
        "run_cmd": [
            "python3",
            "-S", "-E", "-s", "-B",
            "{src}",
        ],
    },
}

# 默认限制（meta.yml 未指定时使用）
DEFAULT_TLE_MS = 1000       # 1 秒
DEFAULT_ML_MB = 256         # 256 MB
DEFAULT_POINTS = 100
DEFAULT_COMPARE = "normalize"
DEFAULT_COMPILE_TLE = 30    # 编译超时秒数
