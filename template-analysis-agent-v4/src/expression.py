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
    NarrativeGroupSpec,
    Scalar,
    ValidationResult,
)


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?")
COUNT_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])(\d+)\s*家")
BANNED_TERMS = {
    "原因",
    "可能",
    "预计",
    "建议",
    "优化",
    "改进",
    "由于",
    "因为",
    "应当",
    "措施",
}
DIRECTION_WORDS = {
    "increase": ("增长", "提升", "正增"),
    "decrease": ("下降", "降低", "负增"),
    "flat": ("持平",),
}


class ExpressionProvider(Protocol):
    def express(
        self,
        title: str,
        facts: list[Fact],
        groups: list[NarrativeGroupSpec],
        context: dict[str, Scalar],
    ) -> list[NarrativeBlock]: ...


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
        fact.rule_text,
    ):
        result.update(_nested_numbers(value))
    if fact.rule:
        display_threshold = fact.rule.get("display_threshold")
        if display_threshold:
            result.update(_nested_numbers(display_threshold))
        else:
            result.update(_nested_numbers(fact.rule.get("rules", [])))
    for item in fact.items:
        result.update(_number_strings(item.raw_value))
        result.update(_number_strings(item.display_value))
    return result


def _contains_one(text_numbers: set[str], value: object) -> bool:
    expected = _number_strings(value)
    return not expected or bool(expected & text_numbers)


class NarrativeValidator:
    """Validate grouped narrative blocks against their referenced facts."""

    def validate(
        self,
        facts: list[Fact],
        blocks: list[NarrativeBlock],
        organization_universe: list[str],
        groups: list[NarrativeGroupSpec],
        context: dict[str, Scalar],
    ) -> ValidationResult:
        errors: list[str] = []
        fact_index = {fact.fact_id: fact for fact in facts}
        group_index = {group.id: group for group in groups}
        seen_groups: set[str] = set()
        seen_facts: set[str] = set()
        for index, block in enumerate(blocks, start=1):
            if block.group_id not in group_index:
                errors.append(f"第{index}个文本块引用未知分组：{block.group_id}")
                continue
            if block.group_id in seen_groups:
                errors.append(f"文案分组被重复表达：{block.group_id}")
                continue
            seen_groups.add(block.group_id)
            expected_ids = group_index[block.group_id].fact_ids
            if block.fact_ids != expected_ids:
                errors.append(
                    f"{block.group_id} 的 fact_ids 与模板分组不一致"
                )
                continue
            duplicate_facts = sorted(set(block.fact_ids) & seen_facts)
            if duplicate_facts:
                errors.append("事实被重复表达：" + "、".join(duplicate_facts))
                continue
            seen_facts.update(block.fact_ids)
            referenced = [fact_index[fact_id] for fact_id in block.fact_ids]
            text_numbers = set(NUMBER_PATTERN.findall(block.text))
            allowed_numbers: set[str] = set()
            for fact in referenced:
                allowed_numbers.update(_fact_numbers(fact))
            for value in context.values():
                allowed_numbers.update(_number_strings(value))
            unsupported = sorted(text_numbers - allowed_numbers)
            if unsupported:
                errors.append(
                    f"{block.group_id} 包含未授权数字：{'、'.join(unsupported)}"
                )
            mentioned = {
                name for name in organization_universe if name and name in block.text
            }
            allowed_organizations = {
                item.organization for fact in referenced for item in fact.items
            }
            extra = sorted(mentioned - allowed_organizations)
            missing = sorted(allowed_organizations - mentioned)
            if extra:
                errors.append(
                    f"{block.group_id} 包含未授权机构：{'、'.join(extra)}"
                )
            if missing:
                errors.append(f"{block.group_id} 遗漏机构：{'、'.join(missing)}")
            used_counts = set(COUNT_PATTERN.findall(block.text))
            allowed_counts = {
                str(fact.count)
                for fact in referenced
                if fact.kind in {"ranking", "classification"}
                and fact.count is not None
            }
            unexpected_counts = sorted(used_counts - allowed_counts)
            if unexpected_counts:
                errors.append(
                    f"{block.group_id} 包含未授权机构数量："
                    + "、".join(unexpected_counts)
                )
            for fact in referenced:
                if fact.kind == "summary" and not _contains_one(
                    text_numbers, fact.raw_value
                ):
                    errors.append(f"{fact.fact_id} 没有表达汇总值")
                if fact.kind == "ranking":
                    if str(fact.count) not in used_counts:
                        errors.append(f"{fact.fact_id} 没有表达排名数量")
                    for item in fact.items:
                        if not _contains_one(text_numbers, item.raw_value):
                            errors.append(
                                f"{fact.fact_id} 没有表达 "
                                f"{item.organization} 的指标值"
                            )
                if fact.kind == "classification":
                    if str(fact.count) not in used_counts:
                        errors.append(f"{fact.fact_id} 没有表达机构数量")
                    display_threshold = (fact.rule or {}).get(
                        "display_threshold"
                    )
                    thresholds = (
                        [display_threshold.get("value")]
                        if display_threshold
                        else [
                            rule.get("threshold")
                            for rule in (fact.rule or {}).get("rules", [])
                        ]
                    )
                    for threshold in thresholds:
                        if threshold is not None and not _contains_one(
                            text_numbers, threshold
                        ):
                            errors.append(
                                f"{fact.fact_id} 没有表达阈值 {threshold}"
                            )
            allowed_directions = {
                fact.direction for fact in referenced if fact.direction
            }
            used_directions = {
                direction
                for direction, words in DIRECTION_WORDS.items()
                if any(word in block.text for word in words)
            }
            wrong_directions = sorted(used_directions - allowed_directions)
            if wrong_directions:
                errors.append(
                    f"{block.group_id} 包含错误方向："
                    + "、".join(wrong_directions)
                )
            banned = sorted(term for term in BANNED_TERMS if term in block.text)
            if banned:
                errors.append(
                    f"{block.group_id} 包含禁止表达：{'、'.join(banned)}"
                )
        missing_groups = [group.id for group in groups if group.id not in seen_groups]
        if missing_groups:
            errors.append("缺少必需文案分组：" + "、".join(missing_groups))
        required = {fact.fact_id for fact in facts if fact.required}
        missing_facts = sorted(required - seen_facts)
        if missing_facts:
            errors.append("缺少必需事实：" + "、".join(missing_facts))
        return ValidationResult(valid=not errors, errors=errors)


