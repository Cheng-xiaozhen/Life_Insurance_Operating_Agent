# 寿险经营分析助手 Agent - MVP

## 快速启动

```bash
cd mvp
pip install -r requirements.txt
python app.py
```

打开浏览器访问 `http://localhost:8000`

## 配置 DeepSeek（可选）

设置环境变量启用 LLM 增强分析：

```bash
export DEEPSEEK_API_KEY="your-api-key"
```

不配置也可运行，系统会使用规则引擎生成基础分析。

## 项目结构

```
mvp/
├── app.py                     # FastAPI 后端主程序
├── requirements.txt           # Python 依赖
├── static/
│   └── index.html             # 前端对话界面
├── knowledge_graph/
│   ├── scenarios.json         # 7 个分析场景定义
│   ├── patterns.json          # 6 个通用分析模式
│   └── metrics.json           # 42 个指标定义
├── mock_data/
│   └── may_data.json          # 基于五月总结文档的模拟数据
└── README.md
```

## 支持场景

| 场景 | 类别 | 关键指标 |
|------|------|----------|
| 标保达成分析 | 保费 | 达成额、达成率、同比、全年进度 |
| 价值达成分析 | 保费 | 价值额、达成率、同比 |
| 活动/阳光人力分析 | 人力 | 活动人力、阳光人力、阳光占比 |
| 主管活动/标准组 | 人力 | 活动率、双星率、标准组占比 |
| 新增人力分析 | 人力 | 新增率、首阳率、学历结构 |
| 同引分析 | 人力 | 送训、上岗达成、挂零识别 |
| 综合评级（红黄蓝） | 综合 | 四维度跑赢大盘评估 |

## MVP 验证目标

- [x] 自然语言问句 → 场景匹配
- [x] 场景匹配 → 数据查询
- [x] 数据分析 → 结论生成
- [x] 推理过程可视化
- [x] 图表自动渲染
- [ ] DeepSeek LLM 增强分析（需配置 API Key）
- [ ] 多轮对话追问
