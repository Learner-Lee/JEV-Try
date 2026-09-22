# 步骤 3：工程接入层调研

> 日期：2026-09-22
> 来源：docs.typesafe.ai（`api`、`models`、`sdk`、`sdk/python`、`sdk/javascript`、`introduction/quickstart`、`model-jaggedness/jev-1.13`）

## 一、结论先行

接入成本极低（一个 POST 端点、两个官方 SDK、环境变量读 key），
**真正要花时间的不是接入，而是绕开 jev-1.13 已知的 9 个"锯齿"**。

## 二、接口契约

### 2.1 端点

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

辅助端点：`GET /v1/models` 列出账号可用的模型名/别名。

### 2.2 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `state` | string / object / array | ✅ | 被评估的内容 |
| `model` | string | ✅ | 如 `"jev-latest"` |
| `questions` | map<string, Question> | ✅ | key 由你定，答案用同样的 key 返回 |

Question 三种形态：

```jsonc
// Noul —— criteria 可选，用于澄清 yes/no 各自含义
{ "type": "noul", "instructions": "...", "criteria": { "true": "...", "false": "..." } }

// Choice —— criteria 必填，map<选项, 描述|null>，最多 255 个选项
{ "type": "choice", "instructions": "...", "criteria": { "billing": "...", "technical": "..." } }

// Score —— criteria 必填，有序数组，≥2 级、≤10 级
{ "type": "score", "instructions": "...", "criteria": ["Calm", "Frustrated", "Very angry"] }
```

`instructions` 支持 string / object / array。结构化写法：一个字段放问题，其余放它引用的数据，用反引号在问题里点名：

```json
"instructions": {
  "potential_duplicate": { "name": "John Smith", "location": "Oakland, California", "last_employer": "Google" },
  "question": "Is the resume for the same person as `potential_duplicate`?"
}
```

### 2.3 响应体

```jsonc
{
  "model": "jev-1.13.0",              // 实际应答的版本号，务必记日志
  "answers": {
    "is_urgent":   { "type": "noul",   "noul": 0.95 },
    "department":  { "type": "choice", "choice": "billing",
                     "probabilities": { "billing": 0.88, "technical": 0.12, "sales": 0.0 },
                     "confidence": 0.81 },
    "frustration": { "type": "score",  "score": 1.05,
                     "legend": { "0": "Calm", "1": "Frustrated", "2": "Very angry" },
                     "probabilities": { "0": 0.0, "1": 0.95, "2": 0.05 },
                     "confidence": 0.92 }
  },
  "usage": { "input_tokens": 296, "output_tokens": 20 }
}
```

### 2.4 错误码

| 状态码 | 含义 | 处理 |
|---|---|---|
| `401` | key 缺失/无效 | 检查 Authorization 头 |
| `422` | 请求体校验失败 | body 会指出具体字段 |
| `429` | 超限 | **指数退避重试**，尊重 `retry-after` |
| `529` | 服务过载 | 同上 |

官方 SDK 默认就带退避重试并处理 `retry-after`；裸调 HTTP 要自己实现。

## 三、模型与成本（jev-1.13）

| 项 | 值 |
|---|---|
| 版本 ID | `jev-1.13.0` |
| 价格 | **$42 / Btok，即 $0.042 / Mtok**（仅计输入 token，**输出 token 免费**） |
| 限流 | 250,000 tokens/秒；1,200 requests/分钟 |
| 上下文 | 单次请求 64k token；`state` + 最长的那道题 ≤ 32k token |
| 输入 | 仅文本（string / JSON object / 文本数组） |

> ⚠️ 文档明确警告：**限流正在动态调整，可能无预告变化**。高配额走定制/企业方案。

**成本直觉**：$0.042/百万输入 token —— 比主流 LLM 便宜约两到三个数量级。
这正是"投机提问几乎免费"能成立的经济基础：**多问几道题只多付那几十个 token 的钱，还不增加延迟。**

### 别名

| 别名 | 指向 | 说明 |
|---|---|---|
| `jev-latest` | `jev-1.13.0` | 最新稳定版，SDK 默认 |
| `jev-preview` | `jev-1.13.0` | 最新版（含预览版），当前无预览版 |

