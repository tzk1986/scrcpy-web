# 方案 46「录屏产物独立页面」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增全局 `/recordings` 页统一处理历史录屏产物（列表/播放/下载/删除/导出 webm），设备页 `RecordingPanel` done 态收敛为轻量提示 + 跳转。

**Architecture:** 后端在既有 `recording.py`（prefix `/api/recording`）扩展三个静态段端点（`GET /files`、`GET /files/{filename}`、`DELETE /files/{filename}`），扫盘以落盘文件为准（复用 `RecordingService.resolve_dir`/`LOCAL_FILE_GLOB` 与 containment 先例）；录屏产物命名嵌入设备标识（`openscrcpy_rec_{dev}__{12hex}.mp4`，旧格式兼容解析）。前端新增 `Recordings.vue` 视图 + 路由 + 菜单项，`RecordingPanel` 移除预览/下载/webm 逻辑。

**Tech Stack:** FastAPI（四层架构，接口层薄）、Vue 3 + Element Plus + vue-router、vitest（happy-dom shallowMount + 组件 stub）、pytest（FakeRecordingService dependency_overrides 模式）。

**Spec:** `方案/46-录屏产物独立页面方案.md`（已拍板定稿：删除进第一版、`?device=` 过滤做、旧格式文案「未知设备（旧版产物）」、菜单文案「录屏」）

## Global Constraints

- 零新依赖（前后端均不得新增包）
- master 直做，不建 worktree；每任务独立提交，中文提交信息，无 Co-Authored-By
- 门禁四件套：`ruff check backend/app`、`mypy`（strict，CI 口径）、`pytest tests/ --ignore=tests/e2e` cov≥80、`vitest` + `vue-tsc`
- 测试文件一律放 `tests/` 下（后端）或同目录 `*.test.ts`（前端既有惯例）
- 存量 API 只增不改：`/api/recording/{device_id}` 系列语义保持；`/api/recording`（进行态列表）保持
- 不动抓帧/流链路；录屏会话互斥、自愈巡检、自然结束监听（方案 42）保持不变
- 落盘前缀不变：`LOCAL_FILE_GLOB = "openscrcpy_rec_*.mp4"`（双限清理/关服清盘零影响的前提）
- 错误语义不泄露目录结构：404 一律走既有 `NO_RECORDING` 异常体系
- 仅支持 Chrome（WebCodecs/captureStream 可用）；`<video>` 播 H.264 MP4 不受 SecureContext 限制

## Review Focus

1. **路由遮蔽**：`GET /api/recording/files` 若声明晚于 `GET /{device_id}`，会被 `{device_id="files"}` 吞掉返回 404——列表必须 200（新页全盲）；Task 2 以显式顺序回归测试钉住。
2. **旧文件兼容**：存量 `openscrcpy_rec_{12hex}.mp4` 必须出现在列表（device=null →「未知设备（旧版产物）」）且可播/可下载/可删；不可因新命名解析失败而被列表静默过滤。
3. **路径穿越与白名单**：`filename` 含 `/`、`%2F`、`..`、非 `openscrcpy_rec_` 前缀、非 `.mp4` 后缀——一律 404（不泄露目录结构）；列表只列白名单可过的文件（保证「列表可见即可下载」一致，否则出现删不掉的幽灵行）。
4. **删除响亮失败**：Windows 下文件被下载器/播放器持有时 unlink 抛 `FileExistsError/OSError`——必须返回 500 `RECORDING_DELETE_FAILED` 而非假成功 200 或谎报 404；前端如实 toast。
5. **done 态收敛回归**：设备页 done 不再加载 `<video>`/不发 `/file` 请求；「查看录屏」链接必须携带正确 URL 编码的 `?device=`（含 `:` 的 IP:port 设备 id）。

---

### Task 1: 后端落盘命名改造 + 解析辅助（RecordingService）

**Files:**
- Modify: `backend/app/application/recording_service.py`（`start` 的 `remote_path` 生成 :107-108；新增模块级正则与两个类方法）
- Modify: `tests/application/test_recording_service.py`（:77 命名断言；§3 清理用例 :372-374 增加新命名条目；新增解析专项用例）

**Interfaces:**
- Consumes: 无（本任务是源头）
- Produces:
  - `RecordingService._sanitize_device(device_id: str) -> str`（实例方法，Task 2 不直接用但同文件）
  - `RecordingService.parse_product_name(filename: str) -> tuple[str | None, str | None]`（staticmethod：新格式→`(dev, sid)`；旧格式→`(None, sid)`；不匹配→`(None, None)`）
  - 新落盘名规则：`/sdcard/openscrcpy_rec_{dev}__{12hex}.mp4`，`dev = re.sub("[^A-Za-z0-9._-]", "-", device_id)`

- [ ] **Step 1: 写失败测试（解析辅助 + 新命名）**

在 `tests/application/test_recording_service.py` 追加（文件已有 `RecordingService` 导入与 `make_adb`/构造替身，沿用）：

```python
# ---------------------------------------------------------------------------
# 方案 46 T1：落盘命名嵌设备标识 + 新旧格式解析
# ---------------------------------------------------------------------------


def test_start_remote_path_embeds_device(service: RecordingService) -> None:
    """start 生成 openscrcpy_rec_{dev}__{12hex}.mp4：IP:port 设备 id 被 sanitize。"""
    s = asyncio.run(service.start("192.168.8.18:5555"))
    assert s.remote_path == f"/sdcard/openscrcpy_rec_192.168.8.18-5555__{s.id}.mp4"


def test_start_remote_path_plain_serial_unchanged(service: RecordingService) -> None:
    """USB 序列号形态（合法字符集）原样嵌入。"""
    s = asyncio.run(service.start("rk3568_r"))
    assert s.remote_path == f"/sdcard/openscrcpy_rec_rk3568_r__{s.id}.mp4"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4", ("192.168.8.18-5555", "a1b2c3d4e5f6")),
        ("openscrcpy_rec_a1b2c3d4e5f6.mp4", (None, "a1b2c3d4e5f6")),  # 旧格式
        ("openscrcpy_rec_A1B2C3D4E5F6.mp4", (None, None)),  # 大写 hex 非法
        ("openscrcpy_rec_dev_x__a1b2c3d4e5f6.mp4", ("dev_x", "a1b2c3d4e5f6")),  # dev 内单下划线
        ("openscrcpy_rec_tooshort__a1b2c3d4e5f67.mp4", (None, None)),  # sid 13 位
        ("other.mp4", (None, None)),
        ("openscrcpy_rec_a1b2c3d4e5f6.txt", (None, None)),
    ],
)
def test_parse_product_name(name: str, expected: tuple[str | None, str | None]) -> None:
    assert RecordingService.parse_product_name(name) == expected
```

