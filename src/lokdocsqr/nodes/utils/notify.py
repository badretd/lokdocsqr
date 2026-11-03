"""utils.notify — send notifications via email or webhook.

Supported channels: ``email`` and ``webhook``. Delivery is fail-soft:
any delivery exception is returned as ``{sent: False, error, error_count: 1}``
instead of being raised. Channel validation errors are raised before dispatch.
"""
from __future__ import annotations

from typing import Any, ClassVar

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.nodes.flow._base import _FlowBase


@register_node("utils.notify")
class Notify(_FlowBase):
    id = "utils.notify"
    retry_safe: ClassVar[bool] = False

    @property
    def title(self) -> str:
        return "Notify"

    @property
    def inputs(self) -> dict[str, str]:
        return {
            "channel": "string",
            "title": "string",
            "body": "string",
            "target": "string",
            "config": "object",
        }

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "sent": "boolean",
            "channel": "string",
            "error": "string",
            "error_count": "integer",
            "dry_run": "boolean",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _preview(self, ctx: Any, payload: Any) -> Any:
        # OVERRIDE: never deliver in dry-run.
        channel = payload.get("channel")
        self._validate_channel(channel)
        return {
            "sent": False,
            "channel": channel,
            "error": None,
            "error_count": 0,
            "dry_run": True,
        }

    def _execute(self, ctx: Any, payload: Any) -> Any:
        channel = payload.get("channel")
        title = payload.get("title", "")
        body = payload.get("body", "")
        target = payload.get("target", "")
        config = payload.get("config") or {}

        self._validate_channel(channel)

        try:
            if channel == "email":
                self._send_email(target, title, body, config)
            elif channel == "webhook":
                self._send_webhook(target, title, body, config)
        except Exception as exc:
            return {
                "sent": False,
                "channel": channel,
                "error": str(exc),
                "error_count": 1,
                "dry_run": False,
            }
        return {
            "sent": True,
            "channel": channel,
            "error": None,
            "error_count": 0,
            "dry_run": False,
        }

    @staticmethod
    def _validate_channel(channel: Any) -> None:
        if channel == "desktop":
            raise NodeExecutionError(
                "notify: desktop channel is not supported; "
                "use 'email' or 'webhook'"
            )
        if channel not in ("email", "webhook"):
            raise NodeExecutionError(
                f"notify: invalid channel {channel!r}, "
                f"expected 'email' or 'webhook'"
            )

    def _send_email(
        self, target: str, title: str, body: str, config: dict[str, Any]
    ) -> None:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        if not target or "@" not in target:
            raise NodeExecutionError(
                f"notify: email channel requires 'target' as valid address, "
                f"got {target!r}"
            )
        host = config.get("smtp_host")
        if not host:
            raise NodeExecutionError(
                "notify: email channel requires config.smtp_host"
            )
        port = config.get("smtp_port", 587)
        from_addr = config.get("from_addr", config.get("smtp_user", ""))
        if not from_addr:
            raise NodeExecutionError(
                "notify: email channel requires config.from_addr or "
                "config.smtp_user"
            )

        msg = MIMEMultipart()
        msg["Subject"] = title or "LokDocSQR notification"
        msg["From"] = from_addr
        msg["To"] = target
        msg.attach(MIMEText(body or "", "plain", "utf-8"))

        timeout = config.get("timeout", 10)
        if config.get("smtp_tls", True):
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.starttls()
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        try:
            if config.get("smtp_user"):
                server.login(
                    config["smtp_user"], config.get("smtp_password", "")
                )
            server.send_message(msg)
        finally:
            server.quit()

    def _send_webhook(
        self, target: str, title: str, body: str, config: dict[str, Any]
    ) -> None:
        import json
        import urllib.request

        if not target or not target.startswith(("http://", "https://")):
            raise NodeExecutionError(
                f"notify: webhook channel requires 'target' as http(s) URL, "
                f"got {target!r}"
            )
        data = json.dumps({"title": title, "body": body}).encode("utf-8")
        req = urllib.request.Request(
            target,
            data=data,
            method=config.get("method", "POST"),
            headers={
                "Content-Type": "application/json",
                **config.get("headers", {}),
            },
        )
        with urllib.request.urlopen(
            req, timeout=config.get("timeout", 10)
        ) as resp:
            status = resp.status
        if status >= 400:
            raise NodeExecutionError(
                f"notify: webhook returned HTTP {status}"
            )
