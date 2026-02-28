"""TitanFlow Plugin SDK — stable API for plugin authors.

This module defines the contracts that plugins implement.
Import ONLY from this module — never from plugin_manager or internal runtime.

Plugin types:
    ToolPlugin  — A capability the LLM can invoke during conversation
    ModulePlugin — Background service with start/stop lifecycle
    HookPlugin  — Event interceptor (message:before, message:after, etc.)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class PluginContext:
    """Injected at load time. Provides access to TitanFlow services.

    Plugin authors use this to interact with the host system without
    importing any internal TitanFlow modules.
    """

    instance_name: str  # "TitanFlow" or "TitanFlow-Ollie"
    config: dict[str, Any]  # Plugin-specific config from manifest + YAML overrides
    send_message: Callable[..., Awaitable[None]]  # async (chat_id, text) -> None
    llm_chat: Callable[..., Awaitable[str]]  # async (messages) -> str
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("titanflow.plugin"))

    # Optional capabilities — set by runtime if available
    mem0_recall: Callable[..., Awaitable[list[str]]] | None = None
    mem0_store: Callable[..., Awaitable[bool]] | None = None


class ToolPlugin(ABC):
    """A tool the LLM can invoke during conversation.

    Implement this to give Flow/Ollie a new capability.
    The LLM sees `name()`, `description()`, and `parameters()`,
    and can request execution by outputting a JSON tool call.

    Example:
        class Plugin(ToolPlugin):
            def name(self) -> str: return "my_tool"
            def description(self) -> str: return "Does something useful"
            def parameters(self) -> dict: return {"type": "object", "properties": {...}}
            async def execute(self, ctx, params) -> str: return "result"
    """

    @abstractmethod
    def name(self) -> str:
        """Unique tool name (snake_case). Used in LLM tool calls."""
        ...

    @abstractmethod
    def description(self) -> str:
        """Human-readable description shown to the LLM."""
        ...

    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema for tool parameters."""
        ...

    @abstractmethod
    async def execute(self, ctx: PluginContext, params: dict) -> str:
        """Execute the tool and return a text result."""
        ...

    def __repr__(self) -> str:
        return f"<ToolPlugin:{self.name()}>"


class ModulePlugin(ABC):
    """Background service with lifecycle management.

    Implement this for long-running services (schedulers, watchers, feeds).
    """

    @abstractmethod
    async def start(self, ctx: PluginContext) -> None:
        """Initialize resources, schedule jobs, etc."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Clean up on shutdown."""
        ...


class HookPlugin(ABC):
    """Event interceptor.

    Implement this to filter, transform, or observe events.
    Return the (possibly modified) data dict, or None to suppress the event.
    """

    @abstractmethod
    def event(self) -> str:
        """Event name to intercept.

        Built-in events:
            message:before  — Before LLM processes a user message
            message:after   — After LLM generates a response
            startup         — Engine startup complete
            shutdown        — Engine shutting down
        """
        ...

    @abstractmethod
    async def handle(self, ctx: PluginContext, data: dict) -> dict | None:
        """Process the event. Return modified data or None to suppress."""
        ...
