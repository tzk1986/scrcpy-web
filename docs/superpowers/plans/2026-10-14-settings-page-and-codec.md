# 方案 45：设置页 / codec 放开 / 检查更新 / 设备级覆盖 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把运行参数从「改 yaml」升级为「独立设置页 + 写接口 + codec(h265) 放开 + 检查更新 + 设备级覆盖」，全链按 spec 生效语义落地。

**Architecture:** 配置在既有 `base.yaml + {env}.yaml` 链中插入固定用户层 `user.yaml`（写接口唯一落点，watcher 无条件监听 mtime 触发重载——写接口自身不 reload，避免双通道竞态）；codec 从 `scrcpy.py` 硬编码放开为 `video_codec={opts.codec}`，WS 侧按 encoder codec 惰性切换 HEVC 解析与 hvcC 配置下发；前端解码链路按 codec family 泛化并用 `VideoDecoder.isConfigSupported` 探测门控；检查更新走标准库 urllib 手动触发。

**Tech Stack:** FastAPI + pydantic v2（白名单校验）+ 既有 PyYAML（零新依赖）；Vue 3 + Element Plus + vitest；标准库 `urllib.request`（检查更新，禁第三方）。

**Spec:** `方案/45-FreeControl2对比与参数设置及运行环境优化方案.md`（§4.1/4.2/4.4 拍板 + §5 落地设计 + §六任务分解 + §七风险。spec 是绑定权威；本计划与其冲突处以 spec 为准并记录 ruling。）

## Global Constraints

- **master 直做**，不建 worktree/分支；每任务一个中文提交，**无 Co-Authored-By**；**禁止 push**（push 需用户单独显式授权，不顺延）。
- **零新依赖**：yaml 读写用既有 pyyaml；检查更新用标准库 urllib；前端不加组件库之外的新包。
- **门禁四件套**（每任务收尾全量跑，全绿才算完成）：
  ```bash
  python -m ruff check backend tests
  python -m mypy backend/app          # mypy strict（pyproject 已配），mypy_path=backend
  PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80
  cd frontend && npx vitest run && npx vue-tsc -b
  ```
  备注：`tests/e2e` 需真机不入 CI；`tests/manual/` 同样跑时忽略（pytest 全量命令自然跳过 manual 无测试函数的文件，spike 脚本带 `if __name__ == "__main__"` 保护）。
- **四层架构**：新代码落位 core（settings_store）/interfaces（http）/config（settings）既有层级；application 消费函数单向引用 config；禁止跨层。
- **存量 API 只增不改**：`/api/recording`、`/api/devices` 等既有端点与语义不动；录屏/截图/流链路行为零回归（h264 路径逐回归测试钉住）。
- 测试文件一律放 `tests/` 下（`tests/unit/`、`tests/scrcpy/`、`tests/manual/`）。
- **不得杀掉用户正在使用的 8765 端口进程**；本地验证用 pytest/vitest，真机 spike 单独跑（不影响 8765 服务）。
- Git Bash 下执行 adb 命令带 `MSYS_NO_PATHCONV=1`（T3 真机 spike）。
- 浏览器仅支持 Chrome；h265 不做降级 hack，以 `isConfigSupported` 探测门控。

## Review Focus

