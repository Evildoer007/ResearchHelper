#!/bin/zsh
# Double-click local delivery. It updates dist, never installs or publishes online.
set -euo pipefail
researchhelper_finish() {
  local researchhelper_status="$1"
  trap - EXIT
  if (( researchhelper_status == 130 )); then
    print -u2 "构建已取消。未发布或安装新版本。"
  elif (( researchhelper_status != 0 )); then
    print -u2 "构建未完成。请根据上方失败原因处理后重试。"
  fi
  if [[ -t 0 && -t 1 ]]; then
    print -n "按回车关闭构建窗口。"
    IFS= read -r researchhelper_reply || true
  fi
  exit "$researchhelper_status"
}
trap 'researchhelper_finish $?' EXIT
researchhelper_project="${0:A:h}"
researchhelper_saved="$researchhelper_project/.researchhelper/runtime/build-python-path"
researchhelper_python="${RESEARCHHELPER_PYTHON:-}"
researchhelper_source="指定环境"
if [[ -z "$researchhelper_python" && -f "$researchhelper_saved" && ! -L "$researchhelper_saved" ]]; then
  IFS= read -r researchhelper_python < "$researchhelper_saved" || researchhelper_python=""
  researchhelper_source="本机保存"
  if [[ "$researchhelper_python" != /* || ! -x "$researchhelper_python" ]]; then
    print -u2 "本机保存的解释器已失效，检查现有MachineLearning环境。"
    researchhelper_python=""
  fi
fi
if [[ -z "$researchhelper_python" ]]; then
  researchhelper_python="/opt/anaconda3/envs/MachineLearning/bin/python"
  researchhelper_source="MachineLearning默认环境"
fi
if [[ "$researchhelper_python" != /* ]]; then
  print -u2 "构建解释器须为绝对路径。未开始构建。"
  exit 1
fi
if [[ ! -x "$researchhelper_python" ]]; then
  print -u2 "未找到现有MachineLearning环境。未创建新环境，也未开始构建。"
  exit 1
fi
cd "$researchhelper_project"
print "ResearchHelper macOS一键打包与离线验收：DMG、Skill与demo。"
print "打包后自动验收实际DMG和独立Skill，通过后更新项目级Skill；不安装App、不覆盖现有demo、不调用真实模型或金融数据。"
print "解释器来源：$researchhelper_source"
"$researchhelper_python" -I -B -u "$researchhelper_project/packaging/build_current.py"
