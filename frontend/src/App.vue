<!--
  应用根组件
  ============

  提供全局布局结构：
    - 顶部导航栏（el-header）：显示应用标题和主菜单
    - 主内容区（el-main）：通过 <router-view> 渲染当前路由对应的页面组件

  子组件关系：
    App.vue → router/index.ts → views/*.vue
-->
<template>
  <div id="app">
    <el-container>
      <el-header>
        <div class="header-content">
          <h1>OpenScrcpy</h1>
          <el-menu mode="horizontal" :router="true">
            <el-menu-item index="/">Dashboard</el-menu-item>
          </el-menu>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </div>
</template>

<script setup lang="ts">
// 根组件无需额外逻辑，仅提供布局骨架
</script>

<style>
#app {
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
}

.header-content {
  display: flex;
  align-items: center;
  gap: 2rem;
}

h1 {
  margin: 0;
  font-size: 1.5rem;
}
</style>