1. **user.yaml 被手改成损坏内容**（语法错 / 根非映射 / 未知键 / 越界值）：启动或热重载必须报错并**保旧配置**，不得静默清空或崩溃；GET/PUT 面对坏文件返回 500 `SETTINGS_STORE_ERROR` 可见错误。→ T1（非映射保旧）、T2（坏文件读失败 / PUT 失败用例）。
2. **PUT 并发与中断写**：写路径必须原子落盘（同目录 `*.tmp` + `os.replace`），磁盘上永不出现半截 yaml。→ T2（write_user_config 实现 + tmp 替换语义）。
3. **frozen 只读 config/**（Program Files 安装场景）：PUT 必须 500 `SETTINGS_STORE_ERROR` 明确报错，不得静默回「已保存」。→ T2（monkeypatch write_user_config 抛 OSError）。
4. **h265 混合场景**：后端已是 h265 而当前浏览器不支持 → 设置页 codec 选项禁用并给出原因；SPS 过短时 codec 串构造走兜底串不得崩解码器。→ T3（`_hevc_codec_string` 短 payload 兜底分支）、T4（`isH265DecoderSupported` 三态 + 禁用逻辑）。
5. **devices 键含 `:` `.`**（如 `192.168.8.18:5555`）：yaml 键 safe_dump→safe_load 往返与 GET 回读一致；设备 id 键格式未过白名单（含空格/斜杠）必须 422 拒绝。→ T8（往返测试 + put 拒绝用例）。

## 任务总览

| # | 任务 | 新/改核心文件 | 提交信息 |
|---|---|---|---|
| T1 | 配置分层 user.yaml | config/settings.py、app/core/config_watch.py、.gitignore | feat: 配置分层 user.yaml 加载链与无条件监听（方案 45 T1） |
| T2 | 设置读写接口 | app/core/settings_store.py(新)、app/interfaces/http/settings.py(新)、app/core/exceptions.py、app/main.py、config/settings.py | feat: 设置读写接口 GET/PUT /api/settings（方案 45 T2） |
| T3 | codec 后端放开 | app/scrcpy/{constants,h264_parser,stream_protocol}.py、app/infrastructure/stream/scrcpy.py、app/interfaces/ws/video.py、app/application/stream_service.py | feat: codec 后端放开与 h265 WS 链路（方案 45 T3） |
| T4 | 前端解码链路泛化 | frontend/src/services/{h264NalUtils,codecSupport(新),h264VideoStream}.ts | feat: 前端 h265 解码链路泛化与支持面探测（方案 45 T4） |
| T5 | 独立设置页 | frontend/src/{services/api.ts,router/index.ts,App.vue,views/DeviceDetail.vue,views/Settings.vue(新),components/settings/*(新)} | feat: 独立设置页与设备页跳转入口（方案 45 T5） |
| T6 | 生效参数面板+运行环境 | backend/app/interfaces/http/system.py、Settings.vue、api.ts | feat: 生效参数面板与运行环境信息区（方案 45 T6） |
| T7 | 检查更新 | backend/app/interfaces/http/system.py、Settings.vue、api.ts | feat: scrcpy 检查更新接口与设置页按钮（方案 45 T7） |
| T8 | 设备级覆盖 | config/settings.py、http/settings.py、application/{stream,recording}_service.py、Settings.vue、DeviceDetail.vue | feat: 设备级参数覆盖（方案 45 T8） |
| T9 | 文档回写 | docs/用户手册.md、README.md、方案/进度追踪.md | docs: 方案 45 用户手册与 README 回写（方案 45 T9） |

---

## Task 1: 配置分层 `user.yaml` 加载链与无条件监听

**Files:**
- Modify: `config/settings.py`（`get_settings` 加 user 层合并）
- Modify: `backend/app/core/config_watch.py`（`watched_paths` 无条件含 user.yaml）
- Modify: `.gitignore`（加 `config/user.yaml`）
- Test: `tests/unit/test_config_reload.py`（追加用例）

**Interfaces:**
- Consumes: 既有 `load_yaml_config(env)` / `_deep_merge` / `reload_settings` / `reload_config` / `ConfigWatcher`（均存在）。
- Produces: 加载链 `base → user → {env} → env vars`；`user.yaml` 解析失败与 base 失败同语义（ValueError → reload 保旧）。

- [ ] **Step 1: 写失败测试**（`tests/unit/test_config_reload.py` 追加；文件顶部加 `import copy` 已有，直接加 helper 与用例）

```python
def _layered_loader(layers: dict):
    """按层名取配置的 loader（env 参数即层名；user 层可回非 dict 以测损坏场景）。"""

    def loader(env: str = "base"):
        value = layers.get(env, {})
        return copy.deepcopy(value) if isinstance(value, dict) else value

    return loader


def test_user_layer_overrides_base(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": _base_config(),
            "user": {"stream": {"bit_rate": "6M"}},
        }),
    )
    reloaded = reload_settings()

    assert reloaded.stream.bit_rate == "6M"
    assert reloaded.stream.fps == 30  # 未覆盖键回落默认


def test_env_layer_overrides_user(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": _base_config(),
            "user": {"stream": {"bit_rate": "6M"}},
            "production": {"stream": {"bit_rate": "8M"}},
        }),
    )
    assert reload_settings().stream.bit_rate == "8M"


def test_user_layer_non_mapping_keeps_old_config(monkeypatch):
    old = settings()

    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({"base": _base_config(), "user": ["not", "a", "map"]}),
    )
    result = reload_config()

    assert result["reloaded"] is False
    assert "user.yaml" in result["error"]
    assert settings() is old


def test_watched_paths_includes_user_yaml_unconditionally():
    assert "user.yaml" in [p.name for p in watched_paths()]


def test_watcher_detects_first_creation_of_user_yaml(tmp_path, monkeypatch):
    target = tmp_path / "user.yaml"  # 不存在 → scan 记 mtime 0.0
    monkeypatch.setattr("app.core.config_watch.watched_paths", lambda: [target])
    watcher = ConfigWatcher(interval=0.01)
    watcher._mtimes = watcher.scan()
    assert watcher.poll_changed() is False

    target.write_text("stream: {}\n", encoding="utf-8")
    assert watcher.poll_changed() is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_config_reload.py -v`
Expected: `test_user_layer_overrides_base` / `test_env_layer_overrides_user` FAIL（bit_rate 仍 4M/6M 不受 user 影响）；`test_watched_paths_includes_user_yaml_unconditionally` FAIL；`test_watcher_detects_first_creation_of_user_yaml` FAIL（首个路径不存在时 poll 不触发）。`test_user_layer_non_mapping_keeps_old_config` 可能已通过（现状无 user 层即天然保旧）——仍保留为回归钉。

- [ ] **Step 3: 实现**

`config/settings.py` 的 `get_settings()`：在 base 加载之后、`if env != "base"` 之前插入：

```python
    env = os.getenv("APP_ENV", "base")
    config = load_yaml_config("base")
    user_config = load_yaml_config("user")
    if user_config:
        if not isinstance(user_config, dict):
            raise ValueError(
                "config/user.yaml must be a YAML mapping; keeping previous settings"
            )
        config = _deep_merge(config, user_config)
    if env != "base":
        config = _deep_merge(config, load_yaml_config(env))
```

（module docstring 第 2 行注释同步为 `2. config/user.yaml (user overrides)`；实际行序以现文件为准，勿动其余。）

`backend/app/core/config_watch.py` 的 `watched_paths()`：初始列表改为无条件包含两个文件：

```python
    paths = [config_dir() / "base.yaml", config_dir() / "user.yaml"]
```

env 文件的 exists-条件追加逻辑**保留原样**（照抄现实现）。

`.gitignore` 追加一行（放在既有 config 相关条目附近）：

```
config/user.yaml
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_config_reload.py -v`
Expected: 全 PASS（含既有 env/热重载用例零回归）。

- [ ] **Step 5: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80
git add config/settings.py backend/app/core/config_watch.py .gitignore tests/unit/test_config_reload.py
git commit -m "feat: 配置分层 user.yaml 加载链与无条件监听（方案 45 T1）"
```

---

## Task 2: 设置读写接口 `GET/PUT /api/settings`

**Files:**
- Create: `backend/app/core/settings_store.py`
- Create: `backend/app/interfaces/http/settings.py`
- Modify: `backend/app/core/exceptions.py`（加 `SettingsStoreError`）
- Modify: `config/settings.py`（加 `StreamOverride` / `RecordingOverride` 白名单模型）
- Modify: `backend/app/main.py`（注册路由，注意命名冲突用别名）
- Test: `tests/unit/test_settings_api.py`（新）

**Interfaces:**
- Consumes: `config_base_dir()`、`load_yaml_config`、`settings()`；`http_testkit.make_client()`；exceptions 既有 `OpenScrcpyException` 形状（`{"error": {"code", "message"}}`）。
- Produces:
  - `StreamOverride(BaseModel)`：全部 Optional，`extra="forbid"`；`max_size: int|None (ge=0, le=4096)`、`bit_rate: str|None (pattern r"^\d+[MK]$")`、`fps: int|None (ge=1, le=120)`、`codec: Literal["h264","h265"]|None`、`adaptive_bitrate: bool|None`。
  - `RecordingOverride(BaseModel)`：`extra="forbid"`；`bit_rate` 同 stream、`time_limit: int|None (ge=1, le=1800)`。
  - `read_user_config() -> dict`、`write_user_config(config) -> None`、`merge_section(user, section, patch|None) -> None`（T8 复用读取与写函数）。
  - `GET /api/settings` → `{"stream": {key: {"value", "source"}}, "recording": {...}}`，key 集合 = T2 的 `_STREAM_FORM_KEYS` / `_RECORDING_FORM_KEYS`；T8 加 `devices` 段。
  - `PUT /api/settings` → `{"saved": true}`；坏文件/不可写 → 500 `SETTINGS_STORE_ERROR`；未知键/越界/空体 → 422 `VALIDATION_ERROR`（spec 文字 400 按项目 pydantic 现状统一为 422，此为执行层 ruling）。

- [ ] **Step 1: 先加白名单模型**（`config/settings.py`）

顶部 import 扩展：`from pydantic import BaseModel, ConfigDict, Field, field_validator`（新增 BaseModel/ConfigDict）；`from typing import Annotated, Literal`（新增，Literal 供 StreamOverride 使用）。文件尾部（Settings 类定义之后）追加：

```python
class StreamOverride(BaseModel):
    """stream 节写入白名单（方案 45 T2；T8 设备级覆盖复用）。"""

    model_config = ConfigDict(extra="forbid")

    max_size: int | None = Field(default=None, ge=0, le=4096)
    bit_rate: str | None = Field(default=None, pattern=r"^\d+[MK]$")
    fps: int | None = Field(default=None, ge=1, le=120)
    codec: Literal["h264", "h265"] | None = None
    adaptive_bitrate: bool | None = None


class RecordingOverride(BaseModel):
    """recording 节写入白名单（方案 45 T2；T8 设备级覆盖复用）。"""

    model_config = ConfigDict(extra="forbid")

    bit_rate: str | None = Field(default=None, pattern=r"^\d+[MK]$")
    time_limit: int | None = Field(default=None, ge=1, le=1800)
```

- [ ] **Step 2: 加异常**（`backend/app/core/exceptions.py`，紧邻既有 `ConfigReloadError`）

```python
class SettingsStoreError(OpenScrcpyException):
    """用户设置存储（config/user.yaml）读写失败（损坏/不可写）。"""

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SETTINGS_STORE_ERROR")
```

（参照同文件既有子类写法适配；`OpenScrcpyException.__init__(message, code)` 签名不变。）

- [ ] **Step 3: 写失败测试**（`tests/unit/test_settings_api.py`）

```python
"""GET/PUT /api/settings 测试（方案 45 T2；devices 节用例在 T8 追加）。"""
import yaml

import pytest

import config.settings as config_settings
from config.settings import reload_settings, settings

from .http_testkit import make_client


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """user.yaml 落点重定向到 tmp_path；配置加载器换成分层 fake，user 层直读该文件。"""
    user_path = tmp_path / "user.yaml"
    monkeypatch.setattr("app.core.settings_store.user_yaml_path", lambda: user_path)
    for var in ("BACKEND_PORT", "BACKEND_HOST", "APP_ENV", "DB_PATH"):
        monkeypatch.delenv(var, raising=False)

    def layered(env: str = "base"):
        if env == "base":
            return {"app": {"name": "OpenScrcpy"},
                    "server": {"host": "0.0.0.0", "port": 8765}}
        if env == "user":
            if not user_path.exists():
                return {}
            loaded = yaml.safe_load(user_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        return {}

    monkeypatch.setattr(config_settings, "load_yaml_config", layered)
    saved = config_settings._settings
    config_settings._settings = None
    reload_settings()
    try:
        yield user_path
    finally:
        config_settings._settings = saved
```

用例（同文件）：

```python
def test_get_returns_default_values_with_default_source(settings_env):
    res = make_client().get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert body["stream"]["max_size"] == {"value": 1080, "source": "default"}
    assert body["stream"]["codec"] == {"value": "h264", "source": "default"}
    assert body["recording"]["time_limit"] == {"value": 60, "source": "default"}


def test_put_writes_nested_section_and_creates_file(settings_env):
    res = make_client().put("/api/settings",
                            json={"stream": {"bit_rate": "6M", "fps": 24}})
    assert res.status_code == 200
    assert res.json() == {"saved": True}
    assert "6M" in settings_env.read_text(encoding="utf-8")

    reload_settings()  # 生产路径 watcher 会在轮询间隔内完成同一动作
    assert settings().stream.bit_rate == "6M"
    assert settings().stream.fps == 24


def test_put_merges_with_existing_keys(settings_env):
    make_client().put("/api/settings", json={"stream": {"fps": 24}})
    res = make_client().put("/api/settings", json={"stream": {"bit_rate": "6M"}})
    assert res.status_code == 200

    reload_settings()
    assert settings().stream.fps == 24
    assert settings().stream.bit_rate == "6M"


def test_put_rejects_unknown_key(settings_env):
    res = make_client().put("/api/settings", json={"stream": {"bogus": 1}})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_rejects_out_of_range_value(settings_env):
    res = make_client().put("/api/settings", json={"stream": {"fps": 0}})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_rejects_empty_body(settings_env):
    res = make_client().put("/api/settings", json={})
    assert res.status_code == 422


def test_put_null_section_resets_to_defaults(settings_env):
    make_client().put("/api/settings", json={"stream": {"fps": 24}})
    res = make_client().put("/api/settings", json={"stream": None})
    assert res.status_code == 200

    reload_settings()
    assert settings().stream.fps == 30


def test_get_marks_user_source_after_put(settings_env):
    make_client().put("/api/settings", json={"stream": {"bit_rate": "6M"}})
    reload_settings()
    body = make_client().get("/api/settings").json()
    assert body["stream"]["bit_rate"] == {"value": "6M", "source": "user"}


def test_put_fails_loudly_on_corrupt_user_yaml(settings_env):
    settings_env.write_text("stream: [bad\n", encoding="utf-8")
    res = make_client().put("/api/settings", json={"stream": {"fps": 24}})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SETTINGS_STORE_ERROR"


def test_get_fails_loudly_on_non_mapping_user_yaml(settings_env):
    settings_env.write_text("- just\n- a\n- list\n", encoding="utf-8")
    res = make_client().get("/api/settings")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SETTINGS_STORE_ERROR"


def test_put_fails_loudly_when_config_dir_not_writable(settings_env, monkeypatch):
    def boom(config):
        raise OSError("read-only fs")

    monkeypatch.setattr("app.core.settings_store.write_user_config", boom)
    res = make_client().put("/api/settings", json={"stream": {"fps": 24}})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SETTINGS_STORE_ERROR"
```

- [ ] **Step 4: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_settings_api.py -v`
Expected: 全部 FAIL（`SettingsStoreError` / `settings_store` / `/api/settings` 不存在 → 404 或 ImportError，非断言误命中）。

- [ ] **Step 5: 实现存储模块**（`backend/app/core/settings_store.py`）

```python
"""用户设置存储：config/user.yaml 的读、合并、原子写（方案 45 T2）。

写接口自身不调 reload_settings——生效统一由 config_watch 对 user.yaml
的 mtime 轮询驱动（方案 45 §5.2，避免双通道竞态）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from app.core.exceptions import SettingsStoreError
from config.settings import config_base_dir


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
```

- [ ] **Step 6: 实现 HTTP 端点**（`backend/app/interfaces/http/settings.py`）

```python
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

from app.core.settings_store import merge_section, read_user_config, write_user_config
from config.settings import RecordingOverride, StreamOverride, load_yaml_config, settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

_STREAM_FORM_KEYS = ("max_size", "bit_rate", "fps", "codec", "adaptive_bitrate")
_RECORDING_FORM_KEYS = ("bit_rate", "time_limit")


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream: StreamOverride | None = None
    recording: RecordingOverride | None = None

    @model_validator(mode="after")
    def _require_any_section(self) -> "SettingsPatch":
        if not self.model_fields_set:
            raise ValueError("至少提交一个配置节（stream/recording）")
        return self


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
    user_cfg = read_user_config()
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
    }


@router.put("")
async def put_settings_payload(patch: SettingsPatch) -> dict[str, bool]:
    """节级合并写入（仅 model_fields_set 内的节生效；节传 null = 清除该节）。"""
    user = read_user_config()
    if "stream" in patch.model_fields_set:
        stream_patch = (
            None if patch.stream is None
            else patch.stream.model_dump(exclude_unset=True, exclude_none=True)
        )
        merge_section(user, "stream", stream_patch)
    if "recording" in patch.model_fields_set:
        recording_patch = (
            None if patch.recording is None
            else patch.recording.model_dump(exclude_unset=True, exclude_none=True)
        )
        merge_section(user, "recording", recording_patch)
    write_user_config(user)
    return {"saved": True}
```

- [ ] **Step 7: 注册路由**（`backend/app/main.py`）

`from app.interfaces.http import ...` 行追加 `settings`，**因与 line 38 `from app.core.config import settings` 同名冲突，import 为别名**：

```python
from app.interfaces.http import settings as settings_api
```

`include_router` 区末尾（system.router 之后）追加：

```python
    app.include_router(settings_api.router)  # /api/settings（方案 45 设置读写）
```

- [ ] **Step 8: 跑测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_settings_api.py tests/unit/test_config_reload.py -v`
Expected: 全 PASS。

- [ ] **Step 9: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80
git add backend/app/core/settings_store.py backend/app/interfaces/http/settings.py \
  backend/app/core/exceptions.py config/settings.py backend/app/main.py \
  tests/unit/test_settings_api.py
git commit -m "feat: 设置读写接口 GET/PUT /api/settings（方案 45 T2）"
```

---

## Task 3: codec 后端放开与 h265 WS 链路

**Files:**
- Modify: `backend/app/scrcpy/constants.py`（HEVC NAL 类型常量）
- Modify: `backend/app/scrcpy/h264_parser.py`（`scan_packet_nalus` 加 `hevc` 分支）
- Modify: `backend/app/scrcpy/stream_protocol.py`（接受 `SC_CODEC_ID_H265`）
- Modify: `backend/app/infrastructure/stream/scrcpy.py`（`video_codec={opts.codec}` + 守卫 + `codec` 属性）
- Modify: `backend/app/interfaces/ws/video.py`（惰性 HEVC 判定 + hvcC 配置下发）
- Modify: `backend/app/application/stream_service.py`（raw fallback × h265 守卫）
- Modify: `config/settings.py`（`StreamConfig.codec` 收紧为 Literal）
- Test: `tests/scrcpy/test_scrcpy_encoder.py`、`tests/scrcpy/test_stream_protocol.py`、`tests/scrcpy/test_h264_parser.py`、`tests/unit/test_video.py`
- Test: `tests/manual/spike_45_codec.py`（新，真机 spike，不入 CI）

**Interfaces:**
- Consumes: `EncoderOpts.codec`（已存在，`scrcpy.py` 132-137 行已透传 opts.codec——本任务移除 override 语义的硬编码）；`stream_service.get_encoder(device_id)`（`video.py:336` 同款取法）；`_send_config` 现有签名。
- Produces:
  - `ScrcpyEncoder` 新增 `@property codec -> str`（"h264"/"h265"；stop 复位 "h264"）。
  - `scan_packet_nalus(data, *, hevc: bool = False) -> list[tuple[int,int,int]]`（h264 默认行为不变）。
  - 常量 `NALU_TYPE_HEVC_VPS=32` / `NALU_TYPE_HEVC_SPS=33` / `NALU_TYPE_HEVC_PPS=34` / `HEVC_IRAP_MIN=16` / `HEVC_IRAP_MAX=21`。
  - `video.py` 内部：`_skip_start_code(nalu) -> int`、`_hevc_codec_string(sps) -> str`、`_send_config_hevc(...)`、公共 `_send_config_message(...)`（h264 路径仅重构不换行为）。

- [ ] **Step 1: StreamConfig.codec 收紧 + 常量 + 协议 id**（先做小而独立的改动，各配 Step 2 一次性测试）

`config/settings.py`：`StreamConfig` 内 `codec` 字段类型改 `Literal["h264", "h265"]`（默认值 `"h264"` 不变，import Literal 已由 T2 引入）。

`backend/app/scrcpy/constants.py` 追加（贴 NAL 类型常量区）：

```python
# HEVC（h265）NAL 类型（(header >> 1) & 0x3F；2 字节 NAL 头）
NALU_TYPE_HEVC_VPS = 32
NALU_TYPE_HEVC_SPS = 33
NALU_TYPE_HEVC_PPS = 34
HEVC_IRAP_MIN = 16  # BLA_W_LP
HEVC_IRAP_MAX = 21  # CRA_NUT
```

`backend/app/scrcpy/stream_protocol.py`：现 169-170 行 `if codec_id != SC_CODEC_ID_H264: raise ...` 改为集合判定，前加常量：

```python
SC_CODEC_ID_H265 = 0x68323635  # "h265" 的 4 字节 little-endian 序号
...
    if codec_id not in (SC_CODEC_ID_H264, SC_CODEC_ID_H265):
        raise UnsupportedCodecError(codec_id)
```

- [ ] **Step 2: 协议/常量测试**（`tests/scrcpy/test_stream_protocol.py` 追加）

```python
def test_accepts_h265_codec_id():
    # SC_CODEC_ID_H265 = 0x68323635；沿用本文件既有「构造 codec id 报头」范式断言不抛
    ...

def test_unknown_codec_id_still_rejected():
    # 既有用例保持 PASS（h264 之外一律拒绝的集合判定未破坏）
```

（按本文件既有用例结构写；关键断言：h265 id 被接受、既有拒绝用例零变化。）

- [ ] **Step 3: 跑协议测试确认失败→通过**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy/test_stream_protocol.py -v`
Expected: 新用例先 FAIL（0x68323635 被拒），实现后 PASS。

- [ ] **Step 4: 扫描器 hevc 分支**（`backend/app/scrcpy/h264_parser.py`）

`scan_packet_nalus` 签名加 `*, hevc: bool = False`；NAL type 推导一处改为：

```python
        ntype = (data[header] >> 1) & 0x3F if hevc else data[header] & 0x1F
```

（其余扫描/边界逻辑一字不动。）

- [ ] **Step 5: 扫描器测试**（`tests/scrcpy/test_h264_parser.py` 追加）

```python
from app.scrcpy.constants import (
    NALU_TYPE_HEVC_SPS,
    NALU_TYPE_HEVC_VPS,
)


def test_scan_packet_nalus_hevc():
    # 2B NAL 头：VPS=0x40,0x01；SPS=0x42,0x01；IDR=0x26,0x01（type=(0x26>>1)=19）；
    # 非关键=0x02,0x01（type=1）
    data = (
        b"\x00\x00\x00\x01" + bytes([0x40, 0x01]) + b"\x00\x00\x01" + bytes([0x42, 0x01])
        + b"\x00\x00\x01" + bytes([0x26, 0x01]) + b"\x00\x00\x01" + bytes([0x02, 0x01])
    )
    ranges = scan_packet_nalus(data, hevc=True)

    assert [ntype for _, _, ntype in ranges] == [
        NALU_TYPE_HEVC_VPS,
        NALU_TYPE_HEVC_SPS,
        19,
        1,
    ]


def test_scan_packet_nalus_h264_default_unchanged():
    # 既有 h264 断言照抄一条（回归钉：默认参数行为不变）
```

- [ ] **Step 6: 跑扫描器测试确认失败→通过**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy/test_h264_parser.py -v`
Expected: hevc 用例先 FAIL（TypeError 无 hevc 参数），实现后 PASS。

- [ ] **Step 7: Encoder 放开 + 守卫**（`backend/app/infrastructure/stream/scrcpy.py`）

  - `__init__` 中 `self._resolution` 旁加 `self._codec: str = "h264"`；stop（818 行 `self._resolution = (0, 0)` 旁）加 `self._codec = "h264"`；新增：

```python
    @property
    def codec(self) -> str:
        """当前会话编解码器（"h264"/"h265"），供 WS config 下发路由。"""
        return self._codec
```

  - `start()` 顶部（opts 参数校验区，第一处 opts 重赋值之前）加白名单守卫：

```python
        if opts.codec not in ("h264", "h265"):
            raise ValueError(f"Unsupported video codec: {opts.codec!r}")
        self._codec = opts.codec
```

  - 223 行 `"video_codec=h264",` → `f"video_codec={opts.codec}",`

  - **255 字节上限的潜在放大**：h265 下 SPS 体积与 h264 不同，本任务不调 max_size 传输预算（服务器推送走既有打包逻辑）；无需改动。

- [ ] **Step 8: Encoder 测试**（`tests/scrcpy/test_scrcpy_encoder.py` 追加，照 `drive_start` 范式）

```python
async def test_start_uses_h265_codec():
    harness = ...  # 照本文件既有 harness 范式
    try:
        async for _ in drive_start(ScrcpyEncoder(), harness=harness,
                                   opts=EncoderOpts(codec="h265")):
            pass
    except Exception:
        pass
    assert "video_codec=h265" in harness.server_cmd()


def test_start_rejects_unknown_codec():
    with pytest.raises(ValueError, match="Unsupported video codec"):
        # drive_start(ScrcpyEncoder(), opts=EncoderOpts(codec="vp9")) 迭代即触发
```

（执行时按既有用例的 drive_start/harness 结构适配；h264 既有用例 `video_codec=h264` 断言保持零回归。）

- [ ] **Step 9: 跑 encoder 测试确认失败→通过**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy/test_scrcpy_encoder.py -v`
Expected: 新用例先 FAIL，实现后 PASS。

- [ ] **Step 10: application 守卫**（`backend/app/application/stream_service.py` 的 `start_stream`）

在读取流配置处加：

```python
        s = settings().stream  # 既有行（方法内现名按文件实际）
        if s.raw_stream_fallback and s.codec != "h264":
            raise ValueError("raw_stream_fallback 仅支持 h264 编解码（冲突：codec=h265）")
```

（video.py 主循环已捕获异常转脱敏 internal error，日志可见原因。）

- [ ] **Step 11: WS 侧 HEVC 链路**（`backend/app/interfaces/ws/video.py`，改动最重的文件，逐处核）

  a. import 区加 HEVC 常量。
  b. `packet_mode` 变量旁加 `is_hevc: bool | None = None`（lazy：首 chunk 时向 encoder 取）。
  c. 主循环 `if packet_mode:` 分支最前插入惰性判定：

```python
            if is_hevc is None:
                encoder = stream_service.get_encoder(device_id)
                is_hevc = encoder is not None and encoder.codec == "h265"
```

  d. `ranges = scan_packet_nalus(chunk)` → `ranges = scan_packet_nalus(chunk, hevc=is_hevc)`
  e. has_config / VCL 过滤的 ntype 集合改为：

```python
            config_ntypes = (
                (NALU_TYPE_HEVC_VPS, NALU_TYPE_HEVC_SPS, NALU_TYPE_HEVC_PPS)
                if is_hevc
                else (NALU_TYPE_SPS, NALU_TYPE_PPS)
            )
            has_config = any(ntype in config_ntypes for _, _, ntype in ranges)
```

  （`if has_config:` 内拦截循环判断与 VCL join 过滤均用 `config_ntypes`；config 包内 VCL 合并直发逻辑不变。）

  f. `intercept_nalu` 加 `nonlocal vps_data`（函数外 sps_data/pps_data 旁声明 `vps_data = None`；epoch reset 处同步置 None），函数体顶部改：

```python
        if is_hevc:
            start = _skip_start_code(nalu)
            ntype = (nalu[start] >> 1) & 0x3F
            if ntype == NALU_TYPE_HEVC_VPS:
                if vps_data != nalu:
                    if config_sent:
                        config_sent = False
                    vps_data = nalu
            elif ntype == NALU_TYPE_HEVC_SPS:
                if sps_data != nalu:
                    if config_sent:
                        config_sent = False
                    sps_data = nalu
            elif ntype == NALU_TYPE_HEVC_PPS:
                if pps_data != nalu:
                    if config_sent:
                        config_sent = False
                    pps_data = nalu
                if vps_data and sps_data and pps_data and not config_sent:
                    await _send_config_hevc(
                        websocket, vps_data, sps_data, pps_data, stream_service, device_id
                    )
                    config_sent = True
            return
        nalu_type = parser.get_nalu_type(nalu)  # h264 原路径，一字不动
```

  g. 底部 `_send_config` 抽取公共发信 + 新增两个函数：

```python
def _skip_start_code(nalu: bytes) -> int:
    """起始码长度（4/3/0）；0 表示无起始码（退化按裸 NAL 解析）。"""
    if nalu[:4] == b"\x00\x00\x00\x01":
        return 4
    if nalu[:3] == b"\x00\x00\x01":
        return 3
    return 0


def _hevc_codec_string(sps: bytes) -> str:
    """由 SPS 构建 WebCodecs hvc1 codec 串（profile_space·profile_idc·compat·层级·约束）。"""
    payload = sps[_skip_start_code(sps) + 2:]  # 去起始码 + 2B HEVC NAL 头
    if len(payload) < 12:
        return "hvc1.1.6.L93.B0"  # 短 SPS 兜底（Review Focus #4）
    b0 = payload[0]
    profile_space = (b0 >> 6) & 0x3
    tier = (b0 >> 5) & 1
    profile_idc = b0 & 0x1F
    compat = int.from_bytes(payload[1:5], "big")
    compat_rev = int(f"{compat:032b}"[::-1], 2)
    constraints = payload[5:11].rstrip(b"\x00")
    level_idc = payload[11]
    codec = f"hvc1.{'ABC'[profile_space]}{profile_idc}.{compat_rev:X}.{'H' if tier else 'L'}{level_idc}"
    if constraints:
        codec += "." + constraints.hex().upper()
    return codec


async def _send_config_message(
    websocket: WebSocket, *, codec: str, description: bytes,
    stream_service: StreamService, device_id: str,
) -> None:
    encoder = stream_service.get_encoder(device_id)
    width = encoder.resolution[0] if encoder else 0
    height = encoder.resolution[1] if encoder else 0
    await websocket.send_json({
        "type": "config",
        "codec": codec,
        "width": width,
        "height": height,
        "description": description.hex(),
        "idle_reset_seconds": stream_service.idle_reset_seconds(),
    })


async def _send_config_hevc(
    websocket: WebSocket, vps: bytes, sps: bytes, pps: bytes,
    stream_service: StreamService, device_id: str,
) -> None:
    codec = _hevc_codec_string(sps)
    logger.info("hevc_codec_extracted_from_sps", device=device_id, codec=codec)
    await _send_config_message(websocket, codec=codec, description=vps + sps + pps,
                               stream_service=stream_service, device_id=device_id)
```

  h. 现 `_send_config`：起止码段改用 `_skip_start_code(sps)`（原名 `sps_data_start`），尾部 `await websocket.send_json({...})` 整块替换为 `await _send_config_message(websocket, codec=codec, description=sps + pps, stream_service=stream_service, device_id=device_id)`。**行为逐字节等价**（codec 串、宽高、description hex、idle_reset_seconds 全同）。

  i. 兜底模式（`parser.feed` 分支）**不改**——h265 与 raw_fallback 冲突已由 Step 10 守卫禁入；epoch reset 处 `vps_data = None` 一并复位。

- [ ] **Step 12: WS 测试**（`tests/unit/test_video.py` 扩展）

  a. 本文件 `FakeEncoder` 加 `codec` 构造参数与属性（默认 `"h264"`，既有用例零变化）：

```python
class FakeEncoder:
    def __init__(self, resolution=(1080, 1920), codec="h264"):
        self._resolution = resolution
        self._codec = codec

    @property
    def resolution(self):
        return self._resolution

    @property
    def codec(self):
        return self._codec
```

  b. 追加 h265 config 用例（测试特征值：payload b0=0x01 → profile_space 0/profile 1；compat=0x60000000 → 反转位 32bit=6；level=120；constraints 首字节 0xB0 → 串尾 `.B0`）：

```python
HEVC_VPS_TEST = b"\x00\x00\x00\x01\x40\x01\x0c\x01\xff\xff\x01\x60"
HEVC_SPS_TEST = (
    b"\x00\x00\x00\x01\x42\x01"
    + bytes([0x01])                              # profile_space=0, tier=0, profile_idc=1
    + (0x60000000).to_bytes(4, "big")            # compat → "6"
    + bytes([0xB0, 0x00, 0x00, 0x00, 0x00, 0x00])  # constraints → 非零尾 ".B0"
    + bytes([120])                               # level_idc → L120
)
HEVC_PPS_TEST = b"\x00\x00\x00\x01\x44\x01\x08\x00"
HEVC_IDR_TEST = b"\x00\x00\x00\x01\x26\x01\xff"
HEVC_P_TEST = b"\x00\x00\x00\x01\x02\x01\xff"


def test_h265_config_message():
    svc = FakeStreamService(
        [HEVC_VPS_TEST + HEVC_SPS_TEST + HEVC_PPS_TEST + HEVC_IDR_TEST, HEVC_P_TEST],
        encoder=FakeEncoder(resolution=(1080, 1920), codec="h265"),
    )
    # 照本文件既有 recv_json/wait_for 范式驱动
    config = ...  # recv_json() 直至 {"type": "config"}
    assert config["codec"] == "hvc1.1.6.L120.B0"
    assert config["description"] == (HEVC_VPS_TEST + HEVC_SPS_TEST + HEVC_PPS_TEST).hex()
    assert config["width"] == 1080 and config["height"] == 1920


def test_h265_short_sps_falls_back_to_default_codec_string():
    # SPS 去起始码+2B 头后 < 12B → "hvc1.1.6.L93.B0"，不抛异常
    short_sps = b"\x00\x00\x00\x01\x42\x01\x01\x02\x03"
    assert ...  # 直接调用 _hevc_codec_string 断言（from app.interfaces.ws.video import _hevc_codec_string）
```

  c. 既有 h264 用例全量 PASS（零回归钉）。

- [ ] **Step 13: 跑 WS 测试确认失败→通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_video.py -v`
Expected: 新用例先 FAIL（FakeEncoder 无 codec / _hevc_codec_string 不存在），实现后全 PASS。

- [ ] **Step 14: 真机 spike 脚本**（`tests/manual/spike_45_codec.py`）

照既有 `tests/manual/` spike 范式：可选参数 `--device`（默认 `192.168.8.18:5555`）+ `--codec h265|h264`（默认 h265）→ 走 `ScrcpyEncoder` start → 读 WS 网络流的 4 字节 codec id（或直接断言 encoder.codec / SPS 拦截日志）→ 断言 h265 时 codec id = `0x68323635` / stream_protocol 接受。文件头注明：**需真机、手动跑、不入 CI**；Git Bash 下 adb 命令带 `MSYS_NO_PATHCONV=1`；`if __name__ == "__main__":` 保护。

- [ ] **Step 15: 真机 spike 执行**（关键验收：.18/.33/.25 三台在 h265 下能否正常出流）

```bash
cd D:/tangzk/py/scrcpy-web && PYTHONPATH=backend MSYS_NO_PATHCONV=1 \
  python tests/manual/spike_45_codec.py --device 192.168.8.18:5555
```

  - h265 下 SO（stream open）→ 4B codec id 应为 0x68323635（判读后即停，不抢 8765 服务会话）；
  - 若某台不支持 h265：scrcpy-server 会回退 h264 或断流——把该设备行为记录进 spike 输出与提交信息正文；
  - 若 h265 出流后 SPS 解析异常：检查 `_hevc_codec_string` 保底串日志。

- [ ] **Step 16: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80
cd frontend && npx vitest run && npx vue-tsc -b && cd ..
git add backend/app/scrcpy/constants.py backend/app/scrcpy/h264_parser.py \
  backend/app/scrcpy/stream_protocol.py backend/app/infrastructure/stream/scrcpy.py \
  backend/app/interfaces/ws/video.py backend/app/application/stream_service.py \
  config/settings.py tests/scrcpy/ tests/unit/test_video.py tests/manual/spike_45_codec.py
git commit -m "feat: codec 后端放开与 h265 WS 链路（方案 45 T3）"
```

---

## Task 4: 前端 h265 解码链路泛化与支持面探测

**Files:**
- Modify: `frontend/src/services/h264NalUtils.ts`（`scanFrame` 加 codec 参数）
- Create: `frontend/src/services/codecSupport.ts`
- Modify: `frontend/src/services/h264VideoStream.ts`（family 泛化 + hvcC 构建）
- Test: `frontend/src/services/h264NalUtils.test.ts`（不存在则新建）、`frontend/src/services/h264VideoStream.test.ts`（不存在则新建）、`frontend/src/services/codecSupport.test.ts`（新）

**Interfaces:**
- Consumes: 后端 T3 的 config 消息扩展（`codec: "hvc1.*"`、`description: (vps+sps+pps).hex()`）；既有 `extractNalus` / `buildAvccDescription` / `hexToUint8Array`。
- Produces:
  - `scanFrame(data, codec: 'h264' | 'h265' = 'h264')`——h265 关键帧判定 `((header >> 1) & 0x3F) ∈ [16, 21]`。
  - `isH265DecoderSupported(): Promise<boolean>`（三态：无 API / 探测异常 / 支持与否）。
  - `buildHvcCDescription(vps, sps, pps): Uint8Array`（HEVCDecoderConfigurationRecord）。

- [ ] **Step 1: 写失败测试**

`frontend/src/services/codecSupport.test.ts`：

```ts
import { describe, expect, it, vi } from 'vitest'

import { isH265DecoderSupported } from './codecSupport'

describe('isH265DecoderSupported', () => {
  it('returns false when VideoDecoder API is missing', async () => {
    vi.stubGlobal('VideoDecoder', undefined)
    expect(await isH265DecoderSupported()).toBe(false)
    vi.unstubAllGlobals()
  })

  it('returns true when the codec is supported', async () => {
    vi.stubGlobal('VideoDecoder', {
      isConfigSupported: vi.fn().mockResolvedValue({ supported: true }),
    })
    expect(await isH265DecoderSupported()).toBe(true)
    vi.unstubAllGlobals()
  })

  it('returns false when the codec is unsupported', async () => {
    vi.stubGlobal('VideoDecoder', {
      isConfigSupported: vi.fn().mockResolvedValue({ supported: false }),
    })
    expect(await isH265DecoderSupported()).toBe(false)
    vi.unstubAllGlobals()
  })

  it('returns false when probing throws', async () => {
    vi.stubGlobal('VideoDecoder', {
      isConfigSupported: vi.fn().mockRejectedValue(new Error('probe failed')),
    })
    expect(await isH265DecoderSupported()).toBe(false)
    vi.unstubAllGlobals()
  })
})
```

`frontend/src/services/h264NalUtils.test.ts`（照现有测试文件命名与结构；若已有则追加）：

```ts
import { describe, expect, it } from 'vitest'

import { scanFrame } from './h264NalUtils'

describe('scanFrame with h265', () => {
  it('detects HEVC keyframe (IRAP NAL types 16..21)', () => {
    // header=0x26 → (0x26>>1)&0x3F = 19 ∈ [16,21]
    const data = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x26, 0x01, 0xff])
    const frame = scanFrame(data, 'h265')
    expect(frame.isKey).toBe(true)
  })

  it('detects HEVC delta frame', () => {
    // header=0x02 → (0x02>>1)&0x3F = 1
    const data = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0xff])
    expect(scanFrame(data, 'h265').isKey).toBe(false)
  })
})
```

`frontend/src/services/h264VideoStream.test.ts`（照现有测试文件结构；重点用例）：

```ts
it('builds hvcC description from h265 config message', () => {
  const vps = new Uint8Array([0x40, 0x01, 0x0c, 0x01])
  const sps = new Uint8Array([0x42, 0x01, 0x01, 0x60, 0x00, 0x00, 0x00, 0xb0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x78])
  const pps = new Uint8Array([0x44, 0x01, 0x08])
  const desc = buildHvcCDescription(vps, sps, pps)
  expect(desc[0]).toBe(1)        // configurationVersion
  expect(desc[22]).toBe(3)       // numOfArrays
  expect(desc[21]).toBe(0x0f)
  // …（数组区断言：首数组 tag 0xA0、长度前缀两字节 = vps.length）
})
```

（若 `buildHvcCDescription` 非导出，从组件路径导出以实现可测——导出纯函数、不引 DOM。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/services/codecSupport.test.ts src/services/h264NalUtils.test.ts`
Expected: FAIL（module 不存在 / scanFrame 无 h265 语义 / buildHvcCDescription 未定义）。

- [ ] **Step 3: 实现**

`codecSupport.ts`（新）：

```ts
/** Chrome 侧 h265（HEVC）解码支持面探测（方案 45 T4）。 */
export async function isH265DecoderSupported(): Promise<boolean> {
  if (typeof VideoDecoder === 'undefined') return false
  try {
    const support = await VideoDecoder.isConfigSupported({
      codec: 'hvc1.1.6.L93.B0',
      codedWidth: 1920,
      codedHeight: 1080,
    })
    return !!support.supported
  } catch {
    return false
  }
}
```

`h264NalUtils.ts`：`scanFrame` 签名与关键帧判定改为（其余一字不动）：

```ts
export function scanFrame(data: Uint8Array, codec: 'h264' | 'h265' = 'h264') {
  // …现扫描逻辑…
  const header = data[dataStart]
  let isKey: boolean
  if (codec === 'h265') {
    const ntype = (header >> 1) & 0x3f
    isKey = ntype >= 16 && ntype <= 21
  } else {
    isKey = (header & 0x1f) === 5
  }
  // …返回结构含 isKey（现字段名按文件实际）…
}
```

`h264VideoStream.ts`：

  a. 字段：`private vpsData: Uint8Array | null = null`（spsData/ppsData 旁）；`private codecFamily: 'h264' | 'h265' = 'h264'`。reset（251-252 区）两处都复位。
  b. config 消息分支（现 330-412 区）：拿 `msg.codec` 后先定 family：

```ts
    const codec = msg.codec || 'avc1.42E01E'
    this.codecFamily = codec.startsWith('hvc1.') || codec.startsWith('hev1.') ? 'h265' : 'h264'
