# JEV-Try

对 **TypeSafe Jev**（System One 决策模型）的技术调研，以及一个突出其核心优势的演示 CLI。

Jev 不是更便宜的 LLM，而是另一类东西：**不产出文本，只产出类型化判断 + 校准概率**。
它要替换的是你代码里那段「写 prompt 让 LLM 返回 JSON，再解析、再兜底」的脆弱逻辑。

---

## 快速开始

```bash
# 1. 虚拟环境
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. API key（https://console.typesafe.ai/keys）
export TYPESAFE_API_KEY=...

# 3a. 命令行 demo
./.venv/bin/python jev_demo.py all

# 3b. 或者可视化面板
./.venv/bin/python server.py      # → http://127.0.0.1:8000
```

---

## 可视化面板：`server.py` + `web/`

浏览器里看概率分布、置信度分档和成本对比。**API key 只留在服务端进程，浏览器只拿结果**
（TypeSafe 文档对 Web 应用的明确要求）。无构建步骤，前端是原生 ES module。

| 视图 | 看什么 |
|---|---|
| **投机扇出** | 13 道题的完整概率分布 + 置信度条；投机提问标注为「已采纳 / 已丢弃」，被丢弃的卡片整体压暗 |
| **权重实验** | 3 个维度打一次分，然后**拖动权重滑块** —— 页面上的「API 调用次数」始终是 1 |
| **打包 vs 拆开** | 成本和耗时两张独立图表；可设重复轮数，把「打包是否改变答案」与「运行间噪声」分开 |

右上角可切换 跟随系统 / 亮色 / 暗色。

---

## demo：`jev_demo.py`

素材是一份约 2200 字符的事故复盘报告，四个模式共用同一份 state，各演示一个优势。

| 模式 | 演示什么 | 看点 |
|---|---|---|
| `fanout` | **投机扇出** | 13 道题一次请求（Choice + Score + Noul 混搭），其中 5 道是投机提问。再演示代码侧路由如何采纳或丢弃它们 —— 丢弃的那些没浪费任何一次往返 |
| `bench` | **打包 vs 拆开** | 在你自己的数据上复现官方的 12.2x / 10.0x，并逐题校验两种方式的答案是否一致 |
| `weights` | **一次推理，多套权重** | 3 个维度打一次分，喂进工程/管理/审计三套权重出三个结论，**API 调用数恒为 1** |
| `gate` | **置信度门控** | 同一判断按动作后果分层设阈值：打标签 ≥0.50、派单 ≥0.70、全员通报 ≥0.90 |

```bash
./.venv/bin/python jev_demo.py fanout            # 单个模式
./.venv/bin/python jev_demo.py all               # 四个都跑
./.venv/bin/python jev_demo.py fanout --zh       # 切中文报告，对比 CJK 差距
./.venv/bin/python jev_demo.py bench --file x.md # 换成自己的文档
```

---

## 调研文档

| 文档 | 内容 |
|---|---|
| [00-plan](docs/00-plan.md) | 调研计划与范围 |
| [01-concepts](docs/01-concepts.md) | RLCD 训练路线、state/questions 结构、三原语、组合规则、confidence |
| [02-integration](docs/02-integration.md) | API 契约、价格限流、SDK，**以及 jev-1.13 的 9 个已知锯齿** |
| [03-use-cases](docs/03-use-cases.md) | 4 个架构模式、10 种决策形状、8 个 cookbook 实测数据 |
| [04-research-report](docs/04-research-report.md) | 汇总报告与落地建议 |
| [05-jev-vs-qwen](docs/05-jev-vs-qwen.md) | Jev 与 Qwen 小模型的任务分工（含速查表） |
| [06-laya-vs-jev](docs/06-laya-vs-jev.md) | Laya 调研：「吊打 Jev」这个说法站不站得住 |
| [07-build-your-own](docs/07-build-your-own.md) | 自建类 Jev 模型的四级阶梯与真实工作量 |

---

## 核心结论摘要

**经济性是数量级的，这解锁了新架构**
$0.042 / 百万输入 token，输出免费。官方实测：54,000 字符文档问 13 道题，打包一次调用
= **$0.000497 / 0.27 秒**，比拆成 13 次便宜 12.2x、快 10.0x，**答案完全不变**。
于是「多问几道用不上的题」接近免费 —— 这是 LLM 做不到的投机扇出。

**最危险的坑：结构不变式不成立**
官方实测同一问题，用 Noul 得 `0.22`，用 yes/no Choice 得 `yes=0.01, confidence=0.97`；
一个问题和它的否定，两个 Noul 相加 = **1.19**。
→ Noul 上调好的阈值**不能**搬到 Choice 上，别指望独立问题之间满足算术恒等式。

**最现实的风险：中文**
官方明确 CJK 准确率低于英文，要求先在自己内容上测。这是本项目最大的未决问题。

**Laya 不是替代品**
Laya（ModernBERT-large 421M，Apache 2.0）上下文只有 512 / 1024 token，Jev 是 64k；
高基数 Choice 上 Jev 0.870 vs Laya 0.425。但 Laya 可微调、可私有化、多语言 —— 两者能力错开。

---

## 工程注意事项

- **钉死模型版本**。`jev-latest` 会漂移，调好的 confidence 阈值会失效。生产用 `jev-1.13.0`，并记录响应里的 `model` 字段。
- **多问题、瘦 state**。加问题安全（并行隔离、不产生 context rot）；加无关 state 有害（准确率随 state 膨胀下降）。
- **API key 只留服务端**，不入库、不进前端。
- **SDK 响应读法**：`response.answers[qid]`。SDK 0.7.1 里**不存在**官方文档页所示的 `.nouls` / `.choices` / `.scores`。

---

## 下一步

最该先做的是**中文四方对照实验**（见 [06 第七节](docs/06-laya-vs-jev.md)）：

| 方案 | 说明 |
|---|---|
| A | 中文直喂 Jev（基线） |
| B | 中文 → 译英 → Jev |
| C | 中文喂 Qwen 小模型（约束解码出枚举） |
| D | 中文喂 laya-multilingual（零样本 + 微调各一版） |

同一批中文数据、同一组判断、人工标注真值，比四件事：**准确率、校准质量（ECE / confidence-准确率曲线是否单调）、成本、延迟**。

> 先做评测集，再选模型。评测集是整个项目里最持久的资产 —— 换模型它不变，模型会被淘汰，它不会。

---

## 参考

| 用途 | 链接 |
|---|---|
| 文档索引（定向检索入口） | https://docs.typesafe.ai/llms.txt |
| Playground | https://console.typesafe.ai/playground |
| **已知缺陷清单（写问题前必读）** | https://docs.typesafe.ai/model-jaggedness/jev-1.13 |
| Python SDK 源码 | https://github.com/typesafe-ai/typesafe-sdk-python |
| Laya 模型卡 | https://huggingface.co/convaiinnovations/laya |

> Mintlify 文档：任意页面路径后加 `.md` 即得 Markdown 原文。
