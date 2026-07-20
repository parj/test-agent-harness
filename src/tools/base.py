"""
Tool registry. Tools are async functions registered with the @tool
decorator; their input schema comes from a pydantic model so every
provider adapter can translate the same JSONSchema into its own wire
format.

A tool may also declare an `approval_check` — a callable that inspects a
concrete input and returns an ApprovalRequest when a human should sign
off before execution (e.g. an expensive scan). The runtime consults it
each time the tool is about to run.
"""
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Type

from pydantic import BaseModel


@dataclass
class ApprovalRequest:
    """Details shown to the human approver before a gated tool runs."""
    tool: str
    summary: str                    # e.g. the SQL text
    estimated_rows: Optional[int] = None
    estimated_cost: Optional[float] = None   # USD
    detail: dict = field(default_factory=dict)


@dataclass
class ToolRegistration:
    name: str
    description: str
    input_model: Type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    requires_approval: bool = False
    # Optional per-invocation gate: (input) -> ApprovalRequest | None.
    approval_check: Optional[Callable[[BaseModel], Optional[ApprovalRequest]]] = None

    def check_approval(self, raw_input: dict) -> Optional[ApprovalRequest]:
        parsed = self.input_model(**raw_input)
        if self.approval_check is not None:
            return self.approval_check(parsed)
        if self.requires_approval:
            return ApprovalRequest(tool=self.name, summary=str(raw_input))
        return None


_REGISTRY: dict[str, ToolRegistration] = {}


def tool(
    name: str | None = None,
    description: str | None = None,
    requires_approval: bool = False,
    approval_check: Optional[Callable] = None,
):
    """Registers an async function as an agent tool. The function must take
    a single pydantic-model argument that defines its input schema."""
    def decorator(fn):
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        if len(params) != 1 or not (
            isinstance(params[0].annotation, type)
            and issubclass(params[0].annotation, BaseModel)
        ):
            raise TypeError(f"Tool {fn.__name__} must take exactly one pydantic model argument")
        reg = ToolRegistration(
            name=name or fn.__name__,
            description=description or (fn.__doc__ or "").strip(),
            input_model=params[0].annotation,
            handler=fn,
            requires_approval=requires_approval,
            approval_check=approval_check,
        )
        _REGISTRY[reg.name] = reg
        return fn
    return decorator


def get_tool(name: str) -> ToolRegistration:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown tool: {name}")
    return _REGISTRY[name]


def list_tools() -> list[ToolRegistration]:
    return list(_REGISTRY.values())


def get_tool_schemas() -> list[dict]:
    """Provider-agnostic tool descriptions: name, description, JSONSchema input."""
    return [
        {
            "name": reg.name,
            "description": reg.description,
            "input_schema": reg.input_model.model_json_schema(),
        }
        for reg in _REGISTRY.values()
    ]


async def execute_tool(name: str, raw_input: dict) -> Any:
    reg = get_tool(name)
    parsed = reg.input_model(**(raw_input or {}))
    result = reg.handler(parsed)
    if inspect.isawaitable(result):
        result = await result
    return result