```

  NAL 循环 type 判定与存储按 family：

```ts
      for (const nalu of nalus) {
        const naluType = this.codecFamily === 'h265'
          ? ((nalu.data[0] as number) >> 1) & 0x3f
          : nalu.data[0] & 0x1f
        if (this.codecFamily === 'h265') {
          if (naluType === 32) this.vpsData = nalu.data
          else if (naluType === 33) this.spsData = nalu.data
          else if (naluType === 34) this.ppsData = nalu.data
        } else {
          if (naluType === 7) this.spsData = nalu.data
          else if (naluType === 8) this.ppsData = nalu.data
        }
      }
```

  description 构建（现 `buildAvccDescription` 调用处）：

```ts
      if (this.codecFamily === 'h265' && this.vpsData && this.spsData && this.ppsData) {
        this.lastAvccDesc = buildHvcCDescription(this.vpsData, this.spsData, this.ppsData)
      } else if (this.spsData && this.ppsData) {
        this.lastAvccDesc = buildAvccDescription(this.spsData, this.ppsData)
      }
```

  （字段名 `_lastCodec/_lastAvccDesc` 等按现文件原名适配；`initialized`/initDecoder 复用逻辑不动。）

  c. 新函数（与 `buildAvccDescription` 同文件同风格）：

```ts
/**
 * 构建 HEVCDecoderConfigurationRecord（hvcC，23B 头 + 3 参数集数组）。
 * 头 12B 直取 SPS 的 profile/level 段；lengthSizeMinusOne 置 3（4B 长度）。
 */