注：`service` fixture 名以该文件现状为准（若无则新建 `RecordingService(AsyncMock 驱动)` 的既有替身——本文件顶部已有构造 `adb` 替身工厂，直接复用；若无 pytest-asyncio 则沿用文件内既有的 `asyncio.run` / 事件循环模式）。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/application/test_recording_service.py -k "embeds_device or parse_product_name" -v`
Expected: FAIL（`parse_product_name` 不存在 / remote_path 断言不符）

- [ ] **Step 3: 实现命名与解析**

`backend/app/application/recording_service.py`：

顶部 import 区加 `import re`；类内新增（放在 `LOCAL_FILE_GLOB` 常量之后）：

```python
    # 新命名：openscrcpy_rec_{dev}__{12hex}.mp4；旧命名：openscrcpy_rec_{12hex}.mp4
    # dev 段 sanitize 后取 [A-Za-z0-9._-]+；__ 从右一次分割天然容纳 dev 内单下划线
    _NEW_NAME_RE = re.compile(r"^openscrcpy_rec_(?P<dev>[A-Za-z0-9._-]+)__(?P<sid>[0-9a-f]{12})\.mp4$")
    _OLD_NAME_RE = re.compile(r"^openscrcpy_rec_(?P<sid>[0-9a-f]{12})\.mp4$")

    @staticmethod
    def _sanitize_device(device_id: str) -> str:
        """设备 id → 文件名片段：非法字符替换为 `-`（方案 46 §3.3）。"""
        return re.sub(r"[^A-Za-z0-9._-]", "-", device_id)

    @staticmethod
    def parse_product_name(filename: str) -> tuple[str | None, str | None]:
        """
        落盘文件名 → (device, session_id)（方案 46 §3.3）。

        新格式返回 (dev, sid)；旧格式返回 (None, sid)；
        两者皆不匹配（含大写 hex、段数异常）返回 (None, None)。
        """
        m = RecordingService._NEW_NAME_RE.match(filename)
        if m:
            return m.group("dev"), m.group("sid")
        m = RecordingService._OLD_NAME_RE.match(filename)
        if m:
            return None, m.group("sid")
        return None, None
```

`start()` 内 :107-108 改为：

```python
        session_id = uuid4().hex[:12]
        remote_path = (
            f"{self.REMOTE_PREFIX}{self._sanitize_device(device_id)}__{session_id}.mp4"
        )
```

（设备端与本地同名规则不变——`pull_recording` 返回 basename，零改动。）

- [ ] **Step 4: 更新存量命名断言（:77）**

将 `assert s.remote_path == f"/sdcard/openscrcpy_rec_{s.id}.mp4"` 改为：

```python
    assert s.remote_path.endswith(f"__{s.id}.mp4") and s.remote_path.startswith(
        "/sdcard/openscrcpy_rec_"
    )
```

同文件其余含 `openscrcpy_rec_` 字面量的会话构造（:205/:211/:250/:257/:349/:363）是**手塞的 remote_path 替身值**，不参与生成逻辑，无需改动（旧格式字符串仍合法存在于历史会话对象中）。

- [ ] **Step 5: 双限清理兼容新命名断言（§3 用例 :372-374）**

`test_cleanup_all_recordings...` 的 `hits` 列表追加一条新命名文件：

```python
    hits = [
        tmp_path / "openscrcpy_rec_aaa111222333.mp4",
        tmp_path / "openscrcpy_rec_bbb111222333.mp4",
        tmp_path / "openscrcpy_rec_192.168.8.18-5555__ccc111222333.mp4",
    ]
```

（该用例按 hits 逐一 touch 并断言全部被删——glob 前缀不变即天然通过，此断言是回归钉。）

- [ ] **Step 6: 跑目标测试 GREEN + 全文件**

Run: `PYTHONPATH=backend python -m pytest tests/application/test_recording_service.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 门禁局部 + 提交**

```bash
ruff check backend/app && mypy backend/app/application/recording_service.py
git add backend/app/application/recording_service.py tests/application/test_recording_service.py
git commit -m "feat: 录屏落盘命名嵌设备标识与新旧格式解析辅助（方案 46 T1）"
```

---

### Task 2: 后端 files 三端点（列表/下载/删除）

**Files:**
- Modify: `backend/app/interfaces/http/recording.py`（新增 3 端点 + 白名单校验辅助；模块 docstring 补端点清单）
- Modify: `backend/app/core/exceptions.py`（新增 `RecordingDeleteError`）
- Modify: `tests/unit/test_recording_api.py`（Fake 扩展 + 三组新用例）

**Interfaces:**
- Consumes: Task 1 的 `RecordingService.LOCAL_FILE_GLOB`、`RecordingService.parse_product_name`
- Produces（Task 3 前端消费的响应契约）:
  - `GET /api/recording/files` → `{"files": [{"filename": str, "device": str | None, "session_id": str | None, "size_bytes": int, "created_at": str（UTC ISO）}]}`，mtime 降序
  - `GET /api/recording/files/{filename}` → `FileResponse(video/mp4)`；非法/越界/不存在 → 404 `NO_RECORDING`
  - `DELETE /api/recording/files/{filename}` → `{"deleted": true}`；同 404 语义；unlink 失败 → 500 `RECORDING_DELETE_FAILED`
  - `RecordingDeleteError(device_id: str)`：`status_code = 500`，`code = "RECORDING_DELETE_FAILED"`，仿 `RecordingNotFoundError` 结构

- [ ] **Step 1: 写失败测试（Fake 扩展 + 列表用例）**