**别名会漂移**。如果你的 confidence 阈值是针对某个版本调出来的，**就钉死版本号**，自己安排升级节奏。响应里的 `model` 字段会报告实际应答的版本 —— 记日志。

### 定制方式

Jev **不做 fine-tune、不做 LoRA、不用客户数据训练**，全账号共享同一套权重。适配你的领域只有三条路：
1. 把专有内容塞进 `state`
2. 把领域规则和边界情况写进 `instructions` / `criteria`
3. 拆成原子问题，在代码里组合（必要时用 Jev 的概率当特征去训练传统 ML 模型）

### 语言支持

**英语是主训练语言，准确率最好。CJK 等其他语言"能处理但不同等"** —— 中文场景**必须**在自己的内容上先测，并特别依赖 confidence 做路由。
（对本项目：这是一个需要早期验证的关键风险点。）

### 数据处理

不用客户请求/响应做训练；企业客户可零数据保留（ZDR）。

## 四、SDK

| | Python | JavaScript / TypeScript |
|---|---|---|
| 安装 | `pip install typesafe-sdk` / `uv add typesafe-sdk` | `npm install @typesafe-ai/sdk` |
| 运行时要求 | Python ≥ 3.10 | Node.js ≥ 20 |
| 客户端 | `TypeSafeClient` / `AsyncTypeSafeClient` | `TypeSafeClient` |
| 方法 | `client.system_one(state=..., questions=...)` | `client.systemOne({ state, questions })` |
| Key | 读环境变量 `TYPESAFE_API_KEY` | 同左 |
| 默认模型 | `jev-latest` | 同左 |
| 类型 | `Choice` / `Noul` / `Score` 对象 | `choice()` / `noul()` / `score()` 函数，答案类型从问题推断 |
| 重试 | 默认带退避重试策略（`RetryPolicy` 可配） | 同左 |

### Python 最小示例

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

with TypeSafeClient() as client:
    response = client.system_one(
        state={"document": "I was charged twice. Please fix this ASAP."},
        questions={
            "billing": Noul(instructions="Is this ticket about billing?"),
            "tone": Choice(
                instructions="What is the customer's tone?",
                criteria={"calm": None, "frustrated": None, "angry": None},
            ),
            "urgency": Score(
                instructions="How urgent is this ticket?",
                criteria=["can wait", "this week", "today"],
            ),
        },
    )

print(response.nouls["billing"].noul)
print(response.choices["tone"].choice)
print(response.scores["urgency"].score)
```

> 📌 **待验证**：Python SDK 的响应读取有两种写法同时出现在官方文档里 ——
> `response.answers["x"].choice`（quickstart 页、primitives 页）与
> `response.nouls["x"].noul` / `response.choices["x"].choice` / `response.scores["x"].score`（SDK 页）。
> 推测是按类型分组的便捷视图 + 统一视图并存。真正写代码前需以安装后的 SDK 类型定义为准。

### JS 最小示例

```ts
import { choice, TypeSafeClient } from "@typesafe-ai/sdk";