function buildHvcCDescription(vps: Uint8Array, sps: Uint8Array, pps: Uint8Array): Uint8Array {
  const header = new Uint8Array(23)
  header[0] = 1 // configurationVersion
  header.set(sps.subarray(0, 12), 1) // general_profile_space … general_level_idc
  header[21] = 0x0f // min_spatial_segmentation 补位（0b1111 低 4 位）
  header[22] = 3 // numOfArrays
  const arrays: number[] = []
  for (const [nal, tableCode] of [
    [vps, 0xa0],
    [sps, 0xa1],
    [pps, 0xa2],
  ] as const) {
    arrays.push(tableCode, 0x00, 0x01, nal.length >> 8, nal.length & 0xff, ...nal)
  }
  const out = new Uint8Array(23 + arrays.length)
  out.set(header)
  out.set(arrays, 23)
  return out
}
```

  d. 发送路径若现代码用 `scanFrame(...)` 判关键帧，改传 `this.codecFamily`。
  e. h264 全路径行为不变（family='h264' 分支与原逻辑逐行等价）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run`
Expected: 全 PASS（含全部既有前端用例零回归）。

- [ ] **Step 5: 门禁 + 提交**

Run:
```bash
cd frontend && npx vitest run && npx vue-tsc -b && cd ..
git add frontend/src/services/h264NalUtils.ts frontend/src/services/codecSupport.ts \
  frontend/src/services/h264VideoStream.ts frontend/src/services/*.test.ts
git commit -m "feat: 前端 h265 解码链路泛化与支持面探测（方案 45 T4）"
```

