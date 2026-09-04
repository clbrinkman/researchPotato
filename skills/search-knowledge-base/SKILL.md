---
name: search-knowledge-base
description: "查询 Dr. Pie 本地 AI/ML 知识库（热点档案 + 精选论文库，FTS5+RRF 混合检索）。Use when: 用户询问 AI/ML 最新动态/技术进展/公司产品动向、论文相关工作/文献来源、idea 可行性/新颖性分析、研究方向头脑风暴/创新方向梳理、行业趋势/竞品调研、autoresearch、任何可受益于已有 AI 研究背景的问题。Keywords: 最新进展, 相关工作, 有没有论文, 前沿动态, 创新方向, idea 分析, 行业动态, latest, related papers, brainstorm, autoresearch."
argument-hint: [query]
allowed-tools: Bash(python3*)
---

# 本地知识库检索

查询：**$ARGUMENTS**

---

## Step 1：关键词推导（内部思考，不输出到回复）

在思考中确定以下内容，**不在回复中写出**：

**双语互译（无论用户用哪种语言，两者都必须填）：**
- 图神经网络 ↔ graph neural network
- 大语言模型 ↔ LLM / large language model
- 扩散模型 ↔ diffusion model
- 推理加速 ↔ inference acceleration
- 多模态 ↔ multimodal
- 强化学习 ↔ reinforcement learning
- 知识蒸馏 ↔ knowledge distillation

**提取规则：**
- 只取 2-4 个核心名词，不传完整句子（错误："Anthropic 最近有什么动向？"；正确："Anthropic Claude"）
- 公司/专有名词（Anthropic、GPT-4）中英文相同
- 目标库：公司/产品/行业动态 → hotspot；论文/方法/相关工作 → papers；idea 分析/autoresearch → 两库
- 原始查询（[原始查询]）用于 embedding 语义检索，保留完整语义

---

## Step 2：执行双语检索

每个 bash 调用自包含（路径发现 + 搜索合并，无跨调用变量依赖）。
将 `[中文查询词]`、`[英文查询词]`、`[库]` 替换为 Step 1 推导的关键词后执行。
`[原始查询]` 通过 bash 环境变量传入（避免单引号等特殊字符破坏 Python 字符串字面量）。

**单库（2 次查询）：**

```bash
BIGPIE_EMBED_QUERY="[原始查询]" python3 -c "
import subprocess, sys, os, pathlib
base = os.environ.get('BIGPIE_BUNDLED_SKILLS_DIR', '')
candidates = [
    *([ pathlib.Path(base) / 'cs-research/search-knowledge-base/search.py' ] if base else []),
    pathlib.Path.home() / '.claude/skills/search-knowledge-base/search.py',
    pathlib.Path('bundled-skills/cs-research/search-knowledge-base/search.py'),
]
script = next((str(p) for p in candidates if pathlib.Path(p).exists()), None)
if not script:
    print('[KB] search.py not found — use Inline Fallback'); sys.exit(1)
orig_q = os.environ.get('BIGPIE_EMBED_QUERY', '')
for idx, q in enumerate(['[中文查询词]', '[英文查询词]']):
    cmd = [sys.executable, script, '[库]', q, '--limit', '8']
    if idx == 0 and orig_q:
        cmd += ['--embed-text', orig_q]
    subprocess.run(cmd)
"
```

**两库（4 次查询）：**

```bash
BIGPIE_EMBED_QUERY="[原始查询]" python3 -c "
import subprocess, sys, os, pathlib
base = os.environ.get('BIGPIE_BUNDLED_SKILLS_DIR', '')
candidates = [
    *([ pathlib.Path(base) / 'cs-research/search-knowledge-base/search.py' ] if base else []),
    pathlib.Path.home() / '.claude/skills/search-knowledge-base/search.py',
    pathlib.Path('bundled-skills/cs-research/search-knowledge-base/search.py'),
]
script = next((str(p) for p in candidates if pathlib.Path(p).exists()), None)
if not script:
    print('[KB] search.py not found — use Inline Fallback'); sys.exit(1)
orig_q = os.environ.get('BIGPIE_EMBED_QUERY', '')
for mode in ['hotspot', 'papers']:
    for idx, q in enumerate(['[中文查询词]', '[英文查询词]']):
        cmd = [sys.executable, script, mode, q, '--limit', '8']
        if idx == 0 and orig_q:
            cmd += ['--embed-text', orig_q]
        subprocess.run(cmd)
"
```

