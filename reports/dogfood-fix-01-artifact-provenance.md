# Dogfood Fix 01 — Artifact Provenance Binding

## 结果与范围

修复产物发布、completion ledger、Task Detail 与 Artifact Center 之间的来源契约。没有修改 UI 布局、调度决策、Final Verification、HUMAN_REVIEW 或 STOP 语义；没有修改父 Builder Runtime，没有部署到现有 dogfood Runtime，也没有 push、merge、tag 或 release。

现有 dogfood 的 700100、700101、700102 共四条历史发布关联可以通过已有权威记录恢复，其中两条指向同一个 CSV。700103、700104 没有可恢复的新发布条目。这里只读核验历史，不改写任何历史 ledger。

## 根因：生产者与消费者的具体差异

原生产路径：

1. Executor 在 attempt workspace 写候选文件。
2. `executor_fence.publish()` 在 Runtime 锁内检查 claim token、完整身份、授权、项目、期限、STOP/HUMAN_REVIEW 等条件，核验候选 SHA-256，然后发布 canonical 文件。
3. Runtime 写入 `handoff/executor_publications/<COMMIT_ID>/<SHA256(path)>.json`。记录包含 `MESSAGE_ID / TASK_ID / STAGE_ID / ATTEMPT / NONCE / PROJECT_ID / path / sha256 / PUBLISHED_AT`。
4. staging 使用顶层 `EVIDENCE`、`DELIVERABLES` 列表，每项为 `{path, sha256}`。`RECEIPT` 是 Executor 报告。
5. completion commit 原本验证这些输出与 Runtime publication record 一致，但 ledger 只保存 `RECEIPT`、`RECEIPT_SHA256`、`BRIEF_SHA256`、`STAGING_MANIFEST_SHA256` 等信息，没有保存可直接读取的发布清单。原始 staging 通常被归档。
6. Artifact Center 与 Task Detail 分别只读取 `RECEIPT.PUBLISHED_PATHS`。生产流程没有生成该字段，测试却用手工构造的该字段模拟来源。因此真实发布成功、文件存在，但来源为空。
7. 旧 Artifact Center 索引按路径合并，并选择最大 MESSAGE_ID。即使 receipt 有路径，早期轮次也会失去该路径的筛选结果。

## 权威来源模型

新增的 ledger 字段由 Runtime 在现有 completion commit 锁内生成，Executor 无须修改 receipt 或 staging 协议：

```text
PUBLICATION_MANIFEST = {
  schema_version: 1,
  COMMIT_ID, PROJECT_ID,
  MESSAGE_ID, TASK_ID, STAGE_ID, ATTEMPT, NONCE,
  publications: [完整 Runtime publication record, ...]
}
PUBLICATION_MANIFEST_SHA256 = SHA256(canonical JSON(PUBLICATION_MANIFEST))
```

清单捕获本轮所有 Runtime publication records，包括 Executor 未列入 staging 输出列表的已发布文件。提交前验证完整身份、项目、路径、记录文件名、发布时间、哈希、重复路径、canonical 输出字节及文件安全边界。最多 128 个不同路径；每份证据读取有大小限制。完成清单读取后再次检查 fence，避免读取耗时使 claim 超期后仍被提交。验证失败时拒绝 commit，不产生 completion 事实。

清单嵌入同一个原子创建的 authoritative completion entry。`entry_hashes_intact()` 同时校验已有 receipt/brief 绑定与新增发布清单的摘要、schema、身份和项目绑定。清单存在但损坏或缺字段时，不退回旧来源。消费和封存 completion 不改变清单。

控制面 `feedback --json` 添加 `artifact_provenance`，包含 `integrity`、`source` 和 `publications`。两个 Console 页面共用同一个严格读取函数，只接收经过控制面验证的 Runtime 发布信息，不将 Executor 自报的 `PUBLISHED_PATHS` 当成发布证明。

## 同路径重复发布与内容校验

每个 `(COMMIT_ID, canonical project-relative path)` 生成独立 artifact ID，目录不再选择最大 MESSAGE_ID 合并来源。同一路径跨两轮发布，保留两个不同标识、各自的身份及 SHA-256；按任一 MESSAGE_ID 筛选都能找到对应条目。反馈引用也验证所选轮次与路径的精确绑定。

v1.3 对一次 attempt 内同路径多次 publish 仍沿用发布器现有的“最终记录”语义；本修复封存每轮每路径的最终发布记录，并保留跨轮历史，不声称提供每次中间写入的版本档案。

详情显示 MESSAGE、Task、Stage、Attempt、canonical 路径、发布时间、completion ID、发布 SHA-256、当前 SHA-256、来源及校验状态。历史内容不由本功能保存或提供。预览展示当前文件的有界快照，只有该快照的完整哈希等于所选发布记录的哈希时才允许展示；否则返回 `ARTIFACT_HASH_MISMATCH`（409），文件不存在则返回 404。哈希校验和响应内容取自同一次读取，避免验证后文件替换造成内容冒认。图片/PDF 原始字节路由使用相同规则。

没有权威发布关联的目录文件继续显示 unbound；文件名中的 MESSAGE_ID、时间戳或任务名称不会产生关联。

## 向后兼容与只读历史恢复

保留 `COMPLETION_PROTOCOL_VERSION = 1`、原 receipt 字节语义及已有 staging schema。旧 completion 缺少新清单时仍按原规则保持 completion 有效；来源能否恢复另行判断，不改变历史执行状态。

