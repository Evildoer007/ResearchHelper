# ResearchHelper

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

面向A股、港股权益研究人员，将标的、行业、产业链、事件、宏观或混合问题转为有证据的研究论点与完整报告。

## Product Purpose

问题驱动的独立投研Agent。研究完成与判断成立分开；结论、数字、来源、计算和后续验证条件使用同一版本。

## Operating Context

macOS原生窗口承载离线网页与本地研究服务；Skill由宿主Agent推理，使用项目内独立研究档案。默认先用可用数据生成研究草稿，再按需深化核验。离线与真实模型、数据权限及安装物分别验收。

## Capabilities and Constraints

prd.md为唯一产品依据。八类入口、五种意图。ResChat与ResDesk共用同一任务：ResChat发起与继续研究，ResDesk是客户报告的研究底稿，默认进入论证底稿，按实际投资逻辑展开理论、数据、分析与计算、反证及原始来源。研究范围、取证记录、客户报告和判断追踪作为同版辅助视图；执行进度不取代底稿。切换保留任务和未发文本，无任务时明确显示空态，不创建第二Agent。不要求人工审批、估值或固定论点数量。追问新建关联研究，不覆盖旧题或冻结结果。知识只读，执行确定性登记方法。取消、恢复继承预算；交付冻结、更新形成新版本。

OptionHelper与DataFetcher只读，不复制凭据或生产数据，不push。ResearchHelper_hist已按用户要求移到废纸篓，当前只有ResearchHelper主项目。正式安装包应自包含，不依赖开发环境。

## Brand Commitments

统一名称ResearchHelper。保留demo红色圆形、白色轨迹和四节点中心Logo。App图标采用与OptionHelper同几何的白色圆角方框；Chat直接沿用OptionHelper前端DOM和公共样式，在本项目保留独立副本，只替换品牌、研究内容和接口。原生标题栏遵循同系列规范，业务与数据保持独立。中文凝练，减少小字；报告完整，不因脱敏删掉公开数字和论述。

## Evidence on Hand

历史版本包含六份已脱敏研究HTML与来源目录。本轮合成案例明确标识，不冒充真实证券或实时行情；历史案例不证明当前研究有效。

## Product Principles

- 问题决定计划，不让菜单或旧阶段决定研究。
- 判断可定位到同版证据与计算。
- 缺口与识别限制分开，保存成功才交付。
- 材料无指令权限，凭据不进入输出。

## Accessibility & Inclusion

五个视图始终可达。正文不小于13px，键盘焦点可见，窄屏主控件不小于44px，页面无横向溢出。
