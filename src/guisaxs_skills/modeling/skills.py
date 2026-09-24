"""Skill names owned exclusively by modeling mini-apps (never liveview SkillRunner)."""

from __future__ import annotations

MODELING_SKILLS = frozenset(
    {
        "model_bodies",
        "model_dam",
        "model_density",
        "model_mixture",
        # later: "model_dr_mc",
    }
)
