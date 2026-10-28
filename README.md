# 纳西妲 AI 助手

一个基于大语言模型的角色陪伴 AI，支持 QQ 群聊/私聊、语音对话（TTS+ASR）、长期记忆、RAG 知识检索、多模型额度轮换、主动陪伴等功能。

> 同人项目，非官方，仅供学习交流。

## 架构

```
┌─────────────┐     WebSocket      ┌──────────────────┐
│   NapCat QQ  │ ◄────────────────► │   bot/run_bot.py  │
│  (OneBot v11)│                    │  QQ 消息接入层     │
└─────────────┘                    └────────┬─────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    │                       │                       │
              ┌─────▼─────┐        ┌───────▼───────┐    ┌──────────▼──────────┐
              │ naxida_   │        │   memory.py   │    │    rag/rag.py        │
              │ voice_brain│        │  SQLite 记忆   │    │  混合检索 RAG        │
              │ (对话大脑)  │        │  四层结构化    │    │  向量+BM25+Rerank   │
              └─────┬─────┘        └───────────────┘    └─────────────────────┘
                    │
     ┌──────────────┼──────────────┬───────────────┐
     │              │              │               │
┌────▼────┐  ┌─────▼─────┐  ┌────▼─────┐  ┌───────▼───────┐
│ llm_    │  │ GPT-SoVITS│  │ 网络搜索  │  │  company.py   │
│ rotate  │  │  TTS :9880│  │ Bocha/DS │  │  主动陪伴调度  │
│ 模型轮换 │  └───────────┘  └──────────┘  └───────────────┘
│ (12模型) │
└─────────┘
```

## 核心模块

### 对话大脑 (`naxida_voice_brain.py`)
- 系统提示词构建：人设 + 记忆 + RAG 检索结果 + 时间/节日上下文
- 多轮对话管理：`_trim_messages` 自动截断旧对话，摘要压缩长期记忆
- 联网搜索：判断是否需要搜索 → Bocha/DeepSeek Search → 结果清洗 → 注入上下文
- 流式输出：支持逐句流式 TTS 合成
- 情绪系统：gentle / happy / playful / thoughtful 四种情绪，影响 TTS 参考音频

### 记忆系统 (`memory.py`)
- **对话持久化**：SQLite 存全部对话，自动清理 90 天前和超量轮次
- **结构化用户画像**：四层分类
  - `stable`：长期事实（城市、学校、喜好），不过期
  - `temporary`：短期状态，14 天自动过期
  - `event`：有时间的事，60 天过期
  - `taboo`：用户不想聊的话题，绝不主动提起
- **自动提炼**：每 5 轮对话后，后台线程让 LLM 从用户发言中提取/更新/删除事实
- **对话摘要**：旧对话自动压缩成摘要注入上下文
- **多用户隔离**：不同 QQ 号互不串记忆

### RAG 知识库 (`rag/rag.py`)
- 混合检索：**向量检索 (bge-small-zh-v1.5)** + **BM25 关键词** → **bge-reranker-base 重排**
- 阈值过滤：向量相似度 < 0.68 直接跳过，日常聊天不触发 RAG
- 知识库内容：原神须弥剧情、纳西妲设定、AI 知识库等
- 候选少时跳过重排，省推理开销

### 模型额度轮换 (`llm_rotate.py`)
- 支持 12 个模型按优先级自动切换（DeepSeek / Qwen 等）
- 本地 token 记账，用到 90% 上限自动切下一个
- **绝不回落付费额度**——全部免费模型用尽时拒绝发请求
- 跨进程文件锁（QQ bot + web 同时跑不丢账）
- 原子写入 + 备份恢复

### QQ 接入层 (`bot/run_bot.py`)
- OneBot v11 WebSocket 协议对接 NapCat
- **用户分级权限**：
  - `IGNORE_LIST`：完全忽略
  - `RESTRICTED_LIST`：固定短语回应，不调模型
  - `GUARDED_LIST`：可聊天但冷淡，每日 token 预算 30K
  - 主人：无限制
- **NapCat watchdog**：端口不通自动重启 NapCat，每 2 分钟探活
- 单实例锁：防止重复启动双开
- 群聊 @ 才回复，私聊正常聊
- 消息分段：模拟真人打字节奏，不一次性甩长文本

### 语音系统
- **TTS**：GPT-SoVITS，4 种情绪参考音频，自动拉起服务（冷启动 20-30s）
- **ASR**：FunASR SenseVoiceSmall，QQ 语音消息转文字
- **后处理**：ffmpeg 增益 + 响度归一化（-10 LUFS）
- 自动分段：按句切分语音，避免长句 TTS 异常

