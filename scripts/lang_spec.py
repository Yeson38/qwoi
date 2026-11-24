SUPPORTED = {
    "cpp": {
        "ext": ".cpp",
        "compiled": True,
        "compile_cmd": ["g++", "-O2", "-std=c++17", "-o", "prog", "{src}"],
        "run_cmd": ["./prog"],
        # C++ 快，时限按题面给
        "tle_scale": 1.0,
        "ml_scale": 1.0,
    },
    "py": {
        "ext": ".py",
        "compiled": False,
        "compile_cmd": None,
        "run_cmd": ["python3", "-u", "{src}"],
        # Python 解释器开销大，时限放宽 3~5 倍，内存放宽 2 倍
        "tle_scale": 4.0,
        "ml_scale": 2.0,
    },
}
