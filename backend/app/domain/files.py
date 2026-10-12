"""
文件管理领域模型
==================

层：领域。

方案 39：设备文件管理的数据模型。

定义：
    FileEntry — 设备端目录条目（toybox ls -la/-ld 行解析产物）
    TransferJob — 一次上传/下载任务（内存任务表条目，状态机 running→done|failed）
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileEntry:
    """设备端目录条目（ls 解析结果，非 UTF-8 名字节以替换符呈现）。"""

    name: str
    is_dir: bool
    is_symlink: bool
    size: int
    mtime: str
    permissions: str
    link_target: str | None = None


@dataclass
class TransferJob:
    """传输任务表条目。

    status: running | done | failed
    substage: pulling | pushing | zipping | http（running 期间细分阶段）
    """

    id: str
    device_id: str
    direction: str  # "download" | "upload"
    paths: list[str]
    total_size: int
    status: str = "running"
    substage: str | None = None
    error: str | None = None
    created_at: float = 0.0
    finished_at: float | None = None
