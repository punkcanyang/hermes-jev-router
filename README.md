# jev-router — Hermes × Jev（TypeSafe 直连）

<p align="center">
  <img src="./docs/readme-assets/hero.svg" width="100%" alt="jev-router: TypeSafe Jev model routing and trim-then-compress for Hermes">
</p>

给 [Hermes Agent](https://hermes-agent.nousresearch.com/) 加两件事：

1. **模型路由**：每轮调用前用 TypeSafe **Jev** 选模型（简单走便宜、复杂／工具重／长上下文走更强），失败或低置信回退主模型。
2. **先裁再压**：上下文引擎 `trim_compress` 强制 **trim → compress**，禁止不裁直接压。

硬规矩：只 TypeSafe 直连（环境变量 `TYPESAFE_API_KEY`）；**禁止** Vercel AI Gateway；密钥不进仓、不打印。

<p align="center">
  <img src="./docs/readme-assets/architecture.svg" width="100%" alt="Hermes chat → Jev routing → trim_compress">
</p>

## 前置条件

1. **Hermes CLI 已安装**

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
export PATH="$HOME/.local/bin:$PATH"
hermes --version
```

2. **主模型已配好**（示例：DeepSeek）

- `~/.hermes/.env` 里有对应 provider Key
- `~/.hermes/config.yaml` 例如：`provider: deepseek`，`model.default: deepseek-flash`

3. **TypeSafe Key 在环境里**

```bash
export TYPESAFE_API_KEY=...   # 或写入 ~/.hermes/.env，由 Hermes 加载；不要把值写进 git
```

## 安装插件（约 1 分钟）

在本仓根目录：

```bash
export PATH="$HOME/.local/bin:$PATH"
cd /path/to/hermes-jev-router
bash scripts/install_local.sh
```

脚本会：symlink 到 `~/.hermes/plugins/jev-router`；必要时复制 `config.example.yaml` → `~/.hermes/jev-router.yaml`；启用 `plugins.entries.jev-router`；把空的／`compressor` 的 `context.engine` 写成 **`trim_compress`**。

手工等价：

```bash
ln -sfn "$(pwd)" ~/.hermes/plugins/jev-router
hermes plugins enable jev-router
# 编辑 ~/.hermes/config.yaml → context.engine: trim_compress
```

确认：

```bash
hermes plugins list --plain | grep -i jev
hermes plugins doctor ~/.hermes/plugins/jev-router
# 期望：hooks 已注册（现为 3 个）；无阻断错误
```

## 日常使用

装好后照常开 Hermes 即可：

```bash
hermes
```

每轮大致：`pre_llm_call` → Jev Choice（如 `cheap` / `primary` / `complex`）→ 改写本轮 `model`；超时／异常／低置信 → 主模型；压缩时走 `trim_compress`。

事件日志默认：`~/.hermes/jev-router/events.jsonl`。

## 开 / 关

| 作用 | 推荐环境变量 | 效果 |
|------|----------------|------|
| 关路由 | `JEV_ROUTER_ROUTING_ENABLED=false` | 一直用主模型 |
| 关裁剪 | `JEV_ROUTER_TRIM_ENABLED=false` | 跳过 trim；compress 仍可跑 |
| 卸整插件 | `hermes plugins disable jev-router` | 回退 Hermes 默认 |

## 配置要点

可改：`~/.hermes/jev-router.yaml`、`plugins.entries.jev-router.settings`、环境变量。常用字段默认见 `plugin.yaml` / `settings.py`（如 `min_confidence=0.55`、`timeout_seconds=8`、`primary_model=deepseek-flash`、`keep_last_n_turns=6`）。

引擎名必须是 `trim_compress`（不要用旧文档里的 `jev-trim-compress`）。

## 怎么验

```bash
cd /path/to/hermes-jev-router
bash scripts/install_local.sh
hermes plugins doctor ~/.hermes/plugins/jev-router --ci
python3 scripts/eval_routing.py --force-live          # → reports/routing-eval.md
python3 scripts/prove_trim_compress.py                # → reports/trim-compress-proof.md
JEV_ROUTER_ROUTING_ENABLED=false python3 scripts/eval_routing.py --force-live
```

## 已知限制

- 离线顺序证明里的压缩步是**确定性摘要**，不是完整肉眼聊一轮验收。
- hooks 可见不等于已强制做完整交互 chat 确认。
- `cheap` 与 `primary` 若配成同一 model id，对照仍可跑，但体感「没换模型」。
- 禁止把买家大模型 Key 代持进本方案。

## 目录

```text
plugin.yaml  LICENSE  __init__.py
routing.py  engine.py  trim.py  settings.py  events.py
jev_router/flags.py
data/routing_samples.jsonl
scripts/install_local.sh  eval_routing.py  prove_trim_compress.py
config.example.yaml
```

## License

MIT — 见 `LICENSE`。

## 自动同意（可选，默认关）

Hermes 走到共享人审闸前，可先问 TypeSafe Jev。**仅高置信 `approve` → 宿主 `once`（只本次）**；`deny`／`unsure`／低置信／畸形／超时／异常 → `needs_human`（原人审；无人则 fail-closed）。**不**做 session／always，**不**复用 YOLO／`approvals.mode: off`。

```bash
# 配置（~/.hermes/jev-router.yaml 或 plugins.entries.jev-router.settings）
auto_approve_enabled: true
auto_approve_confidence: 0.80

# 或环境变量
export JEV_ROUTER_AUTO_APPROVE_ENABLED=true
# $0 演示（不打 TypeSafe）
export JEV_ROUTER_AUTO_APPROVE_MOCK=approve   # deny|unsure|low_confidence|malformed|timeout|error
```

硬禁第二开关（默认关；用户 `approvals.deny` **永不**绕过）：

```bash
export JEV_ROUTER_AUTO_APPROVE_HARDLINE_ENABLED=true
# 必须先风险确认（写 ~/.hermes/jev-router/hardline-ack.json）：
python -c "from auto_approve import write_hardline_risk_ack; write_hardline_risk_ack()"
```

日志：`~/.hermes/jev-router/events.jsonl`（`decided_by: jev`；无原始命令／密钥）。演示脚本见 `notes/jev-auto-approve/`。