### 主动陪伴 (`company.py`)
- 后台每 2 分钟检查一次，到点且用户没在聊时主动开口
- 基于真实对话记忆选话题，不编造
- 质量闸门：不合格的回复自动重写一次，还不行就不发

### 人格回归测试 (`tests/test_persona.py`)
- 18 个测试场景，11 条自动检查规则：
  - 网友腔（哈哈/绝了/笑死）
  - 套话（辛苦啦/注意身体/加油）
  - 假装有身体体验（踩到/闻到/翻书）
  - 索取式结尾（对不对/是吧/好不好）
  - 替对方断言事实
  - 问看法却反问回去
  - 长度超标 (>80 字)
- 每次改 prompt 跑一遍，防止人格退化

## 目录结构

```
naxida_ai/
├── naxida_voice_brain.py   # 对话大脑（主逻辑）
├── memory.py               # SQLite 记忆 + 画像提炼
├── llm_rotate.py           # 多模型额度轮换
├── company.py              # 主动陪伴调度
├── festival.py             # 节日问候
├── weather.py              # 天气查询
├── reminder.py             # 定时提醒
├── music_singer.py         # 点歌/RVC 变声
├── asr_server.py           # ASR 语音识别服务
├── mic_input.py            # 麦克风输入/热词纠错
├── config.json             # 配置（API Key / QQ 号 / 用户列表）
├── bot/
│   ├── run_bot.py          # QQ 机器人入口
│   └── qq_bot.py           # OneBot WebSocket 客户端
├── rag/
│   ├── rag.py              # 混合检索 RAG
│   └── data/               # 知识库文本 + 向量索引
├── intent/
│   ├── train.py            # 意图分类器训练
│   ├── predict.py          # 意图分类推理
│   └── generate_data.py    # 训练数据生成
├── web/
│   ├── app.py              # Flask Web 版后端
│   ├── templates/          # 前端页面（含 Live2D）
│   └── static/             # 静态资源
├── tests/
│   ├── test_persona.py     # 人格回归测试
│   ├── test_regression.py  # 记忆/意图回归测试
│   └── test_emotions.py     # 情绪测试
├── memory/                 # SQLite 数据库
├── state/                  # 运行状态（额度账本/预算/问候记录）
└── 素材/                   # TTS 参考音频 / 逐字稿
```

## 快速开始

### 前置依赖
- Python 3.10+
- [NapCat.QQ](https://github.com/NapNeko/NapCatQQ)（QQ 协议端）
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)（TTS 语音合成）
- CUDA GPU（RAG 向量化和 ASR 用，不强制）

### 安装
```bash
pip install -r requirements.txt
```

### 配置
复制并编辑 `config.json`：
```json
{
  "LLM_BASE_URL": "https://api.deepseek.com/v1",
  "LLM_API_KEY": "your-key",
  "LLM_MODEL": "deepseek-chat",
  "LLM_MODEL_LIST": ["deepseek-chat", "qwen-plus"],
  "NAPCAT_WS_URL": "ws://127.0.0.1:3001",
  "NAPCAT_TOKEN": "your-token",
  "OWNER_QQ": "你的QQ号"
}
```

### 启动 QQ 机器人
```bash
# 1. 先启动 NapCat QQ
# 2. 启动机器人
python bot/run_bot.py
```

### 启动 Web 版
```bash
python web/app.py
# 浏览器打开 http://127.0.0.1:5000
```

### 跑人格测试
```bash
python tests/test_persona.py
```

## 技术栈

| 层 | 技术 |
|---|---|
| LLM | DeepSeek / Qwen（百炼兼容模式） |
| RAG | bge-small-zh-v1.5 + BM25 + bge-reranker-base |
| 记忆 | SQLite + LLM 自动提炼 |
| TTS | GPT-SoVITS（多情绪参考音频） |
| ASR | FunASR SenseVoiceSmall |
| QQ | NapCat / OneBot v11 WebSocket |
| Web | Flask + Live2D |
| 意图分类 | BERT 微调 |
| 变声唱歌 | RVC + Demucs 人声分离 |

## 注意事项

- 同人项目，非官方，不用于商业用途
- QQ 接入请使用合规方式，勿公开分发违规客户端
- 不要上传隐私聊天记录，日志已做脱敏
- 模型额度用尽时自动切换，不会产生意外扣费
