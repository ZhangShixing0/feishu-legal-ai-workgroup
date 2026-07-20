# 飞书双 AI 员工 V1

第一版把现有“材料提取填表 → 生成民事起诉状”接入律所内部飞书群：

- **AI 材料专员**：接收群内 ZIP 和 XLSX，调用 `127.0.0.1:8777`，发送核对卡片。
- **AI 诉状专员**：接收后台交接，发送补充信息页，调用 `127.0.0.1:8765`，把 DOCX 发回群里。
- **共享编排服务**：SQLite 保存案件状态、字段快照和审计记录，默认监听 `127.0.0.1:8788`。

V1 约束：一个群同一时间只处理一个活动案件；材料直接发送到飞书内部群；必须在材料核对页生成 Excel 后才能交给第二步。

## 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

## 2. 创建两个飞书企业自建应用

两个应用都要开启“机器人”能力、发布版本，并添加到同一个律所内部群。

### AI 材料专员

开通以下应用身份权限：

- `im:message`：以机器人身份向群发送消息；
- `im:resource`：下载群文件、上传文件资源；
- `im:message.group_msg`：接收群内 ZIP/XLSX 文件消息。这是敏感权限，V1 应只把机器人加入指定内部群。

事件与回调设置为“使用长连接”：

- 事件：`im.message.receive_v1`（接收消息 v2.0）；
- 回调：`card.action.trigger`（卡片回传交互）。

### AI 诉状专员

开通以下应用身份权限：

- `im:message`；
- `im:resource`。

V1 中诉状专员只负责主动发送补充卡片和 Word，不需要订阅群内全部消息。

## 3. 配置

将 `.env.feishu.example` 复制为 `.env.feishu`，填写两个应用的 App ID、App Secret 和正式 HTTPS 地址。`.env.feishu` 已加入 `.gitignore`。

首次联调可以暂时不填 `FEISHU_ALLOWED_CHAT_IDS`。确定正式群的 `chat_id` 后应填入白名单，避免机器人在其他群处理案件文件。

检查配置和现有服务：

```powershell
python -m feishu_workgroup.run --check
```

## 4. 启动

Windows 本地联调可以双击：

```text
run_feishu_v1.bat
```

服务器上应继续独立运行现有 `lawsuit.service` 和 `material-extractor.service`，再启动 `feishu-workgroup.service`。

## 5. 群内使用

1. 在群里发送“新建案件”（也可以直接发送第一个 ZIP/XLSX，系统会自动建案）。
2. 向群里发送一个 `.zip` 材料包和一个 `.xlsx` 案件表。
3. 等待 AI 材料专员发送提取完成卡片。
4. 打开核对页，修改字段并点击“生成并下载 Excel”。
5. 回到群卡片，点击“确认并交给诉状专员”。
6. 打开 AI 诉状专员发送的补充页，核对起诉专用字段并提交。
7. AI 诉状专员把 Word 草稿发回群里。

群内发送“状态”可查询当前活动案件；发送“帮助”可查看简要用法。

## 6. 数据和安全边界

- V1 的原始文件会先进入飞书内部群，再由材料专员下载到律所服务器，因此不再属于原来的“纯本地文件”边界。
- 群必须禁止外部成员；机器人可用范围应限制为参与测试的律师和助理。
- 案件补充链接带随机访问令牌，并设置 `Cache-Control: no-store`；不要把链接转发到群外。
- 飞书事件可能重复推送，服务按消息 ID 幂等处理。
- App Secret 只保存在 `.env.feishu` 或服务器 EnvironmentFile 中，不写入代码和数据库。
- 生成 Word 仍是草稿，不能绕过两次人工确认和律师最终审查。
