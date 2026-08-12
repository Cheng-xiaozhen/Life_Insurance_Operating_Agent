# 模板驱动分析 Agent V4

V4 是一个刻意保持轻量的分析内核：模板决定分析步骤，Python 代码确定性地产出事实，LLM 只负责把事实写成自然语言。

```text
问题 → 规则路由 → YAML 模板 → 确定性事实 → 受约束表达 → 校验 → 报告
```

当前包含标保、价值、活动人力、阳光人力、主管活动、主管双星、标准组、新增、同引九个场景，以及 `summarize`、`rank`、`classify` 三种操作。独立场景接受标准化 JSON；完整月报可直接读取仓库 `docs` 下九个约定名称的 CSV。不包含数据库、网页、图表或多轮会话。

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

## 快速运行

```powershell
template-analysis-v4 `
  --templates .\template-analysis-agent-v4\templates `
  analyze `
  --question "分析本月标保" `
  --input .\template-analysis-agent-v4\examples\standard-premium.json `
  --output .\template-analysis-agent-v4\runs\standard-premium-demo
```

输出目录只包含 `report.md` 和 `run.json`。阶段日志同时输出到终端，并默认保存在输出目录旁的同名 `.log` 文件中；例如上述命令会生成 `runs/standard-premium-demo.log`。为避免误覆盖，输出目录已经存在时命令会失败。

可用 `--log-file` 指定其他日志路径：

```powershell
template-analysis-v4 analyze `
  --question "分析本月标保" `
  --input .\template-analysis-agent-v4\examples\standard-premium.json `
  --output .\template-analysis-agent-v4\runs\standard-premium-demo `
  --log-file .\template-analysis-agent-v4\runs\standard-premium-demo.debug.log
```

日志文件使用 UTF-8 编码；每次有效运行开始前会删除同路径旧日志，只保留本次运行记录。日志文件应位于输出目录之外。

新增场景示例：

```powershell
template-analysis-v4 analyze `
  --question "分析新增人力" `
  --input .\template-analysis-agent-v4\examples\recruitment.json `
  --output .\template-analysis-agent-v4\runs\recruitment-demo
```

## 完整月度报告

从仓库现有九个 CSV 生成完整月报：

```powershell
template-analysis-v4 monthly-report `
  --data-dir .\docs `
  --report-month-name "五月" `
  --data-month-name "5月" `
  --quarter-name "二季度" `
  --cutoff-date "5月31日" `
  --output .\template-analysis-agent-v4\outputs\may-monthly-report
```

如需临时验证 Agent 主链路，可增加 `--allow-invalid-expression`。此时文案校验仍会执行并记录告警及校验结果，但不会阻断报告生成；默认仍保持严格校验。

命令会在调用模型前预检并标准化九个 CSV，然后按标保、价值、活动人力、阳光人力、主管活动、主管双星、标准组、新增、同引的固定顺序逐场景调用 DeepSeek。九个场景全部成功并通过事实校验后才会生成报告；任一场景失败时不生成部分报告。

成功运行后，输出目录包含完整的 `report.md` 和不含原始 CSV 内容的 `run.json`，日志仍保存在输出目录旁的同名 `.log` 文件中。

DeepSeek 是唯一的表达路径，只负责把确定性事实润色为自然语言。调用失败或文案校验不通过时，本次分析返回失败，不生成规则文案作为降级结果。

## Python API

```python
import json
from pathlib import Path

from src import (
    AnalysisAgent,
    AnalysisDataset,
    AnalysisRequest,
    OrganizationRow,
)

raw = json.loads(
    Path("template-analysis-agent-v4/examples/standard-premium.json").read_text(
        encoding="utf-8"
    )
)
dataset = AnalysisDataset(
    summary=raw["summary"],
    rows=[OrganizationRow(**row) for row in raw["rows"]],
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

标准测试通过注入假 LLM 完全离线运行，不调用真实模型；月报回归会将九场景共 73 条事实与 V2 五月报告 context 逐项比较。