const client = new TypeSafeClient();
const response = await client.systemOne({
  state: { document: "I was charged twice. Please fix this ASAP." },
  questions: {
    category: choice("What is this ticket about?", { billing: null, technical: null, other: null }),
  },
});
console.log(response.answers.category.choice);
```

**Web 应用：API 密钥必须留在服务端。**

## 五、jev-1.13 的 9 个已知锯齿（最重要的一节）

> 官方 `model-jaggedness/jev-1.13` 页，最后审阅日期 2026-09-17。

| # | 失败模式 | 症状 | 对策 |
|---|---|---|---|
| 1 | **字面理解** | 它回答你**写下的**问题，不是你**想问的**问题；范围词、否定、隐含条件全按字面读 | 把确切条件写进 instructions，边界情况写进 criteria。**当你看着错误答案想解释"我其实是想问…"，那句解释就是你缺的那半条指令** |
| 2 | **数学与计数** | 不是计算器；不会可靠计数（字符、词频、长列表项数），**误差随规模增长** | 算术留在代码里。要数满足条件的项：代码遍历候选 + 每项一个 Noul + 自己求和 |
| 2b | **数值表示** | 十六进制颜色值不如英文颜色名；汇编不如高级语言 | 在代码里转换，传计算好的数或命名分桶 |
| 2c | **用 Score 做数学** | Score 等级在数值上校准很弱 | 可以拿期望值判断是否过阈值，**不能靠在两级之间插值还原精确数字** |
| 3 | **日期时间比较** | 把日期当文本读，不当有序量；比先后、算间隔、判断是否落在窗口内都不可靠；混合格式和季度/结算周期更糟 | **拆开**：抽取交给模型（月/日/年都是小的封闭集合 → 用 Choice 枚举，并留一个"未说明"选项），**排序/时长/偏移/星期全在代码里算** |
| 4 | **间接引用** | 双重否定、属性的属性、多跳推理，准确率下降 | 指令写得尽量直接，用名字点出 state 的相关部分 |
| 5 | **大而杂的 state** | 无关内容当干扰项，准确率随 state 膨胀下降，且难定位错因 | 先在代码里检索过滤，只发问题需要的字段；实在无法过滤时用 Noul 先筛相关性 |
| 6 | **对抗内容** | **state 被当作数据，默认不视为敌意**。注入指令、误导性框架、为自己分类辩护的文本都能带偏答案 | criteria 写明确，上线前充分测边界。官方表示未来会改进 |
| 7 | **指令与 criteria 矛盾** | 比如 Noul 里 `true` 映射到"否"，表现会变差 | 把 criteria 当作指令的延伸，两者用词对齐 |
| 8 | **结构不变式不成立** | 见下方详述 | 别依赖结构不变性 |
| 9 | **生成** | 没被训练来生成文本；用 Choice 链硬凑会又差又慢 | 用 regex 或生成模型抽候选，**让 Jev 挑正确的那个** |

### 关于 #8（最容易写出隐蔽 bug 的一条）

模型对**语义相似的输入极其一致**，但**你以为该成立的结构不变式并不保证成立**。官方给的两组实测：

**同一问题用 Noul 与用 yes/no Choice 问，结果不可比**
工单："I'm not happy with the fit. What are my options here?"

| Noul `noul` | Choice `yes` | Choice `no` | Choice `confidence` |
|---|---|---|---|
| 0.22 | 0.01 | 0.99 | 0.97 |

**一个问题和它的否定，两个 Noul 加起来 ≠ 1**
工单："I was charged twice for the same order. Can someone look into this?"

| `refund` | `not_refund` | 和 |
|---|---|---|
| 0.72 | 0.47 | **1.19** |

**工程铁律**：
- 不要把在 Noul 上调好的阈值搬到 Choice 上
- 不要指望两道独立问题之间满足算术恒等式
- **Choice 是相对的（哪个选项胜出），Noul 是绝对的（可以全都低）** —— 这是两种不同的问题

### 一条需要留意的文档内部张力

- `introduction.md`：每题独立评估，**加问题不会产生 context rot**
- `model-jaggedness`：**Jev 存在 context rot，state 里的无关材料会损失准确率**

二者并不矛盾，但必须分清：**加"问题"安全，加"无关 state"有害**。
→ 工程含义：**多问题、瘦 state**。

## 六、上手清单

- [ ] 在 [Playground](https://console.typesafe.ai/playground) 手工试几道题，建立手感
- [ ] 在 [console.typesafe.ai/keys](https://console.typesafe.ai/keys) 拿 API key，写进环境变量 `TYPESAFE_API_KEY`（**不入库、不进前端**）
- [ ] 装 SDK，跑通最小请求，核对 `response` 的实际读取方式
- [ ] **用中文内容做一组对照测试**，评估 CJK 准确率损失
- [ ] 钉死模型版本 `jev-1.13.0`，日志记录 `model` 与 `usage`

## 下一步

→ `docs/03-use-cases.md`：架构模式与 cookbook 实测数据
