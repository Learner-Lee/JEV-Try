# 步骤 4：架构模式与实测数据调研

> 日期：2026-09-22
> 来源：docs.typesafe.ai 的 `patterns/*`、`concepts/use-case-map`、`cookbooks/*`

## 一、结论先行

四个官方模式其实只回答两个问题：
- **怎么省** → Speculative Fan-out（打包）、Intent Routing（贵资源按需调用）
- **怎么稳** → Confidence-Gated Routing（置信度当第二根轴）、Composite Scoring（拆维度 + 代码里加权）

**最有说服力的一个数字**：54,000 字符的 GDPR 文档 + 13 道题，打包成一次调用 = **$0.000497 / 0.27 秒**。

## 二、四个架构模式

### 2.1 Speculative Fan-out（投机扇出）—— 省成本、省延迟

**做法**：把系统可能需要的所有问题（包括只在某些分支才有用的）塞进一次请求，然后**用代码决定哪些答案相关**。

工单分诊示例：一次请求同时问 5 题 ——
`category`(Choice) / `bug_severity`(Score) / `has_reproducible_steps`(Noul) / `refund_requested`(Noul) / `frustration`(Score)

其中 `bug_severity` 和 `has_reproducible_steps` 只在 bug 报告时有用，`refund_requested` 只在账单类有用。**照样一起问**，不是 bug 报告就忽略。

```python
category = response.answers["category"]

if category.choice == "bug_report":
    if bug_severity.score > 1.5 and bug_repro.noul > 0.6:
        escalate_to_engineering(ticket_id, severity="high")
    else:
        add_to_bug_backlog(ticket_id)
elif category.choice == "billing":
    ...
# frustration 与分类无关，始终有用
if frustration.score > 1.5:
    flag_for_priority_response(ticket_id)
```

**为什么成立（第一性原理）**：文档在每次请求里占绝大部分 token。N 次单题调用要把文档重发 N 次、走 N 个往返；打包只发一次。**文档越大，节省越接近满额的 Nx。**

### 2.2 Confidence-Gated Routing（置信度门控）—— 保安全

**核心**：答案告诉你**是什么**，置信度告诉你**该不该动手**。

语音银行示例的阈值分层：

| 条件 | 行为 |
|---|---|
| `confidence < 0.6` | 一律转人工（不论什么意图） |
| `check_balance` 且 ≥ 0.6 | 直接显示余额（低风险，最坏就是念错一次） |
| `approve_transfer` 且 > 0.85 | 自动批准 |
| `approve_transfer` 且 0.6~0.85 | 先让用户确认 |
| `other` | 转人工 |

**一句话**：**一个下限兜底 + 每种动作按后果severity 各自设阈值**。

### 2.3 Composite Scoring（复合打分）—— 可解释、可调

简历筛选示例：4 个独立 Score（Python 深度 / 团队领导 / 系统设计 / 通才），各 5 级，一次请求问完，然后在**代码里**归一化并加权：

```python
py      = response.answers["python_depth"].score / 4   # 5 级 → 除以 4 归一化到 0~1
lead    = response.answers["team_leadership"].score / 4
arch    = response.answers["system_design"].score / 4
general = response.answers["generalist"].score / 4

ic_score = (0.40*py) + (0.10*lead) + (0.40*arch) + (0.10*general)   # 资深个人贡献者
em_score = (0.15*py) + (0.40*lead) + (0.20*arch) + (0.25*general)   # 工程经理
```

**真正的价值不是分数本身，是可见性**：排名不符合预期时，你能看到是哪个维度、哪个权重导致的，改系数即可 —— **而且同一批原始判断可以喂给多套权重**（IC 和 EM 两套排序共用一次推理）。

### 2.4 Intent Routing（意图路由）—— 让贵资源按需登场

TypeSafe 站在所有 handler 前面当一个又快又便宜的分类器：

```
客户消息 → [Choice: intent + Score: complexity]（一次请求）
  ├ intent.confidence < 0.5           → 人工坐席
  ├ order_status                       → 确定性代码查库（完全不用 LLM）
  ├ product_question                   → 产品专家 LLM
  ├ return_exchange                    → 退换货专家 LLM
  └ complaint → complexity.score > 1 或 complexity.confidence < 0.5 ? 人工 : 投诉处理 LLM
```

**注意最后一行的细节**：不仅看 complexity 的**值**，还看它的**置信度** —— 「模型说不复杂但自己也不确定」同样要转人工。

## 三、十种决策形状（use-case map）

