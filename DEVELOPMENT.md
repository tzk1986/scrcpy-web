# OpenScrcpy 开发约束文档

> **重要**：本文档是开发人员的必读手册。所有开发活动必须遵循本文档中定义的约束和流程。
> 
> **最后更新**：2026-09-08  
> **版本**：v1.0

---

## 📋 目录

1. [项目概述](#项目概述)
2. [核心约束](#核心约束)
3. [架构约束](#架构约束)
4. [代码规范](#代码规范)
5. [开发流程](#开发流程)
6. [约束修改流程](#约束修改流程)
7. [迭代记录](#迭代记录)

---

## 项目概述

### 项目定位

**OpenScrcpy** 是一个基于 scrcpy 的 Web 化开源远程控制与无限调试平台。

### 技术栈

- **后端**：Python 3.11+ / FastAPI / asyncio / SQLite
- **前端**：Vue 3 / TypeScript / Vite / Element Plus
- **浏览器**：**仅支持 Chrome**（最新两个稳定版）
- **设备通信**：ADB CLI / scrcpy-server
- **传输协议**：WebSocket（主） / WebTransport（Chrome 优化）

### 核心功能

1. **多设备管理**：同时管理 100+ 台 Android 设备
2. **无限调试**：持久化 logcat、远程 Shell、性能监控
3. **低延迟视频流**：WebCodecs + WebSocket
4. **团队协作**：屏幕共享、多人控制（Phase 2）

---

## 核心约束

### 🚨 不可违背的约束

以下约束是项目的基石，**绝对不可违背**，如需修改必须经过**人工确认**并满足变更流程。

#### 1. 浏览器约束

- **仅支持 Chrome 浏览器**（最新两个稳定版）
- 可充分利用 Chrome 专属能力：WebCodecs、WebTransport、SharedArrayBuffer、FileSystem Access
- **禁止**添加 Firefox/Safari/Edge 兼容性代码
- **禁止**使用跨浏览器 polyfill

**理由**：降低复杂度，充分利用 Chrome 特性实现极致体验。

#### 2. 分层架构约束

后端必须严格遵循四层架构，**禁止跨层调用**：

```
Interface Layer (接口层)
    ↓ 只能调用
Application Layer (应用层)
    ↓ 只能调用
Domain Layer (领域层)
    ↑ 依赖抽象
Infrastructure Layer (基础设施层)
```

**规则**：
- Domain 层**不依赖**任何外部库（纯业务逻辑）
- Application 层**不依赖**具体实现（依赖 Protocol）
- Infrastructure 层实现 Domain 层定义的 Protocol
- Interface 层只负责 HTTP/WebSocket 协议转换

#### 3. 依赖倒置约束

- 业务逻辑依赖**抽象接口**（Protocol），不依赖**具体实现**
- 所有外部依赖（ADB、数据库、编码器）必须通过 Protocol 定义
- 依赖注入通过 `deps.py` 集中管理

**禁止**：
- 在 Application/Domain 层直接 import 具体实现类
- 在业务代码中硬编码数据库连接、文件路径等

#### 4. 配置分离约束

- 所有可配置参数集中在 `config/` 目录
- 配置优先级：环境变量 > YAML 文件 > 默认值
- 使用 Pydantic Settings 进行类型验证

**禁止**：
- 在代码中硬编码端口、路径、超时等参数
- 直接读取环境变量（必须通过 Settings 类）

#### 5. 异步优先约束

- 所有 I/O 操作必须使用 asyncio
- 禁止在异步上下文中使用同步阻塞调用
- 长时间运行的任务必须支持取消

**理由**：支持高并发设备管理（100+ 设备）。

---

## 架构约束

### 后端架构

#### 目录结构

```
backend/app/
├── core/              # 横切关注点（配置、日志、异常、遥测）
├── domain/            # 领域层（实体、值对象、Protocol 接口）
├── application/       # 应用层（用例编排、业务服务）
├── infrastructure/    # 基础设施层（具体实现）
│   ├── adb/           # ADB 驱动实现
│   ├── persistence/   # 数据库实现
│   ├── stream/        # 视频编码实现
│   └── transport/     # 传输协议实现
├── interfaces/        # 接口层（HTTP/WebSocket）
│   ├── http/          # REST API
│   └── ws/            # WebSocket
├── deps.py            # 依赖注入（必须在此注册所有依赖）
├── lifecycle.py       # 应用生命周期
└── main.py            # 应用工厂
```

#### 必须遵循的规则

1. **新模块必须按分层放置**
   - 新增实体 → `domain/`
   - 新增业务服务 → `application/`
   - 新增外部集成 → `infrastructure/`
   - 新增 API → `interfaces/`

2. **Protocol 定义在 Domain 层**
   - 所有抽象接口必须在 `domain/ports.py` 或对应的实体文件中定义
   - 实现类放在 `infrastructure/` 对应子目录

3. **依赖注册在 deps.py**
   - 所有依赖工厂函数必须在 `deps.py` 中定义
   - 使用 `@lru_cache()` 实现单例
   - FastAPI 通过 `Depends()` 注入

4. **异常统一处理**
   - 自定义异常继承 `OpenScrcpyException`
   - 在 `core/exceptions.py` 中定义
   - 返回统一格式：`{"error": {"code": "...", "message": "..."}}`

### 前端架构

#### 目录结构

```
frontend/src/
├── components/        # 可复用组件
│   ├── ui/            # 通用 UI 组件
│   ├── device/        # 设备相关组件
│   ├── stream/        # 视频流组件
│   └── debug/         # 调试面板组件
├── views/             # 页面视图
├── stores/            # Pinia 状态管理
├── services/          # API 服务、WebSocket、WebTransport
├── composables/       # 组合式函数
├── router/            # 路由配置
└── workers/           # Web Worker
```

#### 必须遵循的规则

1. **组件职责单一**
   - 每个组件只做一件事
   - 超过 300 行必须拆分

2. **状态管理集中**
   - 全局状态用 Pinia Store
   - 组件状态用 `ref`/`reactive`
   - **禁止** props drilling 超过 3 层

3. **API 调用封装**
   - 所有 API 调用在 `services/api.ts` 中封装
   - 组件通过 Store 调用，不直接调用 API
   - WebSocket/WebTransport 在 `services/` 中封装

4. **类型安全**
   - 所有组件使用 `<script setup lang="ts">`
   - 所有 props、emit 必须定义类型
   - 所有 API 响应必须定义接口

---

## 代码规范

### Python 后端

#### 必须遵循

1. **类型注解**
   ```python
   # ✅ 正确
   async def get_device(self, device_id: str) -> DeviceInfo | None:
       ...
   
   # ❌ 错误
   async def get_device(self, device_id):
       ...
   ```

2. **文档字符串**
   ```python
   class DeviceService:
       """设备管理业务服务
       
       负责设备发现、状态监控、批量操作等用例编排。
       依赖 AdbDriver 和 DeviceRepository 抽象接口。
       """
       
       async def list_devices(self) -> list[DeviceInfo]:
           """列出所有连接的设备
           
           Returns:
               设备信息列表
           """
           ...
   ```

3. **日志规范**
   ```python
   from app.core.logging import get_logger
   
   logger = get_logger(__name__)
   
   # ✅ 结构化日志
   logger.info("device_connected", device_id=device_id, model=model)
   
   # ❌ 字符串拼接
   logger.info(f"Device {device_id} connected")
   ```

4. **异常处理**
   ```python
   # ✅ 自定义异常
   from app.core.exceptions import DeviceNotFoundError
   
   if not device:
       raise DeviceNotFoundError(device_id)
   
   # ❌ 通用异常
   raise ValueError("Device not found")
   ```

#### 禁止事项

- ❌ 使用 `print()` 输出日志
- ❌ 在异步函数中使用 `time.sleep()`（使用 `asyncio.sleep()`）
- ❌ 在 Domain 层 import `fastapi`、`sqlalchemy` 等框架
- ❌ 硬编码字符串（使用配置或常量）
- ❌ 超过 100 行的函数（拆分为小函数）

### TypeScript 前端

#### 必须遵循

1. **组件结构**
   ```vue
   <template>
     <!-- 模板 -->
   </template>
   
   <script setup lang="ts">
   // 1. imports
   import { ref, computed } from 'vue'
   import { useDeviceStore } from '@/stores/device'
   
   // 2. props & emits
   const props = defineProps<{
     deviceId: string
   }>()
   
   const emit = defineEmits<{
     (e: 'update', value: string): void
   }>()
   
   // 3. 状态
   const loading = ref(false)
   
   // 4. 计算属性
   const device = computed(() => store.devices.find(d => d.id === props.deviceId))
   
   // 5. 方法
   function handleClick() {
     emit('update', props.deviceId)
   }
   
   // 6. 生命周期
   onMounted(() => {
     // ...
   })
   </script>
   
   <style scoped>
   /* 样式 */
   </style>
   ```

2. **命名规范**
   - 组件文件：PascalCase（`DeviceCard.vue`）
   - 组合式函数：camelCase，以 `use` 开头（`useDevice.ts`）
   - 常量：UPPER_SNAKE_CASE（`MAX_RETRY_COUNT`）
   - 类型/接口：PascalCase（`DeviceInfo`）

3. **注释规范**
   ```typescript
   /**
    * 设备信息接口
    * 
    * 对应后端 DeviceInfo 实体，用于前后端数据交换。
    */
   export interface DeviceInfo {
     id: string           // 设备序列号
     model: string        // 设备型号
     os_version: string   // Android 版本
     // ...
   }
   
   /**
    * 获取设备列表
    * 
    * 调用后端 /api/devices 接口，返回所有连接的设备。
    * 
    * @returns 设备信息数组
    * @throws 网络错误时抛出异常
    */
   async function fetchDevices(): Promise<DeviceInfo[]> {
     // ...
   }
   ```

#### 禁止事项

- ❌ 使用 `any` 类型（必须定义具体类型）
- ❌ 在组件中直接调用 `fetch`/`axios`（必须通过 `services/`）
- ❌ 使用 `console.log()` 提交到生产环境
- ❌ 超过 300 行的组件（拆分为子组件）
- ❌ 使用 `var`（使用 `const`/`let`）

---

## 开发流程

### 开始开发前的检查清单

每次开始开发任务前，**必须**完成以下检查：

- [ ] 阅读 `DEVELOPMENT.md`（本文档）
- [ ] 阅读对应的方案文档（`方案/XX-*.md`）
- [ ] 检查 `方案/进度追踪.md` 确认任务状态
- [ ] 确认任务依赖的前置任务已完成
- [ ] 理解相关的 Protocol 接口定义

### 开发步骤

1. **阅读方案文档**
   - 理解目标、依赖、实现步骤
   - 确认技术细节和数据结构
   - 了解测试策略

2. **创建功能分支**
   ```bash
   git checkout -b feature/模块名称-功能描述
   ```

3. **实现代码**
   - 遵循分层架构
   - 添加完整注释
   - 编写单元测试

4. **本地测试**
   ```bash
   # 后端测试
   pytest backend/tests -v
   
   # 前端测试
   cd frontend && npm run test
   
   # 代码检查
   ruff check backend/app
   cd frontend && npm run lint
   ```

5. **提交代码**
   ```bash
   git add .
   git commit -m "feat: 实现 XXX 功能"
   ```

6. **更新文档**
   - 更新 `方案/进度追踪.md`
   - 更新 `DEVELOPMENT.md` 的迭代记录
   - 如有 API 变更，更新 `docs/api.md`

### 代码注释要求

#### 文件级注释

每个文件必须包含文件级注释，说明：
- 文件作用
- 文件关系（属于哪一层、依赖哪些模块）
- 主要功能

```python
"""
设备管理 HTTP 端点

属于 Interface 层，负责处理设备的 REST API 请求。
依赖 Application 层的 DeviceService 进行业务处理。

主要端点：
- GET /api/devices：列出所有设备
- POST /api/devices/{id}/install：安装 APK
"""
```

#### 类/函数级注释

每个类和公共函数必须包含：
- 作用说明
- 参数说明
- 返回值说明
- 异常说明

```python
class DeviceService:
    """
    设备管理业务服务
    
    负责设备发现、状态监控、批量操作等用例编排。
    依赖 AdbDriver 和 DeviceRepository 抽象接口，
    不直接操作 ADB 命令或数据库。
    
    Attributes:
        adb: ADB 驱动抽象接口
        repo: 设备仓储抽象接口
    """
    
    async def list_devices(self) -> list[DeviceInfo]:
        """
        列出所有连接的设备
        
        调用 ADB 驱动获取设备列表，并为每个设备获取详细信息。
        获取到的设备信息会保存到仓储中持久化。
        
        Returns:
            设备信息列表，包含设备 ID、型号、系统版本等
            
        Raises:
            AdbError: ADB 命令执行失败时抛出
        """
        ...
```

#### 关键逻辑注释

复杂算法、特殊处理、业务规则必须添加注释：

```python
def _parse_logcat_line(self, line: str) -> LogEntry:
    """
    解析 logcat threadtime 格式日志
    
    格式示例：
    "09-08 10:23:45.123  1234  5678 I TagName: message text"
    
    字段说明：
    - 09-08 10:23:45.123: 时间戳
    - 1234: 进程 ID (PID)
    - 5678: 线程 ID (TID)
    - I: 日志级别 (V/D/I/W/E/F)
    - TagName: 日志标签
    - message text: 日志内容
    """
    parts = line.strip().split(None, 6)
    if len(parts) < 6:
        # 格式不正确，返回默认值
        return LogEntry(ts=time.time(), level="I", ...)
    
    # 提取日志级别（单字符）
    level = parts[2] if len(parts[2]) == 1 else "I"
    
    # ...
```

---

## 约束修改流程

### ⚠️ 重要原则

**任何约束修改都可能影响整个项目，必须谨慎处理。**

### 修改流程

#### 1. 提出变更申请

在 `DEVELOPMENT.md` 的 [约束变更提案](#约束变更提案) 部分添加提案：

```markdown
### 提案：[变更标题]

**提出日期**：YYYY-MM-DD  
**提出人**：[姓名]  
**约束类型**：[核心约束/架构约束/代码规范]

**变更内容**：
- 原约束：[描述原约束]
- 新约束：[描述新约束]

**变更原因**：
[详细说明为什么要修改]

**影响分析**：
- 影响范围：[列出受影响的模块/文件]
- 向后兼容：[是否兼容，如何处理]
- 工作量估算：[预估修改工作量]

**替代方案**：
[是否有其他不修改约束的方案]
```

#### 2. 人工评审

- **核心约束**：必须项目负责人**书面批准**
- **架构约束**：必须技术负责人**书面批准**
- **代码规范**：团队讨论后决定

#### 3. 实施变更

获得批准后：

1. 更新 `DEVELOPMENT.md` 中的约束定义
2. 修改受影响的代码
3. 更新相关文档
4. 在 [迭代记录](#迭代记录) 中记录变更

#### 4. 验证变更

- 所有测试通过
- 代码审查通过
- 文档更新完成

### 约束变更提案

*当前无提案*

<!-- 
示例格式：

### 提案：支持 Firefox 浏览器

**提出日期**：2026-10-15  
**提出人**：张三  
**约束类型**：核心约束

**变更内容**：
- 原约束：仅支持 Chrome 浏览器
- 新约束：支持 Chrome 和 Firefox

**变更原因**：
部分客户使用 Firefox，需要支持...

**影响分析**：
- 影响范围：前端视频播放器、WebTransport
- 向后兼容：完全兼容
- 工作量估算：2 周

**替代方案**：
提供降级方案，Firefox 用户使用 MSE 而非 WebCodecs
-->

---

## 迭代记录

### 版本历史

#### v0.1.0 (2026-09-08)

**初始版本**

**新增**：
- ✅ 项目脚手架搭建
- ✅ 后端分层架构
- ✅ 配置管理系统
- ✅ 依赖注入框架
- ✅ 设备管理基础功能
- ✅ 调试会话持久化框架
- ✅ Logcat 日志收集
- ✅ 远程 Shell 基础框架
- ✅ 前端 Vue 3 项目
- ✅ 调试面板基础组件

**修改**：
- 无

**修复**：
- 无

**约束变更**：
- 无

**参与人员**：
- 架构设计：Claude
- 代码实现：Claude

---

### 迭代模板

每次迭代完成后，按以下模板记录：

```markdown
#### vX.Y.Z (YYYY-MM-DD)

**迭代主题**：[本次迭代的主题]

**新增**：
- ✅ [功能 1]
- ✅ [功能 2]

**修改**：
- 🔄 [修改 1]
- 🔄 [修改 2]

**修复**：
- 🐛 [修复 1]
- 🐛 [修复 2]

**约束变更**：
- 📝 [变更 1]（如有，需说明提案编号）

**技术债务**：
- ⚠️ [债务 1]（如有）

**下一步计划**：
- [ ] [计划 1]
- [ ] [计划 2]

**参与人员**：
- [角色]：[姓名]
```

---

## 附录

### A. 常用命令

```bash
# 启动开发环境
make dev

# 运行测试
make test

# 代码检查
make lint

# 代码格式化
make format

# 构建
make build

# 清理
make clean

# Docker 启动
make docker-up

# 数据库初始化
make db-init
```

### B. 关键文件索引

| 文件 | 作用 |
|------|------|
| `DEVELOPMENT.md` | 开发约束文档（本文档） |
| `方案/README.md` | 实施方案索引 |
| `方案/进度追踪.md` | 实时进度跟踪 |
| `方案/00-总览.md` | 项目总体架构 |
| `docs/architecture.md` | 架构设计文档 |
| `docs/api.md` | API 文档 |
| `backend/app/deps.py` | 依赖注入配置 |
| `backend/app/domain/ports.py` | 核心抽象接口 |
| `config/settings.py` | 配置加载 |

### C. 联系方式

- 问题反馈：GitHub Issues
- 技术讨论：GitHub Discussions

---

**文档结束**

*本文档由项目维护团队更新，任何修改必须遵循约束修改流程。*
