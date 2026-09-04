# researchPotato — 科研 Agent Skills 集

一套 Claude Code / agent 通用 skill 合集，覆盖**科研工作流全链路**和**在线 RAG**。
源自内部 agent 产品的内置 skill 包，已做公开化脱敏：内网域名、内部平台名、
凭据引用、内部文档编号全部移除或泛化；纯内部生态适配的 skill（发布平台、
内部训练平台提交、内部 wiki/BI 工具等 15 个）未收录。

## 安装

```bash
# bash
cp -r ./skills/* ~/.claude/skills/
# PowerShell
Copy-Item -Path .\skills\* -Destination $HOME\.claude\skills\ -Recurse -Force
```

## 科研工作流（按链路排序）

| 阶段 | Skill |
|---|---|
| 调研 | `deep-survey` 深度调研 · `idea-discovery` 找 idea · `novelty-check` 新颖性核查 |
| 规划 | `idea-to-plan` · `paper-to-plan` · `paper-plan` · `experiment-plan` |
| 实验 | `plan-to-experiment` · `run-experiment` · `monitor-experiment` · `remote-ssh` |
| 评审 | `auto-review-loop` 多轮自审 · `research-review` · `research-refine` |
| 写作 | `paper-write` / `paper-writing` · `paper-figure` · `paper-illustration` · `paper-appendix-lift` · `paper-compile` |
| 复现 | `autosota-replicate` SOTA 复现（含审计门禁脚本） |
| 编排 | `evo-master` 全链路状态机编排 · `research-pipeline` · `auto-paper-improvement-loop` |

## 在线 RAG / 检索

| Skill | 说明 |
|---|---|
| `web-search` | 多源网页搜索：可配聚合端点（env），否则 DuckDuckGo → Wikipedia → arXiv 级联 |
| `search-knowledge-base` | 本地知识库检索：Qwen3-VL-Embedding 向量化（代理/自定义端点可配），无服务时降级 FTS5 全文 |
| `kb-add` / `kb-reflect` / `kb-synthesize` | 知识库写入 / 反思 / 综合 |
| `arxiv` | arXiv 论文检索与下载 |
| `airgap-benchmark-fetch` | 代理受限/离线 GPU 服务器上拉取 benchmark 数据 |
| `hfd-download` | HuggingFace 模型/数据集多线程下载（aria2c，支持 hf-mirror） |

## 工具类

`read-image` 视觉侧车读图 · `mermaid-diagram` 绘图 · `numbers-grounding` 数字核查

## 环境变量（可选）

| 变量 | 作用 |
|---|---|
| `SEARCH_AGGREGATOR_ENDPOINT` / `SEARCH_AGGREGATOR_APPID` | web-search 的聚合搜索端点（universal-search 兼容协议），不配则走 DDG 级联 |
| `KB_EMBED_PROXY_URL` / `KB_EMBED_URL` | search-knowledge-base 的 embedding 服务，不配则 FTS5 全文模式 |

## 备注

- 部分 skill 引用了未收录的内部 skill（如 `/hope-job-submit`），这些分支仅在其
  对应平台存在时才有意义，公开环境下忽略即可。
- `monitor-experiment` / `plan-to-experiment` 中的 `hope` 指通用集群作业 CLI 的
  占位叫法，替换为你自己平台的作业命令即可。
