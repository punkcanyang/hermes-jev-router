# jev-router — Hermes × Jev（TypeSafe 直连）

给 Hermes Agent 加两件事：

1. **模型路由**：每轮调用前用 TypeSafe **Jev** 选模型（简单走便宜、复杂／工具重／长上下文走更强），失败或低置信回退主模型。
2. **先裁再压**：上下文引擎 `trim_compress` 强制 **trim → compress**，禁止不裁直接压。

硬规矩：只 TypeSafe 直连（`TYPESAFE_API_KEY`）；禁止 Vercel AI Gateway；密钥不进仓、不打印。本仓在共享 Linux box 上自用，不动 Mac。

---

## 前置条件

1. **Hermes CLI 已安装**（示例：官方一键，可跳过浏览器）

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
export PATH="$HOME/.local/bin:$PATH"
hermes --version
```

2. **主模型已配好**（本 box 现用 DeepSeek）

- `~/.hermes/.env` 里有 DeepSeek Key（或你自己的 provider）
- `~/.hermes/config.yaml` 里例如：`provider: deepseek`，`model.default: deepseek-flash`

3. **TypeSafe Key 在环境里**（路由评测与线上路由都要）

```bash
# 已有则跳过；不要把值写进 git 或聊天
export TYPESAFE_API_KEY=...   # 或写入 ~/.hermes/.env，由 Hermes 加载
```

---

## 安装插件（约 1 分钟）

在本仓根目录执行：

```bash
export PATH="$HOME/.local/bin:$PATH"
cd /path/to/hermes-jev-router
bash scripts/install_local.sh
```

脚本会：

- 把本仓 **symlink** 到 `~/.hermes/plugins/jev-router`
- 若没有则复制 `config.example.yaml` → `~/.hermes/jev-router.yaml`
- 在 `~/.hermes/config.yaml` 启用 `plugins.entries.jev-router`
- 若当前 `context.engine` 为空或 `compressor`，写成 **`trim_compress`**

手工等价：

```bash
ln -sfn "$(pwd)" ~/.hermes/plugins/jev-router
hermes plugins enable jev-router
# 编辑 ~/.hermes/config.yaml：
#   context:
#     engine: trim_compress
```

确认：

```bash
hermes plugins list --plain | grep -i jev
hermes plugins doctor ~/.hermes/plugins/jev-router
# 期望：hooks 已注册（现为 3 个）；无阻断错误
```

---

## 日常使用

装好、开着路由时，**照常开 Hermes 对话**即可，不必多敲命令：

```bash
export PATH="$HOME/.local/bin:$PATH"
hermes          # 或你平时用的子命令／入口
```

每轮大致流程：

1. `pre_llm_call`：Jev 根据本轮消息等做 Choice，得到标签（如 `cheap` / `primary` / `complex` …）与目标模型。
2. 请求中间件把本轮 `model` 改成选型结果；超时、异常、置信度低于阈值 → **主模型**（默认 `deepseek-flash`）。
3. 需要压缩时走 `trim_compress`：先按配置裁掉过旧噪声、保留最近 N 轮，再交给压缩步。

事件日志默认：`~/.hermes/jev-router/events.jsonl`（路径可在配置里改）。

---

## 开 / 关

| 作用 | 推荐环境变量 | 其它写法 | 效果 |
|------|----------------|----------|------|
| 关路由 | `JEV_ROUTER_ROUTING_ENABLED=false` | `HERMES_JEV_ROUTING=0`，或配置里 `routing_enabled` / `enabled: false` | 不改模型，一直用主模型 |
| 关裁剪 | `JEV_ROUTER_TRIM_ENABLED=false` | `HERMES_JEV_TRIM=0`，或 `trim_enabled: false` | 跳过 trim；compress 仍可跑 |
| 卸整插件 | `hermes plugins disable jev-router` | 并把 `context.engine` 改回 `compressor` | 完全回退 Hermes 默认行为 |

示例（当前 shell 临时关路由再测）：

```bash
JEV_ROUTER_ROUTING_ENABLED=false hermes …
# 或评测脚本同环境变量 → 样本应全部落在 primary
```

---

## 配置

可改三处（后者覆盖前者的同名字段，以实际加载为准）：

1. `~/.hermes/jev-router.yaml`（安装脚本从 `config.example.yaml` 复制）
2. `~/.hermes/config.yaml` → `plugins.entries.jev-router.settings`
3. 环境变量（见下）

常用字段（默认见 `plugin.yaml` / `settings.py`）：

| 字段 | 默认 | 含义 |
|------|------|------|
| `routing_enabled` | true | 是否路由 |
| `trim_enabled` | true | 是否先裁 |
| `min_confidence` | 0.55 | 低于此置信度 → 主模型 |
| `timeout_seconds` | 8.0 | Jev 超时秒数 |
| `primary_model` | deepseek-flash | 主模型／兜底 |
| `cheap_model` | deepseek-flash | 简单任务 |
| `complex_model` | deepseek-chat | 复杂任务 |
| `tool_heavy_model` | deepseek-chat | 工具重 |
| `long_context_model` | deepseek-chat | 长上下文 |
| `keep_last_n_turns` | 6 | trim 保留最近 N 轮 |
| `drop_old_tool_noise` | true | 丢弃过旧工具噪声 |

环境变量（与字段对应）：

```text
TYPESAFE_API_KEY
JEV_ROUTER_ROUTING_ENABLED / HERMES_JEV_ROUTING
JEV_ROUTER_TRIM_ENABLED / HERMES_JEV_TRIM
JEV_ROUTER_MIN_CONFIDENCE
JEV_ROUTER_TIMEOUT
JEV_ROUTER_PRIMARY_MODEL（或 JEV_ROUTER_MAIN_MODEL）
JEV_ROUTER_CHEAP_MODEL
JEV_ROUTER_COMPLEX_MODEL
```

**必须显式**（安装脚本通常已写好）：

```yaml
# ~/.hermes/config.yaml
context:
  engine: trim_compress