---

## Task 5: 独立设置页与设备页跳转入口

**Files:**
- Modify: `frontend/src/services/api.ts`（`getSettings` / `putSettings` + 类型）
- Modify: `frontend/src/router/index.ts`（`/settings` 路由）
- Modify: `frontend/src/App.vue`（菜单「设置」）
- Modify: `frontend/src/views/DeviceDetail.vue`（actions 加「设置」跳转）
- Create: `frontend/src/components/settings/StreamFieldSet.vue`
- Create: `frontend/src/components/settings/RecordingFieldSet.vue`
- Create: `frontend/src/views/Settings.vue`
- Test: `frontend/src/views/Settings.test.ts`（新）

**Interfaces:**
- Consumes: T2 的 `GET/PUT /api/settings`（响应形状见之）；T4 的 `isH265DecoderSupported()`。
- Produces:
  - `api.getSettings(): Promise<SettingsView>` / `api.putSettings(patch): Promise<{ saved: boolean }>`。
  - 复用组件：`StreamFieldSet.vue`（v-model 表单，供 T8 设备覆盖复用）、`RecordingFieldSet.vue` 同构。
  - 页面 `Settings.vue`（T6/T7/T8 在同一文件追加区块）。

- [ ] **Step 1: api.ts 扩展**

按 api.ts 现有方法体风格追加（URL 无尾斜杠、错误走既有拦截器）：

```ts
export interface SettingEntry {
  value: number | string | boolean
  source: 'default' | 'user' | 'env'
}

export interface SettingsView {
  stream: Record<string, SettingEntry>
  recording: Record<string, SettingEntry>
}

export interface SettingsPatch {
  stream?: Record<string, unknown> | null
  recording?: Record<string, unknown> | null
}

// api 对象内追加：
  async getSettings(): Promise<SettingsView> {
    const res = await http.get<SettingsView>('/api/settings')
    return res.data
  },

  async putSettings(patch: SettingsPatch): Promise<{ saved: boolean }> {
    const res = await http.put<{ saved: boolean }>('/api/settings', patch)
    return res.data
  },
```

（`http` 实例名按现文件实际；方法挂进 api 导出对象。）

- [ ] **Step 2: 路由 + 菜单 + 设备页入口**

`frontend/src/router/index.ts` routes 数组追加（`/device/:id/files` 之后）：

```ts
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/Settings.vue'),
    },
```

`frontend/src/App.vue` 菜单：

```html
          <el-menu mode="horizontal" :router="true">
            <el-menu-item index="/">Dashboard</el-menu-item>
            <el-menu-item index="/settings">设置</el-menu-item>
          </el-menu>
```

`frontend/src/views/DeviceDetail.vue`：actions 区（`[文件] [截图]` 旁）加：

```html
        <el-button data-test="device-settings-btn" @click="goSettings">设置</el-button>
```

```ts
import { useRouter } from 'vue-router'
// script setup 内：
const router = useRouter()
function goSettings() {
  router.push('/settings') // T8 改为带 ?device=<device_id>
}
```

（按钮样式/位置沿用 actions 区既有形态。）

- [ ] **Step 3: 复用字段组件**

`frontend/src/components/settings/StreamFieldSet.vue`（el-form 内联布局，v-model 对象）：

```vue
<!--
  串流参数表单段（方案 45 T5；T8 设备级覆盖复用）。
  props: modelValue（串流段值）；h265Supported（codec h265 是否可选项）
  提交由父组件收集 —— 本组件只维护表单值。
-->
<template>
  <el-form label-width="140px" @submit.prevent>
    <el-form-item label="最大分辨率 max_size">
      <el-input-number v-model="value.maxSize" :min="0" :max="4096" />
      <span class="hint">0 表示不缩放（服务端截图链路恒不缩放，仅影响视频流）</span>
    </el-form-item>
    <el-form-item label="起始码率 bit_rate">
      <el-select v-model="value.bitRate">
        <el-option v-for="v in ['1M', '2M', '4M', '6M', '8M']" :key="v" :label="v" :value="v" />
      </el-select>
      <span class="hint">自适应码率开启时的起始档</span>
    </el-form-item>
    <el-form-item label="帧率 fps">
      <el-input-number v-model="value.fps" :min="1" :max="120" />
    </el-form-item>
    <el-form-item label="编解码 codec">
      <el-select v-model="value.codec">
        <el-option label="h264 (AVC)" value="h264" />
        <el-option label="h265 (HEVC)" value="h265" :disabled="!h265Supported" />
      </el-select>
      <span v-if="!h265Supported" class="hint">当前浏览器不支持 h265 解码，已禁用</span>
    </el-form-item>
    <el-form-item label="自适应码率">
      <el-switch v-model="value.adaptiveBitrate" />
    </el-form-item>
  </el-form>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface StreamFormValue {
  maxSize: number
  bitRate: string
  fps: number
  codec: 'h264' | 'h265'
  adaptiveBitrate: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: StreamFormValue
    h265Supported?: boolean
  }>(),
  { h265Supported: false }
)
const emit = defineEmits<{ 'update:modelValue': [value: StreamFormValue] }>()

const value = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
</script>
```

`frontend/src/components/settings/RecordingFieldSet.vue`（同构精简：`bitRate` + `timeLimit`(1-1800) + hint「每次开始录屏时读取，保存即对新录屏生效」；与 StreamFieldSet 同等 v-model 结构）。

- [ ] **Step 4: 设置页**（`frontend/src/views/Settings.vue`）

结构：页头 + 「运行参数」卡（StreamFieldSet + RecordingFieldSet + 各自保存/重置按钮）。（T6 加「生效参数」卡 + 「运行环境」卡；T8 加「设备级覆盖」卡。）

```vue
<!--
  独立设置页（方案 45 T5；T6/T7/T8 在本文件追加区块）。
  保存语义：串流段「下次连接生效」；录屏段「立即生效」（spec §5.3）。
-->
<template>
  <div class="settings-page">
    <h2>设置</h2>
    <el-card class="section-card">
      <template #header>运行参数</template>
      <div class="section-row">
        <StreamFieldSet v-model="streamForm" :h265-supported="h265Supported" />
        <div class="actions">
          <el-button type="primary" data-test="save-stream" :loading="saving" @click="saveStream">
            保存串流参数
          </el-button>
          <el-button data-test="reset-stream" @click="resetStream">重置串流为出厂默认</el-button>
        </div>
      </div>
      <el-divider />
      <div class="section-row">
        <RecordingFieldSet v-model="recordingForm" />
        <div class="actions">
          <el-button type="primary" data-test="save-recording" :loading="saving" @click="saveRecording">
            保存录屏参数
          </el-button>
          <el-button data-test="reset-recording" @click="resetRecording">重置录屏为出厂默认</el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import RecordingFieldSet, { type RecordingFormValue } from '@/components/settings/RecordingFieldSet.vue'
import StreamFieldSet, { type StreamFormValue } from '@/components/settings/StreamFieldSet.vue'
import { api } from '@/services/api'
import { isH265DecoderSupported } from '@/services/codecSupport'

const h265Supported = ref(false)
const saving = ref(false)
const streamForm = ref<StreamFormValue>({
  maxSize: 1080, bitRate: '4M', fps: 30, codec: 'h264', adaptiveBitrate: true,
})
const recordingForm = ref<RecordingFormValue>({ bitRate: '4M', timeLimit: 60 })

async function loadSettings() {
  const view = await api.getSettings()
  streamForm.value = {
    maxSize: Number(view.stream.max_size.value),
    bitRate: String(view.stream.bit_rate.value),
    fps: Number(view.stream.fps.value),
    codec: view.stream.codec.value as 'h264' | 'h265',
    adaptiveBitrate: Boolean(view.stream.adaptive_bitrate.value),
  }
  recordingForm.value = {
    bitRate: String(view.recording.bit_rate.value),
    timeLimit: Number(view.recording.time_limit.value),
  }
}

const STREAM_PATCH_KEYS = {
  maxSize: 'max_size', bitRate: 'bit_rate', fps: 'fps',
  codec: 'codec', adaptiveBitrate: 'adaptive_bitrate',
} as const

async function saveStream() {
  saving.value = true
  try {
    await api.putSettings({ stream: { ...streamForm.value, max_size: streamForm.value.maxSize } })
    // 键名映射：提交体用后端键（max_size/bit_rate/fps/codec/adaptive_bitrate）
    ElMessage.success('已保存，下次连接生效')
  } finally {
    saving.value = false
  }
}
```

