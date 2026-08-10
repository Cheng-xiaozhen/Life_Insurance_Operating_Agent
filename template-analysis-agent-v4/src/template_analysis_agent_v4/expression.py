"""Deterministic and constrained LLM narration with factual validation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .models import (
    ConfigurationError,
    ExpressionError,
    Fact,
    NarrativeBlock,
    ValidationResult,
)


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?")
COUNT_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])(\d+)\s*家")
BANNED_TERMS = {"原因", "可能", "预计", "建议", "优化", "改进"}


class ExpressionProvider(Protocol):
    name: str

    def express(self, title: str, facts: list[Fact]) -> list[NarrativeBlock]: ...


def _number_strings(value: object) -> set[str]:
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        number = float(value)
        normalized = str(int(number)) if number.is_integer() else str(number)
        result = {normalized}
        if number < 0:
            result.add(normalized.removeprefix("-"))
        return result
    return set(NUMBER_PATTERN.findall(str(value)))


def _nested_numbers(value: object) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(_nested_numbers(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(_nested_numbers(item))
        return result
    return _number_strings(value)


def _fact_numbers(fact: Fact) -> set[str]:
    result: set[str] = set()
    for value in (
        fact.raw_value,
        fact.display_value,
        fact.count,
        fact.rule,
        fact.rule_text,
    ):
        result.update(_nested_numbers(value))
    for item in fact.items:
        result.update(_number_strings(item.raw_value))
        result.update(_number_strings(item.display_value))
    return result


def _contains_one(text_numbers: set[str], value: object) -> bool:
    expected = _number_strings(value)
    return not expected or bool(expected & text_numbers)


class NarrativeValidator:
    """Validate one-fact blocks so numbers cannot drift between metrics."""

    def validate(
        self,
        facts: list[Fact],
        blocks: list[NarrativeBlock],
        organization_universe: list[str],
    ) -> ValidationResult:
        errors: list[str] = []
        fact_index = {fact.fact_id: fact for fact in facts}
        seen: set[str] = set()
        for index, block in enumerate(blocks, start=1):
            if block.fact_id not in fact_index:
                errors.append(f"第{index}个文本块引用未知事实：{block.fact_id}")
                continue
            if block.fact_id in seen:
                errors.append(f"事实被重复表达：{block.fact_id}")
                continue
            seen.add(block.fact_id)
            fact = fact_index[block.fact_id]
            text_numbers = set(NUMBER_PATTERN.findall(block.text))
            unsupported = sorted(text_numbers - _fact_numbers(fact))
            if unsupported:
                errors.append(
                    f"{fact.fact_id} 包含未授权数字：{'、'.join(unsupported)}"
                )
            mentioned = {
                name for name in organization_universe if name and name in block.text
            }
            allowed_organizations = {item.organization for item in fact.items}
            extra = sorted(mentioned - allowed_organizations)
            missing = sorted(allowed_organizations - mentioned)
            if extra:
                errors.append(f"{fact.fact_id} 包含未授权机构：{'、'.join(extra)}")
            if missing:
                errors.append(f"{fact.fact_id} 遗漏机构：{'、'.join(missing)}")
            if fact.kind == "summary" and not _contains_one(
                text_numbers, fact.raw_value
            ):
                errors.append(f"{fact.fact_id} 没有表达汇总值")
            if fact.kind == "ranking":
                used_counts = set(COUNT_PATTERN.findall(block.text))
                if str(fact.count) not in used_counts:
                    errors.append(f"{fact.fact_id} 没有表达排名数量")
                for item in fact.items:
                    if not _contains_one(text_numbers, item.raw_value):
                        errors.append(
                            f"{fact.fact_id} 没有表达 {item.organization} 的指标值"
                        )
            if fact.kind == "classification":
                used_counts = set(COUNT_PATTERN.findall(block.text))
                if str(fact.count) not in used_counts:
                    errors.append(f"{fact.fact_id} 没有表达机构数量")
                for rule in (fact.rule or {}).get("rules", []):
                    threshold = rule.get("threshold")
                    if threshold is not None and not _contains_one(
                        text_numbers, threshold
                    ):
                        errors.append(
                            f"{fact.fact_id} 没有表达阈值 {threshold}"
                        )
            banned = sorted(term for term in BANNED_TERMS if term in block.text)
            if banned:
                errors.append(f"{fact.fact_id} 包含禁止表达：{'、'.join(banned)}")
        required = {fact.fact_id for fact in facts if fact.required}
        missing_facts = sorted(required - seen)
        if missing_facts:
            errors.append("缺少必需事实：" + "、".join(missing_facts))
        return ValidationResult(valid=not errors, errors=errors)


class DeterministicExpressionProvider:
    name = "deterministic"

    def express(self, title: str, facts: list[Fact]) -> list[NarrativeBlock]:
        del title
        blocks: list[NarrativeBlock] = []
        for fact in facts:
            if fact.kind == "summary":
                text = f"- {fact.title}：{fact.display_value}。"
            elif fact.kind == "ranking":
                items = "、".join(
                    f"{item.organization}（{item.display_value}）"
                    for item in fact.items
                )
                text = f"- {fact.title}共{fact.count}家：{items}。"
            elif fact.items:
                organizations = "、".join(item.organization for item in fact.items)
                text = (
                    f"- {fact.title}（{fact.rule_text}）共{fact.count}家："
                    f"{organizations}。"
                )
            else:
                text = f"- {fact.title}（{fact.rule_text}）共0家。"
            blocks.append(NarrativeBlock(fact_id=fact.fact_id, text=text))
        return blocks


class DeepSeekExpressionProvider:
    """One structured LangChain call through the official DeepSeek endpoint."""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com",
        env_file: str | Path | None = None,
    ):
        key = api_key or os.getenv("DEEPSEEK_API_KEY") or _dotenv_value(
            "DEEPSEEK_API_KEY", env_file
        )
        if not key:
            raise ConfigurationError("使用 DeepSeek 表达需要 DEEPSEEK_API_KEY")
        try:
            from langchain_deepseek import ChatDeepSeek
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ConfigurationError("请安装依赖：langchain-deepseek") from exc
        self.model_name = model or os.getenv(
            "DEEPSEEK_MODEL", "deepseek-v4-flash"
        )
        self.last_usage: dict[str, object] = {}
        chat = ChatDeepSeek(
            model=self.model_name,
            api_key=key,
            base_url=base_url,
            temperature=0,
            max_tokens=8192,
            timeout=90,
            max_retries=2,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.structured_model = chat.with_structured_output(
            _NarrativeEnvelope,
            method="json_mode",
            include_raw=True,
        )

    def express(self, title: str, facts: list[Fact]) -> list[NarrativeBlock]:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ConfigurationError("请安装依赖：langchain-core") from exc
        payload = {
            "title": title,
            "facts": [fact.model_dump(mode="json") for fact in facts],
            "json_output_example": {
                "blocks": [{"fact_id": "事实ID", "text": "- 文案。"}]
            },
        }
        messages = [
            SystemMessage(
                content=(
                    "你是经营分析事实表达器。只能改写输入 facts 中已有事实；"
                    "禁止计算、补充数字、改变机构名单、归因、推测或提出建议。"
                    "每个 block 只表达一个事实，每个事实恰好输出一次。"
                    "summary 必须写出 title 和 display_value；"
                    "ranking 必须用阿拉伯数字写出‘共N家’，并逐一写出所有机构及其 display_value；"
                    "classification 必须完整写出 rule_text，用阿拉伯数字写出‘共N家’，"
                    "并逐一写出所有机构；当 count 为0时必须写‘共0家’。"
                    "只返回符合 json_output_example 形状的 JSON 对象。"
                )
            ),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        try:
            response = self.structured_model.invoke(messages)
            parsed = response.get("parsed") if isinstance(response, dict) else response
            if parsed is None:
                parsing_error = (
                    response.get("parsing_error")
                    if isinstance(response, dict)
                    else "empty parsed response"
                )
                raise ExpressionError(f"DeepSeek 结构化输出失败：{parsing_error}")
            envelope = (
                parsed
                if isinstance(parsed, _NarrativeEnvelope)
                else _NarrativeEnvelope.model_validate(parsed)
            )
            raw = response.get("raw") if isinstance(response, dict) else None
            self.last_usage = dict(getattr(raw, "usage_metadata", None) or {})
            return envelope.blocks
        except ExpressionError:
            raise
        except Exception as exc:
            raise ExpressionError(f"DeepSeek 调用失败：{exc}") from exc


class _NarrativeEnvelope(BaseModel):
    """Schema enforced by LangChain JSON mode."""

    model_config = ConfigDict(extra="forbid")
    blocks: list[NarrativeBlock]


def _dotenv_value(name: str, env_file: str | Path | None = None) -> str | None:
    """Read one value from .env without adding a dotenv runtime dependency."""

    if env_file is not None:
        candidates = [Path(env_file)]
    else:
        module_path = Path(__file__).resolve()
        candidates = [
            Path.cwd() / ".env",
            module_path.parents[2] / ".env",
            module_path.parents[3] / ".env",
        ]
    visited: set[Path] = set()
    for candidate in candidates:
        path = candidate.resolve()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value or None
    return None


def assemble_report(title: str, facts: list[Fact], blocks: list[NarrativeBlock]) -> str:
    block_index = {block.fact_id: block for block in blocks}
    sections = [f"# {title}"]
    current_section: str | None = None
    current_lines: list[str] = []
    for fact in facts:
        if fact.section != current_section:
            if current_section is not None:
                sections.append(f"## {current_section}\n\n" + "\n".join(current_lines))
            current_section = fact.section
            current_lines = []
        current_lines.append(block_index[fact.fact_id].text.strip())
    if current_section is not None:
        sections.append(f"## {current_section}\n\n" + "\n".join(current_lines))
    return "\n\n".join(sections).rstrip() + "\n"
