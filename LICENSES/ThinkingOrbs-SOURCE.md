# Thinking Orbs

Version0.3.1，MIT，作者Jakub Antalik。上游仓库https://github.com/Jakubantalik/thinking-orbs，固定提交de85557ca220332586d070d8788c0e1d6e877a0d。

ResearchHelper从本地只读OptionHelper参考仓库复制已冻结的几何引擎及调度器，位于products/app/frontend/ui/thinking-orbs-engine.js及thinking-orb.js。仅将ESM导入导出改为独立RH经典脚本命名空间，以支持file协议离线demo；九种几何、投影、绘制及共享动画调度算法不改写。不依赖OptionHelper运行路径，不引入React或CDN。

上游引擎为未压缩bundle；OH原构建使用esbuild0.28.2、bundle、esm、es2020。ResearchHelper本轮没有重新构建上游，也不要求用户安装Node。

研究进度折叠行沿用OH的20pxshaping图形，邻近文字反映真实研究节点。无自动轮播、估算百分比或预计耗时。减少动态效果时使用静态帧；节点移除、页面离开、任务切换及终态显式卸载实例。实例不可见或文档隐藏时停止动画工作。

ThinkingOrbs-LICENSE.txt保留完整版权及许可。App的共享许可组装器和离线demo生成器均携带这两份文件。
