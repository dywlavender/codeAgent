# Business Code Agent

面向 Java / MyBatis 老项目的 Agent 问答工作台。代码仓库通过 Git 地址配置并自动同步；人工只编写简短的功能知识文档；系统定位入口类和关键表，生成轻量检索索引及带代码证据的流程、规则摘要。Query Agent 最终回到当前代码和业务文档回答问题。

## 一键启动

### macOS

```bash
chmod +x start-mac.sh
./start-mac.sh
```

根目录存在 `project.config.json` 时会自动使用，不需要再传 `--project-config`。当前默认配置使用内置验证项目、`.data/validation-project.db` 和 8083 端口。

### Windows

双击：

```text
start-windows.bat
```

也可以在 PowerShell 中执行：

```powershell
.\scripts\start-windows.ps1
```

启动脚本会创建 Python 环境、安装依赖、同步并索引仓库、构建前端，然后启动服务。目标端口已有本项目旧进程时会先停止旧进程。

## 项目配置

复制示例：

```bash
cp project.config.example.json project.config.json
```

主要配置：

```json
{
  "project": {
    "id": "loan-system",
    "name": "贷款核心系统"
  },
  "startup": {
    "database": ".data/knowledge.db",
    "port": 8082
  },
  "repositoryRoot": ".data/repositories",
  "knowledge": {
    "root": "knowledge/functions"
  },
  "repositories": [
    {
      "id": "loan-core",
      "gitUrl": "https://github.com/your-company/loan-core.git",
      "branch": "main"
    }
  ]
}
```

仓库已是最新版本时不会重复拉取。私有仓库使用本机已有的 SSH Key 或 Git 凭据管理器，配置文件不保存账号密码。

`knowledge.root` 可以使用相对路径；相对路径以 `project.config.json` 所在目录为基准。

## 模型配置

复制环境变量示例：

```bash
cp .env.example .env
```

模型参数全部放在 `.env` 中，并且 `.env` 的值优先：

```dotenv
BUSINESS_CODE_MODEL_ENABLED=true
BUSINESS_CODE_MODEL_PROVIDER=openai
BUSINESS_CODE_MODEL_NAME=gpt-4.1-mini
BUSINESS_CODE_MODEL_API_KEY=your-key
BUSINESS_CODE_MODEL_TEMPERATURE=0
BUSINESS_CODE_MODEL_TIMEOUT=60
BUSINESS_CODE_MODEL_MAX_RETRIES=2
```

未配置模型时，代码同步、入口定位、关键表关联和普通确定性检索仍可工作；功能分析显示“索引完成”，不会伪造流程或规则。

管理员口令可配置为：

```dotenv
BUSINESS_CODE_ADMIN_TOKEN=your-local-admin-token
```

它用于保护“更新知识库”和“重新分析”等管理写操作，不是大模型密钥。

## 编写功能知识

在 `knowledge/functions` 中新增一个 Markdown 文件：

```markdown
---
id: customer-status-change
name: 客户状态变更
aliases:
  - 客户状态修改
tags:
  - 客户管理
---

# 功能说明

用于维护客户当前业务状态。

## 业务场景

- 内管人员修改客户状态
- 批任务刷新客户状态

## 工程与入口

| 工程 | 类型 | 入口类 |
|---|---|---|
| customer-admin | 内管 | CustomerStatusController |
| customer-service | 服务 | CustomerStatusServiceImpl |
| customer-batch | 批任务 | CustomerStatusRefreshJob |

## 关键表

| 表名 | 数据作用 |
|---|---|
| customer_info | 保存客户当前状态 |
| customer_status_log | 保存状态变更记录 |
```

“工程”既可以是独立仓库 ID，也可以是单仓库中的模块目录。入口类只写类名。完整约束见 [功能知识文档模板](docs/functional-knowledge-template.md)。

## 使用流程

1. 在 `project.config.json` 配置代码仓库和功能知识目录。
2. 启动系统，等待仓库同步和代码索引完成。
3. 将人工功能文档放入 `knowledge.root`。
4. 进入“功能知识”，点击“更新知识库”。
5. 检查每个入口是否已定位，查看关键表关联和分析覆盖情况。
6. 模型已配置时，查看带代码证据的业务流程与核心规则。
7. 在问答页面提问；Query Agent 会先使用功能索引制定检索计划，再读取当前代码和业务文档形成答案。

## 知识结构

```text
人工功能定义
  ├─ 名称、场景、工程、入口类、关键表
  ↓
代码检索索引
  ├─ 入口准确位置
  ├─ 直接调用提示
  └─ 关键表读写位置
  ↓
Agent 功能分析
  ├─ 业务流程摘要
  └─ 核心业务规则摘要
  ↓
Query Agent 回到当前代码和业务文档核实后回答
```

人工定义、检索索引和 Agent 分析分开保存。代码重新索引不会改写人工文档，自动摘要也不会代替最终代码证据。

详细说明见 [当前架构](docs/architecture.md)。

## 管理接口

- `POST /api/knowledge/refresh`
- `GET /api/knowledge/functions`
- `GET /api/knowledge/functions/{id}`
- `POST /api/knowledge/functions/{id}/analyze`
- `GET /api/knowledge-graph`
- `POST /api/query`

旧的来源类型、知识提案、审核接口及其后端状态机已经移除；已有数据库在启动迁移时会删除对应旧表。

## 验证

后端：

```bash
python3 -m unittest discover -s tests -v
```

前端：

```bash
cd frontend
npm run build
```

内置 `examples/validation-project` 包含申请服务、提款服务、结算批任务和 MyBatis 表访问，用于验证单仓多工程入口定位和跨工程检索。
