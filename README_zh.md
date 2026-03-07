<div align="center">

# 🤖 evabot

**面向超级个人的专属管家 —— 专注三件事：帮你赚钱、记事与社交**

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square&logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Stars](https://img.shields.io/github/stars/wpydcr/evabot?style=flat-square&color=yellow)](https://github.com/wpydcr/evabot/stargazers)
[![Issues](https://img.shields.io/github/issues/wpydcr/evabot?style=flat-square)](https://github.com/wpydcr/evabot/issues)

[English](./README.md) · [简体中文](./README_zh.md)

</div>



## 🌟 本项目愿景

### 💰 赚钱

你知道很多小钱可以赚，却没时间把它变成收益。

evabot 把你的变现逻辑封装成 Skill，在你睡觉时帮你执行：
直到 API 成本被覆盖，直到出现正向收益。

它不是一个工具，它是你雇来跑副业的员工。



### 🗒️ 记事

你每天产生大量信息，但真正被用上的少之又少。

evabot 在后台持续运行，把你的对话、决策、待办串联成一张网：
谁说过什么，哪件事还没跟进，它都替你记着。

你不需要刻意整理，它会主动把信息送到你需要它的时候。



### 🤝 社交

人际关系最怕的不是不在乎，而是忘了在乎。

evabot 跟踪你重要关系的近期动态，提前提醒你该联系谁、该注意什么变化。
生日、承诺、情绪信号,它都会在合适的时机推到你面前。

你不会再因为太忙而错过重要的人。

> 当前版本已完成核心架构，各项功能正在快速开发中——欢迎 star 跟进进展。

## 👍 亮点技术

### 🔄 一个对话，用到底

你只需要和一个 **Butler** 聊天。不用创建新会话，不用考虑上下文窗口。三层架构在物理层面隔离了任务执行与你的对话，任务越深入，对你的聊天记录影响越小。evabot 越用越聪明，而不是越用越笨。


### 🧬 自进化 —— 三级进化

evabot 不只是修复错误，它会主动进化自己的能力：

**① 失败触发反思**
每次执行失败都会自动触发一次复盘。系统定位自身逻辑中的问题所在，并将修正结果写回配置。同样的错误，不会犯第二次。

**② 主动猎取更优 Skill**
当某个 Skill 需要更新时，evabot 不会等原作者发新版本。它主动全网搜索具备相同功能的所有 Skill，通过安全检测后自动对比并应用最优方案——无需你手动跟进任何社区更新。

**③ 竞争式 Skill 进化**
它不只是选最新的版本。系统会针对你的具体使用模式对候选 Skill 进行横向比较，如果没有一个完全契合你需求的，它会从多个 Skill 中提取最优部分，合成一个专属于你的新 Skill。


### 🎯 智能模型路由 —— 为你寻找最合适的模型
evabot 不相信榜单。测评分数高的模型，不代表在你的具体任务上表现也好。evabot 的做法是：

- 依据**真实任务历史**与**你的领域实际成功率**，为每个模型动态打分
- 对每个新任务进行难度评估，与模型能力档案进行匹配
- 最终选择**在能够胜任该任务的模型中，成本最低的那一个**——不选最贵的，也不选最强的，选最合适的

### 📡 全链路信息同步，任务不偏轨

子任务执行到一半缺少关键参数时，系统不会盲猜。它会逐层向上询问，因为有时候拆分任务时并没有传入完整的背景信息，当每一层都没有需要的信息时，会最终询问到你。

再拿到确切信息后，再逐级向下分发，确保全链路都得到了信息补充，然后继续执行。

任务从不会在你不知情的情况下静默偏离。


### ⚖️ 强制防幻觉审计机制

强制审计，输出必须基于真实工具反馈，不允许模型凭空捏造。




## 🚀 快速开始

### 1. 克隆仓库 & 安装依赖
```bash
git clone https://github.com/wpydcr/evabot.git
cd evabot
pip install -r requirements.txt
```
> 建议 **Python 3.12+**

### 2. 一键启动

```bash
python run.py
```

<table>
  <tr>
    <th align="center">聊天页面</th>
    <th align="center">模型配置页</th>
  </tr>
  <tr>
    <td align="center"><img src="./fig/task.png" height="400"></td>
    <td align="center"><img src="./fig/llm.png" height="400"></td>
  </tr>
</table>



## 🏗️ 任务架构
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
        └──► Memory  ← 仅检索交互层（Butler）历史数据
```
> **核心隔离原则：** 每一层只能看到下层的**结果**，而非内部过程。这正是无论任务多复杂，你的对话始终保持干净的原因。


## 📁 目录结构

```text
evabot/
├── frontend/               # UI
└── backend/
    ├── app/
    │   ├── channels/       # 渠道适配器（终端、消息平台等）
    │   ├── gateway/        # 网关层：消息路由、队列管理、常驻进程
    │   ├── observer/       # 记录与处理用户的日常信息
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
- [x] 基础架构
- [x] 前端页面
- [ ] 定时任务
- [ ] 日常信息记录/处理（记事）
- [ ] 操作电脑（赚钱）
- [ ] 多消息渠道支持（社交）
- [ ] 多模态支持


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