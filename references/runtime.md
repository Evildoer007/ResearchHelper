# 研究运行

字段、来源及方法以session.tools(task_id)为准。宿主负责语言推理，内核取数、计算和交付。

## 一、创建与首稿

使用core/session.py的Session。安装目录只读，研究数据位于使用项目的data/researchhelper，结果位于result/researchhelper；不读取App或其他项目的凭据、生产库。默认离线，实际取数须显式启用allow_network，使用本项目配置或用户提供的内存凭据。

session.create保留原问题、对象和意图。只有明确历史要求才传cutoff。

独立询问ETF价格与净值、折溢价、价格变化与总收益、CPI同比环比或制造业PMI时，创建后用session.resume生成带出处的通用口径报告。涉及实际标的、时点、读数或附件时按下列步骤取证。

尚未启动的任务可用remove_material移除本次不采用的上传文件，须提供文件名、内容哈希及当前任务的version和revision；同一内容的其他保留文件通过retained_files明确提供。原件继续留存，不作为本次输入。已经执行的任务与冻结报告不通过此操作改写。

1. 读取session.tools(task_id,scope="acquisition")。按prepare以空请求准备本地知识，再读本轮取数范围。此步无需数据或模型调用。
2. 用session.draft(task_id,data_requests=...,defer_delivery=True)取得资料，优先iFinD固定API。stated_window_requests列出本轮可直接查询的收盘、ETF单位净值和宏观区间。宏观指标仅有名称时，读取本轮目录查询结果后重新获取取数工具，核对是否已有唯一匹配的区间请求。检查acquisition范围的acquisition_coverage，按预算补取有用缺项，复用已获响应。
3. 用session.tools(task_id,scope="first_report")读取first_report.writing_context中的当前原值及有效calculations，用sources定位原件。追问指向旧论点时，写作阶段可读对应原版摘录。
4. 按research_scope、writing_brief及report_example组织核对、比较、机制或投资逻辑，用session.draft(task_id,report=...)保存，再用export获取实际文件。缺数、未知单位、提取不齐和未独立复核不阻断首稿；不能将草稿称为全面核验通过。

已有同版资料且研究中断时，可用session.finish_draft本地出稿。财报原值同时显示报表范围、所属期间和原单位，数字可定位到原表。

## 二、按需深化

需要正式证据、计算或独立论证复核时，再读取默认tools(task_id)中的完整契约。session.run提交新的研究输入；涉及原件选择时，使用本轮reading_hash和实际读取范围，不猜选择编号。argument_support按工具提供的prepare、inputs和submit执行。

继续已有底稿使用session.resume(task_id,defer_delivery=True)，沿用已提交输入，不新增补证轮次；完成后用session.deliver保存同版报告。run不是空提交恢复入口。未采用、未找到、提取失败和来源未披露各有不同含义，只限制受影响的部分，不删除独立可用资料，也不把内部错误重复写进对客正文。

## 三、预算与恢复

有限预算、交付预留和补证轮次由当前任务工具给出。取消、恢复不重置累计用量；复用已保存响应与登记计算，不为排版、配色或导出重新调用模型。session.cancel停止后续派发，工作进程停止后才继续保存或恢复。

App在规划后保存检查点。相同任务恢复时继续未完成步骤，不重付已有规划或重发已完成请求；研究输入或来源依赖改变时重新规划，仍保留累计用量。只复用版本、问题、截止时点、来源、知识及完整写作上下文均适用的检查点，不给旧记录补写新标记。

原文已保存但整理、呈现或落盘失败时，按delivery_recovery.action调用session.retry_save继续本地交付。Skill未提交新正文或新请求时，resume及默认draft也使用这条恢复路径。交付预留时间可用于本地保存；总时长不足时，recovery_block提供原因，已取得的资料、原文和旧交付仍可读取。此时停止重试。依赖一致时复用已保存的正文。HTML缺失、内容指纹不符或旧记录缺少页面指纹时，仅重新排版。转换失败保留HTML，保存成功才确认生成。

## 四、更新与追问

session.update创建待研究的新版本，之后重新读取工具和当前资料。旧报告与条件冻结，新版本失败不破坏旧交付；旧任务恢复仍使用其锁定规则，显式更新才采用现行规则。

更新时，取数阶段的active_update.tracking_inputs提供原观察指标、来源与窗口。写作阶段的active_update.previous_report提供绑定原任务、版本、截止时点和内容哈希的历史公开表述。依据本轮资料判断是否维持原结论，分别保留两版条件。

更新取得本版账号策略与实际证券映射。相同查询已在本版成功返回时，旧响应只留作历史，不重复进入本版写作或计算；新响应缺值不能从旧响应补齐。刷新失败时可以沿用仍适用且有明确期间的旧值，但保留来源版本和取得时间，不冒充本次取得。

session.follow_up按新消息创建关联任务，重新确定核对、比较、机制或研究用途。原对象可保留入口，替换或增加对象时以本轮research_scope为准；旧上下文仅解释指代，证据、原件、批准及投资意图不自动继承。父报告和原账本不改写。

已展示父轮结果时，可将该任务的revision传入expected_revision。父轮已改变时先重读。conversation_parent记录原任务、版本、修订和报告标识。

## 五、已保存报告更正

报告更正与版本冻结见[报告交付](delivery.md)，取证边界见[取证与分析](evidence.md)。
