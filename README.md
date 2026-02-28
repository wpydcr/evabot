<div align="center">

# 🤖 evabot

**The AI agent that manages itself — so you don't have to.**

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square&logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Stars](https://img.shields.io/github/stars/wpydcr/evabot?style=flat-square&color=yellow)](https://github.com/wpydcr/evabot/stargazers)
[![Issues](https://img.shields.io/github/issues/wpydcr/evabot?style=flat-square)](https://github.com/wpydcr/evabot/issues)

[English](./README.md) · [简体中文](./README_zh.md)

</div>

---

## ⚡ The Problem with Most AI Agents

Most agents end up putting the maintenance burden on **you**:

- You manually manage conversation windows to prevent context from bloating out of control
- You follow community repos and manually upgrade Skills when better versions appear
- You pick the right model for each task yourself
- When the agent starts going off-track, you restart the session and begin again

**evabot takes all of this off your plate.**

---

## 🌟 How It's Different

### 🔄 One Conversation. Forever.

Just talk to a single **Butler**. No need to create new sessions, no need to think about context windows. The three-tier architecture (Butler → Solver → Worker) physically isolates task execution from your conversation — the deeper a task runs, the less it affects your chat history. evabot gets smarter over time, not dumber.

### 🧬 Self-Evolution — Three Layers Deep

evabot doesn't just fix errors — it actively evolves its own capabilities:

**① Failure-triggered reflection**
Every failed execution automatically triggers a retrospective. The system identifies where its own logic went wrong and writes the correction back into its configuration. The same mistake won't happen twice.

**② Proactive Skill acquisition**
When a Skill needs updating, evabot doesn't wait for the original author to release a new version. It actively searches the open web for all Skills with equivalent functionality, runs safety checks, compares candidates, and applies the best one automatically — no manual community tracking required.

**③ Competitive Skill evolution**
It doesn't just pick the newest version. The system benchmarks candidates against your specific usage patterns. If none of them fully match your needs, it extracts the best parts from multiple Skills and synthesizes a new one tailored specifically to you.

### 🎯 Smart Model Routing — Cheapest That's Good Enough

evabot doesn't trust benchmarks. A model that scores well on leaderboards doesn't necessarily perform well on *your* specific tasks. Instead:

- Each model is dynamically scored based on **real task history** and **actual success rates** in your domain
- Every incoming task is analyzed for difficulty and matched against proven capability profiles
- The system selects the **cheapest model whose demonstrated ability meets the task requirement** — not the most powerful, not the most expensive, the most appropriate

### 📡 Full-Chain Context Sync — Tasks Never Go Silent

When a sub-task hits a missing parameter mid-execution, the system doesn't guess. It escalates layer by layer — because sometimes not all context is passed down when a task is decomposed. When no layer has the needed information, it surfaces the question all the way to you.

Once it has a clear answer, it distributes it back down through every layer before resuming. Tasks never silently drift off-track without your knowledge.

---

## 🔧 Other Core Features

<table>
  <tr>
    <td>
      <b>⚖️ Anti-Hallucination Audit</b><br>
      Every Worker output must be grounded in real tool feedback — no fabrication allowed.
    </td>
    <td>
      <b>🌲 Dynamic Skill Tree</b><br>
      Skills load only when needed, keeping context lean.
    </td>
  </tr>
  <tr>
    <td colspan="2">
      <b>🧠 3000-Line Microkernel</b><br>
      The core focuses purely on state-machine transitions and system stability. 100% of domain capabilities are externalized as hot-swappable Skills.
    </td>
  </tr>
</table>

---

## 🚀 Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/wpydcr/evabot.git
cd evabot
pip install -r requirements.txt
```
> Requires **Python 3.12+**

### 2. Configure API Keys
evabot ships pre-configured for **Qwen** and **Moonshot (Kimi)** for hierarchical scheduling. 

Any OpenAI-compatible model can be added in `llm.yaml`.
```bash
# Linux / macOS
export qwen_key="your_qwen_api_key_here"
export kimi_key="your_moonshot_api_key_here"

# Windows (CMD)
set qwen_key="your_qwen_api_key_here"
set kimi_key="your_moonshot_api_key_here"
```

### 3. Launch
```bash
python run.py
```
---

## Architecture
```text
Channel Adapters  (Terminal / WeChat / Slack / ...)
        │
        ▼
    Gateway          ← Daemon: routing, queue, heartbeat
        │
        ├──► Butler  ← Your only interface: intent clarification, task dispatch
        │       │
        │       ▼
        │    Solver  ← Task decomposition, Skill scheduling, inter-layer comms
        │       │
        │       ▼
        │    Worker  ← Execution loop: Worker + Auditor
        │
        ├──► Memory  ← Retrieves Butler-layer history only
        │
        └──► Storage ← Hot Context + Cold Storage
```

> **The key isolation principle:** each layer only ever sees the *result* from the layer below — never the internal process. This is what keeps your conversation clean no matter how complex the underlying task.


---


## 📁 Project Structure

```text
evabot/
├── frontend/               # UI  (Planned)
└── backend/
    ├── app/
    │   ├── channels/       # Channel adapters (Terminal, messaging platforms...)
    │   ├── gateway/        # Routing, queue management, daemon process
    │   ├── butler/         # Intent clarification, chit-chat, task dispatch
    │   ├── solver/         # Task decomposition, Skill scheduling, inter-layer comms
    │   └── workers/        # Execution loop + Auditor strict verification
    ├── core/               # System base utilities
    ├── power/              # Skill Library
    │   ├── active/         # Skills currently running in production
    │   ├── archive/        # Rollback area for version updates
    │   └── power.py        # Skill tree parser and manager
    ├── logs/               # Runtime log archives
    ├── memory/             # History storage, update & retrieval
    ├── llm/                # LLM configuration (llm.yaml)
    └── workspace/          # Isolated sandboxes
run.py                      # System startup entry
```

---

## 🗺️ Roadmap
- [ ] Frontend UI
- [ ] Scheduled Tasks
- [ ] Multi-channel Messaging Support
- [ ] Multimodal Support

> External tools or MCP will not be supported and must be used wrapped in a skill.

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
  <em> Thanks for visiting ✨ evabot!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=wpydcr.evabot&style=for-the-badge&color=00d4ff" alt="Views">
</p>


<p align="center">
  <sub>evabot is for educational, research, and technical exchange purposes only</sub>
</p>