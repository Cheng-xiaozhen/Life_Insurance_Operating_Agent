from __future__ import annotations

import json
import re
from collections import deque
from typing import Any, Callable, Protocol

from .models import AgentContractError, RouteDecision, SceneNarrativeResult
from .router import normalize_month


class LLMClient(Protocol):
    model_name: str

    def route(
        self,
        message: str,
        candidates: list[dict[str, Any]],
        *,
        pending_report_id: str | None = None,
        pending_parameters: list[str] | None = None,
    ) -> RouteDecision | dict[str, Any]: ...

    def analyze_scene(
        self,
        scene_context: dict[str, Any],
        *,
        feedback: str | None = None,
    ) -> SceneNarrativeResult | dict[str, Any]: ...


def _extract_month(text: str) -> dict[str, str]:
    candidates = re.findall(
        r"(?:\d{4}年\s*)?(?:1[0-2]|0?[1-9])\s*月|(?:十一|十二|十|[一二三四五六七八九])月",
        text,
    )
    if not candidates:
        return {}
    report_month, month_label = normalize_month(candidates[-1])
    return {"report_month": report_month, "month_label": month_label}


class FakeLLMClient:
    """Deterministic offline client with optional scripted responses."""

    model_name = "fake-template-llm"

    def __init__(
        self,
        route_responses: list[RouteDecision | dict[str, Any] | Exception] | None = None,
        analysis_responses: list[SceneNarrativeResult | dict[str, Any] | Exception] | None = None,
        route_handler: Callable[..., RouteDecision | dict[str, Any]] | None = None,
        analysis_handler: Callable[..., SceneNarrativeResult | dict[str, Any]] | None = None,
    ):
        self._routes = deque(route_responses or [])
        self._analyses = deque(analysis_responses or [])
        self.route_handler = route_handler
        self.analysis_handler = analysis_handler
        self.route_calls = 0
        self.analysis_calls = 0

    @staticmethod
    def _resolve_scripted(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    def route(
        self,
        message: str,
        candidates: list[dict[str, Any]],
        *,
        pending_report_id: str | None = None,
        pending_parameters: list[str] | None = None,
    ) -> RouteDecision | dict[str, Any]:
        self.route_calls += 1
        if self._routes:
            return self._resolve_scripted(self._routes.popleft())
        if self.route_handler:
            return self.route_handler(
                message,
                candidates,
                pending_report_id=pending_report_id,
                pending_parameters=pending_parameters or [],
            )

        if re.search(r"(?:只|仅).{0,6}(?:价值|标保)|(?:价值|标保).{0,6}(?:单独|专项)", message):
            return RouteDecision(
                action="unsupported",
                confidence=0.95,
                reason="当前仅注册完整月度业绩报告，不动态裁剪场景",
            )
        report_id = pending_report_id
        if not report_id and re.search(r"业绩|经营|月报|复盘|分析报告|生成报告", message):
            report_id = candidates[0]["id"] if len(candidates) == 1 else None
        if report_id:
            return RouteDecision(
                action="execute",
                report_id=report_id,
                confidence=0.98,
                extracted_params=_extract_month(message),
                reason="离线规则匹配到月度经营报告",
            )
        return RouteDecision(
            action="unsupported",
            confidence=0.98,
            reason="请求与已注册报告模板无关",
        )

    def analyze_scene(
        self,
        scene_context: dict[str, Any],
        *,
        feedback: str | None = None,
    ) -> SceneNarrativeResult | dict[str, Any]:
        self.analysis_calls += 1
        if self._analyses:
            return self._resolve_scripted(self._analyses.popleft())
        if self.analysis_handler:
            return self.analysis_handler(scene_context, feedback=feedback)
        return SceneNarrativeResult(
            scene_id=scene_context["scene_id"],
            content=scene_context["baseline_narrative"],
            used_fact_ids=list(scene_context["facts"]),
        )


class DeepSeekLLMClient:
    """DeepSeek adapter. Business modules depend only on the local LLM contract."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: int = 120,
    ):
        if not api_key:
            raise AgentContractError("DEEPSEEK_API_KEY is required")
        try:
            from langchain_deepseek import ChatDeepSeek
        except ImportError as exc:
            raise AgentContractError(
                "langchain-deepseek is required for the DeepSeek adapter"
            ) from exc
        self.model_name = model
        self._router_model = ChatDeepSeek(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            timeout=timeout,
            max_retries=1,
        )
        self._analysis_model = ChatDeepSeek(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.1,
            timeout=timeout,
            max_retries=1,
        )

    @staticmethod
    def _content(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content)

    @classmethod
    def _json_object(cls, response: Any) -> dict[str, Any]:
        text = cls._content(response).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise AgentContractError("model response does not contain a JSON object")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AgentContractError(f"model response is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise AgentContractError("model response must be a JSON object")
        return value

    def route(
        self,
        message: str,
        candidates: list[dict[str, Any]],
        *,
        pending_report_id: str | None = None,
        pending_parameters: list[str] | None = None,
    ) -> RouteDecision:
        from langchain_core.messages import HumanMessage, SystemMessage

        schema = {
            "action": "execute | clarify | unsupported",
            "report_id": "candidate id or null",
            "confidence": "number from 0 to 1",
            "extracted_params": "object using only params_schema names",
            "missing_params": "string array",
            "clarification": "string or null",
            "reason": "short string",
        }
        payload = {
            "message": message,
            "candidates": candidates,
            "pending_report_id": pending_report_id,
            "pending_parameters": pending_parameters or [],
            "output_schema": schema,
        }
        response = self._router_model.invoke(
            [
                SystemMessage(
                    content=(
                        "你是受限的报告模板路由器。只能选择候选 report_id；不创建模板，"
                        "不裁剪 Scene。用户只要单独场景且无对应 Report 时返回 unsupported。"
                        "抽取月份时同时给出 report_month（如五月）和 month_label（如5月）。"
                        "只输出一个 JSON 对象。"
                    )
                ),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return RouteDecision.from_mapping(self._json_object(response))

    def analyze_scene(
        self,
        scene_context: dict[str, Any],
        *,
        feedback: str | None = None,
    ) -> SceneNarrativeResult:
        from langchain_core.messages import HumanMessage, SystemMessage

        payload = {
            key: value
            for key, value in scene_context.items()
            if key != "baseline_narrative"
        }
        payload["output_schema"] = {
            "scene_id": "must equal input scene_id",
            "content": "non-empty markdown bullet text",
            "used_fact_ids": "ids from input facts only",
            "warnings": "string array",
        }
        if feedback:
            payload["previous_output_problem"] = feedback
        response = self._analysis_model.invoke(
            [
                SystemMessage(
                    content=(
                        "你是经营分析场景撰写器。只能改写输入 compact facts，不新增机构、"
                        "数字、指标、排名或因果解释，不重新计算。数字照抄 display。"
                        "只输出一个 JSON 对象。"
                    )
                ),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return SceneNarrativeResult.from_mapping(self._json_object(response))
