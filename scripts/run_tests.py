#!/usr/bin/env python3
"""
评测入口。
用法：
  python3 scripts/run_tests.py \
      --lang cpp --task A --source submissions/cpp/xxx/A.cpp \
      --player alice --out out/result.json
"""

import argparse, json, os, subprocess, sys, time, tempfile, shutil, resource, glob
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from lang_spec import get as get_lang
import sandbox


def set_limits(tle_ms, ml_mb):
    def _apply():
        cpu = tle_ms // 1000 + 1
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (ml_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    return _apply


def normalize(text):
    lines = [l.rstrip() for l in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def compile_src(src, workdir, spec, tle=30):
    if not spec["compiled"]:
        return True, ""
    cmd = [c.replace("{src}", os.path.basename(src)) for c in spec["compile_cmd"]]
    try:
        r = subprocess.run(cmd, cwd=workdir, capture_output=True, timeout=tle)
        if r.returncode != 0:
            return False, r.stderr.decode("utf-8", "ignore")[:4000]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "编译超时"


def run_one(cmd, inp_path, out_path, tle_ms, ml_mb, workdir):
    with open(inp_path, "rb") as fin:
        start = time.perf_counter()
        try:
            r = subprocess.run(
                cmd, stdin=fin, capture_output=True,
                timeout=tle_ms / 1000 + 1.5,
                preexec_fn=set_limits(tle_ms, ml_mb),
                cwd=workdir,
            )
            elapsed = int((time.perf_counter() - start) * 1000)
        except subprocess.TimeoutExpired:
            return {"verdict": "TLE", "time_ms": tle_ms, "ok": False}
        except Exception as e:
            return {"verdict": "RE", "time_ms": 0, "ok": False, "msg": str(e)[:200]}

    got = r.stdout.decode("utf-8", "ignore")
    with open(out_path, "r") as f:
        want = f.read()

    if r.returncode != 0:
        return {"verdict": "RE", "time_ms": elapsed, "ok": False,
                "msg": r.stderr.decode("utf-8", "ignore")[:500]}

    ok = normalize(got) == normalize(want)
    return {"verdict": "AC" if ok else "WA", "time_ms": elapsed, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True, choices=["cpp", "py"])
    ap.add_argument("--task", required=True)
    ap.add_argument("--source", required=True, help="选手源码路径")
    ap.add_argument("--player", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    spec = get_lang(args.lang)
    result = {
        "lang": args.lang, "task": args.task,
        "player": args.player,
        "score": 0, "verdict": "IE", "tests": [],
    }

    prob_dir = f"problems/{args.lang}/{args.task}"
    meta_path = f"{prob_dir}/meta.yml"
    if not os.path.exists(meta_path):
        result["verdict"] = "NO_SUCH_TASK"
        return finish(result, args.out)

    with open(meta_path) as f:
        meta = yaml.safe_load(f)

    if not os.path.exists(args.source):
        result["verdict"] = "NO_SUCH_FILE"
        return finish(result, args.out)

    # 时限：优先取 meta 中对应语言的分支
    limits = meta.get("limits", {})
    if args.lang in limits:
        tle = limits[args.lang].get("tle_ms", meta.get("tle_ms", 1000))
        ml  = limits[args.lang].get("ml_mb",  meta.get("ml_mb", 512))
    else:
        tle = meta.get("tle_ms", 1000)
        ml  = meta.get("ml_mb", 512)

    points_total = meta.get("points", 100)

    workdir = tempfile.mkdtemp(prefix="judge_")
    try:
        shutil.copy(args.source, workdir)
        src_name = os.path.basename(args.source)

        ok, log = compile_src(f"{workdir}/{src_name}", workdir, spec)
        if not ok:
            result["verdict"] = "CE"
            result["compile_log"] = log
            return finish(result, args.out)

        data_files = sorted(glob.glob(f"{prob_dir}/data/*.in"))
        if not data_files:
            result["verdict"] = "NO_DATA"
            return finish(result, args.out)

        per_point = points_total / len(data_files)
        total_score = 0.0
        all_ac = True

        for inp in data_files:
            outp = inp[:-3] + ".out"
            if not os.path.exists(outp):
                continue
            tname = os.path.basename(inp)[:-3]

            if spec["compiled"]:
                cmd = [f"{workdir}/prog"]
            else:
                cmd = ["python3", "-S", "-c",
                       f"import sys; sys.path.insert(0,'scripts'); "
                       f"import sandbox; sandbox.install(); "
                       f"exec(open('{workdir}/{src_name}').read())"]

            r = run_one(cmd, inp, outp, tle, ml, workdir)
            r["test"] = tname
            r["points"] = round(per_point, 2) if r["ok"] else 0
            result["tests"].append(r)

            if r["ok"]:
                total_score += per_point
            else:
                all_ac = False
                if result["verdict"] in ("IE", "AC"):
                    result["verdict"] = r["verdict"]

        result["score"] = round(total_score, 2)
        if all_ac:
            result["verdict"] = "AC"
        finish(result, args.out)

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def finish(result, out_path):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
