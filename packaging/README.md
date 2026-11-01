# ResearchHelper构建与分发

## 一、与OH相同的构建分层

|路径|职责|
|---|---|
|build_current.py|一键本地交付、临时目录、内容寻址校验、原子更新下载|
|release_retention.py、release-retention.json|新交付通过后退役旧安装包和生成副本，不清空废纸篓|
|build_all.py|环境预检、源码冻结、候选构建，独立执行可保留诊断快照|
|source_snapshot.py、package-source-map.json|明确白名单，不按目录扫入私密文件|
|release_contract.py|统一版本与ResearchHelper独立协议|
|shared_payload.py|组装App、Skill和demo共同使用的唯一内核|
|build_progress.py|可见阶段、持续日志、实际耗时与构建进程回收|
|app/build_app.py|自包含Python后端、原生壳、签名和DMG|
|app/macos/|图标、构建工具版本及冻结安装入口|
|app/windows/|WinForms/WebView2原生壳构建、Inno安装器与隔离验收|
|skill/build_skill.py|项目级独立Skill包|
|skill/install_project_skill.py|核验后更新受控项目安装副本，保留恢复路径，未知文件不覆盖|
|skill/SKILL_README.md|随包安装说明|
|demo/build_demo.py、sync_demo.py|离线发布副本生成与受验收约束的同步|
|build_environment.py、build-requirements.lock|只读环境检查及打包依赖锁|

macOS双击根目录build-researchhelper-macos.command，Windows双击build-researchhelper-windows.bat。使用已有Python3.12环境，优先MachineLearning。解释器可显式设置RESEARCHHELPER_PYTHON，并保存为本机.researchhelper/runtime/build-python-path。构建不会建立环境、安装App或全局Skill、调用真实模型或金融数据。macOS候选提交后自动执行包后验收，成功后才更新项目级Skill；本地修改、未知文件和其他发现入口均保留并提示，不改变研究数据。两平台均复制并校验assets/icons的受控图标，常规打包不依赖Node、Playwright或Chrome。图标再生成是单独设计流程，不能在打包时悄悄更换Logo。

Windows使用同OH的.NET SDK 8、WinForms/WebView2与Inno Setup 6.3或更新6.x。原生壳已适配ResearchHelper的JSON就绪协议、独立Store、报告、打印、保存和缩放；不是旧Qt入口。Python与.NET运行时内置，WebView2 Evergreen Runtime为已有安装前提，不冒称已经随包内置。图标从现有已核对的白色圆角方形原图转换，保留十层ICO及SVG来源哈希。

Windows原生依赖仅从已有NuGet缓存恢复，清除远端包源并关闭审计联网。若缓存不完整，先用已有开发环境打开products/app/desktop/windows/ResearchHelper.Windows.csproj，由开发者单独恢复依赖，再重新双击构建；本入口不会静默下载。打包需要现有Inno编译器，可用RESEARCHHELPER_ISCC明确指定。

## 二、同源组装

开发代码分布在core/src/researchhelper与五个modules下。运行时依然采用统一researchhelper命名空间；shared_payload将唯一实现组装成自包含运行包，不复制另一套业务代码。

App的冻结后端从组装的运行包分析依赖，Skill与demo也使用该组装器。App不依赖开发目录、Conda、OptionHelper或共享SDK路径。

浏览器资源许可也由shared_payload的固定清单组装：App和demo包含Apache ECharts6.0.0与ThinkingOrbs0.3.1，Skill只携带报告图表需要的ECharts许可。完整原LICENSE、NOTICE、d3子组件许可及来源记录保留在根LICENSES；构建不下载、升级或修改图表库。组装前检查全部必需文件，拒绝缺失和源链接，不扫描任意许可文件。App的inventory另并入已有Python依赖许可。

## 三、候选不等于发布

双击入口通过build_current.py调用唯一构建内核。源码快照与编译输出位于当次进程拥有的系统临时工作区，成功、失败或主动取消后自动回收；日志和阶段回执保留在result/build-records/时间戳。工作区绑定本项目及存活期，不能按目录前缀授权任意外部路径。result/build-work保留旧诊断入口兼容，不再承载根双击入口的新编译。正式源码、安装包、候选demo和归档始终保留在本项目。App仅存在于构建临时目录和安装包内，不长期保留第二份裸App、Python冻结工作区或编译缓存。macOS系统沙盒阻断外网。demo目录记录完整文件集合及树摘要，缺失、替换或新增文件均使候选完整性检查失败。

