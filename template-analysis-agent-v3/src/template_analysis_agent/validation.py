"""Factual validation for LLM-generated narrative blocks."""

from __future__ import annotations

import re
from typing import Iterable

from .models import Fact, FactBundle, NarrativeDraft, ValidationReport


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?")
COUNT_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])(\d+)\s*家")
LIST_SEGMENT_PATTERN = re.compile(r"[:：]([^。；;\n]+)")
DEFAULT_BANNED_TERMS = {
    "建议",
    "预计",
    "推测",
    "可能",
    "原因",
    "由于",
    "因为",
    "因此",
    "可能是因为",
    "主要原因",
    "应当",
    "需要加强",
    "措施",
    "行动方案",
    "优化",
    "改进",
}
RELATION_WORDS = {
    "eq": ("等于", "为0", "挂0"),
    "gt": ("高于", "超过", "超"),
    "gte": ("不低于", "达到", "达成"),
    "lt": ("低于", "不足"),
    "lte": ("不高于", "不超过"),
    "is_missing": ("缺失",),
    "not_missing": ("非缺失", "有值"),
}
DIRECTION_WORDS = {
    "increase": ("增长", "提升", "正增"),
    "decrease": ("下降", "降低", "负增"),
    "flat": ("持平",),
}


def _number_strings(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        number = float(value)
        normalized = str(int(number)) if number.is_integer() else str(number)
        result = {normalized}
        if number < 0:
            result.add(normalized.removeprefix("-"))
        return result
    return set(NUMBER_PATTERN.findall(str(value)))


def _allowed_numbers(facts: Iterable[Fact], bundle: FactBundle) -> set[str]:
    result: set[str] = set()
    for value in bundle.parameters.values():
        result.update(_number_strings(value))
    for fact in facts:
        result.update(_number_strings(fact.raw_value))
        result.update(_number_strings(fact.display_value))
        result.update(_number_strings(fact.threshold))
        result.update(_number_strings(fact.threshold_display))
        result.update(_number_strings(fact.count))
        for rule in (fact.condition or {}).get("rules", []):
            result.update(_number_strings(rule.get("threshold")))
    return result


class NarrativeValidator:
    """Reject unsupported numbers, entities, relations, and advice."""

    def __init__(self, banned_terms: set[str] | None = None):
        self.banned_terms = banned_terms or DEFAULT_BANNED_TERMS

    def validate(
        self,
        bundle: FactBundle,
        draft: NarrativeDraft,
    ) -> ValidationReport:
        errors: list[str] = []
        if draft.scene_id != bundle.scene_id:
            errors.append(
                f"scene_id 不一致：{draft.scene_id} != {bundle.scene_id}"
            )
        fact_index = {fact.fact_id: fact for fact in bundle.facts}
        covered: set[str] = set()
        for block_index, block in enumerate(draft.blocks, start=1):
            if not block.fact_refs:
                errors.append(f"第{block_index}个文本块没有 fact_refs")
                continue
            unknown = [ref for ref in block.fact_refs if ref not in fact_index]
            if unknown:
                errors.append(
                    f"第{block_index}个文本块引用未知事实：{', '.join(unknown)}"
                )
                continue
            covered.update(block.fact_refs)
            referenced = [fact_index[ref] for ref in block.fact_refs]
            allowed_numbers = _allowed_numbers(referenced, bundle)
            used_numbers = set(NUMBER_PATTERN.findall(block.markdown))
            unsupported_numbers = sorted(used_numbers - allowed_numbers)
            if unsupported_numbers:
                errors.append(
                    f"第{block_index}个文本块包含未授权数字："
                    + "、".join(unsupported_numbers)
                )

            allowed_organizations = {
                organization
                for fact in referenced
                for organization in fact.organizations
            }
            mentioned = {
                organization
                for organization in bundle.organization_universe
                if organization and organization in block.markdown
            }
            unsupported_organizations = sorted(mentioned - allowed_organizations)
            if unsupported_organizations:
                errors.append(
                    f"第{block_index}个文本块包含未授权机构："
                    + "、".join(unsupported_organizations)
                )
            classification_facts = [
                fact for fact in referenced if fact.fact_type == "classification"
            ]
            allowed_counts = {
                str(fact.count)
                for fact in classification_facts
                if fact.count is not None
            }
            used_counts = set(COUNT_PATTERN.findall(block.markdown))
            unsupported_counts = sorted(used_counts - allowed_counts)
            if unsupported_counts:
                errors.append(
                    f"第{block_index}个文本块包含与事实不一致的机构数量："
                    + "、".join(unsupported_counts)
                )
            allowed_directions = {
                fact.direction
                for fact in referenced
                if fact.direction in DIRECTION_WORDS
            }
            used_directions = {
                direction
                for direction, words in DIRECTION_WORDS.items()
                if any(word in block.markdown for word in words)
            }
            wrong_directions = sorted(used_directions - allowed_directions)
            if wrong_directions:
                errors.append(
                    f"第{block_index}个文本块包含与事实不一致的方向："
                    + "、".join(wrong_directions)
                )
            for fact in referenced:
                fact_numbers = _number_strings(
                    fact.raw_value
                    if fact.fact_type == "summary"
                    else fact.count
                )
                if fact_numbers and not fact_numbers.intersection(used_numbers):
                    errors.append(
                        f"第{block_index}个文本块没有表达事实值：{fact.fact_id}"
                    )
                if fact.fact_type != "classification":
                    continue
                if fact.threshold is not None:
                    threshold_numbers = _number_strings(fact.threshold)
                    if not threshold_numbers.intersection(used_numbers):
                        errors.append(
                            f"第{block_index}个文本块没有表达阈值：{fact.fact_id}"
                        )
                if not fact.organizations:
                    continue
                missing = [
                    organization
                    for organization in fact.organizations
                    if organization not in block.markdown
                ]
                if missing:
                    errors.append(
                        f"第{block_index}个文本块遗漏 {fact.fact_id} 的机构："
                        + "、".join(missing)
                    )
                rules = (fact.condition or {}).get("rules", [])
                if len(rules) == 1:
                    operator = str(rules[0].get("operator"))
                    expected = RELATION_WORDS.get(operator, ())
                    if expected and not any(
                        word in block.markdown for word in expected
                    ):
                        errors.append(
                            f"第{block_index}个文本块没有正确表达运算符 "
                            f"{operator}：{fact.fact_id}"
                        )
            if len(classification_facts) == 1 and classification_facts[0].organizations:
                segments = LIST_SEGMENT_PATTERN.findall(block.markdown)
                listed = {
                    token.strip()
                    for segment in segments
                    for token in re.split(r"[、,，]", segment)
                    if token.strip()
                }
                expected = set(classification_facts[0].organizations)
                unknown_list_items = sorted(listed - expected)
                if not segments:
                    errors.append(
                        f"第{block_index}个文本块没有显式机构名单"
                    )
                elif unknown_list_items:
                    errors.append(
                        f"第{block_index}个文本块包含事实外名单项："
                        + "、".join(unknown_list_items)
                    )
            used_banned = sorted(
                term for term in self.banned_terms if term in block.markdown
            )
            if used_banned:
                errors.append(
                    f"第{block_index}个文本块包含禁止表达："
                    + "、".join(used_banned)
                )
        required = {fact.fact_id for fact in bundle.facts if fact.required}
        missing_facts = sorted(required - covered)
        if missing_facts:
            errors.append("缺少必需事实：" + "、".join(missing_facts))
        return ValidationReport(
            scene_id=bundle.scene_id,
            valid=not errors,
            errors=errors,
        )
