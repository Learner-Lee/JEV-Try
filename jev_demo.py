#!/usr/bin/env python3
"""
Jev 能力演示 —— 用一份事故复盘报告，展示 TypeSafe System One 模型的四个核心优势。

    fanout   一次请求问完 13 道题（含投机提问），代码事后挑相关的
    bench    打包 vs 拆开：成本 / 延迟 / 答案一致性的实测对比
    weights  同一次推理喂两套权重，得到两种排序，无需重新调用
    gate     置信度三档门控：自动执行 / 人工确认 / 转人工

用法:
    export TYPESAFE_API_KEY=...
    ./.venv/bin/python jev_demo.py fanout
    ./.venv/bin/python jev_demo.py bench
    ./.venv/bin/python jev_demo.py all --zh
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

# jev-1.13: $42 / Btok 输入，输出免费
USD_PER_INPUT_TOKEN = 42.0 / 1_000_000_000

DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
OFF = "\033[0m"


# ── 被评估的材料 ────────────────────────────────────────────────────────────

INCIDENT_EN = """
INCIDENT REPORT INC-2291 — Checkout API returning 503 for EU customers

Summary
On 14 March, between 09:12 and 11:47 UTC, the checkout API returned 503 to
roughly 34% of requests originating from our eu-west-1 region. Customers in
the EU could not complete purchases for approximately two and a half hours.
North American traffic was unaffected throughout.

Timeline
09:12  Deploy of checkout-service v4.18.2 begins rolling out to eu-west-1.
09:19  Error rate alarm fires. On-call acknowledges at 09:24.
09:40  On-call suspects the new connection-pool settings, begins investigating.
10:15  Escalated to the platform team after initial hypothesis is ruled out.
10:52  Root cause identified: the new release lowered the pool ceiling from
       200 to 20 connections, a typo in the Helm values file.
11:20  Rollback to v4.18.1 initiated.
11:47  Error rate returns to baseline. Incident declared resolved.

Root cause
A configuration typo. The Helm values file for eu-west-1 set
`connectionPool.maxSize: 20` where `200` was intended. The value passed schema
validation because 20 is a legal integer within the allowed range. No canary
stage existed for this region, so the change went to 100% of eu-west-1 at once.

Impact
An estimated 11,400 checkout attempts failed. Support received 217 tickets.
No customer data was lost or exposed. No payment was double-charged; the
payment provider rejected the incomplete sessions cleanly.

What went well
The alarm fired within seven minutes. The rollback, once initiated, was clean
and took under thirty minutes to fully propagate.

What went poorly
It took 100 minutes from the first alarm to identifying a one-character
configuration error. The on-call engineer spent 35 minutes on a hypothesis
that a diff of the release would have ruled out in two.

Action items
- Add a canary stage for eu-west-1 (owner: platform, due 28 March)
- Alert on connection-pool utilisation, not just error rate (owner: checkout)
- Add a range assertion to the Helm schema so pool sizes below 50 fail CI
""".strip()

INCIDENT_ZH = """
事故报告 INC-2291 —— 结账 API 对欧洲客户返回 503

概述
3 月 14 日 09:12 至 11:47 UTC 期间，结账 API 对来自 eu-west-1 区域约 34% 的请求返回
503。欧洲客户约两个半小时内无法完成purchase。北美流量全程未受影响。

时间线
09:12  checkout-service v4.18.2 开始向 eu-west-1 灰度发布。
09:19  错误率告警触发。值班工程师 09:24 认领。
09:40  值班怀疑是新的连接池配置，开始排查。
10:15  初始假设被排除后升级给平台团队。
10:52  定位根因：新版本把连接池上限从 200 降到 20，Helm values 文件里的一个笔误。
11:20  开始回滚到 v4.18.1。
11:47  错误率回到基线，事故宣告解决。

根本原因
一个配置笔误。eu-west-1 的 Helm values 文件把 `connectionPool.maxSize` 写成了 20，
本意是 200。由于 20 是合法范围内的合法整数，它通过了 schema 校验。该区域没有金丝雀
阶段，所以变更一次性推到了 eu-west-1 的 100% 流量。

影响
估计 11,400 次结账尝试失败。客服收到 217 张工单。没有客户数据丢失或泄露。没有发生
重复扣款，支付服务商干净地拒绝了未完成的会话。

做得好的地方
告警在七分钟内触发。回滚一旦启动就很干净，三十分钟内完成全量生效。

