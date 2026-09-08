# 06 - Logcat 日志系统

## 目标

实现实时 logcat 流、历史回溯、分级过滤、Tag/PID 过滤、虚拟滚动。

## 状态

- ✅ 后端 logcat 收集框架
- ⏳ 前端日志展示组件
- ⏳ 虚拟滚动优化
- ⏳ 实时过滤
- ⏳ 导出功能

## 依赖

- [05-调试会话管理.md](./05-调试会话管理.md) ⏳

## 后端实现

### 已实现

- `DebugService._collect_logcat()`: 异步收集 logcat
- `DebugService._parse_logcat_line()`: 解析 logcat 格式
- SQLite 持久化

### 待完善

#### 1. 日志级别过滤 API

```python
@router.get("/sessions/{session_id}/logs")
async def get_logs(
    session_id: str,
    level: str | None = None,          # V/D/I/W/E/F
    tag: str | None = None,            # Tag 名称
    pid: int | None = None,            # 进程 ID
    search: str | None = None,         # 全文搜索
    limit: int = 1000,
    service: DebugService = Depends(get_debug_service),
):
    """Query logs with filters"""
    logs = await service.get_logs(
        session_id,
        level=level,
        tag=tag,
        pid=pid,
        search=search,
        limit=limit,
    )
    return {"logs": logs}
```

#### 2. 日志导出

```python
from fastapi.responses import StreamingResponse
import csv
import io

@router.get("/sessions/{session_id}/logs/export")
async def export_logs(
    session_id: str,
    format: str = "json",              # json / csv / txt
    level: str | None = None,
    tag: str | None = None,
    service: DebugService = Depends(get_debug_service),
):
    """Export logs"""
    logs = await service.get_logs(session_id, level=level, tag=tag, limit=100000)
    
    if format == "json":
        return logs
    elif format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["ts", "level", "pid", "tid", "tag", "message"])
        writer.writeheader()
        for log in logs:
            writer.writerow(log)
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=logs.csv"}
        )
    elif format == "txt":
        text = "\n".join([log["raw"] for log in logs])
        return StreamingResponse(
            io.BytesIO(text.encode()),
            media_type="text/plain",
            headers={"Content-Disposition": "attachment; filename=logs.txt"}
        )
```

## 前端实现

### LogcatView 组件

**文件**：`frontend/src/components/debug/LogcatView.vue`

**已实现**：基础日志展示

**待完善**：

#### 1. 虚拟滚动

**问题**：5 万条日志直接渲染会卡顿

**方案**：使用 `vue-virtual-scroller`

```vue
<template>
  <div class="logcat-view">
    <div class="toolbar">
      <el-select v-model="filterLevel" placeholder="Level" clearable size="small">
        <el-option label="Verbose" value="V" />
        <el-option label="Debug" value="D" />
        <el-option label="Info" value="I" />
        <el-option label="Warning" value="W" />
        <el-option label="Error" value="E" />
        <el-option label="Fatal" value="F" />
      </el-select>
      <el-input v-model="filterTag" placeholder="Tag filter" clearable size="small" />
      <el-input v-model="filterSearch" placeholder="Search" clearable size="small" />
      <el-button size="small" @click="refresh">刷新</el-button>
      <el-button size="small" @click="clear">清空</el-button>
      <el-button size="small" @click="exportLogs">导出</el-button>
    </div>

    <RecycleScroller
      class="log-list"
      :items="filteredLogs"
      :item-size="24"
      key-field="seq"
      v-slot="{ item }"
    >
      <div :class="['log-entry', `level-${item.level.toLowerCase()}`]">
        <span class="timestamp">{{ formatTime(item.ts) }}</span>
        <span class="level">{{ item.level }}</span>
        <span class="pid">{{ item.pid }}</span>
        <span class="tag">{{ item.tag }}</span>
        <span class="message">{{ item.message }}</span>
      </div>
    </RecycleScroller>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { RecycleScroller } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import { useDebugStore } from '@/stores/debug'

const debugStore = useDebugStore()

const filterLevel = ref<string | null>(null)
const filterTag = ref<string | null>(null)
const filterSearch = ref<string | null>(null)

const filteredLogs = computed(() => {
  let logs = debugStore.logs
  
  if (filterLevel.value) {
    logs = logs.filter(l => l.level === filterLevel.value)
  }
  if (filterTag.value) {
    logs = logs.filter(l => l.tag.includes(filterTag.value!))
  }
  if (filterSearch.value) {
    const search = filterSearch.value.toLowerCase()
    logs = logs.filter(l => l.message.toLowerCase().includes(search))
  }
  
  return logs
})

async function refresh() {
  await debugStore.fetchLogs()
}

function clear() {
  debugStore.logs = []
}

async function exportLogs() {
  const sessionId = debugStore.sessionId
  if (!sessionId) return
  
  const url = `/api/debug/sessions/${sessionId}/logs/export?format=csv`
  window.open(url, '_blank')
}

function formatTime(ts: number) {
  const date = new Date(ts * 1000)
  return date.toISOString().substr(11, 12)
}
</script>

<style scoped>
.logcat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.toolbar {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem;
  border-bottom: 1px solid #eee;
}

.log-list {
  flex: 1;
  overflow-y: auto;
}

.log-entry {
  padding: 2px 4px;
  border-bottom: 1px solid #f5f5f5;
  display: flex;
  gap: 8px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  height: 24px;
  align-items: center;
}

.log-entry:hover {
  background: #f9f9f9;
}

.timestamp {
  color: #999;
  flex-shrink: 0;
  width: 100px;
}

.level {
  width: 20px;
  text-align: center;
  font-weight: bold;
  flex-shrink: 0;
}

.pid {
  width: 60px;
  color: #666;
  flex-shrink: 0;
}

.tag {
  color: #0088aa;
  flex-shrink: 0;
  min-width: 100px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.level-v { color: #999; }
.level-d { color: #0088ff; }
.level-i { color: #00aa00; }
.level-w { color: #ff8800; background: #fff8e0; }
.level-e { color: #ff0000; background: #ffe0e0; }
.level-f { color: #ff00ff; background: #ffe0ff; }
</style>
```

