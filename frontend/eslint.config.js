// ESLint 9 flat config：显式组合 @typescript-eslint v7（parser + plugin）与 eslint-plugin-vue 9.33 的 flat configs。
// 不依赖 @vue/eslint-config-typescript：v13 无 flat 导出，v14 会引入 @typescript-eslint v8 改变规则集；
// 其残留依赖还曾拖垮 npm install（peer eslint ^8 与 eslint 9 冲突），已从 devDependencies 移除。
import { defineConfig } from 'eslint/config'
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'

// 浏览器全局（不引入 globals 包：手动声明本项目常用项）
const browserGlobals = {
  window: 'readonly',
  document: 'readonly',
  navigator: 'readonly',
  location: 'readonly',
  localStorage: 'readonly',
  sessionStorage: 'readonly',
  console: 'readonly',
  setTimeout: 'readonly',
  clearTimeout: 'readonly',
  setInterval: 'readonly',
  clearInterval: 'readonly',
  requestAnimationFrame: 'readonly',
  cancelAnimationFrame: 'readonly',
  URL: 'readonly',
  URLSearchParams: 'readonly',
  fetch: 'readonly',
  Blob: 'readonly',
  File: 'readonly',
  FileReader: 'readonly',
  FileWriter: 'readonly',
  FormData: 'readonly',
  WebSocket: 'readonly',
  Worker: 'readonly',
  Image: 'readonly',
  Audio: 'readonly',
  HTMLElement: 'readonly',
  HTMLCanvasElement: 'readonly',
  EventSource: 'readonly',
  VideoDecoder: 'readonly',
  VideoEncoder: 'readonly',
  AudioDecoder: 'readonly',
  EncodedVideoChunk: 'readonly',
  MediaSource: 'readonly',
  AbortController: 'readonly',
  crypto: 'readonly',
  performance: 'readonly',
  process: 'readonly',
  __dirname: 'readonly',
}

const tsRules = {
  // 项目未强制 prettier，关掉与格式/风格冲突或改动面过大的规则
  'no-undef': 'off', // 未定义检查交给 TS/vue-tsc
  'no-unused-vars': 'off',
  '@typescript-eslint/no-unused-vars': [
    'error',
    { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
  ],
  '@typescript-eslint/no-explicit-any': 'off',
  '@typescript-eslint/no-empty-function': 'off',
}

export default defineConfig([
  {
    name: 'app/ignores',
    ignores: [
      'dist/**',
      'node_modules/**',
      '**/*.d.ts',
      '.test-output/**',
      'coverage/**',
      'test-results/**',
      'playwright-report/**',
    ],
  },
  js.configs.recommended,
  pluginVue.configs['flat/essential'],
  {
    name: 'app/ts-globals',
    files: ['**/*.ts', '**/*.tsx', '**/*.vue', '**/*.js'],
    languageOptions: {
      globals: browserGlobals,
    },
  },
  {
    name: 'app/typescript',
    files: ['**/*.ts', '**/*.tsx'],
    languageOptions: {
      parser: tsParser,
      parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
    },
    plugins: { '@typescript-eslint': tsPlugin },
    rules: tsRules,
  },
  {
    name: 'app/vue-typescript',
    files: ['**/*.vue'],
    // .vue 顶层 parser 由 pluginVue flat/essential 提供（vue-eslint-parser），
    // 这里只指定 <script lang="ts"> 的内嵌解析器
    languageOptions: {
      parserOptions: { parser: tsParser, ecmaVersion: 'latest', sourceType: 'module' },
    },
    plugins: { '@typescript-eslint': tsPlugin },
    rules: tsRules,
  },
  {
    name: 'app/rules',
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
])
