<div align="center">

# 🤖 evabot

**自我管理的 AI 智能体 —— 让你专注于目标，而不是维护 AI。**

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square&logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Stars](https://img.shields.io/github/stars/wpydcr/evabot?style=flat-square&color=yellow)](https://github.com/wpydcr/evabot/stargazers)
[![Issues](https://img.shields.io/github/issues/wpydcr/evabot?style=flat-square)](https://github.com/wpydcr/evabot/issues)

[English](./README.md) · [简体中文](./README_zh.md)

</div>

---

## ⚡ 现有 AI 智能体的问题

大多数智能体的维护成本最终都落到了**你**身上：

- 你手动管理对话窗口，防止上下文膨胀失控
- 你关注社区动态，手动升级 Skill 到更好的版本
- 你为每个任务挑选合适的模型
- 当智能体开始跑偏时，你重开会话重新来过

**evabot 把这些都接管了。**

---
## 🌟 它的不同之处

### 🔄 一个对话，用到底

你只需要和一个 **Butler** 聊天。不用创建新会话，不用考虑上下文窗口。三层架构（管家 → 调度员 → 执行者）在物理层面隔离了任务执行与你的对话——任务越深入，对你的聊天记录影响越小。evabot 越用越聪明，而不是越用越笨。

### 🧬 自进化 —— 三个层次

evabot 不只是修复错误，它会主动进化自己的能力：

**① 失败触发反思**
每次执行失败都会自动触发一次复盘。系统定位自身逻辑中的问题所在，并将修正结果写回配置。同样的错误，不会犯第二次。

**② 主动猎取更优 Skill**
当某个 Skill 需要更新时，evabot 不会等原作者发新版本。它主动全网搜索具备相同功能的所有 Skill，通过安全检测后自动对比并应用最优方案——无需你手动跟进任何社区更新。

**③ 竞争式 Skill 进化**
它不只是选最新的版本。系统会针对你的具体使用模式对候选 Skill 进行横向比较，如果没有一个完全契合你需求的，它会从多个 Skill 中提取最优部分，合成一个专属于你的新 Skill。

### 🎯 智能模型路由 —— 够用的里选最便宜的

evabot 不相信榜单。测评分数高的模型，不代表在你的具体任务上表现也好。evabot 的做法是：

- 依据**真实任务历史**与**你的领域实际成功率**，为每个模型动态打分
- 对每个新任务进行难度评估，与模型能力档案进行匹配
- 最终选择**在能够胜任该任务的模型中，成本最低的那一个**——不选最贵的，也不选最强的，选最合适的

### 📡 全链路信息同步，任务不偏轨

子任务执行到一半缺少关键参数时，系统不会盲猜。它会逐层向上询问，因为有时候拆分任务时并没有传入完整的背景信息，当每一层都没有需要的信息时，会最终询问到你。

再拿到确切信息后，再逐级向下分发，确保全链路都得到了信息补充，然后继续执行。

任务从不会在你不知情的情况下静默偏离。

---

## 🔧 其他核心特性 
<table>
  <tr>
    <td>
      <b>⚖️ 强制防幻觉审计机制</b><br>
      强制审计，输出必须基于真实工具反馈，不允许模型凭空捏造。
    </td>
    <td>
      <b>🌲 按需加载的动态技能树</b><br>
      贯彻渐进式披露原则，避免无效上下文占用。
    </td>
  </tr>
  <tr>
    <td colspan="2">
      <b>🧠 3000行极简微内核</b><br>
      框架专注状态机流转与底层稳健。能力 100% 技能化外置，二次开发零门槛。
    </td>
  </tr>
</table>

---

## 🚀 快速开始

### 1. 克隆仓库 & 安装依赖
```bash
git clone https://github.com/wpydcr/evabot.git
cd evabot
pip install -r requirements.txt
```
> 建议 **Python 3.12+**

### 2. 配置大模型密钥
系统已预置 **Qwen（千问）** 和 **Moonshot（Kimi）** 进行层级调度。

任何支持 OpenAI 库调用的模型均可在 `llm.yaml` 中添加。
```bash
# Linux / macOS
export qwen_key="your_qwen_api_key_here"
export kimi_key="your_moonshot_api_key_here"

# Windows (CMD)
set qwen_key="your_qwen_api_key_here"
set kimi_key="your_moonshot_api_key_here"
```

### 3. 一键启动

```bash
python run.py
```
---

## 🏗️ 架构
```text
Channel Adapters（渠道适配器：终端 / 微信 / Slack / ...）
        │
        ▼
    Gateway          ← 常驻进程：路由 / 队列 / 心跳推送
        │
        ├──► Butler  ← 你唯一的对话界面：意图澄清、任务派发
        │       │
        │       ▼
        │    Solver  ← 任务拆解、Skill 调度、上下游通讯
        │       │
        │       ▼
        │    Worker  ← 闭环执行：Worker + Auditor 审计
        │
        ├──► Memory  ← 仅检索交互层（Butler）历史数据
        │
        └──► Storage ← Hot Context + Cold Storage
```
> **核心隔离原则：** 每一层只能看到下层的**结果**，而非内部过程。这正是无论任务多复杂，你的对话始终保持干净的原因。
---

## 📁 目录结构

```text
evabot/
├── frontend/               # UI（规划中）
└── backend/
    ├── app/
    │   ├── channels/       # 渠道适配器（终端、消息平台等）
    │   ├── gateway/        # 网关层：消息路由、队列管理、常驻进程
    │   ├── butler/         # 交互层：意图澄清、日常闲聊、向下游派发任务
    │   ├── solver/         # 统筹层：任务拆解、Skill 调度、上下游通讯
    │   └── workers/        # 执行层：Worker 执行机制 + Auditor 严苛审计
    ├── core/               # 系统基座工具
    ├── power/              # 技能库
    │   ├── active/         # 生产环境运行的 Skill
    │   ├── archive/        # 版本更新回滚区
    │   └── power.py        # 技能树解析与管理器
    ├── logs/               # 系统运行日志归档
    ├── memory/             # 记忆层：历史存储、更新与检索
    ├── llm/                # 大模型配置文件（llm.yaml）
    └── workspace/          # 隔离工作区
run.py                      # 系统启动入口

```

## 🗺️ 路线图
- [ ] 前端页面
- [ ] 定时任务
- [ ] 多消息渠道支持
- [ ] 多模态支持

> **不会支持** 外部工具或MCP，必须包在skill中被使用。


## ⭐ Star

<div align="center">
  <a href="https://star-history.com/#wpydcr/evabot&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=wpydcr/evabot&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=wpydcr/evabot&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=wpydcr/evabot&type=Date" style="border-radius: 15px; box-shadow: 0 0 30px rgba(0, 217, 255, 0.3);" />
    </picture>
  </a>
</div>

<p align="center">
  <em> 感谢访问 ✨ evabot!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=wpydcr.evabot&style=for-the-badge&color=00d4ff" alt="Views">
</p>


<p align="center">
  <sub>evabot 仅供教育、研究与技术交流使用。</sub>
</p>