`tests/unit/test_recording_api.py` 顶部 imports 补 `import os`、`from datetime import datetime, timezone`、`from app.core.exceptions import RecordingDeleteError`（用例内 raise 断言不需要）。`FakeRecordingService.__init__` 的 `self.dir` 保持默认，**每个文件用例自行设** `fake.dir = tmp_path`（fixture 已注入 fake）。追加用例（沿用文件既有 `client`/`fake` fixture 模式）：

```python
# ---------------------------------------------------------------------------
# 方案 46 T2：历史产物 files 三端点
# ---------------------------------------------------------------------------


def _make_product(fake: FakeRecordingService, tmp_path, name: str, size: int, mtime: float) -> None:
    fake.dir = tmp_path
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    os.utime(p, (mtime, mtime))


def test_files_list_parses_new_and_old_and_sorts_desc(client, fake, tmp_path) -> None:
    """列表：新旧格式解析、白名单外过滤、mtime 降序、字段完整。"""
    now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    _make_product(fake, tmp_path, "openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4", 1024, now - 5)
    _make_product(fake, tmp_path, "openscrcpy_rec_aaa111222333.mp4", 2048, now - 50)
    _make_product(fake, tmp_path, "other.mp4", 4096, now)  # 白名单外：不得出现
    _make_product(fake, tmp_path, "openscrcpy_rec_bad.MP4", 4096, now)  # 后缀大小写不符 glob

    res = client.get("/api/recording/files")

    assert res.status_code == 200
    files = res.json()["files"]
    assert [f["filename"] for f in files] == [
        "openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4",
        "openscrcpy_rec_aaa111222333.mp4",
    ]
    newest = files[0]
    assert newest["device"] == "192.168.8.18-5555"
    assert newest["session_id"] == "a1b2c3d4e5f6"
    assert newest["size_bytes"] == 1024
    assert newest["created_at"].endswith("+00:00")
    assert files[1]["device"] is None  # 旧格式：设备未知（前端呈现旧版文案）


def test_files_list_missing_dir_returns_empty(client, fake, tmp_path) -> None:
    """目录不存在：空列表不报错（首次使用/清盘后）。"""
    fake.dir = tmp_path / "nope"
    res = client.get("/api/recording/files")
    assert res.status_code == 200
    assert res.json() == {"files": []}


def test_files_not_shadowed_by_device_route(client, fake) -> None:
    """Review Focus ①：GET /files 必须走列表而非被 /{device_id} 吞成 404。"""
    fake.dir = Path("data/recordings")  # 不存在也无妨，关键是 200 而非 404
    res = client.get("/api/recording/files")
    assert res.status_code == 200
    assert "files" in res.json()


def test_files_download_streams_matched_bytes(client, fake, tmp_path) -> None:
    _make_product(fake, tmp_path, "openscrcpy_rec_aaa111222333.mp4", 3, 1700000000)
    res = client.get("/api/recording/files/openscrcpy_rec_aaa111222333.mp4")
    assert res.status_code == 200
    assert res.content == b"xxx"


@pytest.mark.parametrize(
    "bad",
    [
        "other.mp4",  # 前缀白名单外
        "..%2Fsecret.mp4",  # 编码斜杠（解码后含 / → 白名单拒）
        "openscrcpy_rec_aaa111222333.txt",  # 后缀不符
        "openscrcpy_rec_AAA111222333.mp4",  # 大写 hex 不合法命名
    ],
)
def test_files_download_and_delete_reject_invalid_names(client, fake, tmp_path, bad: str) -> None:
    """Review Focus ③：白名单/越界一律 404（不泄露目录结构）。"""
    _make_product(fake, tmp_path, "openscrcpy_rec_aaa111222333.mp4", 1, 1700000000)
    assert client.get(f"/api/recording/files/{bad}").status_code == 404
    assert client.delete(f"/api/recording/files/{bad}").status_code == 404


def test_files_download_missing_404(client, fake, tmp_path) -> None:
    fake.dir = tmp_path
    res = client.get("/api/recording/files/openscrcpy_rec_aaa111222333.mp4")
    assert res.status_code == 404


def test_files_delete_removes_file(client, fake, tmp_path) -> None:
    name = "openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4"
    _make_product(fake, tmp_path, name, 5, 1700000000)

    res = client.delete(f"/api/recording/files/{name}")

    assert res.status_code == 200
    assert res.json() == {"deleted": True}
    assert not (tmp_path / name).exists()


def test_files_delete_busy_returns_500(client, fake, tmp_path, monkeypatch) -> None:
    """Review Focus ④：Windows 持有句柄 unlink 失败 → 响亮 500，不假成功。"""

    def raise_busy(self: Path, missing_ok: bool = False) -> None:
        raise OSError("被占用")

    monkeypatch.setattr(Path, "unlink", raise_busy)
    _make_product(fake, tmp_path, "openscrcpy_rec_aaa111222333.mp4", 5, 1700000000)

    res = client.delete("/api/recording/files/openscrcpy_rec_aaa111222333.mp4")

    assert res.status_code == 500
    assert res.json()["error"]["code"] == "RECORDING_DELETE_FAILED"
    assert (tmp_path / "openscrcpy_rec_aaa111222333.mp4").exists()
```

注：404 响应体错误码断言沿用该文件既有 404 用例的 JSON 结构（`{"error": {"code": "NO_RECORDING", ...}}`——若现状只断 status_code，跟随现状即可，不新增响应体形状）。错误响应形状以 `OpenScrcpyException` 处理器现状为准。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_recording_api.py -k "files" -v`
Expected: FAIL（404/405——`/files` 被 `/{device_id}` 吞或端点不存在）

- [ ] **Step 3: 新增异常**

`backend/app/core/exceptions.py` 紧随 `RecordingNotFoundError` 之后：

```python
class RecordingDeleteError(OpenScrcpyException):
    """录屏产物删除失败（文件被占用等 OS 层错误，方案 46 T2）。"""

    status_code = 500

    def __init__(self, filename: str) -> None:
        super().__init__(f"录屏产物 {filename} 删除失败", code="RECORDING_DELETE_FAILED")