PyInstaller缓存不属于冻结源码或交付。macOS冻结入口为每次调用建立独立系统临时缓存，明确覆盖继承的PYINSTALLER_CONFIG_DIR，完成、失败或取消后由同一所有者回收；不写入源码.cache，也不清理共享用户缓存。正式App、日志、候选demo和归档仍在项目约定位置。此隔离沿用OH的构建工作区归属原则，不代表已经证明空目录重现的操作系统原因；结束验收仍要求本次临时源码路径不存在，不能忽略目录或放宽完整性门槛。Windows构建链未改变。

校验完成后，macOS的dist仅包含当次DMG与Skill ZIP；Windows更新result/windows-candidate，不覆盖macOS的dist。整体切换前按产品、版本与完整SHA256核对旧包和新包，保留失败回滚所需字节。macOS新候选提交、临时目录回收和完整性检查通过后，按照release-retention.json退役旧DMG、旧Skill安装包、清单完全一致的旧候选demo和明确生成的旧裸App。旧产物移入废纸篓，仓库只保留当前候选的内容寻址包；若另有Windows当前下载，则其同摘要Skill归档也保护。旧清单、日志、回执和索引保留为历史证据，不再宣称旧二进制仍在仓库。未知文件、污染归档、额外demo文件和外部链接不自动清理。Windows不执行macOS废纸篓策略。

生成的当前完整离线demo、三个产品清单和可验证回执位于result/build-candidates/时间戳。回执绑定源码、内容寻址安装包与demo，不依赖已删除的临时源码。源码在构建期间变化、复制失败、未知下载文件、归档污染或第二次并发构建均拒绝更新下载；目录切换失败恢复完整旧目录。退役发生在新候选完整提交后，不清理data、研究结果、生产档案或两份保留demo。旧候选的生成demo退役后，其回执仅供历史查阅，不能重新冒充可运行候选。

平台自动匹配本机，不从macOS伪交叉构建Windows。macOS候选产物为App与DMG，Windows为ResearchHelper.exe目录及ResearchHelper-版本-windows-x64-setup.exe，两者都生成同源Skill ZIP和demo。Windows不是macOS沙盒，采用无远端源的构建命令和默认关闭外部研究的临时Store，不能把它写成已经完成操作系统级断网验收。

Windows构建必须通过包内服务检查、Inno临时安装与安装物复检。临时验收不创建快捷方式、卸载注册或正式安装目录。候选未签名，不作SmartScreen或原生视觉验收声明。当前macOS主机无法完成Windows编译、安装或实屏验收，不存在已验收的Windows交付物。

新入口built_local_candidate表示本地下载已更新，published、installed与demos_updated仍为false；不安装App、不覆盖两份展示demo、不联网发布。项目级Skill单独通过project_skill_installation记录实际核验及更新结果，不能由installed字段推断其未安装或已成功。最终回执另列dist_updated及临时目录清理状态。准备回执或构建成功不单独证明目录提交完成，verify_current_build核对保留清单、归档、demo和实际下载字节。

build_all.py独立诊断模式仍生成built_not_published，保留result/build-staging中的快照，不更新交付。旧候选及旧验收记录不改写；诊断构建、当前本地交付和正式发布是不同状态。

七阶段依次为环境预检、源码冻结、Skill、demo、平台App、完整性校验、本地候选交付。macOS随后进入三段包后验收：实际DMG与Skill完整性及隔离启动、App三类固定研究的两版五格式导出、独立Skill同案例及数据一致性。验收使用包内合成案例，不读历史QA数据或生产凭据，不调用模型与供应商，不需要Node或浏览器。这里只确认接口和报告运行，不代替原生UI、浏览器专项或真实投资研究质量检查。失败保留安装包、失败阶段及回执，退出码非0，项目Skill不更新；不会把文件已生成写成研究已通过。长阶段显示活动提示和实际经过时间，不估算百分比或剩余时间。完整编译输出保留在当次build.log，终端仅显示阶段；成功列出简短下载路径、demo、回执与日志，不展示临时快照深层产物路径。失败列出具体阶段和诊断。交互式双击终端保留窗口，非交互测试不等待回车。

预检失败仅留下小型尝试回执与诊断，不复制半套源码、不保存错误环境。主动取消使用cancelled_not_published及130退出码，既有交付不变。macOS构建使用独立进程组回收后代；Windows尝试taskkill及后备回收，尚未经过Windows实机取消验收，不能宣称全部后代进程必然清理。

VERSION统一运行时及包版本。三类产品各自记录相同runtime身份，发行清单绑定平台、架构及三个产品清单；校验时检查版本、内核和平台安装器一致性。不复制OH的Catalog、Capability或业务协议。Skill包中的modules只保留指南，实际运行源码只有平铺内核一份。

后台验收通过RESEARCHHELPER_CANDIDATE_RECEIPT绑定明确候选，通过RESEARCHHELPER_ACCEPTANCE_DIR指定新的记录目录，不按修改时间猜测，不覆盖历史成功记录。macOS运行检查直接启动该候选只读DMG内的后端，并核对挂载源、发布清单与回执身份；不打开原生窗口。

