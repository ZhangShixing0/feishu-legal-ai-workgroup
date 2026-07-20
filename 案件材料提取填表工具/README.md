# 案件材料提取填表工具

Windows 本地离线工具：读取案件 ZIP 中的 PDF、Word、图片和材料路径，匹配用户上传的 XLSX 表头；缺失字段写入“无”并标黄。

双击 `run.bat` 启动，浏览器默认打开 `http://127.0.0.1:8777/`。

实现特点：

- Python 标准库本地服务，不上传材料；
- PDF 文本层使用内置的 `pypdf`，扫描件和图片使用 Windows 自带中文 OCR；
- 直接修改 XLSX 的 OOXML 副本，保留原工作簿、表格范围及样式；
- 先在网页中核对、修改，再生成最终 Excel；
- 按贷款账号或序号更新同案行，否则新增行。
- 多案件压缩包按 Excel 文件名和行内标识选择目标案件，无法唯一确定时停止处理；
- 材料未验证的 Excel 已有值会保留并明确标注，材料冲突单独提示。

版本变化统一记录在仓库根目录的 [`CHANGELOG.md`](../CHANGELOG.md)。

运行测试：

```powershell
python -m unittest discover -s tests -v
```

第三方组件：`pypdf 6.10.0`，BSD-3-Clause；许可证见 `vendor/pypdf-LICENSE.txt`。