按标题/URL 去重合并所有结果。

---

## Step 3：充足性判断

充足性由**深度质量**和**时效质量**两个维度判断，**任一不满足则追加 web_search**。

**维度一：深度质量**

| 深度 | 信号词 | 充足标准 |
|------|--------|----------|
| 浅析 | 了解/概览/有没有/大概/简单 | ≥ 1 条高度相关且摘要实质（> 50 字） |
| 深析 | 梳理/综述/全面/系统/详细/所有相关/autoresearch | ≥ 3 条不同角度的相关结果，能支撑多维分析 |

信号词不明显时默认**浅析**。

**维度二：时效质量**（仅当查询含时效词：今天/本周/最近/latest/just released）

热点库每日更新、论文库覆盖近半年，时效词本身不触发 web。
若所有结果的 date 均超过 30 天，则时效不足。

**硬兜底（无条件追加）：** 检索结果为 0，或全部明显偏题。

**追加 web_search：**

```bash
python3 -c "
import subprocess, sys, os, pathlib
base = os.environ.get('BIGPIE_BUNDLED_SKILLS_DIR', '')
candidates = [
    *([ pathlib.Path(base) / 'cs-research/web-search/search.py' ] if base else []),
    pathlib.Path.home() / '.claude/skills/web-search/search.py',
]
script = next((str(p) for p in candidates if pathlib.Path(p).exists()), None)
if script:
    subprocess.run([sys.executable, script, '[英文查询词]', '--max', '5'])
else:
    print('[web] web-search unavailable')
"
```

---

## Step 4：回答合成

先过滤，再合成：

1. **相关性过滤**：丢弃与问题明显偏题的条目，宁可答案短，不混入不相干内容
2. **格式按深度区分**：
   - 浅析/动态类 → 叙述段落，不粘贴原始条目
   - 深析/论文梳理类 → 允许结构化列表（每条含标题、简评、链接）
3. **不暴露检索过程**：正文不出现"来自知识库/检索到/查询了"等，链接自然列于文末
4. **日期自然嵌入**："近期（2026 年 6 月）…"，而非 "[2026-06-15] 标题"

---

## Inline Fallback（search.py 不可用时）

中英文各执行一次，将 `[库]` 替换为 `hotspot` 或 `papers`，`[查询词]` 替换为实际词：

```bash
python3 - [库] "[查询词]" 8 <<'PYEOF'
import sys, sqlite3, os, json
mode, query = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
if sys.platform == 'darwin':
    default_kb_dir = os.path.expanduser('~/Library/Application Support/dr-pie/kb-cache')
elif sys.platform == 'win32':
    default_kb_dir = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~/AppData/Roaming'), 'dr-pie', 'kb-cache')
else:
    default_kb_dir = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'drpie', 'kb-cache')
kb_dir = os.environ.get('BIGPIE_KB_DIR') or default_kb_dir
db = os.path.join(kb_dir, mode, 'snapshot.db')
if not os.path.exists(db):
    print(json.dumps([])); sys.exit(0)
conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
ascii_tokens = [t for t in query.split() if all(ord(c) < 256 for c in t) and len(t) >= 2]
q = ' '.join(ascii_tokens) if ascii_tokens else f'"{query}"'
try:
    if mode == 'hotspot':
        rows = conn.execute(
            'SELECT h.* FROM hotspot_fts fts JOIN hotspot_items h ON h.rowid=fts.rowid'
            ' WHERE hotspot_fts MATCH ? ORDER BY rank LIMIT ?', (q, limit)).fetchall()
    else:
        rows = conn.execute(
            'SELECT p.* FROM papers_fts fts JOIN papers p ON p.rowid=fts.rowid'
            ' WHERE papers_fts MATCH ? ORDER BY rank LIMIT ?', (q, limit)).fetchall()
    print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
except Exception as e:
    print(json.dumps({'error': str(e)}))
conn.close()
PYEOF
```
