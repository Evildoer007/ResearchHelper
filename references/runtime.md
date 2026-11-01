# 研究运行

本文只说明Skill如何调用现有内核。字段、枚举、来源能力和方法条件以本任务的session.tools(task_id)为准，不在文档中复制接口Schema或历次修补记录。

## 一、开始研究

使用core/session.py中的Session。研究数据归属于使用项目的data/researchhelper，报告与导出副本归属于result/researchhelper。安装目录只读，不读取App、OptionHelper或其他项目的凭据与数据库。

Session默认离线。用户要求实际取数时，宿主显式设置allow_network并采用当前研究项目的独立配置或用户提供的内存凭据。宿主负责语言推理，内核负责取数、登记算术、保存与导出，不另外启动App模型。

session.create保存原问题、研究对象、意图及截止时点。当前研究是默认值；明确要求历史时点时才指定cutoff。个股、ETF、指数、行业、产业链、事件、宏观与混合问题共用同一流程，证据要求按问题选择。

## 二、先取得资料并交付首稿

1. 用session.tools(task_id)读取first_report和data_requests，只查看本任务实际需要的工具。
2. 用session.draft(task_id,data_requests=...,defer_delivery=True)取得资料。优先复用iFinD固定行情、财务与已登记数据接口，PDF只在确实需要原文且接口不足时按需取得。
3. 从session.status读取draft_observations与draft_analysis，用session.sources定位原始响应或材料。读不到的字段保留缺口，不用空表证明现实不存在，不猜证券代码、单位或披露日期。
4. 根据已有资料撰写核心结论、几条不同的策略逻辑及风险。每条逻辑解释判断、依据、机制、投资含义和验证条件。数据与计算引用使用本任务实际ID。
5. 用session.draft(task_id,report=...)保存，再用session.export取得文件。缺数、未知单位、提取不齐或尚未独立复核不阻断首次交付；未回答部分留在底稿，不能伪称全面核验通过。

首稿字段由first_report给出。定性判断也可以先交付；不为凑数强制固定论点数量、通用估值门槛或完整财务报表。

## 三、按需深化

session.run消费宿主提交的证据、问题回答、计算与论点。正式深化之前读取同版tools中的evidence、calculations、analysis和argument_support；需要原件选择时，以实际reading_hash绑定本轮读取范围和选择Schema。

部分材料未采用、未找到和格式不适用是不同状态。只修正受影响的字段与步骤，不删掉独立可用材料，也不把内部失败信息重复写进对客正文。深化结果与首次草稿分别保留；旧研究继续使用创建时锁定的规则，不为让旧任务通过而改版本标记。

需要独立论证复核时，按argument_support提供的prepare、inputs和submit顺序完成，session.deliver保存同版报告。此步骤不能重新成为首次出稿的门槛。

## 四、预算、取消与更新

默认上限为100次数据请求、8次模型调用、600秒，预留1次模型调用和60秒交付，最多两轮补证。宿主复用已保存原件和已执行计算，不为格式、配色或导出重新调用模型。

session.cancel停止继续派发；恢复沿用原账本、任务和版本。宿主已有新输出时按同版Schema继续提交，不重置预算或重放已完成请求。

App自动研究在取数规划完成后保存检查点。取消或重启同一任务时继续未完成请求，复用已有原件，不再次支付同一规划。研究输入、材料或原有来源依赖改变时，重新规划并保留累计用量；检查点缺失或不适用时沿用现有出稿流程，不新增阻断。

session.update创建待研究的新版本，之后由宿主读取工具、取得新资料并继续研究。问题、对象范围或意图发生实质变化时另建关联任务。旧结论与旧HTML保持冻结，新版本失败不破坏旧交付。

保存失败只重试保存；转换失败保留HTML。成功保存后才能向用户确认已生成报告。详细格式见[报告交付](delivery.md)，证据边界见[取证与分析](evidence.md)。

## 已保存报告更正

session.report_history(task_id)列出保存版本及其更正，不包含未交付的检查点。session.report_corrections(task_id,version)读取独立注释；session.correct_report(task_id,reason,version,expected_digest=...)将已知错误绑定到所选报告。expected_digest取该版delivery.digest，不拿其他任务或新版本的标识替代。更正不会重新取数、消耗模型额度或修改已冻结正文。改正文使用新版本，不用更正记录伪称原报告已修复。

新建初稿任务和主动更新的新版本采用第7版原始数据读取规则。iFinD固定财务字段以该字段请求的D参数标记报告期，响应time不替代报告期；未知期间保留原值并限制相应计算。旧版本不自动升级，单位与首次公开时间仍须分别判断。