```

- [ ] **Step 4: 实现三端点**

`backend/app/interfaces/http/recording.py`：

imports 补：

```python
import re
from datetime import datetime, timezone
```

`RecordingService` 已导入（本 Task 用其 `LOCAL_FILE_GLOB`/`parse_product_name` 类级成员，经现有 `get_recording_service` 注入实例访问）。

模块级新增（放在 `_session_body` 之前）：

```python
# 历史产物文件名白名单（与 glob 同源 + 合法命名字符集；列表/下载/删除三处共用，
# 保证「列表可见即可下载/删除」一致，防幽灵行，方案 46 Review Focus ③）
_PRODUCT_NAME_RE = re.compile(r"^openscrcpy_rec_[A-Za-z0-9._-]+\.mp4$")


def _validated_product_path(service: RecordingService, filename: str) -> Path:
    """白名单 + containment + 存在性校验，返回解析后的绝对路径。

    任一不满足 → RecordingNotFoundError（404 NO_RECORDING，不泄露目录结构，
    与 {device_id}/file 端点同语义）。
    """
    if not _PRODUCT_NAME_RE.fullmatch(filename):
        raise RecordingNotFoundError(filename)
    records_dir = service.resolve_dir().resolve()
    target = (records_dir / filename).resolve()
    if not target.is_relative_to(records_dir) or not target.is_file():
        raise RecordingNotFoundError(filename)
    return target
```

在 `list_recordings`（`@router.get("")`）之后、`start_recording` 之前插入三个端点（顺序即遮蔽防线，Review Focus ①）：

```python
@router.get("/files")
async def list_recording_files(
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, list[dict[str, Any]]]:
    """
    历史录屏产物列表（扫盘为准，方案 46 §3.4）。

    返回：
        {"files": [{filename, device, session_id, size_bytes, created_at}, ...]}
        按 mtime 降序；目录不存在 → 空列表；白名单外文件不出现。
    """
    directory = service.resolve_dir()
    entries: list[tuple[float, dict[str, Any]]] = []
    if directory.is_dir():
        for path in directory.glob(RecordingService.LOCAL_FILE_GLOB):
            if not _PRODUCT_NAME_RE.fullmatch(path.name):
                continue
            try:
                st = path.stat()
            except OSError:  # 扫描间隙被清理：跳过
                continue
            device, session_id = RecordingService.parse_product_name(path.name)
            entries.append(
                (
                    st.st_mtime,
                    {
                        "filename": path.name,
                        "device": device,
                        "session_id": session_id,
                        "size_bytes": st.st_size,
                        "created_at": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    },
                )
            )
    entries.sort(key=lambda e: e[0], reverse=True)
    return {"files": [e[1] for e in entries]}


@router.get("/files/{filename}")
async def get_recording_file_by_name(
    filename: str,
    service: RecordingService = Depends(get_recording_service),
) -> FileResponse:
    """
    按文件名下载/播放历史产物（方案 46 §3.4）。

    放行：白名单 + 解析后落在录屏目录内 + 文件存在；否则 404 NO_RECORDING。
    """
    return FileResponse(_validated_product_path(service, filename), media_type="video/mp4")


@router.delete("/files/{filename}")
async def delete_recording_file(
    filename: str,
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, bool]:
    """
    删除单个历史产物（方案 46 拍板项 1：进第一版，与双限自动清理并存）。

    校验同下载；unlink 遇 OS 错误（Windows 占用）→ 500 RECORDING_DELETE_FAILED
    响亮失败不假成功（Review Focus ④）。
    """
    target = _validated_product_path(service, filename)
    try:
        target.unlink()
    except OSError as exc:
        logger.warning("recording_file_delete_failed", filename=filename, error=str(exc))
        raise RecordingDeleteError(filename) from exc
    logger.info("recording_file_deleted", filename=filename)
    return {"deleted": True}
```

文件需补 `from app.core.logging import get_logger` + `logger = get_logger(__name__)`（若模块现状无 logger）与 `from app.core.exceptions import RecordingDeleteError, RecordingNotFoundError`（扩展既有一行）。docstring 头部端点清单补 3 行。

- [ ] **Step 5: 跑测试 GREEN**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_recording_api.py -v`
Expected: 全部 PASS（含既有用例零回归）

- [ ] **Step 6: 门禁局部 + 提交**

```bash
ruff check backend/app && mypy backend/app/interfaces/http/recording.py backend/app/core/exceptions.py
git add backend/app/interfaces/http/recording.py backend/app/core/exceptions.py tests/unit/test_recording_api.py
git commit -m "feat: 录屏历史产物 files 三端点（列表/下载/删除）（方案 46 T2）"
```

---

### Task 3: 前端新页 `/recordings`（Recordings.vue + api + 路由 + 菜单）

**Files:**
- Modify: `frontend/src/services/api.ts`（recording 区块 :631-666 之后新增 3 方法 + `RecordingFileItem` 接口）
- Create: `frontend/src/views/Recordings.vue`
- Create: `frontend/src/views/Recordings.test.ts`
- Modify: `frontend/src/router/index.ts`（新增路由）
- Modify: `frontend/src/App.vue:19-21`（菜单加「录屏」）

**Interfaces:**
- Consumes: Task 2 的 `GET/DELETE /api/recording/files[/{filename}]` 响应契约（字段名逐字一致）
- Produces:
  - `api.RecordingFileItem`（export interface）、`api.listRecordingFiles()`、`api.deleteRecordingFile(name)`、`api.recordingFileUrl(name): string`
  - 路由 `/recordings`（name `recordings`）；query `?device=<id>` 过滤
  - `Recordings.vue`（Task 4 的「查看录屏」链接指向它）

- [ ] **Step 1: 写失败测试（组件用例骨架先行）**

`frontend/src/views/Recordings.test.ts`（仿 `Settings.test.ts`/`FileManager.test.ts` 模式：vi.hoisted mock api 与 webmExporter、el-* 轻量 stub、router-link stub）：