（注意：提交体键名必须用后端键。实现时用显式映射构造 patch，勿直接 spread camelCase——建议把 StreamFieldSet 表单值直接以**后端键**命名（`max_size`/`bit_rate`/`fps`/`codec`/`adaptive_bitrate`），省去映射层；`STREAM_PATCH_KEYS` 即删。此处两种写法二选一，推荐直接后端键。录制段同。）

重置：`ElMessageBox.confirm('将删除该节的用户设置并回到出厂默认？')` → `api.putSettings({ stream: null })` → `await loadSettings()` → success toast。

`onMounted`: `loadSettings()` + `isH265DecoderSupported().then(v => (h265Supported.value = v))`；加载失败 `ElMessage.error`（T2 500 的可见错误透传）。

- [ ] **Step 5: 页面测试**（`frontend/src/views/Settings.test.ts`，照 `FileManager.test.ts` 的 mock 模式：`vi.hoisted` elMock + `vi.mock('element-plus')` + `vi.mock('@/services/api')`）

```ts
import { flushPromises, mount, shallowMount } from '@vue/test-utils'  // 按需
import { beforeEach, describe, expect, it, vi } from 'vitest'

const elMock = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn().mockResolvedValue('confirm') },
}))
vi.mock('element-plus', () => elMock)

const mockApi = vi.hoisted(() => ({
  getSettings: vi.fn(),
  putSettings: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))
vi.mock('@/services/codecSupport', () => ({ isH265DecoderSupported: vi.fn().mockResolvedValue(false) }))

const DEFAULT_VIEW = {
  stream: {
    max_size: { value: 1080, source: 'default' },
    bit_rate: { value: '4M', source: 'default' },
    fps: { value: 30, source: 'default' },
    codec: { value: 'h264', source: 'default' },
    adaptive_bitrate: { value: true, source: 'default' },
  },
  recording: {
    bit_rate: { value: '4M', source: 'default' },
    time_limit: { value: 60, source: 'default' },
  },
}

describe('Settings.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getSettings.mockResolvedValue(DEFAULT_VIEW)
    mockApi.putSettings.mockResolvedValue({ saved: true })
  })

  it('loads and shows current values in form', async () => {
    // shallowMount + flushPromises → max_size input 呈现 1080 等
  })

  it('saves stream section and shows 下次连接生效 toast', async () => {
    // 触发 save-stream → putSettings 收到 { stream: { max_size: 1080, ... } }
    // ElMessage.success 以 已保存，下次连接生效 调用
  })

  it('h265 option disabled when unsupported (mock resolves false)', async () => {
    // StreamFieldSet 的 h265 option disabled / 提示文本存在
  })

  it('reset confirms then puts stream: null', async () => {
    // ElMessageBox.confirm → putSettings({ stream: null }) → loadSettings 重拉
  })
})
```

- [ ] **Step 6: 跑测试确认失败→通过**

Run: `cd frontend && npx vitest run src/views/Settings.test.ts`
Expected: 先 FAIL（Settings.vue 不存在），实现后 PASS。

- [ ] **Step 7: 手动冒烟**（后端 8765 已被用户占用则跳过实测，仅提示；否则）

```bash
# 终端 A（若端口空闲）：python run_server.py
# 终端 B：cd frontend && npm run dev → http://localhost:5173/settings
```

  - 表单加载默认值；保存后 GET 一里显示 source=user；重置回 default；
  - 顶部菜单「设置」可达；设备页行动条「设置」按钮跳转 /settings。

- [ ] **Step 8: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80 && \
cd frontend && npx vitest run && npx vue-tsc -b && cd ..
git add frontend/src/services/api.ts frontend/src/router/index.ts frontend/src/App.vue \
  frontend/src/views/DeviceDetail.vue frontend/src/views/Settings.vue \
  frontend/src/views/Settings.test.ts frontend/src/components/settings/
git commit -m "feat: 独立设置页与设备页跳转入口（方案 45 T5）"
```

---

## Task 6: 生效参数面板与运行环境信息区

**Files:**
- Modify: `backend/app/interfaces/http/system.py`（`GET /api/system/runtime-env`）
- Modify: `frontend/src/services/api.ts`（`getRuntimeEnv`）
- Modify: `frontend/src/views/Settings.vue`（「生效参数」卡 + 「运行环境」卡）
- Test: `tests/unit/test_runtime_env.py`（新）、`Settings.test.ts` 扩展

**Interfaces:**
- Consumes: SCRCPY_SERVER_VERSION 方案 44 单源常量、ServerManager jar 路径方法（本步先 grep 确认精确 import 路径：`grep -rn "SCRCPY_SERVER_VERSION" backend/app` 与 `grep -rn "def get_local_jar_path\|local_jar" backend/app`——实现按 grep 结果 adjust，这是 spec 允许的执行层核对）。
- Produces: `GET /api/system/runtime-env` → `{"scrcpy_server_version", "jar_path", "adb_path", "adb_version", "frozen"}`；前端 `api.getRuntimeEnv()`。

- [ ] **Step 1: 写失败测试**（`tests/unit/test_runtime_env.py`）

```python
"""GET /api/system/runtime-env 测试（方案 45 T6 运行环境信息透明化）。"""
from .http_testkit import make_client


def test_runtime_env_returns_basic_fields(monkeypatch):
    monkeypatch.setattr(
        "app.interfaces.http.system._probe_adb_version",
        lambda path: "Android Debug Bridge version 1.0.41",
    )
    res = make_client().get("/api/system/runtime-env")

    assert res.status_code == 200
    body = res.json()
    assert body["scrcpy_server_version"].startswith("v")
    assert body["jar_path"].endswith(".jar")
    assert body["adb_version"] == "Android Debug Bridge version 1.0.41"
    assert isinstance(body["frozen"], bool)


