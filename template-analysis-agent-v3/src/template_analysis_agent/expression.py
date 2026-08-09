"""Constrained natural-language expression providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

import yaml

from .errors import ExpressionError
from .models import (
    ExpressionResult,
    Fact,
    FactBundle,
    NarrativeBlock,
    NarrativeDraft,
)


class ExpressionProvider(Protocol):
    name: str

    def express(
        self,
        bundle: FactBundle,
        expression: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> ExpressionResult: ...


class ExpressionAssetLoader:
    """Load style, glossary, and paired examples for prompt construction."""

    def __init__(self, assets_directory: Path):
        self.assets_directory = assets_directory

    def load_style(self, style_id: str) -> dict[str, Any]:
        path = self.assets_directory / "styles" / f"{style_id}.yaml"
        if not path.is_file():
            raise ExpressionError(f"找不到表达风格：{style_id}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ExpressionError(f"表达风格格式错误：{path}")
        return value

    def load_glossary(self) -> dict[str, Any]:
        path = self.assets_directory / "glossary.yaml"
        if not path.is_file():
            return {}
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def load_examples(
        self, tags: list[str], limit: int = 2
    ) -> list[dict[str, Any]]:
        index_path = self.assets_directory / "examples" / "index.yaml"
        if not index_path.is_file():
            return []
        index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        result: list[dict[str, Any]] = []
        for item in index.get("examples", []):
            item_tags = set(item.get("tags", []))
            if tags and not item_tags.intersection(tags):
                continue
            facts_path = index_path.parent / str(item["facts"])
            markdown_path = index_path.parent / str(item["markdown"])
            if not facts_path.is_file() or not markdown_path.is_file():
                continue
            result.append(
                {
                    "facts": json.loads(facts_path.read_text(encoding="utf-8")),
                    "markdown": markdown_path.read_text(encoding="utf-8").strip(),
                }
            )
            if len(result) >= limit:
                break
        return result


def _operator_phrase(operator: str) -> str:
    return {
        "eq": "等于",
        "gt": "高于",
        "gte": "不低于",
        "lt": "低于",
        "lte": "不高于",
        "is_missing": "缺失",
        "not_missing": "非缺失",
    }.get(operator, "符合")


def _deterministic_block(fact: Fact) -> NarrativeBlock:
    if fact.fact_type == "summary":
        markdown = f"- {fact.label}：{fact.display_value}。"
    else:
        rules = (fact.condition or {}).get("rules", [])
        if len(rules) == 1:
            relation = _operator_phrase(str(rules[0]["operator"]))
            threshold = (
                fact.threshold_display
                if fact.threshold_display is not None
                else ""
            )
            condition_text = f"{relation}{threshold}"
        elif fact.threshold_display:
            condition_text = f"符合组合规则（参考阈值{fact.threshold_display}）"
        else:
            condition_text = "符合组合规则"
        if fact.organizations:
            organizations = "、".join(fact.organizations)
            markdown = (
                f"- {fact.label}{condition_text}的机构共{fact.count}家："
                f"{organizations}。"
            )
        else:
            markdown = f"- {fact.label}{condition_text}的机构为0家。"
    return NarrativeBlock(fact_refs=[fact.fact_id], markdown=markdown)


class DeterministicExpressionProvider:
    """Safe offline provider used for tests and final fallback."""

    name = "deterministic"

    def express(
        self,
        bundle: FactBundle,
        expression: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> ExpressionResult:
        del expression, repair_errors
        blocks = [
            _deterministic_block(fact)
            for fact in bundle.facts
            if fact.required
        ]
        return ExpressionResult(
            draft=NarrativeDraft(scene_id=bundle.scene_id, blocks=blocks),
            provider=self.name,
        )


class ReplayExpressionProvider:
    """Return pre-recorded drafts for deterministic offline tests."""

    name = "replay"

    def __init__(self, drafts: dict[str, NarrativeDraft]):
        self.drafts = drafts

    def express(
        self,
        bundle: FactBundle,
        expression: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> ExpressionResult:
        del expression, repair_errors
        try:
            draft = self.drafts[bundle.scene_id]
        except KeyError as exc:
            raise ExpressionError(f"没有回放文案：{bundle.scene_id}") from exc
        return ExpressionResult(draft=draft, provider=self.name)


class DeepSeekExpressionProvider:
    """LangChain ChatDeepSeek provider using Pydantic JSON output."""

    name = "deepseek"

    def __init__(
        self,
        assets: ExpressionAssetLoader,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com",
    ):
        try:
            from langchain_deepseek import ChatDeepSeek
        except ImportError as exc:
            raise ExpressionError("未安装 langchain-deepseek") from exc
        key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise ExpressionError("使用 DeepSeek 表达需要 DEEPSEEK_API_KEY")
        self.assets = assets
        self.model_name = model or os.getenv(
            "DEEPSEEK_MODEL",
            "deepseek-v4-flash",
        )
        chat = ChatDeepSeek(
            model=self.model_name,
            api_key=key,
            base_url=base_url,
            temperature=0,
            max_retries=2,
            timeout=90,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.structured_model = chat.with_structured_output(
            NarrativeDraft,
            method="json_mode",
            include_raw=True,
        )

    def express(
        self,
        bundle: FactBundle,
        expression: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> ExpressionResult:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError as exc:
            raise ExpressionError("未安装 langchain-core") from exc

        style_id = str(expression.get("style_profile", "monthly-operation-report"))
        tags = [str(tag) for tag in expression.get("example_tags", [])]
        style = self.assets.load_style(style_id)
        glossary = self.assets.load_glossary()
        examples = self.assets.load_examples(tags)
        system = (
            "你是经营分析报告表达器。只能表达 FactBundle 中已有事实，禁止计算、"
            "补充数据、推断原因、生成建议或改变机构名单。每个文本块必须列出其"
            "使用的 fact_refs。输出必须是符合 NarrativeDraft schema 的 JSON。"
        )
        payload = {
            "scene": {
                "id": bundle.scene_id,
                "title": bundle.scene_title,
                "parameters": bundle.parameters,
            },
            "facts": [fact.model_dump(mode="json") for fact in bundle.facts],
            "style": style,
            "glossary": glossary,
            "examples": examples,
            "repair_errors": repair_errors or [],
            "json_shape_example": {
                "scene_id": bundle.scene_id,
                "blocks": [
                    {
                        "fact_refs": ["example.fact.id"],
                        "markdown": "- 示例事实表达。",
                    }
                ],
            },
        }
        messages = [
            SystemMessage(content=system),
            HumanMessage(
                content=json.dumps(payload, ensure_ascii=False, indent=2)
            ),
        ]
        try:
            response = self.structured_model.invoke(messages)
        except Exception as exc:
            raise ExpressionError(f"DeepSeek 表达失败：{exc}") from exc
        parsed = response.get("parsed") if isinstance(response, dict) else response
        if parsed is None:
            parsing_error = (
                response.get("parsing_error")
                if isinstance(response, dict)
                else "empty parsed response"
            )
            raise ExpressionError(f"DeepSeek 结构化输出失败：{parsing_error}")
        draft = (
            parsed
            if isinstance(parsed, NarrativeDraft)
            else NarrativeDraft.model_validate(parsed)
        )
        raw = response.get("raw") if isinstance(response, dict) else None
        raw_content = None
        usage: dict[str, Any] = {}
        if raw is not None:
            raw_content = (
                raw.content
                if isinstance(getattr(raw, "content", None), str)
                else json.dumps(getattr(raw, "content", None), ensure_ascii=False)
            )
            usage = dict(getattr(raw, "usage_metadata", None) or {})
        return ExpressionResult(
            draft=draft,
            raw_response=raw_content,
            usage=usage,
            provider=self.name,
            model=self.model_name,
        )
