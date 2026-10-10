"""Configuration snapshot, diff, restore.

The configuration surface for sawii-persona is:

  - Settings dataclass fields (config.py)
  - Prompt strings (brains/prompts.py)
  - Feature coefficients embedded in feature modules (e.g., mood bias,
    inertia half-life, recall lane weights - wherever they live in code)

This module snapshots and restores the Settings + prompts portion. Feature
coefficients in code are out of scope for the v1 editor; the editor will
operate against the configurable surface and prompt files only.

Usage:
    snap = Snapshot.capture(settings, prompts_module)
    snap.save("/tmp/before.json")
    later = Snapshot.load("/tmp/before.json")
    later.apply(settings, prompts_module)
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import os
from dataclasses import dataclass, field
from typing import Any


# Prompts that are part of the lobe contract. Editing them changes lobe
# behavior. Listed here so the editor knows what it can touch.
EDITABLE_PROMPTS = [
    "A_BASE",
    "B_SYSTEM",
    "C_SYSTEM",
    "B_LANE_SYSTEM",
    "LIVE_CHECK",
    "REMEMBER",
    "REMEMBER_LIVE",
    "REMEMBER_DEEP",
    "REMEMBER_IDLE",
    "GUT_READ",
    "AUDIT",
    "CALIBRATE",
    "OUTREACH",
    "OUTREACH_CHECK",
    "DEEP_PLAN",
    "DEEP_CRITIC",
    "DEEP_REDUCE",
    "DREAM",
    "NARRATIVE",
    "MIRROR",
    "REPLAY",
    "A_DIARY",
    "TRY_TO_REMEMBER",
]


@dataclass
class Snapshot:
    config_id: str
    settings: dict
    prompts: dict
    captured_at: float

    @classmethod
    def capture(cls, settings, prompts_module, *, config_id: str = "default") -> "Snapshot":
        import time
        s_dict = _dataclass_to_dict(settings)
        p_dict = {name: getattr(prompts_module, name) for name in EDITABLE_PROMPTS
                  if hasattr(prompts_module, name)}
        return cls(config_id=config_id, settings=s_dict, prompts=p_dict, captured_at=time.time())

    def apply(self, settings, prompts_module) -> None:
        """Restore settings and prompts from this snapshot."""
        _dict_to_dataclass(self.settings, settings)
        for name, value in self.prompts.items():
            if hasattr(prompts_module, name):
                setattr(prompts_module, name, value)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "config_id": self.config_id,
                "settings": self.settings,
                "prompts": self.prompts,
                "captured_at": self.captured_at,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Snapshot":
        with open(path) as f:
            data = json.load(f)
        return cls(
            config_id=data["config_id"],
            settings=data["settings"],
            prompts=data["prompts"],
            captured_at=data["captured_at"],
        )

    def diff(self, other: "Snapshot") -> dict:
        """Return settings/prompts that differ between this and other."""
        s_diff = {k: (self.settings.get(k), other.settings.get(k))
                  for k in set(self.settings) | set(other.settings)
                  if self.settings.get(k) != other.settings.get(k)}
        p_diff = {k: (self.prompts.get(k), other.prompts.get(k))
                  for k in set(self.prompts) | set(other.prompts)
                  if self.prompts.get(k) != other.prompts.get(k)}
        return {"settings": s_diff, "prompts": p_diff}


def _dataclass_to_dict(obj: Any) -> dict:
    if dataclasses.is_dataclass(obj):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(v) for v in obj]
    return obj


def _dict_to_dataclass(d: dict, obj: Any) -> None:
    if not dataclasses.is_dataclass(obj):
        if isinstance(obj, dict):
            for k, v in d.items():
                if k in obj:
                    obj[k] = v
        return
    for f in dataclasses.fields(obj):
        if f.name in d:
            current = getattr(obj, f.name)
            new = d[f.name]
            if dataclasses.is_dataclass(current):
                _dict_to_dataclass(new, current)
            else:
                setattr(obj, f.name, new)


def load_prompts_module():
    from sawii_persona.brains import prompts
    return prompts