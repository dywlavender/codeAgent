# 多应用业务流使用指南

## 目标与边界

这一阶段解决的是“从用户入口沿真实代码调用跨应用追踪”，不是把所有代码复制进知识库。首版支持：

```text
Vue H5 点击/提交
→ 前端方法
→ HTTP 请求
→ Spring Controller
→ 本地方法调用
→ Feign
→ 目标 Spring Controller
→ 最终处理方法
```

MQ 消费、定时任务、动态 URL、运行时路由和无法唯一解析的依赖注入不在首版自动闭环范围内。

## 配置系统、应用和仓库

```json
{
  "systems": [
    {"id": "channel", "name": "渠道系统"},
    {"id": "middle", "name": "贷款中台"}
  ],
  "repositories": [
    {"id": "h5-repo", "gitUrl": "ssh://git@git.company.local/h5.git", "branch": "main"},
    {"id": "channel-repo", "gitUrl": "ssh://git@git.company.local/channel.git", "branch": "main"},
    {"id": "middle-repo", "gitUrl": "ssh://git@git.company.local/middle.git", "branch": "main"}
  ],
  "applications": [
    {
      "id": "withdraw-h5", "name": "提款 H5", "systemId": "channel",
      "repositoryId": "h5-repo", "sourceRoot": ".", "type": "FRONTEND",
      "language": "typescript", "framework": "vue"
    },
    {
      "id": "channel-service", "name": "渠道服务", "systemId": "channel",
      "repositoryId": "channel-repo", "sourceRoot": ".", "type": "BACKEND",
      "language": "java", "framework": "spring-boot"
    },
    {
      "id": "loan-middle", "name": "贷款中台", "systemId": "middle",
      "repositoryId": "middle-repo", "sourceRoot": ".", "type": "BACKEND",
      "language": "java", "framework": "spring-boot"
    }
  ]
}
```

一个仓库包含多个应用时，分别配置不同的 `sourceRoot`。同一文件只归属到路径匹配最具体的应用。旧配置没有 `systems/applications` 也能运行，每个仓库会自动建立一个兼容应用，但要获得准确的系统名和应用边界，建议显式配置。

应用 `id` 应尽量与服务发现名一致。例如 `@FeignClient(name = "loan-middle")` 会优先关联 `id=loan-middle` 的目标应用。名称无法唯一匹配时只保留候选，不输出成已确认调用。

## 人工业务基线仍然要少

业务基线只描述代码无法可靠判断的粗粒度事实，不写类名、URL 或调用链：

```markdown
# 提款业务基线

## 提款申请主流程

1. 用户在 H5 发起提款申请。
2. 渠道系统接收并转交提款申请。
3. 贷款中台完成提款申请处理。
```

系统会把它作为 `businessFlow`；代码索引得到的跨应用链作为 `technicalFlow`。两层分别保留原文 Evidence 和代码 Evidence。

## 启动和使用

1. 在 `project.config.json` 配好内网 Git、系统和应用。
2. 按对应平台运行一键启动脚本；启动器会拉取或更新仓库并增量索引。
3. 在管理端导入业务基线。
4. 在用户端直接使用业务语言提问，例如：`H5 点击提款提交按钮以后，后端经过哪些应用，最终在哪里处理提款？`
5. 查看回答中的业务流程、技术调用链、每段代码入口和 Evidence。

仓库内置的 `examples/multi-application-flow` 是最小验收项目。它不在问题或业务基线中提供类名，测试仍要求 Agent 找到 H5、渠道服务、贷款中台、HTTP、Feign 和最终处理方法。

## 可信边界

- HTTP 必须同时有调用端和接收端代码证据；Spring 类级路径与方法级路径分别保留证据后再组合。
- Feign 必须同时有服务名、Feign 方法路径和目标 Endpoint 证据。
- 同一路径出现多个可能目标时不会标成 `VERIFIED`。
- 代码调用链只能证明技术事实，不能自动生成业务规则或业务原因。
- 代码更新后重新建边；旧 Evidence 生命周期会转为历史记录。
