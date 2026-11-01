# ResearchHelper Skill

将researchhelper完整文件夹作为当前研究项目的项目级Skill安装，不安装到全局目录。宿主按SKILL.md读取指南并调用core/session.py，无需用户填写JSON或执行命令。

开发项目双击一键构建后，会从已核验ZIP更新.skills/researchhelper，并通过.agents/skills/researchhelper发现。仅替换清单完整且无本地修改的生成副本；存在未知文件、符号链接或其他Skill入口时保留原安装并明确提示。相同包重复构建不重写安装，旧副本可恢复。不重建研究档案或迁移App数据。

使用现有Python3.12环境，依赖版本见requirements.lock。不自行建立环境、安装依赖或读取其他产品的凭据。宿主负责语言推理，不另外启动研究模型。

安装目录只读。研究项目应独立于安装目录，档案位于研究项目data/researchhelper，报告位于result/researchhelper。App和Skill不共享生产库。

默认离线。仅在用户要求实际取数后启用宿主数据权限；本包验收不证明真实供应商权限、金融研究质量或完整PRD已经通过。