```

引擎名必须是 `trim_compress`（不要写成旧文档里的 `jev-trim-compress`）。

---

## 怎么验（复测）

```bash
export PATH="$HOME/.local/bin:$PATH"
cd /path/to/hermes-jev-router

# 1) 装／刷新 symlink + enable
bash scripts/install_local.sh

# 2) 插件健康
hermes plugins list --plain | grep -i jev
hermes plugins doctor ~/.hermes/plugins/jev-router --ci

# 3) 路由对照（live TypeSafe，≥20 条标注样本）
python3 scripts/eval_routing.py --force-live
# → reports/routing-eval.md（本地生成）

# 4) trim→compress 顺序证明
python3 scripts/prove_trim_compress.py
# → reports/trim-compress-proof.md（本地生成）（看 order_ok）

# 5) 关路由 smoke（应全部 primary）
JEV_ROUTER_ROUTING_ENABLED=false python3 scripts/eval_routing.py --force-live
```

---

## 已知限制（诚实）

- 压缩步当前用于离线顺序证明时，是**确定性摘要**，不是完整「肉眼聊一整轮」验收。
- 主对话里切模型依赖已注册的 hooks／middleware；`hermes plugins doctor` 能看到 hooks，但**未强制**再做一整轮交互 chat 肉眼确认。
- 若 `cheap` 与 `primary` 配成同一模型 id，路由对照仍可跑，但体感上「没换模型」。
- `hermes doctor` 可能仍报未配的可选 Key／依赖，与本插件无关，可不挡使用。
- 禁止把买家大模型 Key 代持进本方案；本阶段自用、$0 新开销外不花。

---

## 目录

```text
plugin.yaml                 # 插件清单与 config_schema
LICENSE                     # MIT
__init__.py                 # register()：引擎 + hooks
routing.py / engine.py / trim.py / settings.py / events.py
jev_router/flags.py         # 开／关与配置合并（含旧 env 别名）
data/routing_samples.jsonl  # ≥20 条路由对照样本
scripts/install_local.sh
scripts/eval_routing.py
scripts/prove_trim_compress.py
config.example.yaml
```

---

