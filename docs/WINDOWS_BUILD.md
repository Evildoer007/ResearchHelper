# ResearchHelper双平台构建

## 一、入口与产物

|系统|双击入口|候选产物|
|---|---|---|
|Apple Silicon macOS|build-researchhelper-macos.command|ResearchHelper.app、arm64.dmg、Skill ZIP、demo|
|Windows x64|build-researchhelper-windows.bat|ResearchHelper.exe目录、windows-x64-setup.exe、Skill ZIP、demo|

两者都通过packaging/build_current.py进入同一冻结构建内核，再按本机系统调用独立构建器。macOS更新dist，Windows隔离更新result/windows-candidate；旧包按版本和完整哈希保留，临时源码及编译缓存自动清理。不跨系统伪造安装包，不安装App或同步保留的demo。

## 二、Windows准备

使用已有64位Python3.12环境，优先MachineLearning。入口提供现有解释器选择，记住本机路径，不创建新环境。安装包不依赖用户的Python或Conda目录。

构建依赖为core/requirements.lock、packaging/build-requirements.lock与packaging/app/windows/requirements.lock。后者单列Tinyshare字节码使用的[WMI](https://pypi.org/project/WMI/1.5.1/)及[pywin32](https://pypi.org/project/pywin32/311/)依赖，不套用macOS的WMI排除项。

已有.NET SDK 8和Inno Setup 6.3或更新6.x。WebView2 SDK固定为1.0.3485.44。原生壳采用OH同系列WinForms/WebView2，已改用ResearchHelper的就绪消息、账户、回环服务、独立Store与报告路径，不调用OH。

原生.NET运行时内置。WebView2 Evergreen Runtime是Windows用户机的前提，尚未随安装包内置；缺少时原生壳明确提示，遵循[微软的运行时分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。

NuGet恢复仅允许已有缓存，不设远端源，不清空缓存或自动下载。缓存机制见[微软NuGet说明](https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders)。缺少缓存时，由开发者使用现有开发工具打开ResearchHelper.Windows.csproj单独准备依赖，再双击构建。

## 三、权限与验收

正常用户安装目录为LocalAppData/Programs/ResearchHelper，不要求管理员权限。数据使用LocalAppData/ResearchHelper/research-v1，构建解释器选择只放项目.researchhelper/runtime。两者均不共享OH目录、凭据或生产库。

构建需要检查包内后端、离线案例、默认账户与临时安装物。临时安装不创建快捷方式、卸载注册或启动原生窗口。安装器未签名；代码或离线单元测试通过，不代表Windows编译、安装、SmartScreen或原生界面通过。

当前主机是macOS，无Windows、.NET或Inno实机环境。Windows入口、原生壳和构建链已实现；Windows安装包尚未构建或实机验收。保留旧macOS安装版及两份demo，不以新源码覆盖已有冻结交付。

2026年10月12日新增OH源码对照，14个共用方法和基础配置一致，30项离线检查通过windows-source-z999qu0s，绑定170732-659716候选。未修改生产C#，不把源码对照提升为Windows编译或安装通过。两项目Windows均缺少macOS式页面恢复；详见[Windows壳对齐记录](OH_ALIGNMENT_STATUS.md)。
