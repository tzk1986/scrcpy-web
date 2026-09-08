<!--
  设备管理面板（Dashboard）
  ===========================

  应用的主页面，展示所有通过 ADB 连接的设备列表。

  功能：
    - 显示设备列表（表格形式）：设备 ID、型号、系统版本、分辨率、电量、状态
    - 刷新按钮：重新扫描设备
    - 连接按钮：跳转到设备详情页（/device/:id）

  数据来源：
    使用 useDeviceStore 管理设备列表状态。
    组件挂载时自动调用 fetchDevices() 加载数据。

  子组件关系：
    Dashboard.vue → stores/device.ts → services/api.ts
-->
<template>
  <div class="dashboard">
    <div class="header">
      <h2>设备管理</h2>
      <el-button @click="refresh">刷新</el-button>
    </div>

    <el-table :data="store.devices" v-loading="store.loading">
      <el-table-column prop="id" label="设备ID" />
      <el-table-column prop="model" label="型号" />
      <el-table-column prop="os_version" label="系统版本" />
      <el-table-column label="分辨率">
        <template #default="{ row }">
          {{ row.resolution[0] }}x{{ row.resolution[1] }}
        </template>
      </el-table-column>
      <el-table-column prop="battery" label="电量" />
      <el-table-column prop="status" label="状态">
        <template #default="{ row }">
          <el-tag :type="row.status === 'online' ? 'success' : 'info'">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button size="small" @click="connect(row.id)">连接</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDeviceStore } from '@/stores/device'

const store = useDeviceStore()
const router = useRouter()

// 组件挂载时自动加载设备列表
onMounted(() => {
  store.fetchDevices()
})

/** 刷新设备列表。 */
function refresh() {
  store.fetchDevices()
}

/** 跳转到设备详情页。 */
function connect(deviceId: string) {
  router.push(`/device/${deviceId}`)
}
</script>

<style scoped>
.dashboard {
  padding: 1rem;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
</style>
