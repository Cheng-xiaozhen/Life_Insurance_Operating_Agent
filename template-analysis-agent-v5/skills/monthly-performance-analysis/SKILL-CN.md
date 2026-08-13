---
name: monthly-performance-analysis
description: 使用本技能内置的确定性运行时和 DeepSeek 叙述能力，基于九份外部 CSV 数据集生成固定的五月寿险月度业绩分析报告。当用户要求进行月度分析、月度经营分析、生成月报、生成五月业绩分析报告，或重新生成当前五月报告时使用本技能。不得用于其他月份、单一业务场景或指标、模板维护或临时 CSV 分析。
---

# 月度业绩分析

使用本技能内置的代码、模板、数据源配置和报告配方生成固定的五月报告。将九份源 CSV 文件保留在技能目录之外，并将其所在目录传给运行程序。

## 内置资源

- `scripts/run_monthly_report.py` 是稳定的命令行入口。
- `scripts/monthly_analysis/` 包含确定性的报告运行时。
- `templates/*.yaml` 包含九个场景模板。
- `templates/profiles/` 包含外部 CSV 绑定配置。
- `templates/reports/` 包含月度报告组装配方。

## 工作流程

1. 如果请求未明确指定月份，则将其视为生成固定五月报告的请求。如果用户明确要求其他月份，说明 MVP 仅支持五月，然后停止。
2. 找到一个包含以下全部九份必需 CSV 文件的外部目录：`标保.csv`、`价值.csv`、`活动人力.csv`、`阳光人力.csv`、`主管活动.csv`、`主管双星.csv`、`标准组.csv`、`新增.csv` 和 `同引.csv`。优先使用用户提供的路径；否则，如果当前工作区下存在 `docs`，则使用该目录。如果无法确定唯一目录，应向用户询问路径，不得猜测。
3. 以本文件所在目录为基准解析 `scripts/`，然后使用 Python 3.11 或更高版本运行 `run_monthly_report.py --data-dir <data-directory>`。如果缺少依赖，则将 `scripts/requirements.txt` 安装到当前环境中，然后重试一次。
4. 读取运行程序输出的 JSON 对象。如果状态为 `failed`，报告错误和日志路径，不得编造报告或只生成部分报告。
5. 如果状态为 `completed`，读取 `report_path` 并返回完整的 Markdown 报告。同时使用绝对路径提供 `report_path`、`run_path` 和 `log_path` 的链接。

示例：

```powershell
python <skill-directory>\scripts\run_monthly_report.py `
  --data-dir C:\path\to\may-data
```

在环境变量或工作目录下的 `.env` 文件中设置 `DEEPSEEK_API_KEY`。也可以设置 `DEEPSEEK_MODEL`；否则，内置运行时将使用其已配置的默认模型。

## 约束规则

- 仅运行本技能目录下的代码和分析资源。不得从相邻的源代码检出目录导入或复制运行时文件。
- 将源 CSV 文件保留在技能目录之外，并以只读方式使用。
- 固定上下文必须保持为 `五月`、`5月`、`二季度` 和 `5月31日`。
- 生成报告时不得重新计算事实、重新解释阈值或编辑内置模板。
- 可以容许叙述验证警告，但不得因此降低对数据、计算或模型调用失败的处理标准。
- 将生成的报告写入技能目录之外。