```ts
/**
 * Recordings 页测试（方案 46 T3）
 * ==============================
 * 覆盖：
 *   - 挂载加载列表：设备列原样 / 旧格式「未知设备（旧版产物）」/ 大小 1 位小数 /
 *     时间本地化 / 行内播放·下载·删除按钮
 *   - ?device= 过滤：仅该设备 + 「仅看」条 + 清除恢复全量
 *   - 下载 href：recordingFileUrl 编码文件名
 *   - 播放弹层：video src 指向文件端点；webm 导出按钮随支持面启用/禁用
 *   - 删除：二次确认 → api → 重拉；失败 → error toast 且列表不消失
 *   - 列表加载失败：error toast + 空态；空态含回 Dashboard 引导
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const h = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({
  ElMessage: h.ElMessage,
  ElMessageBox: h.ElMessageBox,
}))

const api = vi.hoisted(() => ({
  listRecordingFiles: vi.fn(),
  deleteRecordingFile: vi.fn(),
  recordingFileUrl: (name: string) => `/api/recording/files/${encodeURIComponent(name)}`,
}))
vi.mock('@/services/api', () => ({ api }))

const exporter = vi.hoisted(() => ({
  isWebmRecorderSupported: vi.fn(() => false),
  exportWebmClip: vi.fn(),
}))
vi.mock('@/utils/webmExporter', () => exporter)

import Recordings from './Recordings.vue'

const ITEM_NEW = {
  filename: 'openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4',
  device: '192.168.8.18-5555',
  session_id: 'a1b2c3d4e5f6',
  size_bytes: 3145728,
  created_at: '2026-10-14T04:00:00+00:00',
}
const ITEM_OLD = {
  filename: 'openscrcpy_rec_aaa111222333.mp4',
  device: null,
  session_id: 'aaa111222333',
  size_bytes: 1048576,
  created_at: '2026-10-13T04:00:00+00:00',
}

const RouterLinkStub = {
  name: 'RouterLink',
  props: ['to'],
  template: '<a :href="typeof to === \'string\' ? to : to.path">{{ $slots.default?.()[0]?.children }}</a>',
}

function mountPage(deviceQuery?: string) {
  const route = { query: deviceQuery ? { device: deviceQuery } : {} }
  return shallowMount(Recordings, {
    global: {
      mocks: { $route: route },
      stubs: {
        'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        'el-dialog': { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
        'el-empty': { template: '<div><slot /></div>' },
        RouterLinkStub,
      },
      components: { 'router-link': RouterLinkStub },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listRecordingFiles.mockResolvedValue({ files: [ITEM_NEW, ITEM_OLD] })
  h.ElMessageBox.confirm.mockResolvedValue(true)
})

describe('Recordings 页', () => {
  it('挂载加载列表：设备列 / 旧格式文案 / 大小一位小数', async () => {
    const w = mountPage()
    await flushPromises()
    expect(api.listRecordingFiles).toHaveBeenCalledTimes(1)
    const rows = w.findAll('.file-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('.col-device').text()).toBe('192.168.8.18-5555')
    expect(rows[1].find('.col-device').text()).toBe('未知设备（旧版产物）')
    expect(rows[0].find('.col-size').text()).toBe('3.0 MB')
  })

  it('?device= 进入即过滤并显示「仅看」条，清除后恢复全量', async () => {
    const w = mountPage('192.168.8.18:5555')
    await flushPromises()
    expect(w.findAll('.file-row')).toHaveLength(1)
    expect(w.find('.filter-bar').text()).toContain('192.168.8.18:5555')
    await w.find('.filter-clear').trigger('click')
    expect(w.findAll('.file-row')).toHaveLength(2)
  })

  it('下载链接 href 为编码后的文件端点', async () => {
    const w = mountPage()
    await flushPromises()
    const a = w.findAll('.file-row')[1].find('a.col-download')
    expect(a.attributes('href')).toBe('/api/recording/files/openscrcpy_rec_aaa111222333.mp4')
  })

  it('播放打开弹层：video src 指向文件端点，webm 按钮随支持面禁用', async () => {
    const w = mountPage()
    await flushPromises()
    await w.findAll('.file-row')[0].find('.btn-play').trigger('click')
    const video = w.find('video')
    expect(video.exists()).toBe(true)
    expect(video.attributes('src')).toBe(
      '/api/recording/files/openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4'
    )
    expect(w.find('.btn-webm').attributes('disabled')).toBeDefined()
    exporter.isWebmRecorderSupported.mockReturnValue(true)
    await w.find('.btn-play').trigger('click') // 触发重开以走支持分支——组件在点击时读支持面
    expect(w.find('.btn-webm').attributes('disabled')).toBeUndefined()
  })

  it('删除：确认后调 api 并重拉列表；失败仅 toast 不消失行', async () => {
    const w = mountPage()
    await flushPromises()
    api.deleteRecordingFile.mockResolvedValue({ deleted: true })
    await w.findAll('.file-row')[0].find('.btn-delete').trigger('click')
    await flushPromises()
    expect(api.deleteRecordingFile).toHaveBeenCalledWith(ITEM_NEW.filename)
    expect(api.listRecordingFiles).toHaveBeenCalledTimes(2)
    // 失败路径
    api.deleteRecordingFile.mockRejectedValue(new Error('busy'))
    await w.findAll('.file-row')[1].find('.btn-delete').trigger('click')
    await flushPromises()
    expect(h.ElMessage.error).toHaveBeenCalledWith('删除失败，文件可能正被占用')
  })

  it('取消确认不调删除', async () => {
    h.ElMessageBox.confirm.mockRejectedValue('cancel')
    const w = mountPage()
    await flushPromises()
    await w.findAll('.file-row')[0].find('.btn-delete').trigger('click')
    await flushPromises()
    expect(api.deleteRecordingFile).not.toHaveBeenCalled()
  })

  it('列表加载失败：error toast + 空态', async () => {
    api.listRecordingFiles.mockRejectedValue(new Error('boom'))
    const w = mountPage()
    await flushPromises()
    expect(h.ElMessage.error).toHaveBeenCalledWith('录屏列表加载失败')
    expect(w.findAll('.file-row')).toHaveLength(0)
    expect(w.find('.empty-hint').text()).toContain('暂无录屏')
  })
})
```