正式安装仍须核验对应内容寻址DMG。demo同步必须匹配同版包与浏览器验收，不能通过放宽旧哈希检查强制覆盖。普通构建不自动同步；通过独立同步入口后，主项目demo与桌面demo接收同一候选发布副本，不另维护研究逻辑。

## 开发入口与测试配置

开发工作区入口位于products/app/backend/development.py，正式原生App继续使用同目录entry.py。根目录不再保留重复的main.py和launcher.py；测试配置归入tests/pytest.ini，仅配置测试路径，不进入App或Skill发布白名单。测试框架配置移动不改变共享内核、生产Store或原生启动方式。

## 当前UI验收入口

- tests/accept_composer_current_alignment.mjs是当前输入专项。使用真实离线服务和临时研究库，保留多行输入、材料、拖放、失败续传及断点恢复断言。等待连接时先确认DOM就绪，再合成输入并释放受控请求，不让输入动作反过来等待被自身挂起的导航。旧accept_composer_alignment.mjs及其失败回执保留，不用于当前通过声明。
- tests/accept_current_icon_parity.py是当前图标入口。重新渲染当前SVG的九档尺寸，核对源码摘要及原生图标外框、透明边界和Dock位图，不依赖旧icon-parity目录。
- packaging/tests/test_icon_outer_parity_offline.py的像素项以历史render.json为输入。只有先核对该记录的源码路径、摘要和九档图像后才可重放，不通过修改历史记录或迁移旧路径让它批准新版本。结构项由当前图标入口复用。
- tests/test_task_rename_icon_parity_offline.py独立核对OH重命名SVG路径、16px视框及RH归档语义；完整任务菜单另由accept_task_menu_alignment.mjs检查。

Python跨层测试使用tests包的模块入口，由tests/__init__.py完成源码路径和测试命名空间初始化。直接文件调用绕过初始化的导入错误，不等于产品构建失败。源码专项、实际DMG、正式启动器、已安装App及Windows实机分别记录，不能互相替代。

## 四、现版与历史记录

- dist：当次本地候选DMG与Skill ZIP。
- versions/app/v0.10.0：当前DMG的内容寻址副本及历史JSON清单，不保留旧裸App、旧DMG或重复图标。
- versions/skill/v0.10.0：当前Skill ZIP的内容寻址副本，不保留旧解包Skill。
- versions/demo/v0.10.0：按用户要求保留的冻结历史demo，不代表两份展示demo的当前版本。

历史清单和索引不改写，旧安装包按明确退役规则移出仓库，不冒充本次新源码。尚未Apple公证。旧专项发布、Windows脚本和历史验收工具已移入maintenance，不作为当前开发入口。
## 交付目录预检

根一键入口在冻结源码前检查本平台现有下载目录，并在最终提交前重新检查。macOS使用dist，Windows使用result/windows-candidate。遇到未知文件、额外目录或符号链接，原目录保持不变，不启动重构建、不自动清理。开始构建后才会生成当次日志；预检失败直接显示原因。

一次预检列出全部异常项目及其类型，显示完整目录和人工处理方向，不逐个失败后反复重编译。文件名中的换行与制表符转义显示，不能伪装成成功阶段。macOS目录拒绝Windows安装器，Windows目录拒绝DMG；合法旧版本只在新候选提交前保留回滚字节，提交后按项目策略退役。目录路径先检查项目边界及原始路径中的符号链接，不能通过上级路径跳转或链接掩盖越界。交付位置若是普通文件，明确报错而不覆盖。

macOS中唯一的系统元数据例外是普通文件.DS_Store且具有固定Finder格式头。它不计为安装物、不进入发布归档，目录原子切换时复制并核对原字节；回滚同时恢复原文件。只按文件名相似、无格式头、目录或链接均不能例外，Windows仍拒绝此文件。该识别不宣称已解析完整Finder数据库。其他未知文件继续保持原位并阻止构建，不自动清理。

独立验收入口tests/accept_delivery_diagnostics.py仅运行合成目录与受控构建器，保留源码及OH参考哈希、失败记录和回归结果；不编译安装器、不操作真实下载、不调用模型或金融数据。通过这一检查不代表当前dist完整、Windows实机通过或最新UI已进入DMG。


旧交付物清理在候选提交后单独限时运行，默认25秒，沿用统一进程组回收。目录按已核验清单逐项转移，避免同步目录整目录移动阻塞；未知文件保留，部分转移的原目录、恢复目录及清单共同构成恢复依据。超时或失败不记为清理完成，构建保留现版并继续包后验收，回执单独记录待核对状态。