| 决策形状 | 何时用 | 例子 |
|---|---|---|
| Classification 分类 | 应有一个已知类别胜出 | 意图、主题、部门、风险类型、实体类型 |
| Detection 检测 | 需要"某属性存在"的概率 | 垃圾信息、欺诈、紧急、越狱、敏感数据 |
| Scoring 打分 | 答案落在有序量表上 | 严重度、相关性、质量、沮丧度、适配度 |
| Routing 路由 | 类别决定下一段代码路径 | 工具调用、升级、模型路由、客服队列 |
| Search 搜索 | 找出匹配自然语言查询的项 | 语义搜索、文档发现、候选生成 |
| Retrieval 检索 | 工作流需要最相关的上下文/记录 | RAG 上下文、证据检索、知识查询 |
| Ranking 排序 | 按语义相关性/质量排序 | 搜索结果、推荐、候选优先级 |
| Verification 校验 | 检查产物是否有特定失败模式 | 引用支撑、政策违规、工具调用错误、回答质量 |
| ML Feature Extraction 特征抽取 | 下游传统 ML 模型需要语义信号 | 购买意向、产品兴趣、竞争压力、流失信号 |
| Structured Data Extraction 结构化抽取 | 从非结构化输入还原已知字段 | 候选人属性、订单字段、文档标签 |

## 四、Cookbook 实测数据

| Cookbook | 做什么 | 实测结果 |
|---|---|---|
| **Parallel questions** | GDPR 维基条目（约 54,000 字符）+ 13 道题（8 Noul / 2 Choice / 3 Score） | 打包 1 次调用：**$0.000497、0.27s**；拆成 13 次：$0.006090、2.71s → **便宜 12.2x、快 10.0x，答案完全不变**（5 次重复，大多数答案每次完全相同，std dev 精确为 0） |
| **Re-ranking** | 40 条 CLERC 法律查询，BM25 先出 30 条候选，再对每个 query-candidate 对问一道题 | top-1 准确率 **5% → 18%**，top-10 **38% → 62%** |
| **SDE cascade** | 结构化抽取两级级联：便宜模型抽 → Jev 校验 → 只在告警时升级给昂贵推理模型 | 价格对比鲜明：`gpt-5.4-mini` $0.75/$4.50，大推理模型 $5.00/$30.00（约 7x mini），**Jev 校验器 $0.042/$0.00**。最强模型 `gpt-5.5-reasoning` 约 0.81 质量 / 约 $0.10 每次抽取；级联以极小代价拿到其大部分质量 |
| **Skill suggestion** | 从 182 个 skill 里最多选 1 个：一次请求排完全部 + 问"这轮需不需要 skill"，第二次请求细读 top 3 且可以全部拒绝 | 加载错 skill 和"不该加载却加载"的情况**都下降超过一半** |
| **Classification using confidence** | SEC 年报分到 75 个行业组，一个 Choice | 用答案自己的 confidence 决定：报具体行业组，还是退到上一层更宽的 division |
| **Entity alignment** | 两个啤酒目录 450 个候选对判重 | **一个 Score 三级承载全部决策**：合并 / 不关联 / 交给策展人 —— **没有阈值需要调**。同请求另挂 3 个 Noul 告诉策展人两边哪个字段不一致 |
| **LLM guardrails** | 一次请求筛查进出 LLM 的每条消息 | 描述危害（"这是越狱尝试吗"）+ 打严重度分（"照做会有多大伤害"），阈值决定放行/复核/拦截/转客服 |
| **Hierarchical classification** | 专利、零售商品、生物医学、源码的深层分类树 | 对 Choice 概率做**并行 beam search** |

### 从 cookbook 里提炼的两个设计技巧

1. **Score 的等级可以直接就是你的动作集合**（entity alignment）。
   三级 = 合并 / 不关联 / 人工 —— 这样**连阈值都不需要调**，score 落在哪级就做哪件事。这是比"打分再设阈值"更干净的设计。

2. **Choice 和 Noul 回答的是不同问题，可以在同一请求里配合**（skill suggestion）。
   - Choice 是**相对的**：182 个里哪个最合适
   - Noul 是**绝对的**：这轮到底需不需要 skill（可以全都低）

   只用 Choice 会被迫在"必须选一个"的前提下选；配上 Noul 才能说"一个都不要"。

## 五、这一步纠正的一个数字

`primitives.md` 页写"11.5x 便宜、9.6x 快"，`llms.txt` 索引和 cookbook 正文都是 **12.2x / 10.0x**。
已核对 cookbook 正文的实际输出，**以 12.2x / 10.0x 为准**（`primitives.md` 上的数字疑似过期）。

## 下一步

→ `docs/04-research-report.md`：汇总 + 我们该怎么用
