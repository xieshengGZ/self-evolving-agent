# 版本规则

版本号格式：`MAJOR.MINOR.PATCH`，发布标签加 `v`，例如 `v1.2.0`。

| 位置 | 规则 |
|---|---|
| `MAJOR` | 主版本号。一个稳定阶段结束后，下一开发线提升主版本号。 |
| `MINOR` | 开发/稳定标记。**单数为开发中，偶数为稳定版**。 |
| `PATCH` | 同一稳定阶段内的功能更新，例如 `v1.2.1`。 |

稳定版必须通过 `make check`、完成线上回归，并更新 `docs/STATUS.md`。
开发版允许未完成项，但必须标明当前阶段和下一步。

## 当前状态

- 当前版本：**v1.2.0（stable）**
- 下一开发线：**v2.1.0（development）**
- 当前阶段：Phase 0 / 1 / 2 / 3 + P0 稳定性完成

## 发布记录

| 版本 | 日期 | 状态 | 内容 |
|---|---|---|---|
| `v1.2.0` | 2026-10-22 | stable | 稳定内核、插件热插拔、SSE + SQLite、规划状态机、上下文预算/artifact、P0 意图与计划护栏 |

## 发布流程

1. 更新根目录 `VERSION` 与 `app/version.py` 读取结果。
2. 更新 `README.md`、`AGENTS.md`、`docs/STATUS.md`、`docs/JOURNAL.md`。
3. 运行 `make check`，必要时做线上回归。
4. 提交并创建带注释标签 `git tag -a vX.Y.Z -m "..."`。
5. 推送提交和标签：`git push origin main --tags`。
