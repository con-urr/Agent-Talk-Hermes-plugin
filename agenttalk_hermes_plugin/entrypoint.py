from __future__ import annotations

from .cli import agenttalk_command, register_cli


def register(ctx) -> None:
    ctx.register_cli_command(
        name="agenttalk",
        help="Control AgentTalk supervisor and wake settings",
        setup_fn=register_cli,
        handler_fn=agenttalk_command,
        description=(
            "Configure the AgentTalk supervisor for Hermes, start or stop the "
            "local supervisor, and toggle wake dispatch separately."
        ),
    )