做得不好的地方
从第一次告警到定位出一个单字符的配置错误花了 100 分钟。值班工程师在一个假设上花了
35 分钟，而看一眼发布 diff 两分钟就能排除。

行动项
- 为 eu-west-1 增加金丝雀阶段（负责人：平台组，3 月 28 日前）
- 对连接池使用率告警，而不只对错误率告警（负责人：结账组）
- 给 Helm schema 加范围断言，池大小低于 50 时 CI 失败
""".strip()


# ── 13 道题：一次问完 ────────────────────────────────────────────────────────


def build_questions() -> dict:
    """所有问题共享同一份 state，并行评估、互不可见。

    其中有 5 道是**投机提问** —— 只在某些分支才用得上。
    因为多问几道几乎不增加延迟、只多付那几十个 token，所以一律先问。
    """
    return {
        # ── 分诊：决定后面走哪条代码路径 ──
        "category": Choice(
            instructions="What kind of failure does this incident report describe?",
            criteria={
                "config_error": "A misconfiguration, wrong value, or bad deploy parameter.",
                "code_defect": "A bug in application code logic.",
                "capacity": "Resource exhaustion under load that no single change caused.",
                "dependency": "An upstream or third-party service failed.",
                "security": "Unauthorised access, data exposure, or an attack.",
            },
        ),
        "severity": Score(
            instructions="How severe was the customer impact of this incident?",
            criteria=[
                "Internal only; no customer noticed.",
                "Degraded experience; customers could still complete their task.",
                "A subset of customers were fully blocked from a core action.",
                "All customers were fully blocked from a core action.",
                "Customers were blocked and data or money was lost.",
            ],
        ),
        # ── 绝对判断：Noul 可以全都低，Choice 不行 ──
        "customer_facing": Noul(
            instructions="Were external, paying customers affected by this incident?",
        ),
        "data_loss": Noul(
            instructions="Was any customer data lost, corrupted, or exposed?",
            criteria={
                "true": "The report states or implies data was lost, corrupted or exposed.",
                "false": "The report states no data was affected, or does not involve data.",
            },
        ),
        "money_impact": Noul(
            instructions="Did any customer lose money or get charged incorrectly?",
        ),
        # ── 复盘质量的三个独立维度：代码里加权合成 ──
        "q_timeline": Score(
            instructions="How complete and specific is the timeline in this report?",
            criteria=[
                "No timeline given.",
                "Vague ordering of events, no times.",
                "Times given for the major milestones only.",
                "Timestamped entries covering detection, diagnosis and resolution.",
            ],
        ),
        "q_rootcause": Score(
            instructions="How precisely does this report identify the root cause?",
            criteria=[
                "No root cause stated.",
                "A vague area or component is blamed.",
                "The specific mechanism is named.",
                "The specific mechanism is named and it explains why existing safeguards did not catch it.",
            ],
        ),
        "q_actions": Score(
            instructions="How actionable are the follow-up items in this report?",
            criteria=[
                "No action items.",
                "Vague intentions with no owner or date.",
                "Concrete actions, but missing owners or dates.",
                "Concrete actions with named owners and due dates.",
            ],
        ),
        # ── 投机提问：只在 category == config_error 时才读 ──
        "had_canary": Noul(
            instructions="Did a canary or staged rollout stage exist for the affected deployment?",
        ),
        "caught_by_validation": Noul(
            instructions="Did automated validation or CI catch the faulty value before it shipped?",
        ),
        # ── 投机提问：只在 severity 高时才读 ──
        "needs_exec_review": Noul(
            instructions=(
                "Does the scale of this incident warrant review by engineering leadership, "
                "rather than being closed by the owning team alone?"
            ),
        ),
        # ── 投机提问：只在 data_loss 为真时才读 ──
        "regulatory_exposure": Noul(
            instructions=(
                "Does this incident plausibly trigger a regulatory breach-notification "
                "obligation such as GDPR Article 33?"
            ),
        ),
        # ── 与分类无关，始终有用 ──
        "detection_speed": Score(
            instructions="How quickly was this incident detected after it began?",
            criteria=[
                "Detected by a customer complaint, not by monitoring.",
                "Detected by monitoring, but over an hour after onset.",
                "Detected by monitoring within roughly ten minutes.",
                "Detected by monitoring within roughly one minute.",
            ],
        ),
    }


# ── 输出工具 ────────────────────────────────────────────────────────────────


def bar(p: float, width: int = 18) -> str:
    filled = round(p * width)
    return "█" * filled + "·" * (width - filled)


def conf_colour(c: float) -> str:
    return GREEN if c >= 0.8 else (YELLOW if c >= 0.5 else RED)


def cost_of(input_tokens: int | None) -> float:
    return (input_tokens or 0) * USD_PER_INPUT_TOKEN


def header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 74}{OFF}")
    print(f"{BOLD}{CYAN}  {title}{OFF}")
    print(f"{BOLD}{CYAN}{'─' * 74}{OFF}\n")


def show_answer(qid: str, ans) -> None:
    if ans.type == "noul":
        colour = GREEN if ans.noul > 0.7 else (RED if ans.noul < 0.3 else YELLOW)
        print(f"  {qid:<22} {DIM}noul{OFF}   {colour}{ans.noul:>5.2f}{OFF}  {bar(ans.noul)}")

    elif ans.type == "choice":
        c = conf_colour(ans.confidence)
        print(f"  {qid:<22} {DIM}choice{OFF} {BOLD}{ans.choice}{OFF}"
              f"   {DIM}conf{OFF} {c}{ans.confidence:.2f}{OFF}")
        for opt, p in sorted(ans.probabilities.items(), key=lambda kv: -kv[1]):
            if p >= 0.005:
                print(f"  {'':<22} {DIM}  {opt:<24}{p:>5.2f}  {bar(p, 12)}{OFF}")

    elif ans.type == "score":
        c = conf_colour(ans.confidence)
        top = max(ans.probabilities.items(), key=lambda kv: kv[1])[0]
        print(f"  {qid:<22} {DIM}score{OFF}  {BOLD}{ans.score:>5.2f}{OFF}"
              f"   {DIM}conf{OFF} {c}{ans.confidence:.2f}{OFF}   {DIM}{ans.legend[top]}{OFF}")


# ── 模式 1：投机扇出 ─────────────────────────────────────────────────────────


def cmd_fanout(client: TypeSafeClient, state: str) -> None:
    header("投机扇出 —— 13 道题，一次请求")

    questions = build_questions()
    t0 = time.perf_counter()
    r = client.system_one(state=state, questions=questions)
    elapsed = time.perf_counter() - t0

    for qid in questions:
        show_answer(qid, r.answers[qid])

    print(f"\n  {DIM}model {r.model}   {len(questions)} 题   "
          f"{r.usage.input_tokens} input tokens   "
          f"${cost_of(r.usage.input_tokens):.6f}   {elapsed:.2f}s{OFF}")

    # ── 代码决定哪些答案相关 ──
    header("代码侧路由 —— 投机答案在这里被采纳或丢弃")

    a = r.answers
    used, dropped = [], []

    if a["category"].choice == "config_error":
        used += ["had_canary", "caught_by_validation"]
        print(f"  category = {BOLD}config_error{OFF}  →  读取部署防护相关的投机答案")
        if a["had_canary"].noul < 0.5:
            print(f"    {RED}✗{OFF} 无金丝雀阶段 (noul {a['had_canary'].noul:.2f}) "
                  f"→ 建档：为该区域补金丝雀")
        if a["caught_by_validation"].noul < 0.5:
            print(f"    {RED}✗{OFF} CI 未拦截 (noul {a['caught_by_validation'].noul:.2f}) "
                  f"→ 建档：加范围断言")
    else:
        dropped += ["had_canary", "caught_by_validation"]

    if a["data_loss"].noul > 0.5:
        used.append("regulatory_exposure")
        print(f"  data_loss 为真  →  读取合规暴露判断")
    else:
        dropped.append("regulatory_exposure")
        print(f"  data_loss = {a['data_loss'].noul:.2f}  →  {DIM}丢弃 regulatory_exposure"
              f"（它的答案是 {a['regulatory_exposure'].noul:.2f}，但这条分支没走到，不看）{OFF}")

    if a["severity"].score >= 2.0:
        used.append("needs_exec_review")
        verdict = "需要" if a["needs_exec_review"].noul > 0.6 else "不需要"
        print(f"  severity = {a['severity'].score:.2f} ≥ 2.0  →  "
              f"高管复核：{BOLD}{verdict}{OFF} (noul {a['needs_exec_review'].noul:.2f})")
    else:
        dropped.append("needs_exec_review")

    print(f"\n  {GREEN}采纳{OFF} {len(used)} 个投机答案，{DIM}丢弃 {len(dropped)} 个{OFF}")
    print(f"  {DIM}丢弃的那些没有浪费一次往返 —— 它们和其他题一起，在同一个 0.x 秒里回来了。{OFF}")


# ── 模式 2：打包 vs 拆开 ─────────────────────────────────────────────────────


def cmd_bench(client: TypeSafeClient, state: str) -> None:
    header("打包 vs 拆开 —— 在你自己的数据上复现官方的 12.2x / 10.0x")

    questions = build_questions()
    n = len(questions)

    t0 = time.perf_counter()
    batched = client.system_one(state=state, questions=questions)
    batched_latency = time.perf_counter() - t0
    batched_cost = cost_of(batched.usage.input_tokens)

    print(f"  {DIM}打包完成，开始逐题调用（{n} 次往返，请稍候）…{OFF}")

    singles, singles_cost, t0 = {}, 0.0, time.perf_counter()
    for qid, q in questions.items():
        one = client.system_one(state=state, questions={qid: q})
        singles[qid] = one.answers[qid]
        singles_cost += cost_of(one.usage.input_tokens)
    singles_latency = time.perf_counter() - t0

    print(f"\n  {'方式':<20}{'调用数':>8}{'成本':>14}{'总耗时':>12}")
    print(f"  {'─' * 54}")
    print(f"  {f'一次调用，{n} 题':<19}{1:>8}{'$' + format(batched_cost, '.6f'):>14}"
          f"{format(batched_latency, '.2f') + 's':>12}")
    print(f"  {f'{n} 次调用，各 1 题':<17}{n:>8}{'$' + format(singles_cost, '.6f'):>14}"
          f"{format(singles_latency, '.2f') + 's':>12}")
    print(f"\n  {BOLD}{GREEN}打包便宜 {singles_cost / batched_cost:.1f}x，"
          f"快 {singles_latency / batched_latency:.1f}x{OFF}")

    # 答案是否一致 —— 这才是打包能成立的前提
    diffs = []
    for qid, b in batched.answers.items():
        s = singles[qid]
        if b.type == "noul":
            d = abs(b.noul - s.noul)
        else:
            d = abs((b.score if b.type == "score" else b.confidence)
                    - (s.score if s.type == "score" else s.confidence))
        if d > 0.01:
            diffs.append((qid, d))

    if diffs:
        print(f"\n  {YELLOW}答案差异（>0.01）：{OFF}")
        for qid, d in diffs:
            print(f"    {qid}: {d:.3f}")
    else:
        print(f"\n  {GREEN}✓{OFF} {n} 道题的答案两种方式完全一致（差异均 ≤0.01）")
        print(f"  {DIM}每题都是独立对 state 评分的，所以同一请求里有什么别的题，不影响它。{OFF}")


# ── 模式 3：一次推理，多套权重 ───────────────────────────────────────────────


PROFILES = {
    "工程视角": {"q_rootcause": 0.50, "q_timeline": 0.20, "q_actions": 0.30},
    "管理视角": {"q_rootcause": 0.20, "q_timeline": 0.20, "q_actions": 0.60},
    "审计视角": {"q_rootcause": 0.30, "q_timeline": 0.50, "q_actions": 0.20},
}


def cmd_weights(client: TypeSafeClient, state: str) -> None:
    header("复合打分 —— 一次推理，三套权重，零额外调用")

    dims = {
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
                      "The specific mechanism is named and it explains why existing safeguards did not catch it."],
        ),
        "q_actions": Score(
            instructions="How actionable are the follow-up items in this report?",
            criteria=["No action items.", "Vague intentions with no owner or date.",
                      "Concrete actions, but missing owners or dates.",
                      "Concrete actions with named owners and due dates."],
        ),
    }

    r = client.system_one(state=state, questions=dims)
    levels = 4 - 1  # 每个维度 4 级，归一化除以 3

    print(f"  {BOLD}原始判断{OFF}（这是唯一一次推理）")
    norm = {}
    for qid in dims:
        a = r.answers[qid]
        norm[qid] = a.score / levels
        print(f"    {qid:<16}{a.score:.2f} / 3   →  归一化 {norm[qid]:.3f}"
              f"   {DIM}conf {a.confidence:.2f}{OFF}")

    print(f"\n  {BOLD}三套权重，三个结论{OFF}")
    for name, w in PROFILES.items():
        total = sum(w[k] * norm[k] for k in w)
        weights = "  ".join(f"{k.replace('q_', '')} {v:.0%}" for k, v in w.items())
        print(f"    {name}  {BOLD}{total:.3f}{OFF}   {DIM}{weights}{OFF}")

    print(f"\n  {DIM}证据和问题语义都没变，所以改权重不需要重新调用模型。{OFF}")
    print(f"  {DIM}把 PROFILES 改一改再跑，你会发现 API 调用次数还是 1 次。{OFF}")
    print(f"  {DIM}用量：{r.usage.input_tokens} input tokens，"
          f"${cost_of(r.usage.input_tokens):.6f}{OFF}")


# ── 模式 4：置信度三档门控 ───────────────────────────────────────────────────


def cmd_gate(client: TypeSafeClient, state: str) -> None:
    header("置信度门控 —— 答案说是什么，置信度说该不该动手")

    q = {
        "category": build_questions()["category"],
        "severity": build_questions()["severity"],
    }
    r = client.system_one(state=state, questions=q)

    cat, sev = r.answers["category"], r.answers["severity"]
    show_answer("category", cat)
    show_answer("severity", sev)

    print(f"\n  {BOLD}同一个判断，不同动作用不同阈值{OFF}\n")

    # 低风险动作：打标签，错了可恢复
    if cat.confidence >= 0.50:
        print(f"  {GREEN}自动{OFF}  打分类标签 '{cat.choice}'"
              f"   {DIM}conf {cat.confidence:.2f} ≥ 0.50，错了改个标签即可{OFF}")
    else:
        print(f"  {RED}转人工{OFF}  分类不确定   {DIM}conf {cat.confidence:.2f} < 0.50{OFF}")

    # 中风险动作：建工单派给团队
    if cat.confidence >= 0.70:
        print(f"  {GREEN}自动{OFF}  建工单并派给 {cat.choice} 的归属团队"
              f"   {DIM}conf ≥ 0.70{OFF}")
    elif cat.confidence >= 0.50:
        print(f"  {YELLOW}待确认{OFF}  工单建好但不自动派单，等人点一下"
              f"   {DIM}0.50 ≤ conf < 0.70{OFF}")

    # 高风险动作：触发全员事故通报
    if sev.score >= 2.0:
        if sev.confidence >= 0.90:
            print(f"  {GREEN}自动{OFF}  触发全员事故通报"
                  f"   {DIM}severity {sev.score:.2f} 且 conf {sev.confidence:.2f} ≥ 0.90{OFF}")
        else:
            print(f"  {YELLOW}先确认{OFF}  严重度够但置信度不足，先问一下值班经理"
                  f"   {DIM}conf {sev.confidence:.2f} < 0.90{OFF}")
    else:
        print(f"  {DIM}跳过{OFF}  未达通报门槛   {DIM}severity {sev.score:.2f} < 2.0{OFF}")

    print(f"\n  {DIM}破坏性越强的动作，阈值越高。风险容忍度写在代码里，不写在 prompt 里。{OFF}")


# ── 入口 ────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        description="Jev 能力演示", formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("mode", nargs="?", default="fanout",
                   choices=["fanout", "bench", "weights", "gate", "all"])
    p.add_argument("--zh", action="store_true",
                   help="改用中文版报告（Jev 的 CJK 准确率明确低于英文，可自行对比）")
    p.add_argument("--file", help="改用自己的文档作为 state")
    p.add_argument("--model", default=None, help="默认 jev-latest；生产建议钉死 jev-1.13.0")
    args = p.parse_args()

    if not os.environ.get("TYPESAFE_API_KEY"):
        print(f"{RED}未设置 TYPESAFE_API_KEY{OFF}\n"
              f"  去 https://console.typesafe.ai/keys 拿一个，然后：\n"
              f"  {DIM}export TYPESAFE_API_KEY=...{OFF}", file=sys.stderr)
        return 1

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            state = fh.read()
    else:
        state = INCIDENT_ZH if args.zh else INCIDENT_EN

    print(f"{DIM}state: {len(state)} 字符"
          f"{'（中文版）' if args.zh and not args.file else ''}{OFF}")

    modes = ["fanout", "bench", "weights", "gate"] if args.mode == "all" else [args.mode]
    runners = {"fanout": cmd_fanout, "bench": cmd_bench,
               "weights": cmd_weights, "gate": cmd_gate}

    kwargs = {"model": args.model} if args.model else {}
    with TypeSafeClient(**kwargs) as client:
        for m in modes:
            runners[m](client, state)

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
