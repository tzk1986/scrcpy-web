"""用户设置存储：config/user.yaml 的读、合并、原子写（方案 45 T2）。

写接口自身不调 reload_settings——生效统一由 config_watch 对 user.yaml
的 mtime 轮询驱动（方案 45 §5.2，避免双通道竞态）。
"""
from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml

from app.core.exceptions import SettingsStoreError
from config.settings import config_base_dir

# 方案 58 C-09：user.yaml「读→改→写」全链串行化。并发 PUT 在 await 点
# 交错会造成丢更新（后写者整档覆盖，抹掉前者刚写入的节）。
_user_config_lock = asyncio.Lock()


def user_yaml_path() -> Path:
    """用户覆盖层路径（源码=仓库 config/，frozen=exe 同级 config/，与 watcher 同源）。"""
    return config_base_dir() / "user.yaml"


def read_user_config() -> dict[str, Any]:
    """读取用户覆盖层；文件缺失视为空。语法错/根非映射 → SettingsStoreError。"""
    path = user_yaml_path()
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise SettingsStoreError(f"读取 {path.name} 失败：{e}") from e
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise SettingsStoreError(
            f"{path.name} 根节点必须是映射（当前为 {type(loaded).__name__}）"
        )
    return loaded


def write_user_config(config: dict[str, Any]) -> None:
    """原子写回：先写 *.tmp 再 os.replace（防半截 yaml，Review Focus #2）。"""
    path = user_yaml_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as e:
        raise SettingsStoreError(f"写入 {path.name} 失败：{e}") from e


async def mutate_user_config(
    mutate: Callable[[dict[str, Any]], Any | Awaitable[Any]],
) -> None:
    """
    持锁完成 user.yaml 的「读→改→写」全链（方案 58 C-09）。

    参数：
        mutate: 接收 user 映射、就地修改的回调；返回 awaitable 则 await 之。

    异常：
        SettingsStoreError: 读/写失败（read/write_user_config 原样上抛）。
    """
    async with _user_config_lock:
        user = read_user_config()
        result = mutate(user)
        if inspect.isawaitable(result):
            await result
        write_user_config(user)


def merge_section(user: dict[str, Any], section: str, patch: dict[str, Any] | None) -> None:
    """按节合并提交子集；patch=None 清除该节；既有节非映射 → SettingsStoreError。"""
    if patch is None:
        user.pop(section, None)
        return
    existing = user.get(section)
    if existing is None:
        user[section] = dict(patch)
    elif isinstance(existing, dict):
        existing.update(patch)
    else:
        raise SettingsStoreError(
            f"{user_yaml_path().name} 的 {section} 节不是映射，无法合并（请先修复文件）"
        )