注：`$route` mock 采用全局 mocks（组件内 `useRoute` 若有，改为 mock `vue-router` 的 `useRoute`——按实现二选一保持一致；实现选择 `useRoute` 则测试改 `vi.mock('vue-router', () => ({ useRoute: () => ({ query: currentQuery }) }))`，`currentQuery` 用可变量。实现者按组件实际写法对齐，两种先例仓库均有。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/views/Recordings.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: api.ts 扩展**

`frontend/src/services/api.ts`——`RecordingSession` 接口附近加：

```ts
/** 历史录屏产物条目（方案 46，对应后端 GET /api/recording/files 元素）。 */
export interface RecordingFileItem {
  filename: string
  device: string | null
  session_id: string | null
  size_bytes: number
  created_at: string
}
```

api 对象 recording 区块（`listRecordings` 之后）加：

```ts
  /**
   * 历史录屏产物列表（方案 46）
   * 对应：GET /api/recording/files
   */
  async listRecordingFiles() {
    const res = await client.get('/recording/files')
    return res.data as { files: RecordingFileItem[] }
  },

  /**
   * 删除单个历史产物（方案 46 拍板项 1）
   * 对应：DELETE /api/recording/files/{filename}
   */
  async deleteRecordingFile(filename: string) {
    const res = await client.delete(`/recording/files/${encodeURIComponent(filename)}`)
    return res.data as { deleted: boolean }
  },

  /** 产物播放/下载端点 URL（GET /api/recording/files/{filename}，直链不经 axios）。 */
  recordingFileUrl(filename: string): string {
    return `/api/recording/files/${encodeURIComponent(filename)}`
  },
```

- [ ] **Step 4: 实现 Recordings.vue**

`frontend/src/views/Recordings.vue`：

```vue
<!--
  录屏产物页（方案 46）
  ====================

  全局历史录屏列表：扫盘为准（GET /api/recording/files），支持播放（弹层
  <video> + 导出 webm）、下载（<a download>）、删除（二次确认）。
  设备页「查看录屏」经 ?device=<id> 进入时按设备过滤（拍板项 2）。

  关键口径：
    - 设备列原样展示后端解析出的 dev 段（sanitize 后形态，无别名映射，与
      Dashboard 口径一致）；旧格式产物显示「未知设备（旧版产物）」（拍板项 3）
    - 大小一位小数 MB（RecordingPanel 同款口径）
    - webm 导出能力自 RecordingPanel 迁入（复用 webmExporter：captureStream +
      MediaRecorder 前 10s 短片；不支持时按钮禁用降级）
    - 刷新时机：挂载一次 + 手动刷新按钮；不轮询（录制中会话由设备页管理）
-->
<template>
  <div class="recordings-page">
    <div class="page-header">
      <h2>录屏</h2>
      <div class="header-actions">
        <el-button class="btn-refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div v-if="deviceFilter" class="filter-bar">
      <span>仅看 {{ deviceFilter }}</span>
      <el-button class="filter-clear" size="small" text @click="deviceFilter = ''">清除</el-button>
    </div>

    <div v-if="visibleFiles.length" class="file-list">
      <div v-for="f in visibleFiles" :key="f.filename" class="file-row">
        <span class="col-device">{{ f.device ?? '未知设备（旧版产物）' }}</span>
        <span class="col-time">{{ formatTime(f.created_at) }}</span>
        <span class="col-size">{{ formatSize(f.size_bytes) }}</span>
        <el-button class="btn-play" size="small" @click="openPlayer(f)">播放</el-button>
        <a class="col-download" :href="api.recordingFileUrl(f.filename)" :download="f.filename">下载</a>
        <el-button class="btn-delete" size="small" type="danger" text @click="remove(f)">删除</el-button>
      </div>
    </div>
    <div v-else class="empty-hint">
      暂无录屏。在设备页开始录制后，产物会出现在这里。
      <router-link to="/">回设备列表</router-link>
    </div>

    <el-dialog v-model="playerVisible" :title="playing?.filename" width="640px">
      <video ref="videoEl" class="player-video" :src="playingUrl" controls autoplay />
      <div class="player-actions">
        <a :href="playingUrl" :download="playing?.filename">下载</a>
        <el-button
          class="btn-webm"
          :disabled="!webmSupported || exporting"
          :title="webmSupported ? '' : '当前浏览器不支持 webm 录制导出'"
          @click="exportWebm"
        >
          {{ exporting ? '导出中…' : '导出 webm' }}
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script lang="ts">
/** 字节 → 一位小数 MB（面板既有口径，独立导出供测试直接断言）。 */
export function formatSizeMB(bytes: number): string {
  return `${(bytes / 1048576).toFixed(1)} MB`
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type RecordingFileItem } from '@/services/api'
import { exportWebmClip, isWebmRecorderSupported } from '@/utils/webmExporter'

const route = useRoute()

const files = ref<RecordingFileItem[]>([])
const deviceFilter = ref<string>(typeof route.query.device === 'string' ? route.query.device : '')
const playerVisible = ref(false)
const playing = ref<RecordingFileItem | null>(null)
const videoEl = ref<HTMLVideoElement | null>(null)
const exporting = ref(false)

/** 支持面在打开弹层时读取（浏览器状态可变化，测试可重开断言两分支）。 */
const webmSupported = ref(true)

const visibleFiles = computed(() =>
  deviceFilter.value
    ? files.value.filter(
        (f) => f.device && sanitizeDeviceId(deviceFilter.value) === f.device
      )
    : files.value
)

const playingUrl = computed(() =>
  playing.value ? api.recordingFileUrl(playing.value.filename) : ''
)

/** 与后端 _sanitize_device 同规则：非法字符 → `-`（IP:port 查询匹配落盘名 dev 段）。 */
function sanitizeDeviceId(id: string): string {
  return id.replace(/[^A-Za-z0-9._-]/g, '-')
}

function formatSize(bytes: number): string {
  return formatSizeMB(bytes)
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function load() {
  try {
    const res = await api.listRecordingFiles()
    files.value = res.files
  } catch {
    files.value = []
    ElMessage.error('录屏列表加载失败')
  }
}

function openPlayer(f: RecordingFileItem) {
  playing.value = f
  webmSupported.value = isWebmRecorderSupported()
  playerVisible.value = true
}

/** Blob 下载（RecordingPanel 同款范式）。 */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function exportWebm() {
  const el = videoEl.value
  if (!el || exporting.value || !webmSupported.value) return
  exporting.value = true
  try {
    const blob = await exportWebmClip(el)
    triggerDownload(blob, 'clip.webm')
  } catch {
    ElMessage.error('webm 导出失败')
  } finally {
    exporting.value = false
  }
}

async function remove(f: RecordingFileItem) {
  try {
    await ElMessageBox.confirm(`删除录屏 ${f.filename}？该操作不可恢复`, '删除录屏', {
      type: 'warning',
    })
  } catch {
    return // 取消
  }
  try {
    await api.deleteRecordingFile(f.filename)
    ElMessage.success('已删除')
    await load()
  } catch {
    ElMessage.error('删除失败，文件可能正被占用')
  }
}

onMounted(load)
</script>

<style scoped>
.recordings-page {
  padding: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 10px;
  background: #ecf5ff;
  border-radius: 4px;
  color: #409eff;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}

.col-device {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.col-time,
.col-size {
  color: #606266;
  font-variant-numeric: tabular-nums;
}

.empty-hint {
  padding: 32px 0;
  text-align: center;
  color: #909399;
}

.player-video {
  width: 100%;
  background: #000;
}

.player-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
</style>
```

