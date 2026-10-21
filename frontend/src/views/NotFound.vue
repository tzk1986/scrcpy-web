<!--
  未匹配路由兜底页（方案 51 F2-07）

  此前 /:pathMatch(.*)* 缺失，未知路径渲染空白且控制台告警。
  轻页复用空态灰字样式，给回 Dashboard 引导。
-->
<template>
  <div class="not-found">
    <p class="not-found-text">页面不存在或已移除</p>
    <router-link to="/">回设备列表</router-link>
  </div>
</template>

<style scoped>
.not-found {
  padding: 32px 16px;
  text-align: center;
  color: #909399;
}

.not-found-text {
  margin: 0 0 8px;
}
</style>
