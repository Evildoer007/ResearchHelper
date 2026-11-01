# 开发维护

本目录只保留现用维护工具及其安全检查，不保存旧产品源码或按次积累的迁移脚本。

|入口|用途|
|---|---|
|candidate_acceptance.py|核对候选、包内资源和版本身份|
|tidy_development_workspace.py|识别并回收可再生构建副本|
|compact_acceptance.py|压缩已关闭验收的重复数据，保留可复核原件|
|retire_closed_test_stores.py|清理已关闭且明确归属测试的Store|
|retire_duplicate_skill_archive.py、retire_empty_skill_duplicates.py|处理已识别的重复安装物|
|install_from_verified_package.py|核对既有安装后更新App|

其他保留工具由现用测试或交付流程引用。App启动不依赖本目录；候选验证通过显式入口读取这里的核验器。

正式构建、安装、demo同步和交付保留策略分别在packaging/build_current.py、packaging/app/macos/install_app.py、packaging/demo/sync_demo.py及packaging/release_retention.py维护，不另建第二套打包流程。

清理先生成具体清单，再核对归属、依赖和文件哈希。数据、凭据、活动任务、现版交付与仍被测试使用的样本保留；不以失败状态为理由删除研究依据，不修改既有验收回执。清理记录只保留一份范围及恢复映射，不在开发仓库复制整套备份。