注意（实现者对齐测试）：组件使用 `useRoute`——测试文件需把 `$route` mocks 方案换成 `vi.mock('vue-router', ...)`：

```ts
let currentQuery: Record<string, string> = {}
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: currentQuery }),
}))
```

`mountPage(deviceQuery)` 里设 `currentQuery = deviceQuery ? { device: deviceQuery } : {}` 后再 mount（beforeEach 重置）。

- [ ] **Step 5: 路由与菜单**

`frontend/src/router/index.ts`——`/settings` 项之后加：

```ts
    {
      // 录屏产物独立页（方案 46）：历史列表/播放/下载/删除；?device= 过滤
      path: '/recordings',
      name: 'recordings',
      component: () => import('@/views/Recordings.vue'),
    },
```

`frontend/src/App.vue` 菜单（:19-21）——「Dashboard」与「设置」之间加：

```html
            <el-menu-item index="/recordings">录屏</el-menu-item>
```

- [ ] **Step 6: 跑测试 GREEN + vue-tsc**

Run: `cd frontend && npx vitest run src/views/Recordings.test.ts && npx vue-tsc --noEmit`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add frontend/src/services/api.ts frontend/src/views/Recordings.vue frontend/src/views/Recordings.test.ts frontend/src/router/index.ts frontend/src/App.vue
git commit -m "feat: 录屏产物独立页 /recordings（列表/播放/下载/删除/?device= 过滤）（方案 46 T3）"
```

---

### Task 4: RecordingPanel done 态收敛

**Files:**
- Modify: `frontend/src/components/stream/RecordingPanel.vue`（done 分支 + 删除预览/下载/webm 全部逻辑）
- Modify: `frontend/src/components/stream/RecordingPanel.test.ts`（done 断言改造、webm 用例删除）

**Interfaces:**
- Consumes: Task 3 的路由 `/recordings` 与 query `device`
- Produces: 精简后的 `RecordingPanel`（三态只剩启停 + done 一行提示）；`formatElapsed` 导出保持不变（测试依赖）

- [ ] **Step 1: 改造测试（RED）**

`RecordingPanel.test.ts`：
1. 删除整块 webm 导出用例（:245-299 区间，`export-webm` 相关 4 用例）与 `webmExporter` mock；
2. `mountAndSettle` 的 done 恢复函数名保持，但断言改为——`:145-169` 用例替换为：

```ts
  it('停止返回 done → 一行提示：已保存 + 查看录屏链接 + 重新录制', async () => {
    mockApi.stopRecording.mockResolvedValue({
      session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
      file_size: 3145728,
    })
    const wrapper = await mountIdle()
    await wrapper.find('.record-start').trigger('click')
    await flushPromises()
    await wrapper.find('.record-stop').trigger('click')
    await flushPromises()

    expect(wrapper.find('video').exists()).toBe(false)
    expect(wrapper.find('a.record-download').exists()).toBe(false)
    expect(wrapper.find('.export-webm').exists()).toBe(false)
    const view = wrapper.find('.record-view')
    expect(view.exists()).toBe(true)
    expect(view.attributes('href')).toBe(
      '/recordings?device=192.168.8.18%3A5555'
    )
    expect(wrapper.find('.record-info').text()).toContain('已保存')
    expect(wrapper.find('.record-info').text()).toContain('约 3.0 MB')
    expect(wrapper.find('.record-again').exists()).toBe(true)
  })
```

（`mountIdle` 辅助名沿用文件现状；deviceId 用文件 fixture 里现用的 `192.168.8.18:5555`——若现状用其他 id，替换断言中的编码值保持一致。）

3. `:205` 与 `:322` 的挂载恢复 done 用例：断言 `a.record-download` / video 改为 `.record-view` 存在 + `.record-info` 文案；
4. 测试文件加 `router-link` stub（href 渲染）：

```ts
const RouterLinkStub = {
  name: 'RouterLink',
  props: ['to'],
  template: '<a :href="to"></a>',
}
// mount 选项 global.stubs/components 里注册 'router-link': RouterLinkStub
```

（`to` 传字符串 href 即如上断言。）

5. `:121`（recording 态不显示导出按钮）删除该断言行（组件已无此元素，`false` 断言仍过可保留——按最小改动原则保留原断言即可，无需改）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/components/stream/RecordingPanel.test.ts`
Expected: done 用例 FAIL（仍渲染 video/下载链接）

- [ ] **Step 3: 精简组件**

`RecordingPanel.vue` 改动：

模板 done 分支（:36-49）整体替换为：

```html
    <!-- 完成：一行轻量提示 + 跳转录屏页 + 重新录制（产物处理移出设备页，方案 46） -->
    <div v-if="state === 'done'" class="recording-done">
      <span class="record-info">已保存{{ fileSizeText ? `（${fileSizeText}）` : '' }}</span>
      <router-link
        class="record-view"
        :to="`/recordings?device=${encodeURIComponent(props.deviceId)}`"
        >查看录屏</router-link
      >
      <el-button class="record-again" @click="start">重新录制</el-button>
    </div>
```

