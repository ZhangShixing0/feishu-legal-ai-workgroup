# 贡献指南

## 开发环境

1. 安装 Python 3.10+。
2. Windows 运行 `setup.bat`；其他系统按 README 创建 `.venv` 并安装 `requirements.txt`。
3. 不配置飞书凭证也可以开发生成器、提取器和大部分编排逻辑。

## 提交前检查

```powershell
.venv\Scripts\python.exe tools\run_checks.py
```

如果 Node.js 是 CI 必需条件，使用：

```powershell
.venv\Scripts\python.exe tools\run_checks.py --require-node
```

## 数据规则

- 只使用明确标注的虚构姓名、无效行政区码的测试身份证号、测试手机号和示例地址。
- 不上传真实 ZIP、Excel、身份证图片、合同、生成文书、数据库或日志。
- 不提交 `.env.feishu`；新增配置项时同步更新 `.env.feishu.example`。
- 修改 DOCX 模板后必须清除作者/自定义属性，更新模板哈希并运行生成器测试。

## 变更要求

- 一个 Pull Request 只解决一个清晰问题。
- 行为变化必须补充或更新测试。
- 修改流程状态、字段映射或安全边界时，同步更新 `docs/`。
- 不把法律内容判断隐藏在代码默认值里；需要律师确认的规则应明确提示。
- 保持 Python 标准库优先，新增依赖时说明必要性和许可证。

## Pull Request 说明

请写清：改了什么、为什么改、用户影响、风险、测试结果，以及是否改变数据边界或飞书权限。
