# 归刻｜Markdown GTD 工作空间

版本 **0.1.0 · 本地 MVP**。先保存原始 Capture，再整理为提案；核对、编辑并确认后才写入 Markdown。支持任务完成、撤销与搜索。

微信小程序是主客户端；React Web 工作台用于本地体验、联调和浏览器回归，并不替代小程序。当前默认使用明确标识的本地规则解析，不是已经接通的生成式 AI。完整 V1 尚未达到上线门槛，详见 [MVP 验收记录](docs/MVP验收记录.md)。

## 快速启动

本工作区已经准备依赖。Windows PowerShell 中，从仓库根目录运行：

```powershell
npm.cmd run build
npm.cmd start
```

打开 [本地工作台](http://127.0.0.1:8000)，选择“第一次使用？创建本地账号”。没有默认账号或预置密码。API 文档位于 [OpenAPI 页面](http://127.0.0.1:8000/docs)。数据默认保存到 `.data/`，不是远端云服务。

首次安装到其他机器：

```powershell
python -m venv services/api/.venv
.\services\api\.venv\Scripts\python.exe -m pip install -r services/api/requirements-local.txt
npm.cmd ci
npm.cmd run build
npm.cmd start
```

Node 要求 ≥22.12，本次使用 24.13.1；Python 声明支持 3.12—3.14，本次实际验证 3.14.3。`requirements-local.txt` 固定此次运行依赖与 pytest，`package-lock.json` 固定 Node 依赖。Linux/macOS 可使用 `npm`，Python 位于 `.venv/bin/python`；跨系统尚未实测。

安装需要访问包源。本环境网络受限时复用了已有公共包缓存；`scripts/prepare-offline-cache.mjs` 可整理既有 npm 缓存，但仓库不是包含全部 Python 依赖的离线发行包。无需重新安装本工作区已就绪的依赖。

## 第一次体验

1. 粘贴“明天下午给李总发合同；周五18点前提交方案”，点击“保存并整理”。
2. 在提案中核对日期、负责人及目标文件，编辑后保存修订，更新预览。
3. 点击“确认写入”，再到“今日”或“工作空间”查看任务。
4. 完成一项任务，使用提示中的“撤销”；在文件详情查看 Markdown 和历史差异。
5. 在“我的”查看存储用量；原始 Capture 始终保留，可重新查看。

本地规则只识别部分常见行动与明确日期。模糊意图、复杂对话需人工核对；“明天下午”不会被擅自补成具体时刻。

## 开发与验证命令

| 命令 | 用途 |
| --- | --- |
| `npm.cmd start` | 启动 API，并托管已构建的 Web 页面 |
| `npm.cmd run api:dev` | 启动 API 源码热重载；不能与同数据目录的另一 API 同时运行 |
| `npm.cmd run dev` | 启动 Web 开发服务器，默认 5173，代理到本机 8000 API |
| `npm.cmd run typecheck` | Web 与小程序 TypeScript 检查 |
| `npm.cmd test` | 共享客户端 Vitest 单元测试 |
| `npm.cmd run test:api` | 后端 pytest；临时数据放入独立 `.cache/pytest-*` |
| `npm.cmd run build` | 构建 Web、小程序并检查 11 个页面的静态事件绑定 |
| `npm.cmd run test:e2e` | 使用独立临时后端运行浏览器和小程序页面逻辑测试，需先 build |
| `npm.cmd run check` | 类型、单测与构建（不含后端 pytest 和端到端） |
| `npm.cmd run verify` | 类型、单测、构建、后端及端到端完整回归 |

热开发使用两个终端：先 `api:dev`，再 `dev`。API 从根目录 `.env` 读取 `HOST`、`PORT` 等配置；Web 开发代理默认固定为 8000。`GKD_PYTHON` 可指定解释器完整路径。

端到端默认使用本机 Chrome；其他路径可设置 `GKD_BROWSER`。没有 Chrome 时，安装 Playwright Chromium：`npx playwright install chromium`。测试默认占用 18640，已被占用时拒绝运行；可用 `GKD_E2E_PORT` 指定空闲端口。测试不使用真实 `.data/`。

Ruff 配置已提供，但本环境缺少 Ruff，未执行 lint 或覆盖率统计；ESLint/Prettier 尚未配置。不要把测试通过等同于已满足覆盖率、性能或安全发布指标。

## 微信小程序

1. 运行 `npm.cmd run build --workspace @gkd/miniprogram`。
2. 用微信开发者工具导入 `apps/miniprogram/`；项目配置已将源码根设为 `dist/`，不要直接导入 `src/`。
3. 当前 `touristappid` 仅用于开发占位。填入自己的 AppID 后，才能验证相应真实微信能力。
4. 默认 API 为 `http://127.0.0.1:8000`。本机模拟器若需要，可临时开启开发工具“不校验合法域名”；仓库配置仍保持域名校验开启。手机上的 `127.0.0.1` 是手机自身，不是开发电脑。
5. 真机/发布前配置可访问的 HTTPS 域名、微信后台的 request/downloadFile 合法域名，并重新构建。例如在独立终端运行：

```powershell
$env:GKD_API_BASE_URL = "https://api.example.com"
$env:NODE_ENV = "production"
npm.cmd run build --workspace @gkd/miniprogram
```

示例域名需替换为自己的服务。该终端的环境变量会继续生效，返回本地调试时使用新终端或恢复原配置。生产模式拒绝 HTTP 构建。

服务端 `.env` 配置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET` 才能使用微信登录；AppSecret 绝不放入小程序。`APP_ENV=production` 强制关闭开发账号入口（`ENABLE_PASSWORD_AUTH=false`）。这项保护不代表服务已具备生产部署条件。

当前仅完成 TypeScript、静态绑定与实际页面 JS 调用真实 API 的自动化验证，尚未在官方开发工具或 iOS/Android 真机验收。

## 配置与隐私

默认无需 `.env`。需要配置时参考 `.env.example` 创建；已有配置不要整体覆盖。密钥、真实原文、数据盘和备份均不得提交。

- `AI_PROVIDER=local`：规则解析不访问模型。外部适配器需 `AI_PROVIDER=responses`、HTTPS `AI_BASE_URL`、`AI_MODEL`、`AI_API_KEY`，并由应用用户阅读说明后开启。
- 外部适配器使用 OpenAI Responses 协议（`{AI_BASE_URL}/responses`）。智谱 GLM 已官方支持该协议：`AI_BASE_URL=https://open.bigmodel.cn/api/v1`，`AI_MODEL` 填账号可用模型（如 `glm-5.3`），密钥来自智谱开放平台控制台。该端点面向 GLM Coding Plan 套餐，普通按量密钥是否可用需自行确认。供应商返回 HTTP 200 错误信封（`success:false`）时适配器会归为凭据或请求配置错误，不进入格式修正重试。截至 2026-09-12 仍未用真实密钥验证，`text.format` 严格 JSON Schema 在该端点的实际支持情况待首次真实调用确认。
- 外部请求只包含本条原文、参照时间、时区和明确选中的任务组标题/标签。模型或地址变化后需重新授权；供应商的留存政策仍需核实。关闭开关只能阻止后续请求。
- 每日额度按外部请求次数计算；网络失败和格式修正也计次，不等同于人民币预算。没有验证真实供应商调用或模型准确率。
- 本机草稿按账号隔离，但以明文保存在浏览器/小程序存储中，退出后仍为该账号保留。共用设备请谨慎使用并管理本地存储。
- API 默认只监听本机。不要把开发账号模式直接暴露到互联网；HTTPS、反向代理、审计与隐私流程须在发布前补齐。

## 代码与数据布局

```text
apps/miniprogram/src/     原生小程序：11 个页面、运行时与样式
apps/web/src/             React 本地工作台
packages/client/src/      两端共用类型、传输和幂等客户端
services/api/app/         API、领域、存储、变更、提案、队列、备份
services/api/tests/       后端单元、API、恢复及安全边界测试
tests/e2e/                浏览器与小程序控制器集成测试
scripts/                  运行入口与离线 npm 缓存工具
docs/                     验收、实现决策与原始需求归档
```

`.data/gkd.sqlite3` 保存身份、目录、提案、操作和队列；`.data/workspaces/<workspace-id>/captures/` 保存原文 JSON，`files/` 保存当前 Markdown，`versions/` 保存哈希版本，`journals/` 保存恢复日志，`trash/` 保存回收站内容。数据库并非全部可丢弃：只有 Task/Group 活跃索引可以重建。

任务是 Markdown 任务组内的内容，不能在创建时直接把任务标题当作文件/任务组名称。整理优先使用明确选中或完整名称唯一匹配的可写任务组；无明确主题时，使用或在当前工作空间根目录新建 `日常任务.md`。确认页可改选文件夹、已有任务组或主题名称；同一位置同名的可写任务组会复用，多义匹配需要用户选择。确认前不创建业务文件，也不会批量改名已有文件。

当前存储层新增和任务写入使用 `schema_version: 2`：任务位于正文 `gtd:task:start/end` 块中，Front Matter 只保留组属性。仍兼容旧格式 1 的 Front Matter `tasks` 与生成区；读取或重建索引不批量改写文件。完整混合块编辑界面尚未全部接通。外部编辑冲突会阻止覆盖，可通过索引检查处理；合法未知字段和普通正文保留，YAML 注释、引号样式和排版不保证逐字往返。

Python 使用四空格，TypeScript/JSON 使用两空格；接口字段沿用 `snake_case`。测试命名描述行为，新增写入逻辑必须覆盖幂等、版本冲突和恢复。`AGENTS.md` 已于 2026-09-10 重写为反映实际目录、命令与架构；工程说明以 `AGENTS.md`、本 README 和实现记录为准。

## 内容可携、备份与恢复

应用不提供导出或打包下载。任务组就是 `.data/workspaces/<workspace-id>/files/` 下的 Markdown，原始 Capture 就是 `captures/` 下的开放 JSON，两者都是普通文本，用任意编辑器或文件管理器即可直接取用、复制和备份；工作目录本身即为可携形态。2026-09-10 已删除此前的导出 Job、ZIP 打包与下载接口，理由见 [实现决策](docs/实现决策.md) 第 4 节。

系统备份是维护者操作用于灾难恢复的手段，包含账号、提案、操作记录等数据库内容，需要先正常停止 API 并等待后台操作结束；备份期间也不要用编辑器修改数据目录。从仓库根目录运行：

```powershell
npm.cmd run backup -- create --output .backups/gkd-20260908.zip
npm.cmd run backup -- restore --archive .backups/gkd-20260908.zip --destination .data-restored-20260908
```

命令只支持默认位置的 SQLite；拒绝运行中的服务、已有备份文件及已存在的恢复目录。包内路径、类型、大小、SHA-256 和 SQLite 完整性均校验后才发布恢复目录。恢复后设置 `DATA_ROOT=.data-restored-20260908`、清空自定义 `DATABASE_URL`，重新启动并登录；旧会话不会复活。原数据目录不会被覆盖。

备份包含敏感原文与账号数据，**未加密**，不含 `.env` 和锁文件；应自行安全保存配置并把备份复制到独立存储。MVP 限制为未压缩 4 GiB、100,000 项以内。尚未实现自动备份、保留策略、加密或承诺 RPO/RTO。

## 后续开发

范围与验收见 [产品需求文档](产品需求文档.md)、[开发计划](开发计划.md)、[实现决策](docs/实现决策.md)。优先完成微信真机与真实模型验证，再推进数据库迁移、部署、永久删除/留存清理、性能与内测；录音、团队协作及双向同步不在本 MVP 中。
