"""设置读写 HTTP 端点（方案 45 T2；devices 节 T8 扩展）。

层：接口 → HTTP。

    GET /api/settings — 生效值 + 来源（default/user/env）合并视图
    PUT /api/settings — 白名单校验后合并写入 config/user.yaml
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, model_validator

from app.core import settings_store
from app.core.exceptions import SettingsStoreError
from config.settings import (
    DeviceId,
    DeviceOverride,
    RecordingOverride,
    StreamOverride,
    load_yaml_config,
    settings,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_STREAM_FORM_KEYS = ("max_size", "bit_rate", "fps", "codec", "adaptive_bitrate")
_RECORDING_FORM_KEYS = ("bit_rate", "time_limit")


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream: StreamOverride | None = None
    recording: RecordingOverride | None = None
    # 设备级覆盖（方案 45 T8）：devices.<id> 节合并；整设备传 null = 删除该设备覆盖
    devices: dict[DeviceId, DeviceOverride | None] | None = None

    @model_validator(mode="after")
    def _require_any_section(self) -> "SettingsPatch":
        if not self.model_fields_set:
            raise ValueError("至少提交一个配置节（stream/recording/devices）")
        return self


def _merge_devices(user: dict[str, Any], patch: dict[str, DeviceOverride | None]) -> None:
    """devices.<id> 节合并：整设备传 null = 删除；否则逐节 merge 提交子集。"""
    if not patch:
        return
    existing = user.get("devices")
    if existing is None:
        existing = {}
        user["devices"] = existing
    elif not isinstance(existing, dict):
        raise SettingsStoreError(
            f"{settings_store.user_yaml_path().name} 的 devices 节不是映射，无法合并（请先修复文件）"
        )
    for dev_id, override in patch.items():
        if override is None:
            existing.pop(dev_id, None)
            continue
        dev_cfg = existing.setdefault(dev_id, {})
        if not isinstance(dev_cfg, dict):
            raise SettingsStoreError(f"devices.{dev_id} 不是映射，无法合并")
        for section in ("stream", "recording"):
            section_override = getattr(override, section)
            if section_override is None:
                continue
            section_patch = section_override.model_dump(exclude_unset=True, exclude_none=True)
            if not section_patch:
                continue
            target = dev_cfg.get(section)
            if target is None:
                dev_cfg[section] = section_patch
            elif isinstance(target, dict):
                target.update(section_patch)
            else:
                raise SettingsStoreError(f"devices.{dev_id}.{section} 不是映射，无法合并")
        if override.alias is not None:
            stripped = override.alias.strip()
            if stripped:
                dev_cfg["alias"] = stripped
            else:
                dev_cfg.pop("alias", None)  # 空串 = 清除（方案 50 D1）


def _source_of(
    env_cfg: dict[str, Any], user_cfg: dict[str, Any], section: str, key: str
) -> str:
    env_section = env_cfg.get(section)
    if isinstance(env_section, dict) and key in env_section:
        return "env"
    user_section = user_cfg.get(section)
    if isinstance(user_section, dict) and key in user_section:
        return "user"
    return "default"


@router.get("")
async def get_settings_payload() -> dict[str, Any]:
    """生效值（settings() 合并结果）+ 来源（配置文件层次判定）。"""
    s = settings()
    env = os.getenv("APP_ENV", "base")
    env_cfg = {} if env == "base" else load_yaml_config(env)
    env_cfg = env_cfg if isinstance(env_cfg, dict) else {}
    user_cfg = settings_store.read_user_config()
    return {
        "stream": {
            key: {
                "value": getattr(s.stream, key),
                "source": _source_of(env_cfg, user_cfg, "stream", key),
            }
            for key in _STREAM_FORM_KEYS
        },
        "recording": {
            key: {
                "value": getattr(s.recording, key),
                "source": _source_of(env_cfg, user_cfg, "recording", key),
            }
            for key in _RECORDING_FORM_KEYS
        },
        "devices": {
            dev_id: override.model_dump(exclude_none=True)
            for dev_id, override in s.devices.items()
        },
    }


@router.put("")
async def put_settings_payload(patch: SettingsPatch) -> dict[str, bool]:
    """节级合并写入（仅 model_fields_set 内的节生效；节传 null = 清除该节）。"""
    user = settings_store.read_user_config()
    if "stream" in patch.model_fields_set:
        stream_patch = (
            None if patch.stream is None
            else patch.stream.model_dump(exclude_unset=True, exclude_none=True)
        )
        settings_store.merge_section(user, "stream", stream_patch)
    if "recording" in patch.model_fields_set:
        recording_patch = (
            None if patch.recording is None
            else patch.recording.model_dump(exclude_unset=True, exclude_none=True)
        )
        settings_store.merge_section(user, "recording", recording_patch)
    if "devices" in patch.model_fields_set and patch.devices is not None:
        _merge_devices(user, patch.devices)
    try:
        settings_store.write_user_config(user)
    except OSError as e:
        # 存储层内部已把 OSError 转 SettingsStoreError；此处兜底保证任何写盘
        # OSError 都响亮映射为 500 SETTINGS_STORE_ERROR（不泄 INTERNAL_ERROR）
        raise SettingsStoreError(f"写入用户设置失败：{e}") from e
    return {"saved": True}