#### 2. 实时更新

```typescript
// frontend/src/stores/debug.ts
export const useDebugStore = defineStore('debug', () => {
  const ws = ref<WebSocket | null>(null)
  
  function connectWebSocket() {
    if (!sessionId.value) return
    
    ws.value = new WebSocket(`ws://localhost:8000/ws/debug/${sessionId.value}`)
    
    ws.value.onmessage = (event) => {
      const data = JSON.parse(event.data)
      
      if (data.type === 'log') {
        appendLog(data)
      } else if (data.type === 'log_batch') {
        // 断线续传
        data.logs.forEach(appendLog)
      }
    }
    
    ws.value.onclose = () => {
      // 自动重连
      setTimeout(connectWebSocket, 3000)
    }
  }
  
  function appendLog(entry: LogEntry) {
    logs.value.push(entry)
    if (logs.value.length > 50000) {
      logs.value.shift()
    }
  }
  
  return {
    // ...
    connectWebSocket,
  }
})
```

#### 3. 日志高亮

```typescript
function highlightMessage(message: string): string {
  // 高亮关键字
  const keywords = ['Exception', 'Error', 'Warning', 'Fatal']
  let highlighted = message
  
  keywords.forEach(keyword => {
    const regex = new RegExp(`(${keyword})`, 'gi')
    highlighted = highlighted.replace(regex, '<span class="highlight">$1</span>')
  })
  
  // 高亮数字
  highlighted = highlighted.replace(/\b\d+\b/g, '<span class="number">$&</span>')
  
  // 高亮字符串
  highlighted = highlighted.replace(/"[^"]*"/g, '<span class="string">$&</span>')
  
  return highlighted
}
```

## 技术细节

### Logcat 格式

```
# threadtime 格式
09-08 10:23:45.123  1234  5678 I TagName: message text

# 字段说明
- 日期时间: 09-08 10:23:45.123
- PID: 1234
- TID: 5678
- Level: I (V/D/I/W/E/F)
- Tag: TagName
- Message: message text
```

### 日志级别

| 级别 | 含义 | 颜色 |
|------|------|------|
| V | Verbose | 灰色 |
| D | Debug | 蓝色 |
| I | Info | 绿色 |
| W | Warning | 橙色 |
| E | Error | 红色 |
| F | Fatal | 紫色 |

## 测试策略

### 单元测试
- 日志解析
- 过滤逻辑
- 导出功能

### 性能测试
- 5 万条日志渲染
- 实时推送吞吐
- 内存占用

## 验收标准

- ⏳ 虚拟滚动流畅（5 万条不卡顿）
- ⏳ 实时日志推送
- ⏳ 多级过滤（级别/Tag/PID/搜索）
- ⏳ 导出为 CSV/JSON/TXT
- ⏳ 日志语法高亮

## 交付物

- ⏳ LogcatView 组件（虚拟滚动）
- ⏳ 实时更新逻辑
- ⏳ 导出 API
- ⏳ 日志高亮

## 参考

- [vue-virtual-scroller](https://github.com/Akryum/vue-virtual-scroller)
- [Android Logcat](https://developer.android.com/studio/command-line/logcat)
