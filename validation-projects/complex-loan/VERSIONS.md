# 源码版本固定记录

V0 阶段只接入 Apache Fineract 原生源码与其官方配对的 Mifos X Web 前端。
渠道前端、渠道后端、外部模拟服务属于 V1/V2 计划内容，尚未存在，不在此登记。

| 仓库 | 目录 | 上游 | 固定标签 | 说明 |
| --- | --- | --- | --- | --- |
| fineract-repo | `repos/fineract` | https://github.com/apache/fineract | `1.14.0` | Fineract 最新稳定版之一；以浅克隆（depth 1）检出该标签 |
| mifos-web-repo | `repos/mifos-web` | https://github.com/openMF/web-app | `v1.0.0-fineract1.14` | Mifos X 新版 Web 前端，官方与 Fineract 1.14 配对的发布标签 |

选 1.14.0 而不是 1.15.0 的原因：openMF/web-app 目前最新的配对发布标签是
`v1.0.0-fineract1.14`，没有对应 Fineract 1.15 的配对标签。V0 要求版本固定且
前后端 API 配套，因此选择两边都有明确配对标签的组合。升级到新版本时应同时
更新本文件与两份源码，并重新核对主干与题库的源码依据。

`gitUrl` 使用 `unused` 占位：源码由人工按上表固定版本检出到 `repos/` 下，
`sync-project` 以 LOCAL_SNAPSHOT 方式直接索引本地目录，不会执行 Git 拉取，
也不会因浅克隆（detached HEAD）导致同步失败。
