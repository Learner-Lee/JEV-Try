#!/usr/bin/env python3
"""
Jev 可视化面板的本地服务端。

API key 只存在于这个进程里 —— 浏览器拿到的永远只有评估结果。
TypeSafe 文档明确要求 Web 应用把凭据留在服务端。

用法:
    export TYPESAFE_API_KEY=...
    ./.venv/bin/python server.py          # 默认 http://127.0.0.1:8000
    ./.venv/bin/python server.py --port 9000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient, TypeSafeError

from jev_demo import INCIDENT_EN, INCIDENT_ZH, USD_PER_INPUT_TOKEN, build_questions

WEB_ROOT = Path(__file__).parent / "web"

# weights 视图用的三个维度（与 jev_demo.py 的 PROFILES 对应）
WEIGHT_DIMS = {
    "q_timeline": Score(
        instructions="How complete and specific is the timeline in this report?",
        criteria=["No timeline given.", "Vague ordering of events, no times.",
                  "Times given for the major milestones only.",
                  "Timestamped entries covering detection, diagnosis and resolution."],
    ),
    "q_rootcause": Score(
        instructions="How precisely does this report identify the root cause?",
        criteria=["No root cause stated.", "A vague area or component is blamed.",
                  "The specific mechanism is named.",
                  "The specific mechanism is named and it explains why existing "
                  "safeguards did not catch it."],
    ),
    "q_actions": Score(
        instructions="How actionable are the follow-up items in this report?",
        criteria=["No action items.", "Vague intentions with no owner or date.",
                  "Concrete actions, but missing owners or dates.",
                  "Concrete actions with named owners and due dates."],
    ),
}

# 哪些问题是投机提问，以及它们依赖哪个前置判断（前端据此做「采纳/丢弃」的视觉区分）
SPECULATIVE = {
    "had_canary": "category == config_error",
    "caught_by_validation": "category == config_error",
    "needs_exec_review": "severity >= 2.0",
    "regulatory_exposure": "data_loss > 0.5",
}


def answer_to_dict(qid: str, ans, question) -> dict:
    """把 SDK 的答案对象转成前端能直接画的结构。"""
    out = {
        "id": qid,
        "type": ans.type,
        "instructions": _instr_of(question),
        "speculative": SPECULATIVE.get(qid),
    }
    if ans.type == "noul":
        out["value"] = ans.noul
        out["confidence"] = None
        out["bars"] = [
            {"label": "yes", "p": ans.noul},
            {"label": "no", "p": 1 - ans.noul},
        ]
        out["top"] = "yes" if ans.noul >= 0.5 else "no"
    elif ans.type == "choice":
        out["value"] = ans.choice
        out["confidence"] = ans.confidence
        out["bars"] = [{"label": k, "p": v} for k, v in
                       sorted(ans.probabilities.items(), key=lambda kv: -kv[1])]
        out["top"] = ans.choice
    elif ans.type == "score":
        out["value"] = ans.score
        out["confidence"] = ans.confidence
        out["levels"] = len(ans.legend)
        out["bars"] = [{"label": f"{i} · {ans.legend[i]}", "p": ans.probabilities.get(i, 0.0)}
                       for i in sorted(ans.legend)]
        out["top"] = max(ans.probabilities.items(), key=lambda kv: kv[1])[0]
        out["legend"] = {str(k): v for k, v in ans.legend.items()}
    return out


def _instr_of(question) -> str:
    instr = getattr(question, "instructions", "")
    return instr if isinstance(instr, str) else json.dumps(instr, ensure_ascii=False)


def cost_of(tokens: int | None) -> float:
    return (tokens or 0) * USD_PER_INPUT_TOKEN


class Api:
    def __init__(self, client: TypeSafeClient):
        self.client = client

    # ── 一次请求问完所有题 ──
    def evaluate(self, body: dict) -> dict:
        state = body.get("state") or INCIDENT_EN
        questions = build_questions()

        t0 = time.perf_counter()
        r = self.client.system_one(state=state, questions=questions)
        elapsed = time.perf_counter() - t0

        answers = [answer_to_dict(qid, r.answers[qid], q) for qid, q in questions.items()]
        return {
            "model": r.model,
            "answers": answers,
            "usage": {"input_tokens": r.usage.input_tokens,
                      "output_tokens": r.usage.output_tokens},
            "cost": cost_of(r.usage.input_tokens),
            "latency": elapsed,
            "calls": 1,
        }

    # ── 3 个维度打一次分，权重交给前端 ──
    def dimensions(self, body: dict) -> dict:
        state = body.get("state") or INCIDENT_EN

        t0 = time.perf_counter()
        r = self.client.system_one(state=state, questions=WEIGHT_DIMS)
        elapsed = time.perf_counter() - t0

        dims = []
        for qid, q in WEIGHT_DIMS.items():
            a = r.answers[qid]
            levels = len(a.legend)
            dims.append({
                "id": qid,
                "score": a.score,
                "confidence": a.confidence,
                "levels": levels,
                "normalised": a.score / (levels - 1),
                "instructions": _instr_of(q),
                "legend": {str(k): v for k, v in a.legend.items()},
            })
        return {
            "model": r.model, "dimensions": dims,
            "usage": {"input_tokens": r.usage.input_tokens},
            "cost": cost_of(r.usage.input_tokens), "latency": elapsed, "calls": 1,
        }

    # ── 打包 vs 逐题，可重复多轮以区分噪声 ──
    def bench(self, body: dict) -> dict:
        state = body.get("state") or INCIDENT_EN
        repeats = max(1, min(5, int(body.get("repeats", 1))))
        questions = build_questions()
        n = len(questions)

        batched_runs, serial_runs = [], []

        for _ in range(repeats):
            t0 = time.perf_counter()
            r = self.client.system_one(state=state, questions=questions)
            batched_runs.append({
                "latency": time.perf_counter() - t0,
                "cost": cost_of(r.usage.input_tokens),
                "tokens": r.usage.input_tokens,
                "values": {q: _scalar(a) for q, a in r.answers.items()},
            })

        for _ in range(repeats):
            cost, values, t0 = 0.0, {}, time.perf_counter()
            tokens = 0
            for qid, q in questions.items():
                one = self.client.system_one(state=state, questions={qid: q})
                cost += cost_of(one.usage.input_tokens)
                tokens += one.usage.input_tokens or 0
                values[qid] = _scalar(one.answers[qid])
            serial_runs.append({"latency": time.perf_counter() - t0, "cost": cost,
                                "tokens": tokens, "values": values})

        # 组内噪声（同一方式重复之间的极差）vs 组间差异（两种方式的均值之差）
        noise, between = {}, {}
        for qid in questions:
            b_vals = [run["values"][qid] for run in batched_runs]
            s_vals = [run["values"][qid] for run in serial_runs]
            noise[qid] = max(max(b_vals) - min(b_vals), max(s_vals) - min(s_vals))
            between[qid] = abs(_mean(b_vals) - _mean(s_vals))

        return {
            "n_questions": n,
            "repeats": repeats,
            "batched": {"calls": 1,
                        "cost": _mean([r["cost"] for r in batched_runs]),
                        "latency": _mean([r["latency"] for r in batched_runs]),
                        "tokens": _mean([r["tokens"] or 0 for r in batched_runs])},
            "serial": {"calls": n,
                       "cost": _mean([r["cost"] for r in serial_runs]),
                       "latency": _mean([r["latency"] for r in serial_runs]),
                       "tokens": _mean([r["tokens"] for r in serial_runs])},
            "noise": noise,
            "between": between,
        }

    def samples(self, _body: dict) -> dict:
        return {"en": INCIDENT_EN, "zh": INCIDENT_ZH}


def _scalar(ans) -> float:
    return ans.noul if ans.type == "noul" else (
        ans.score if ans.type == "score" else ans.confidence)


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def make_handler(api: Api):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB_ROOT), **kw)

        def log_message(self, fmt, *args):  # 静音静态资源日志
            if "/api/" in (args[0] if args else ""):
                sys.stderr.write("  %s\n" % (fmt % args))

        def do_POST(self):
            route = self.path.rstrip("/").removeprefix("/api/")
            handler = {"evaluate": api.evaluate, "dimensions": api.dimensions,
                       "bench": api.bench, "samples": api.samples}.get(route)
            if handler is None:
                return self._json({"error": f"unknown route {self.path}"}, 404)

            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or "{}")
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": f"bad request body: {exc}"}, 400)

            try:
                print(f"  → {route}", file=sys.stderr)
                return self._json(handler(body))
            except TypeSafeError as exc:
                traceback.print_exc()
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 502)
            except Exception as exc:  # noqa: BLE001 - 本地工具，回传给前端便于排查
                traceback.print_exc()
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _json(self, payload: dict, status: int = 200):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> int:
    p = argparse.ArgumentParser(description="Jev 可视化面板")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--model", default=None, help="默认 jev-latest")
    args = p.parse_args()

    if not os.environ.get("TYPESAFE_API_KEY"):
        print("未设置 TYPESAFE_API_KEY\n"
              "  去 https://console.typesafe.ai/keys 拿一个，然后：\n"
              "  export TYPESAFE_API_KEY=...", file=sys.stderr)
        return 1

    kwargs = {"model": args.model} if args.model else {}
    with TypeSafeClient(**kwargs) as client:
        server = HTTPServer((args.host, args.port), make_handler(Api(client)))
        url = f"http://{args.host}:{args.port}"
        print(f"Jev 面板  {url}\n"
              f"  API key 只留在这个进程里，浏览器只拿结果。\n"
              f"  Ctrl-C 停止。\n", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
