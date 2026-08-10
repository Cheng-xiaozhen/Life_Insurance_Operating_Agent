# 模板驱动分析 Agent V4

V4 是一个刻意保持轻量的分析内核：模板决定分析步骤，Python 代码确定性地产出事实，LLM 只负责把事实写成自然语言。

```text
问题 → 规则路由 → YAML 模板 → 确定性事实 → 受约束表达 → 校验 → 报告
```

首版只包含“标保”和“新增”两个场景，以及 `summarize`、`rank`、`classify` 三种操作。不包含数据源、数据库、网页、图表或多轮会话。

## 安装

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\template-analysis-agent-v4 `
  --no-build-isolation
```

项目使用 LangChain 的 `ChatDeepSeek` 集成。把 API Key 写入仓库根目录的 `.env`：

```dotenv
DEEPSEEK_API_KEY="your-key"
```

默认配置为 `base_url=https://api.deepseek.com`、`model=deepseek-v4-flash`；可用环境变量 `DEEPSEEK_MODEL` 覆盖模型名。

## 一键验证

```powershell
template-analysis-v4 validate-templates

template-analysis-v4 analyze `
  --question "分析本月标保" `
  --input .\template-analysis-agent-v4\examples\standard-premium.json `
  --provider deterministic `
  --output .\template-analysis-agent-v4\runs\standard-premium-demo
```

输出目录只包含 `report.md` 和 `run.json`。为避免误覆盖，目录已经存在时命令会失败。

新增场景示例：

```powershell
template-analysis-v4 analyze `
  --question "分析新增人力" `
  --input .\template-analysis-agent-v4\examples\recruitment.json `
  --output .\template-analysis-agent-v4\runs\recruitment-demo
```

DeepSeek 只替换表达阶段。调用失败或文案校验不通过时，系统立即降级为规则表达，并把模型、Token 用量和降级原因写入 `run.json`：

```powershell
template-analysis-v4 analyze `
  --question "分析本月标保" `
  --input .\template-analysis-agent-v4\examples\standard-premium.json `
  --provider deepseek `
  --output .\template-analysis-agent-v4\outputs\deepseek-demo
```

## Python API

```python
from pathlib import Path

from template_analysis_agent_v4 import AnalysisAgent, AnalysisDataset, AnalysisRequest

dataset = AnalysisDataset.model_validate_json(
    Path("template-analysis-agent-v4/examples/standard-premium.json")
    .read_text(encoding="utf-8")
)
agent = AnalysisAgent("template-analysis-agent-v4/templates")
result = agent.analyze(AnalysisRequest(question="分析本月标保", dataset=dataset))
print(result.report_markdown)
```

## 测试

```powershell
$env:PYTHONPATH=(Resolve-Path .\template-analysis-agent-v4\src).Path
.\.venv\Scripts\python.exe -m unittest discover `
  -s .\template-analysis-agent-v4\tests -v
```

标准测试完全离线，不调用真实模型。
