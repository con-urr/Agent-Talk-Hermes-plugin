from __future__ import annotations

from pathlib import Path

from .cli import agenttalk_command, register_cli


def _register_skills(ctx) -> None:
    register_skill = getattr(ctx, "register_skill", None)
    if not callable(register_skill):
        return
    skills_dir = Path(__file__).resolve().parent / "skills"
    if not skills_dir.is_dir():
        return
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file():
            register_skill(
                child.name,
                skill_md,
                description="Use AgentTalk from Hermes safely and manage the plugin supervisor.",
            )


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
    _register_skills(ctx)
