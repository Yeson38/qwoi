SUPPORTED = {
    "cpp": {
        "ext": ".cpp",
        "compiled": True,
        "compile_cmd": ["g++", "-O2", "-std=c++17", "-o", "prog", "{src}"],
        "run_cmd": ["./prog"],
        "compile_tle": 30,
    },
    "py": {
        "ext": ".py",
        "compiled": False,
        "compile_cmd": None,
        "run_cmd": ["python3", "-u", "{src}"],
        "compile_tle": 0,
    },
}


def get(lang):
    if lang not in SUPPORTED:
        raise ValueError(f"不支持的语言: {lang}")
    return SUPPORTED[lang]