script setup 删除：`videoEl`、`exporting`、`webmSupported`、`fileUrl`、`downloadName`、`triggerDownload`、`exportWebm`、`import { exportWebmClip, isWebmRecorderSupported } ...` 整行、`fileSizeText` 改为返回纯尺寸文本（现值 `约 X MB` 保留，模板拼接括号）。`PanelState`、计时、start/stop/onMounted/onUnmounted 全保留。

样式删除：`.record-preview`（`.record-download` 若无样式规则不用管）。

模板头注释（:10-14）done 行改为：

```
    done      — 一行提示「已保存（约 X MB）」+「查看录屏」跳转 /recordings?device=
              +「重新录制」（方案 46：预览/下载/webm 导出移入独立录屏页）
```

- [ ] **Step 4: 跑测试 GREEN + 全前端**

Run: `cd frontend && npx vitest run src/components/stream/RecordingPanel.test.ts && npx vitest run && npx vue-tsc --noEmit`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/stream/RecordingPanel.vue frontend/src/components/stream/RecordingPanel.test.ts
git commit -m "refactor: RecordingPanel done 态收敛为已保存提示+跳转，产物处理移出设备页（方案 46 T4）"
```

---

### Task 5: 全量门禁 + 真机冒烟 + 文档回写

**Files:**
- Modify: `docs/用户手册.md`（录屏章节：产物移到「录屏」页处理；新增 5.x 录屏页小节）
- Modify: `README.md`（特性 bullets + 方案表 46 行 + 页面结构加 /recordings）
- Modify: `CHANGELOG.md`（`[Unreleased]` 段加方案 46 条目）
- Modify: `方案/进度追踪.md`（第八十四次更新：T1-T4 提交链 + 冒烟结论；最后更新行）
- Modify: `方案/46-录屏产物独立页面方案.md`（状态行 → 实施完成）

**Interfaces:**
- Consumes: Task 1-4 全部产物
- Produces: 无后续任务

- [ ] **Step 1: 全量门禁四件套**

```bash
ruff check backend/app
mypy backend/app
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e --cov=app --cov-fail-under=80 -q
cd frontend && npx vitest run && npx vue-tsc --noEmit
```

Expected: 全绿；pytest 计数在 Task 1-2 新增用例后 ≥1200 passed 量级、覆盖率不低于现状基线（93.x%）。

- [ ] **Step 2: 真机冒烟（.18，需设备在线；dev 口径 8765 或隔离端口自定）**

```bash
# 后端 dev 起服 + 前端 build 或 dev，浏览器操作：
# 1. 设备页录屏 start → 5s → stop → done 一行提示（无内嵌 video）
# 2. 「查看录屏」→ /recordings?device=192.168.8.18%3A5555 过滤命中新产物
# 3. 播放（弹层出画）/ 下载（字节与设备文件一致）/ 导出 webm（可选）
# 4. data/recordings/ 落盘名 = openscrcpy_rec_192.168.8.18-5555__{12hex}.mp4
# 5. 预置旧命名文件 → 列表「未知设备（旧版产物）」且可播可删
# 6. 删除：确认 → 行消失；播放中删除（先 open 视频再删）→ 500 toast 不假成功
```

Expected: 全链路通过；`GET /api/recording/files` mtime 降序与页面一致。

- [ ] **Step 3: 文档回写**

`docs/用户手册.md` 录屏相关章节：done 态描述改为「已保存提示 + 查看录屏跳转」；新增小节「录屏页」——列表字段、播放/下载/导出 webm、删除与二次确认、`?device=` 进入自动过滤与「仅看」清除、产物保留策略（3 天 / 512MB 双限 + 关服清盘不变 + 手动删除即时腾空间）、旧产物说明。

`README.md`：特性加「录屏产物独立页（/recordings）：历史列表/播放/下载/删除」；方案表 46 行状态更新；页面结构补 `/recordings`。

`CHANGELOG.md` `[Unreleased]` 加：

```markdown
## [Unreleased]

### Added

- 录屏产物独立页面（方案 46）：全局 `/recordings` 页历史录屏列表（扫盘为准，含旧格式兼容）——播放弹层 / 下载 / 导出 webm / 删除（二次确认，占用时响亮报错）；落盘命名嵌设备标识（`openscrcpy_rec_{dev}__{id}.mp4`）；设备页 done 态收敛为「已保存 + 查看录屏 + 重新录制」一行，产物处理不再挤占设备页
```

`方案/进度追踪.md` 追加第八十四次更新条目（提交链、门禁数字、冒烟结论），更新「最后更新」行。`方案/46-*.md` 状态行改「实施完成（2026-10-14）」。

- [ ] **Step 4: 提交**

```bash
git add docs/用户手册.md README.md CHANGELOG.md 方案/进度追踪.md 方案/46-录屏产物独立页面方案.md
git commit -m "docs: 方案 46 录屏页用户手册/README/CHANGELOG/进度追踪回写（T5）"
```

---

## 自检记录（plan-time）

- 覆盖核对：方案 46 §3.1 七项需求 → T1（命名）/T2（列表·下载·删除接口）/T3（新页+webm 迁入+?device=）/T4（done 收敛）/T5（文档）；§3.7 测试影响面全部落到任务步骤（命名断言 :77、glob 兼容、路由遮蔽、穿越白名单、RecordingPanel done 断言改造）。
- 类型一致性：`parse_product_name -> tuple[str | None, str | None]`（T1 产出，T2 消费解包两值）；`RecordingFileItem` 字段名与 T2 JSON key 逐字一致（filename/device/session_id/size_bytes/created_at）；`api.recordingFileUrl` 与测试 mock 同定义；`RECORDING_DELETE_FAILED` 异常 code 前后端断言一致。
- 已知实现期对齐点（非占位）：测试文件内 fixture/辅助函数名以现状为准（`make_adb`/`mountIdle`/404 响应体形状）——步骤已注明「沿用文件现状」与具体替换文本。