def test_runtime_env_adb_probe_failure_returns_unknown(monkeypatch):
    monkeypatch.setattr("app.interfaces.http.system._probe_adb_version", lambda path: "未知")
    res = make_client().get("/api/system/runtime-env")

    assert res.json()["adb_version"] == "未知"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_runtime_env.py -v`
Expected: FAIL（404，端点不存在）。

- [ ] **Step 3: 实现**（`backend/app/interfaces/http/system.py`）

顶部 import 加 `asyncio`、`subprocess`、`sys`；经 Step 0 grep 确认的常量/管理器 import；追加：

```python
def _probe_adb_version(adb_path: str) -> str:
    """adb --version 探测（阻塞子进程，端点内经 asyncio.to_thread 执行）；失败返回「未知」。"""
    try:
        result = subprocess.run(
            [adb_path, "version"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "Version" in line:
                return line.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "未知"


@router.get("/runtime-env")
async def runtime_env_endpoint() -> dict[str, Any]:
    """运行环境信息（方案 45 §4.1-③）：只读透明化，不改动二进制获取方式。"""
    adb_path = settings().adb.adb_path or "adb"
    adb_version = await asyncio.to_thread(_probe_adb_version, adb_path)
    return {
        "scrcpy_server_version": SCRCPY_SERVER_VERSION,
        "jar_path": str(ServerManager().get_local_jar_path()),
        "adb_path": adb_path,
        "adb_version": adb_version,
        "frozen": bool(getattr(sys, "frozen", False)),
    }
```

（`settings` 从 config 导入：本文件现无 config import，按 repo 既有 style 补 `from config.settings import settings`。）

- [ ] **Step 4: 前端接入**

`api.ts`:

```ts
export interface RuntimeEnvView {
  scrcpy_server_version: string
  jar_path: string
  adb_path: string
  adb_version: string
  frozen: boolean
}

// api 对象内：
  async getRuntimeEnv(): Promise<RuntimeEnvView> {
    const res = await http.get<RuntimeEnvView>('/api/system/runtime-env')
    return res.data
  },
```

`Settings.vue` 追加两张卡：

  - 「生效参数」卡：`el-descriptions` 分 stream/recording 两组，每行 = 参数名 + 当前值 + 来源徽标（`default→出厂默认` / `user→用户设置` / `env→环境覆盖`，`el-tag`）；数据来自 `getSettings()` 同一次拉取（存 `settingsView` ref）。
  - 「运行环境」卡：四行只读（scrcpy-server 版本 / adb 版本 / jar 路径 / adb 路径）+ 折叠显示长路径；`onMounted` 追加 `api.getRuntimeEnv()` 拉取。（「检查更新」按钮 T7 加。）

`Settings.test.ts` 扩展：

```ts
const mockApi = vi.hoisted(() => ({
  getSettings: vi.fn(),
  putSettings: vi.fn(),
  getRuntimeEnv: vi.fn(),
}))
// beforeEach 补 mockApi.getRuntimeEnv.mockResolvedValue({
//   scrcpy_server_version: 'v5.0.1', jar_path: 'C:/x/scrcpy-server.jar',
//   adb_path: 'adb', adb_version: 'Android Debug Bridge version 1.0.41', frozen: false,
// })

it('shows effective values with source badges', async () => {
  // 生效参数卡渲染 stream.bit_rate 值 4M + 来源徽标 出厂默认
})

it('shows runtime env info from api', async () => {
  // 运行环境卡出现 v5.0.1 与 adb 版本行
})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_runtime_env.py -v && cd frontend && npx vitest run src/views/Settings.test.ts && cd ..`
Expected: 全 PASS。

- [ ] **Step 6: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80 && \
cd frontend && npx vitest run && npx vue-tsc -b && cd ..
git add backend/app/interfaces/http/system.py frontend/src/services/api.ts \
  frontend/src/views/Settings.vue frontend/src/views/Settings.test.ts \
  tests/unit/test_runtime_env.py
git commit -m "feat: 生效参数面板与运行环境信息区（方案 45 T6）"
```

---

## Task 7: scrcpy 检查更新（仅手动触发提示）

**Files:**
- Modify: `backend/app/interfaces/http/system.py`（`GET /api/system/scrcpy-update`）
- Modify: `frontend/src/services/api.ts`（`checkScrcpyUpdate`）
- Modify: `frontend/src/views/Settings.vue`（「检查更新」按钮 + 四态文案）
- Test: `tests/unit/test_scrcpy_update.py`（新）、`Settings.test.ts` 扩展

**Interfaces:**
- Consumes: T6 的 SCRCPY_SERVER_VERSION 常量（同 import）。
- Produces: `GET /api/system/scrcpy-update` → `{"status": "upToDate"|"updateAvailable"|"installedNewer"|"unknown", "current_version", "latest_version", "url", "error"}`；`_parse_version(text) -> tuple[int,int,int] | None`（固定 3 段、零填充）；`_fetch_latest_release() -> dict`（阻塞网络，线程池执行）。

- [ ] **Step 1: 写失败测试**（`tests/unit/test_scrcpy_update.py`）

```python
"""GET /api/system/scrcpy-update 测试（方案 45 T7；网络调用以 monkeypatch 隔离）。"""
import urllib.error

import pytest

from .http_testkit import make_client


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v5.0.1", "upToDate"),        # 与本地 SCRCPY_SERVER_VERSION 同值（执行时用 grep 核对）
        ("v5.1.0", "updateAvailable"),
        ("v4.9.0", "installedNewer"),
        ("somerelease", "unknown"),    # 无法解析上游版本号
    ],
)
def test_update_status_variants(monkeypatch, tag, expected):
    monkeypatch.setattr(
        "app.interfaces.http.system._fetch_latest_release",
        lambda: {"tag_name": tag, "html_url": "https://github.com/Genymobile/scrcpy/releases"},
    )
    res = make_client().get("/api/system/scrcpy-update")

    assert res.status_code == 200
    assert res.json()["status"] == expected


def test_update_network_failure_reports_unknown(monkeypatch):
    def boom():
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("app.interfaces.http.system._fetch_latest_release", boom)
    res = make_client().get("/api/system/scrcpy-update")

    body = res.json()
    assert body["status"] == "unknown"
    assert "GitHub" in body["error"]


def test_parse_version():
    from app.interfaces.http.system import _parse_version

    assert _parse_version("v5.0.1") == (5, 0, 1)
    assert _parse_version("3.2") == (3, 2, 0)
    assert _parse_version("junk") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_scrcpy_update.py -v`
Expected: FAIL（404，端点不存在）。

- [ ] **Step 3: 实现**（`backend/app/interfaces/http/system.py` 追加；import 加 `json`、`re`、`urllib.error`、`urllib.request`）

```python
_GITHUB_RELEASES_LATEST = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
_UPDATE_USER_AGENT = "OpenScrcpy-check-update"


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """解析 vX.Y.Z / X.Y / X 为固定三元组（缺省段补 0）；非版本串返回 None。"""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def _fetch_latest_release() -> dict:
    """GitHub Releases latest 查询（阻塞网络调用，仅在 asyncio.to_thread 内执行）。"""
    request = urllib.request.Request(
        _GITHUB_RELEASES_LATEST,
        headers={
            "User-Agent": _UPDATE_USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


@router.get("/scrcpy-update")
async def scrcpy_update_endpoint() -> dict[str, Any]:
    """检查上游 scrcpy-server 版本（方案 45 §4.2：仅手动触发提示，不自动下载）。

    不联网轮询；离线/内网环境报明确 unknown 原因。升级仍走方案 44 换包 SOP。
    """
    current = SCRCPY_SERVER_VERSION
    cur = _parse_version(current)
    try:
        release = await asyncio.to_thread(_fetch_latest_release)
    except urllib.error.URLError as e:
        return {
            "status": "unknown", "current_version": current, "latest_version": None,
            "url": None, "error": f"无法访问 GitHub（离线/内网环境？）：{e.reason}",
        }
    except Exception as e:
        return {
            "status": "unknown", "current_version": current, "latest_version": None,
            "url": None, "error": f"检查更新失败：{e}",
        }
    latest_tag = str(release.get("tag_name") or "")
    url = release.get("html_url")
    latest = _parse_version(latest_tag)
    if latest is None:
        return {
            "status": "unknown", "current_version": current,
            "latest_version": latest_tag or None, "url": url,
            "error": f"无法解析上游版本号：{latest_tag or '(空)'}",
        }
    if cur is None:
        return {
            "status": "unknown", "current_version": current, "latest_version": latest_tag,
            "url": url, "error": f"无法解析本地版本号：{current}",
        }
    if latest > cur:
        status = "updateAvailable"
    elif latest == cur:
        status = "upToDate"
    else:
        status = "installedNewer"
    return {
        "status": status, "current_version": current,
        "latest_version": latest_tag, "url": url, "error": None,
    }
```

- [ ] **Step 4: 前端接入**

`api.ts`:

```ts
export interface ScrcpyUpdateResult {
  status: 'upToDate' | 'updateAvailable' | 'installedNewer' | 'unknown'
  current_version: string
  latest_version: string | null
  url: string | null
  error: string | null
}

// api 对象内：
  async checkScrcpyUpdate(): Promise<ScrcpyUpdateResult> {
    const res = await http.get<ScrcpyUpdateResult>('/api/system/scrcpy-update')
    return res.data
  },
```

`Settings.vue` 运行环境卡加：

```html
        <el-button data-test="check-update" :loading="checkingUpdate" @click="checkUpdate">
          检查更新
        </el-button>
        <div v-if="updateResult" data-test="update-result">
          <!-- 四态文案 -->
          <template v-if="updateResult.status === 'updateAvailable'">
            上游有 {{ updateResult.latest_version }}（本地 {{ updateResult.current_version }}），
            升级请走方案 44 换包流程
            <a v-if="updateResult.url" :href="updateResult.url" target="_blank" rel="noopener">
              查看发布页
            </a>
          </template>
          <template v-else-if="updateResult.status === 'upToDate'">
            已是最新（本地 {{ updateResult.current_version }}）
          </template>
          <template v-else-if="updateResult.status === 'installedNewer'">
            本地版本高于上游（本地 {{ updateResult.current_version }} / 上游 {{ updateResult.latest_version }}）
          </template>
          <template v-else>{{ updateResult.error }}</template>
        </div>
```

```ts
const checkingUpdate = ref(false)
const updateResult = ref<ScrcpyUpdateResult | null>(null)

async function checkUpdate() {
  checkingUpdate.value = true
  try {
    const result = await api.checkScrcpyUpdate()
    updateResult.value = result
    if (result.status === 'unknown') ElMessage.warning(result.error ?? '检查更新失败')
  } catch (e) {
    ElMessage.error('检查更新失败')
  } finally {
    checkingUpdate.value = false
  }
}
```

`Settings.test.ts` 扩展：mock `checkScrcpyUpdate` 依次返回四态，断言对应文案（updateAvailable 文案 + 链接 href；unknown 显示 error + warning toast）。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_scrcpy_update.py -v && cd frontend && npx vitest run src/views/Settings.test.ts && cd ..`
Expected: 全 PASS。

- [ ] **Step 6: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80 && \
cd frontend && npx vitest run && npx vue-tsc -b && cd ..
git add backend/app/interfaces/http/system.py frontend/src/services/api.ts \
  frontend/src/views/Settings.vue frontend/src/views/Settings.test.ts \
  tests/unit/test_scrcpy_update.py
git commit -m "feat: scrcpy 检查更新接口与设置页按钮（方案 45 T7）"
```

---

## Task 8: 设备级参数覆盖

**Files:**
- Modify: `config/settings.py`（`DeviceId`/`DeviceOverride`/`Settings.devices` + 两个合并 helper）
- Modify: `backend/app/interfaces/http/settings.py`（GET/PUT 扩展 `devices` 节）
- Modify: `backend/app/application/stream_service.py`（`start_stream` 消费 `device_stream_settings`）
- Modify: `backend/app/application/recording_service.py`（`start` 与 `_self_heal` 消费）
- Modify: `frontend/src/services/api.ts`（SettingsView/Patch 扩展 devices）
- Modify: `frontend/src/views/Settings.vue`（「设备级覆盖」卡 + `?device=` 聚焦）
- Modify: `frontend/src/views/DeviceDetail.vue`（跳转带 `?device=`)
- Test: `tests/unit/test_device_overrides.py`（新）、`tests/unit/test_settings_api.py`、`tests/unit/test_recording_service.py` 扩展、`Settings.test.ts` 扩展

**Interfaces:**
- Consumes: T2 的 StreamOverride/RecordingOverride、settings_store 读写函数；T5 的字段组件与 api 类型。
- Produces:
  - `DeviceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]`
  - `device_stream_settings(device_id) -> StreamConfig` / `device_recording_settings(device_id) -> RecordingConfig`（覆盖节存在即深优于全局，未覆盖键回落）。

- [ ] **Step 1: 模型与 helpers**（`config/settings.py`；import 加 `StringConstraints`，`Annotated` 已由 T2 引入）

Settings 类加字段（现类体内 stream/recording 旁）：

```python
    devices: dict[DeviceId, DeviceOverride] = Field(default_factory=dict)
```

类定义前加：

```python
DeviceId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
]
```

类定义后（helper 区）加：

```python
class DeviceOverride(BaseModel):
    """单设备覆盖节（stream/recording 各可选；节为 None 表示不覆盖该侧）。"""

    model_config = ConfigDict(extra="forbid")

    stream: StreamOverride | None = None
    recording: RecordingOverride | None = None


def device_stream_settings(device_id: str) -> StreamConfig:
    """设备级 stream 合并视图：覆盖节内非空字段深优于全局，未覆盖键回落全局。"""
    base = settings().stream
    dev = settings().devices.get(device_id)
    if dev is None or dev.stream is None:
        return base
    patch = dev.stream.model_dump(exclude_none=True)
    return base.model_copy(update=patch) if patch else base


def device_recording_settings(device_id: str) -> RecordingConfig:
    """设备级 recording 合并视图（同 device_stream_settings 语义）。"""
    base = settings().recording
    dev = settings().devices.get(device_id)
    if dev is None or dev.recording is None:
        return base
    patch = dev.recording.model_dump(exclude_none=True)
    return base.model_copy(update=patch) if patch else base
```

（mypy strict 下 `settings().devices.get(device_id)` 的 key 类型与 Annotated 别名相容性若有告警，用 `# type: ignore[arg-type]` 与行内理由；StreamConfig/RecordingConfig 需为 BaseModel——现实现 `class StreamConfig(BaseSettings)` 满足 `.model_copy(update=...)`，核对属实即可。）

- [ ] **Step 2: 写失败测试**（`tests/unit/test_device_overrides.py`）

```python
"""设备级参数覆盖 helpers 测试（方案 45 T8）。"""
import yaml

import config.settings as config_settings
from config.settings import (
    device_recording_settings,
    device_stream_settings,
    reload_settings,
    settings,
)


def _layered_loader(layers):
    def loader(env: str = "base"):
        value = layers.get(env, {})
        import copy
        return copy.deepcopy(value) if isinstance(value, dict) else value
    return loader


def test_device_stream_override_and_fallback(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": {"app": {"name": "OpenScrcpy"},
                     "server": {"host": "0.0.0.0", "port": 8765}},
            "user": {"devices": {"dev-a": {"stream": {"max_size": 720}}}},
        }),
    )
    reload_settings()

    ds = device_stream_settings("dev-a")
    assert ds.max_size == 720
    assert ds.fps == 30  # 未覆盖键回落全局默认
    assert device_stream_settings("other").max_size == 1080


def test_device_recording_override(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": {"app": {"name": "OpenScrcpy"},
                     "server": {"host": "0.0.0.0", "port": 8765}},
            "user": {"devices": {"dev-a": {"recording": {"time_limit": 120}}}},
        }),
    )
    reload_settings()

    dr = device_recording_settings("dev-a")
    assert dr.time_limit == 120
    assert device_recording_settings("other").time_limit == 60


def test_device_key_with_special_chars_roundtrip():
    dev_id = "192.168.8.18:5555"  # ':' '.' 在 yaml 键内往返一致（Review Focus #5）
    cfg = {"devices": {dev_id: {"stream": {"fps": 24}}}}
    dumped = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=True)
    reloaded = yaml.safe_load(dumped)

    assert reloaded["devices"][dev_id] == {"stream": {"fps": 24}}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_device_overrides.py -v`
Expected: FAIL（helpers 不存在），实现后 PASS。

- [ ] **Step 4: HTTP 扩展**（`backend/app/interfaces/http/settings.py`）

  - `SettingsPatch` 加 `devices: dict[DeviceId, DeviceOverride | None] | None = None`（import 加 `DeviceId`/`DeviceOverride`；`_require_any_section` 的 fields_set 判定自然覆盖 devices 段）。
  - 新增合并函数（复用 settings_store 的写函数与异常）：

```python
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
            f"{user_yaml_path().name} 的 devices 节不是映射，无法合并（请先修复文件）"
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
```

  （import 加 `SettingsStoreError`、`user_yaml_path`。）
  - `PUT` 处理体加：

```python
    if "devices" in patch.model_fields_set and patch.devices is not None:
        _merge_devices(user, patch.devices)
```

  - `GET` 返回体加 `devices` 段：

```python
        "devices": {
            dev_id: override.model_dump(exclude_none=True)
            for dev_id, override in s.devices.items()
        },
```

- [ ] **Step 5: 接口测试扩展**（`tests/unit/test_settings_api.py` 追加）

```python
def test_put_device_override_and_get_lists_it(settings_env):
    res = make_client().put(
        "/api/settings",
        json={"devices": {"192.168.8.18:5555": {"stream": {"max_size": 720}}}},
    )
    assert res.status_code == 200

    reload_settings()
    body = make_client().get("/api/settings").json()
    assert body["devices"]["192.168.8.18:5555"]["stream"] == {"max_size": 720}


def test_put_device_override_null_removes_device(settings_env):
    make_client().put(
        "/api/settings",
        json={"devices": {"d1": {"stream": {"fps": 24}}, "d2": {"stream": {"fps": 10}}}},
    )
    res = make_client().put("/api/settings", json={"devices": {"d1": None}})
    assert res.status_code == 200

    reload_settings()
    body = make_client().get("/api/settings").json()
    assert "d1" not in body["devices"]
    assert "d2" in body["devices"]


def test_put_device_override_rejects_bad_device_key(settings_env):
    res = make_client().put(
        "/api/settings", json={"devices": {"bad key!": {"stream": {"fps": 24}}}}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 6: 消费点改造**（零行为变化设备；逐处替换后跑既有回归）

`stream_service.py` 的 `start_stream`：读配置处 `s = settings().stream` 替换为 `s = device_stream_settings(device_id)`（import 调整；T3 加的 raw fallback 守卫顺带改用该变量——语义不变）。

`recording_service.py`：
  - `start`：`cfg = settings().recording` → `cfg = device_recording_settings(device_id)`。
  - `_self_heal`：模块级 `cfg` 删除；stale 判定改为 `device_recording_settings(s.device_id).time_limit`；`stuck_cutoff` 从循环外标量移入循环内按会话设备计算 `cutoff = now - (device_recording_settings(s.device_id).time_limit + self.STUCK_STATE_TIMEOUT_SECONDS)`。

`tests/unit/test_recording_service.py` 扩展一条设备级巡检用例：user 层给会话设备设更大 time_limit → `_self_heal` 不把该会话判 stale（照现有 self-heal 用例 harness 写）。

- [ ] **Step 7: 前端覆盖卡**（`Settings.vue` + `api.ts` + `DeviceDetail.vue`）

`api.ts`: `SettingsView` 加 `devices: Record<string, { stream?: Record<string, number | string | boolean>; recording?: ... }>`；`SettingsPatch` 加 `devices?: Record<string, Record<string, Record<string, unknown>> | null>`。

`Settings.vue` 加「设备级覆盖」卡：

```html
    <el-card class="section-card">
      <template #header>设备级参数覆盖</template>
      <div class="section-row">
        <el-select v-model="overrideDeviceId" placeholder="选择设备" data-test="override-device-select">
          <el-option v-for="d in deviceList" :key="d.device_id" :label="d.device_id" :value="d.device_id" />
        </el-select>
        <template v-if="overrideDeviceId">
          <StreamFieldSet v-model="overrideStreamForm" :h265-supported="h265Supported" />
          <div class="actions">
            <el-button type="primary" data-test="save-override" :loading="saving" @click="saveOverride">
              保存该设备覆盖
            </el-button>
            <el-button data-test="clear-override" @click="clearOverride">清除该设备覆盖</el-button>
          </div>
        </template>
        <span v-else class="hint">选择设备后可为该设备单独调整串流参数（弱设备降规格）</span>
      </div>
    </el-card>
```

逻辑：`deviceList` 来自 `api.listDevices()`（现有方法）；`overrideDeviceId` 初始取 `route.query.device`（`useRoute`；String(query.device) 且在设备列表内则选中聚焦——spec §5.4 `?device=` 聚焦）；选中变化时以 `settingsView.devices[id]?.stream` 初始化 `overrideStreamForm`（无覆盖则用全局值）；`saveOverride` → `putSettings({ devices: { [id]: { stream: {...后端键} } } })` → 重拉 `loadSettings()`；`clearOverride` → confirm → `putSettings({ devices: { [id]: null } })`；保存 toast「已保存，该设备下次连接生效」。

`DeviceDetail.vue`: `goSettings` 改 `router.push({ path: '/settings', query: { device: deviceId } })`（deviceId 取现组件内设备 id 变量）。

`Settings.test.ts` 扩展：mock `api.listDevices`（`[{ device_id: 'd1' }, ...]`）+ `useRoute` query 注入（mount 时 mock `vue-router` 的 useRoute 或在 wrapper 上提供 route）——用例：query.device=d1 进入即选中 d1；选设备保存 → putSettings 带 devices 键；清除 → null。

- [ ] **Step 8: 跑测试确认通过 + 零回归**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_device_overrides.py tests/unit/test_settings_api.py tests/application/test_recording_service.py -v && cd frontend && npx vitest run && cd ..`
Expected: 全 PASS（含既有流/录屏用例零回归）。

- [ ] **Step 9: 门禁 + 提交**

Run:
```bash
python -m ruff check backend tests && python -m mypy backend/app && \
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80 && \
cd frontend && npx vitest run && npx vue-tsc -b && cd ..
git add config/settings.py backend/app/interfaces/http/settings.py \
  backend/app/application/stream_service.py backend/app/application/recording_service.py \
  frontend/src/services/api.ts frontend/src/views/Settings.vue \
  frontend/src/views/DeviceDetail.vue frontend/src/views/Settings.test.ts \
  tests/unit/test_device_overrides.py tests/unit/test_settings_api.py \
  tests/application/test_recording_service.py
git commit -m "feat: 设备级参数覆盖（方案 45 T8）"
```

---

## Task 9: 文档回写

**Files:**
- Modify: `docs/用户手册.md`（「设置页」章节：入口、三卡说明、codec 切换与 h265 支持面说明、生效语义表、设备级覆盖、检查更新、user.yaml 手改注意事项）
- Modify: `README.md`（页面结构加「设置页」；配置分层说明加 user.yaml 一行）
- Modify: `方案/进度追踪.md`（方案 45 实施完成归档，T1-T9 勾选 + 时间）

**Interfaces:** 无代码接口。内容要点（spec §5 为准）：

1. 加载链图示：`base.yaml → user.yaml → {env}.yaml → 环境变量`；user.yaml 可手改（watcher 自动热重载）；
2. 设置页三卡 + 设备覆盖卡逐一说明；保存语义「串流下次连接生效 / 录屏立即生效」醒目提示；
3. h265：仅当浏览器支持才可选项；后端 `video_codec` 已放开，h264 默认不变；
4. 检查更新：手动触发、仅提示，升级走方案 44 SOP；
5. 设备覆盖：`devices.<id>` 节示例 yaml 片段（含 `192.168.8.18:5555` 键的写法）；
6. 已知限制：写接口并发按后写覆盖；config/ 只读（Program Files）时保存会报错。

- [ ] **Step 1: 用户手册**——按上述要点改写/新增
- [ ] **Step 2: README + 进度追踪**

`方案/进度追踪.md`：方案 45 状态改「已实施（2026-10-14）」，T1-T9 全勾；保持与既有条目同格式（回写前先读该文件核对跨节一致性——项目教训：归段/表格数字/里程碑勾选须复查）。

- [ ] **Step 3: 提交**（文档任务跑一遍 pytest 冒烟即可，门禁四件套完整跑为终验）

Run:
```bash
PYTHONPATH=backend python -m pytest tests/unit/test_config_reload.py -q
git add docs/用户手册.md README.md 方案/进度追踪.md
git commit -m "docs: 方案 45 用户手册与 README 回写（方案 45 T9）"
```

---

## 计划自审结果

**Spec 覆盖核对**（方案 45 文档逐节 → 计划任务）：
- §5.1 配置分层（加载链 / 无条件监听 / frozen 同源）→ T1 ✓（frozen 同源由 `config_base_dir()` 复用天然满足，写向只读目录的报错 → T2 用例）
- §5.2 写接口（白名单校验 / merge / 原子写 / 不调 reload_settings / 响应回读）→ T2 ✓（「不调 reload_settings」照 spec 原文执行，测试中以手动 reload 模拟 watcher 收敛）
- §5.3 生效语义（stream 下次连接 / recording 立即 / codec 后端放开）→ T3/T5 提示文案 ✓；`adaptive_bitrate` 档位联动回归 → T3 既有测试零回归要求 ✓
- §5.4 页面形态（菜单 / 独立页 / 设备页跳转入口 / 三卡）→ T5/T6 ✓（`?device=` 聚焦 → T8）
- §4.1-② 生效参数来源标注 → T6 徽标 ✓（来源判定在后端 `_source_of`，前端零猜测，对齐 §七-4）
- §4.1-③ 运行环境信息（版本/路径）→ T6 ✓
- §4.2 检查更新（手动触发 / 离线报因 / 不自动下载）→ T7 ✓
- §4.2 设备级覆盖（user.yaml devices 节 / 消费点流启动+录制 start / `?device=` 聚焦）→ T8 ✓（巡检消费点一并覆盖——spec「消费点在流启动读配置处」的最小完备扩展，超出部分为既有 `_self_heal` 的 per-device 一致性必要项）
- §七风险：①后写覆盖（原子写）→ T2；②值域联动 hint → T5；③frozen 只读报错 → T2；④来源后端判定 → T2/T6；⑤外联合规拍板 → T7 手动触发 ✓

**Review Focus 兑现核对**：①→T1/T2、②→T2 write/merge、③→T2 boom 用例、④→T3 兜底分支 + T4 三态、⑤→T8 往返 + 422 用例。全部有测试钉住。

**类型一致性**：`StreamOverride`/`RecordingOverride` 定义于 T2、T3/T8 复用（同一 Literal 白名单）；`scan_packet_nalus(hevc=)` 签名 T3 定义、T4 前端对应 family 常量同值；`device_stream_settings` 签名 T8 定义、T3 守卫与 T8 消费同一变量；前端 `SettingsView`/`SettingsPatch` 形状 T5→T8 扩展向后兼容。

**执行顺序依赖**：T2→T5（接口），T3→T4（config 消息），T4→T5（codec 下拉门控），T5→T6/T7（同页追加），T2+T5→T8（覆盖卡复用字段组件 + devices 节扩展）。