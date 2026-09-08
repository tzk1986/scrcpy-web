# OpenScrcpy 测试目录

本目录包含项目所有模块的单元测试和集成测试。

## 目录结构

```
tests/
├── README.md              # 本文件
├── conftest.py            # 全局测试配置和共享 fixture
├── scrcpy/                # scrcpy 模块测试
│   ├── __init__.py
│   ├── test_constants.py
│   ├── test_h264_parser.py
│   ├── test_input_controller.py
│   ├── test_server_manager.py
│   └── test_encoder.py
├── application/           # 应用层服务测试（待实现）
├── infrastructure/        # 基础设施层测试（待实现）
└── integration/           # 集成测试（待实现）
```

## 运行测试

### 运行所有测试

```bash
# 在项目根目录执行
pytest tests/ -v

# 或使用 make 命令
make test
```

### 运行特定模块测试

```bash
# 只运行 scrcpy 模块测试
pytest tests/scrcpy/ -v

# 只运行应用层测试
pytest tests/application/ -v
```

### 运行单个测试文件

```bash
# 运行 H264 解析器测试
pytest tests/scrcpy/test_h264_parser.py -v

# 运行输入控制器测试
pytest tests/scrcpy/test_input_controller.py -v
```

### 运行特定测试用例

```bash
# 运行特定的测试函数
pytest tests/scrcpy/test_h264_parser.py::test_parse_sps -v
```

### 带覆盖率报告

```bash
# 生成覆盖率报告
pytest tests/ --cov=backend/app --cov-report=html

# 查看报告
open htmlcov/index.html
```

## 测试文件命名规范

- 测试文件必须以 `test_` 开头
- 测试类必须以 `Test` 开头
- 测试函数必须以 `test_` 开头

示例：
```python
def test_h264_parser():  # ✓ 正确
    pass

class TestH264Parser:     # ✓ 正确
    pass

def h264_parser():        # ✗ 错误：缺少 test_ 前缀
    pass
```

## 测试分类

### 单元测试 (Unit Tests)

测试单个函数或类的功能，不依赖外部服务。

- 位置：对应模块目录（如 `tests/scrcpy/`）
- 特点：快速、隔离、可重复
- 使用 mock 替换外部依赖

### 集成测试 (Integration Tests)

测试多个组件的协作，可能涉及外部服务。

- 位置：`tests/integration/`
- 特点：测试组件间交互
- 可能需要真实设备或模拟环境

### 端到端测试 (E2E Tests)

测试完整的用户场景。

- 位置：`tests/e2e/`（待创建）
- 特点：模拟真实用户操作
- 需要完整的测试环境

## 测试数据管理

测试数据应该：

1. 放在 `tests/data/` 目录（待创建）
2. 使用 fixture 动态生成
3. 避免硬编码大段数据
4. 使用相对路径引用文件

示例：
```python
@pytest.fixture
def sample_data():
    return {"key": "value"}

@pytest.fixture
def large_data():
    with open("tests/data/large.json") as f:
        return json.load(f)
```

## Mock 和 Fixture

### 全局 Fixture

在 `tests/conftest.py` 中定义所有测试共享的 fixture。

### 模块级 Fixture

在对应测试目录的 `conftest.py` 中定义模块特有的 fixture。

示例：
```python
# tests/scrcpy/conftest.py
@pytest.fixture
def mock_device_id():
    return "emulator-5554"
```

## 异步测试

对于异步代码，使用 `pytest-asyncio`：

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result == expected
```

## 测试覆盖率目标

- 核心模块（scrcpy）：≥ 80%
- 应用层服务：≥ 70%
- 基础设施层：≥ 60%
- 总体覆盖率：≥ 75%

查看当前覆盖率：
```bash
make coverage
```

## 持续集成

所有测试在 CI 中自动运行：

- 提交 PR 时自动运行
- 必须通过所有测试才能合并
- 覆盖率不能低于阈值

## 故障排查

### 测试失败

1. 查看错误信息
2. 运行单个测试定位问题
3. 检查 fixture 配置
4. 验证 mock 是否正确

### 导入错误

确保 Python 路径正确：
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/backend"
```

### 异步测试问题

确保使用 `@pytest.mark.asyncio` 装饰器。

## 参考

- [pytest 文档](https://docs.pytest.org/)
- [pytest-asyncio 文档](https://pytest-asyncio.readthedocs.io/)
- [Mock 对象文档](https://docs.python.org/3/library/unittest.mock.html)

## 下一步

- [ ] 完善 scrcpy 模块测试
- [ ] 添加应用层服务测试
- [ ] 添加基础设施层测试
- [ ] 创建集成测试框架
- [ ] 配置 CI 自动运行测试
- [ ] 达到覆盖率目标
