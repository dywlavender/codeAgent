# AST 问答价值对照（2026-11-17）

## 对照设置

取公开的 RuoYi-Vue v3.9.1（Spring Boot 2.5.15），冻结六个 Java 后端模块及根项目资料，共 261 个 Java 文件。六道题预先固定 22 个源码检查项，覆盖入口、正反向调用、接口实现、Service 到 Mapper 和跨模块调用链。题库检查项与关键文件清单仅用于复核。 [RuoYi-Vue v3.9.1](https://github.com/yangzongzhuan/RuoYi-Vue/tree/v3.9.1)

通过现有 Pi 评测脚本执行生产 `QueryService`。两组均使用 Pi 1.5.0、`deepseek/gpt-6-luna`、现行 `baseline` 提示词和相同的仓库范围。每题每组两次，每次新会话，超时 600 秒。三个案例先跑 `none`，三个先跑 `ast`，最多四个案例并行。两种产品模式保留各自的资料说明与工具定义。

## 索引抽查

在「AST 结构」页面手动生成索引 `ast-20261117-232019`。生成用时 1.12 秒，索引覆盖 280 个文件、3,346 个符号，含 1,199 条调用、56 条继承和 16 条实现关系索引条目。另有 4,330 条原始调用事实，其中也包括后来被解析的调用。

符号搜索返回了 `SysDeptController.add`（第 74–85 行）、`SysDeptServiceImpl.updateDept`（第 230–250 行）、`SysDictDataMapper.countDictDataByType`（第 53 行）和 `ISysPostService`（第 11 行起）。在浏览器关系图打开 `buildMenuTree → recursionFn` 的源码位置，显示了第 233 行的调用及附近条件。

从 Reader 可返回的已解析关系中分层抽样，10 条的起点和目标均与源码一致。8 条直接定位到关系语句。两条实现关系定位到类声明上方一行的 `@Service`。

| 类型 | 源码位置 | 目标 |
| --- | --- | --- |
| 同类调用 | `ExcelUtil.java:1613` | `ExcelUtil.getSubMethod:1912` |
| 同类调用 | `R.java:108` | `R.isSuccess:111` |
| 同类调用 | `HTMLFilter.java:384` | `HTMLFilter.allowedAttribute:566` |
| 跨类静态调用 | `SysRoleController.java:133` | `StringUtils.isNotNull:145` |
| 跨类静态调用 | `FileUploadUtils.java:146` | `DateUtils.datePath:96` |
| 跨类静态调用 | `SysConfigServiceImpl.java:75` | `StringUtils.isNotNull:145` |
| 继承 | `SysUser.java:21` | `BaseEntity:16` |
| 继承 | `SysDept.java:18` | `BaseEntity:16` |
| 实现 | `SysDictDataServiceImpl.java:16`，声明在 17 | `ISysDictDataService:11` |
| 实现 | `SysDeptServiceImpl.java:29`，声明在 30 | `ISysDeptService:12` |

在六道评测题涉及的代码中，Reader 一次查询就能给出菜单递归的三个反向调用点，也能从岗位接口反查实现类。部门新增的 `deptService.insertDept`、批量删字典的 `dictDataMapper.countDictDataByType` 以及查字典的 `dictDataMapper.selectDictDataByType` 都保留了调用表达式和位置，缺少到目标方法的已解析连线。索引能连上同一服务中按类型名调用的 `DictUtils.getDictCache` 和 `setDictCache`。 [菜单递归源码](https://github.com/yangzongzhuan/RuoYi-Vue/blob/v3.9.1/ruoyi-system/src/main/java/com/ruoyi/system/service/impl/SysMenuServiceImpl.java) · [字典服务源码](https://github.com/yangzongzhuan/RuoYi-Vue/blob/v3.9.1/ruoyi-system/src/main/java/com/ruoyi/system/service/impl/SysDictTypeServiceImpl.java)

## 真实问答结果

24 轮均完成。Trace 核对了实际模型、仓库、资料版本、提示词版本和会话状态。每轮均从新会话开始。所有 `ast` 运行都启用了三项结构工具，绑定同一索引版本。两组基础资料相同，均有源码、README 和应用导航，本工程未登记需求文档。

按源码复核了 88 项判断。下表每格依次为 `none / ast`。检查项以「通过＋部分通过＋未通过」计，耗时和 Token 为各组两轮中位数。Token 取 Pi 用量，包含缓存 Token。

| 题目 | 检查项 | 耗时（秒） | Token（千） |
| --- | --- | --- | --- |
| 部门新增入口与校验 | 8＋0＋0 / 8＋0＋0 | 86.3 / 99.6 | 364.6 / 212.7 |
| 部门更新正向调用 | 6＋2＋0 / 8＋0＋0 | 66.9 / 75.7 | 105.3 / 138.1 |
| 菜单递归反向调用 | 6＋0＋0 / 6＋0＋0 | 57.4 / 51.9 | 111.8 / 142.3 |
| 岗位接口实现和委派 | 6＋0＋0 / 6＋0＋0 | 48.2 / 70.1 | 132.6 / 213.5 |
| 字典删除与 Mapper SQL | 8＋0＋0 / 4＋2＋2 | 99.3 / 78.0 | 305.1 / 174.0 |
| 字典读取跨模块链路 | 8＋0＋0 / 7＋1＋0 | 103.4 / 100.2 | 323.9 / 261.1 |

- **答案完整度。** `none` 有 11/12 轮全部检查项通过，共 42/44 项通过。`ast` 为 10/12 轮、39/44 项。部门更新有一轮 `none` 漏掉当前部门的祖先路径更新和保存返回值。菜单递归与岗位实现四轮均答全。
- **实际 AST 使用。** 12 轮 `ast` 中只有两轮调用结构工具。两次都向 `find_code_symbol` 传入空格拼接的类名和方法名，返回 0 个符号，随后用 Grep 和 Read 作答。Reader 用 `类名.方法名` 可以定位这两个符号。本轮没有一轮 Pi 调查取得有效的结构结果。
- **源码核实。** 按每轮预先标注的关键文件计，`none` 完成 26/26 次 Read 覆盖，`ast` 为 21/26。删除字典题的 `ast` 第 2 轮找到 Service 和 Mapper XML 后未做 Read，误写批量预校验、级联删除和事务回滚。读取字典题的 `ast` 第 1 轮读了 Controller 和 Service，未读 Mapper XML，漏掉 `status = '0'`。 [删除实现](https://github.com/yangzongzhuan/RuoYi-Vue/blob/v3.9.1/ruoyi-system/src/main/java/com/ruoyi/system/service/impl/SysDictTypeServiceImpl.java#L120-L132) · [查询和计数 SQL](https://github.com/yangzongzhuan/RuoYi-Vue/blob/v3.9.1/ruoyi-system/src/main/resources/mapper/system/SysDictDataMapper.xml#L44-L61)
- **调查成本。** 全组耗时中位数由 66.3 秒变为 78.0 秒，约增加 18%。Token 中位数由 15.8 万变为 16.7 万，约增加 6%。工具调用中位数由 8 降至 5，Read 中位数由 4 降至 3。两道字典题的 `ast` 用量下降，同时出现了源码覆盖和答题完整度下降。

## 决策

建议采用方案 C，保留 AST 作为可选导航，暂停解析器投入。最大的结构缺口是 Java 实例字段调用。Service 字段所引用的接口、注入 Mapper 的声明及 Mapper XML 仍需通过文件工具串联。`none` 在两道字典题的四轮中均通过所有检查，当前样本不足以支持为此扩展解析器。若再验证 AST 收益，先检查 Pi 能否命中符号，并跟踪关系返回后用到的源码位置及定位成本。

结论限于这个公开单仓、这个模型及每题两次的产品模式对照，耗时包含并行执行和模型服务波动。

索引状态、十条抽查、浏览器记录、题库、24 份答案与 Trace，以及逐项复核保存在本地 `.data/evaluations/ast-value-ab-20261117/`。各题的 `checks.md` 由现有 `review` 命令生成，`comparison.json` 汇总用量与人工判定。
