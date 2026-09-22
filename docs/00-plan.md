# JEV-Try 项目计划

> 创建日期：2026-09-22
> 目标：技术调研 —— 搞清 TypeSafe / Jev（System One 模型）是什么、能做什么、怎么接入

## 一、结论先行（待调研验证的初始假设）

TypeSafe 提供的不是"又一个会聊天的 LLM"，而是**可被代码直接消费的类型化判断单元**。
因此本项目的调研目标不是"它能生成什么文本"，而是三个问题：

1. **它的判断原语是什么**——Choice / Noul / Score 各自的语义边界与概率含义
2. **判断如何组合成能力**——并行提问、投机提问、置信度阈值、策略与原始判断分离
3. **工程上怎么落地**——API 契约、SDK、延迟与成本、密钥管理、失败排查

## 二、调研范围

| 层次 | 要回答的问题 | 文档入口 |
|---|---|---|
| 概念层 | System One 是什么？和常规 LLM prompt-and-parse 的本质区别？ | `concepts/system-one.md`、`concepts/how-to-build-with-system-one.md` |
| 原语层 | Choice / Noul / Score 分别在什么时候用？概率怎么解释？ | `primitives.md` 及各原语页 |
| 输入层 | state 怎么组织？instructions 与 criteria 怎么写？ | `concepts/state.md` |
| 决策层 | 置信度怎么用？阈值怎么定？什么时候该升级给人/推理模型？ | `confidence.md` |
| 工程层 | HTTP API 与 SDK 怎么调？限制有哪些？ | `api.md`、`sdk/python.md`、`sdk/javascript.md` |
| 场景层 | 有哪些已验证的组合模式（路由、抽取、重排、校验…）？ | `concepts/use-case-map.md` + cookbooks |

## 三、执行步骤

- [x] 步骤 0：安装 TypeSafe skill，初始化 git 仓库
- [x] 步骤 1：抓取文档索引 `https://docs.typesafe.ai/llms.txt`，绘制文档地图
- [x] 步骤 2：读概念层 + 原语层，产出 `docs/01-concepts.md`
- [x] 步骤 3：读工程层（API/SDK），产出 `docs/02-integration.md`
- [x] 步骤 4：读场景层 cookbooks，产出 `docs/03-use-cases.md`
- [x] 步骤 5：汇总为 `docs/04-research-report.md`（含"我们该怎么用"的建议）

## 四、原则

- **线上文档是唯一真相源**。SKILL.md 只给方向，版本相关细节一律以 `docs.typesafe.ai` 为准，不凭记忆编造。
- **定向阅读**，不整站加载。
- 每完成一步写一份 markdown，步骤文档与最终报告分离。

## 五、不做什么

- 本阶段不写业务代码、不申请/配置 API key、不做性能压测。
- 调研结论中凡涉及价格、限流、模型版本的数字，必须标注来源页面。