绑定产物的 artifact ID 改为 completion 与路径共同确定；调用方应从目录响应获取该 ID。旧的单路径 ID 不会被猜测映射到任意历史轮次。unbound 文件仍使用原路径 ID，页面的 MESSAGE_ID 导航方式保持不变。

恢复旧来源必须同时满足：

- 原 completion receipt/brief 的摘要与完整身份验证通过，COMMIT_ID 正确。
- 归档 staging 的原始字节 SHA-256 等于 ledger 中的 `STAGING_MANIFEST_SHA256`。
- staging 的完整身份、项目、schema、状态及 receipt 与 ledger 一致。
- `EVIDENCE` / `DELIVERABLES` 的路径和哈希存在匹配的 Runtime publication record，且该记录的完整身份和项目一致。

恢复范围仅限上述交叉验证覆盖的路径。缺失归档、损坏 JSON、重复 JSON 键、错误哈希、身份/项目不一致、同路径冲突，或同一 MESSAGE_ID 对应多个 completion entries，均不绑定来源。只出现 receipt 自报路径也不够。读取历史时不要求当前 canonical 字节等于旧哈希，否则无法诚实保留已被后续更新的历史发布。

现有 dogfood 核验结果：

| MESSAGE_ID | 路径 | 当前字节匹配历史发布 |
| --- | --- | --- |
| 700100 | `workspace/dogfood-note.md` | 是 |
| 700100 | `evidence/dogfood-rounds.csv` | 否；只保留该轮元数据，拒绝其历史预览 |
| 700101 | `evidence/dogfood-rounds.csv` | 是 |
| 700102 | `reports/final-verification-700102.json` | 是 |
| 700103 / 700104 | 没有可恢复的新发布条目 | 不据报告中的旧文件引用虚构新发布 |

完整 SHA-256 和证据清单见 `evidence/dogfood-fix-01/dogfood-history.json`。读取前后核验了 completion ledger、归档 staging、publication records 共 15 个文件，全部保持不变。该核验使用当前开发工作区的修复后读取器，未更新 dogfood 安装中的脚本；现有 dogfood 服务需使用修复版本后才能在页面上显示这些关联。

## 文件变更

- `scripts/executor_fence.py`：发布清单验证、收集与只读历史恢复；原发布授权入口保持原检查。
- `scripts/executor_completion.py`：在既有 commit 中封存清单，扩展 integrity 校验。
- `scripts/supervisor_control.py`：输出验证后的来源投影，拒绝歧义 MESSAGE_ID。
- `scripts/web_console_artifacts.py`：共用来源读取、每轮独立索引及精确轮次反馈绑定。
- `scripts/web_console_history.py`：Task Detail 使用相同来源契约。
- `scripts/web_console_server.py`：预览和原始字节响应使用同一次读取的哈希与内容。
- `web_console/index.html`：增加发布/当前哈希与历史内容说明，修正空来源提示；无布局重设计。
- `scripts/test_artifact_publication_provenance.py`：新增真实 Runtime/HTTP 回归。
- `scripts/test_web_console_artifacts.py`、`scripts/test_web_console_artifacts_http.py`、`scripts/test_web_console_history.py`、`scripts/test_web_console_timeline.py`：将假设性的 receipt 路径 fixtures 替换为 Runtime 验证后的来源投影，并验证独立历史条目。
- `scripts/test_web_console_artifacts_frontend.py`：校验新增来源信息与历史内容说明。
- 本报告及 `evidence/dogfood-fix-01/`：测试结果和只读历史核验记录。

## 验证

新增真实链路测试使用 disposable Runtime，执行真实 Supervisor dispatch 授权、claim、candidate、fence publish、completion commit、消费/封存、真实 `supervisor_control.py` 子进程和本地 HTTP 服务。未 mock 来源生产过程，也未手写 `PUBLISHED_PATHS` 使成功路径通过。

覆盖：新来源绑定、Task Detail 路径列表、MESSAGE_ID 目录筛选、同路径跨轮独立身份、旧哈希预览拒绝、新哈希预览通过、unbound 文件、遗漏于 staging 的已发布文件、清单持久性、只读历史恢复、缺失/损坏/冲突来源、receipt 冒充来源、歧义 ledger、commit 拒绝、预览读取期间 canonical 文件替换，以及清单读取期间 claim 过期。

测试结果：

| 测试范围 | 执行数 | 通过 | 跳过 | 失败/错误 |
| --- | ---: | ---: | ---: | ---: |
| 全量 `python -m unittest discover -s scripts -p 'test_*.py' -v` | 1270 | 1267 | 3 | 0 |
| 最终代码：全部非 `test_web_console*` Runtime 测试及新增来源测试 | 437 | 434 | 3 | 0 |
| 最终代码：`test_artifact_publication_provenance.py` | 10 | 10 | 0 | 0 |

全量运行完成后，最后增加了清单读取后的 fence 授权复核和一项过期回归；随后重新执行全部 Runtime 与来源测试（437 项）。Console 代码与全量通过时相同。三个跳过项均属于 `test_g5a5_1_hotfix`，原因是 clean Runtime 没有 active project pointer。测试命令全部退出 0。`git diff --check` 通过。

全量测试日志含 Windows 服务清理的 ResourceWarning / WinError 10038 消息，unittest 结果仍为 OK；原始输出保留，未将其删除或误报为额外测试通过。测试删除的 `web_console_data/instance.json` 已按运行前的受版本管理内容恢复，不属于修复改动。

精确模块计数、跳过项、命令、最终源文件 SHA-256 和日志位置记录于 `evidence/dogfood-fix-01/test-results.json`；同目录的 `full-suite.txt`、`final-runtime.txt`、`production-provenance.txt` 保存测试输出。
