"""Alert system (§37). V1: console (rich) + structured log. Email /
webhook / desktop sinks are stubbed with a clean interface."""
from __future__ import annotations

import abc

from rich.console import Console
from rich.panel import Panel

from ..core.logging import get_logger

log = get_logger("monitoring.alerts")


class AlertSink(abc.ABC):
    @abc.abstractmethod
    async def alert(self, title: str, body: str) -> None: ...


class ConsoleAlertSink(AlertSink):
    def __init__(self):
        self.console = Console()

    async def alert(self, title: str, body: str) -> None:
        self.console.print(Panel(body, title=f"[bold red]{title}[/bold red]"))
        log.warning(f"ALERT {title}: {body}")


class EmailAlertSink(AlertSink):
    """V1 STUB (§37). Interface is stable; wire SMTP config later."""

    async def alert(self, title: str, body: str) -> None:
        raise NotImplementedError(
            "Email alerts are a v1 stub (spec §37). Use ConsoleAlertSink.")


class WebhookAlertSink(AlertSink):
    """V1 STUB (§37). Interface is stable; wire webhook URL later."""

    async def alert(self, title: str, body: str) -> None:
        raise NotImplementedError(
            "Webhook alerts are a v1 stub (spec §37). Use ConsoleAlertSink.")


class MultiSink(AlertSink):
    def __init__(self, sinks: list[AlertSink]):
        self.sinks = sinks

    async def alert(self, title: str, body: str) -> None:
        for sink in self.sinks:
            try:
                await sink.alert(title, body)
            except NotImplementedError:
                pass