class DeepSeekExpressionProvider:
    """One structured LangChain call through the official DeepSeek endpoint."""

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
        model_name = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        chat = ChatDeepSeek(
            model=model_name,
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
        )

    def express(
        self,
        title: str,
        facts: list[Fact],
        groups: list[NarrativeGroupSpec],
        context: dict[str, Scalar],
    ) -> list[NarrativeBlock]:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ConfigurationError("请安装依赖：langchain-core") from exc
        payload = {
            "title": title,
            "context": context,
            "facts": [fact.to_dict() for fact in facts],
            "groups": [group.to_dict() for group in groups],
            "json_output_example": {
                "blocks": [
                    {
                        "group_id": "分组ID",
                        "fact_ids": ["事实ID"],
                        "text": "- 文案。",
                    }
                ]
            },
        }
        messages = [
            SystemMessage(
                content=(
                    "你是经营分析事实表达器。只能改写输入 facts 中已有事实；"
                    "禁止计算、补充数字、改变机构名单、归因、推测或提出建议。"
                    "必须严格按 groups 输出，一个 group 对应一个 block，group_id 和 "
                    "fact_ids 必须原样返回，每个事实恰好表达一次。"
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
            envelope = (
                response
                if isinstance(response, _NarrativeEnvelope)
                else _NarrativeEnvelope.model_validate(response)
            )
            return envelope.blocks
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


def assemble_report(
    title: str,
    groups: list[NarrativeGroupSpec],
    blocks: list[NarrativeBlock],
) -> str:
    block_index = {block.group_id: block for block in blocks}
    sections = [f"# {title}"]
    for group in groups:
        sections.append(
            f"## {group.title}\n\n{block_index[group.id].text.strip()}"
        )
    return "\n\n".join(sections).rstrip() + "\n"
