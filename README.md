# CadJSON Studio

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![SolidWorks](https://img.shields.io/badge/SolidWorks-2024--2026-red.svg)](https://www.solidworks.com/)
[![Upstream](https://img.shields.io/badge/based%20on-SolidWorks%20Automation%20Skill-00599c.svg)](https://github.com/wzyn20051216/solidworks-automation-skill)

**用自然语言描述零件，AI 自动生成参数化 CAD JSON，一步驱动 SolidWorks 生成 3D 结构。**
自然语言需求 → 参数化 CAD JSON → 原生 SLDPRT + STEP + 自审查报告；建模、保存、导出、自审查由固定的执行器一次完成——**Agent 只写 JSON，引擎负责建模。**

> **二次开发声明**：本项目基于开源项目 [SolidWorks Automation Skill](https://github.com/wzyn20051216/solidworks-automation-skill)（MIT License，作者 wzyn20051216 及其贡献者）进行二次开发。上游提供了完整的 SolidWorks COM API 封装、能力清单、自审查与交付体系、MCP Server 和桌面客户端；本仓库在其之上引入 **参数化 CAD JSON 声明式建模管线**，把 AI 建模从「逐行生成 Python 脚本」改为「生成一份结构化 JSON、由已验证引擎一次执行」。完整改动清单见[与上游的差异](#与上游的差异本仓库二次开发清单)。

---

## 为什么做 CadJSON Studio

上游项目已经把 CAD 自动化里最难的部分做完了：

- 稳定的 `scripts/sw_*.py` SolidWorks COM API 封装（真机验证）
- 以 `capabilities.yaml` 为唯一真源的能力等级清单与多语言后端路由
- 自审查（`sw_review`）、交付清单与哈希账本、MCP Server、CAD Studio 桌面端

但 AI 驱动建模的「最后一公里」仍然建立在**逐行生成代码**上，这带来三类结构性开销：

| 逐行脚本 / 逐条工具调用模式 | 后果 |
| --- | --- |
| Agent 每次都要生成 import、会话管理、COM 调用、审查、导出的全套样板 | 输出 token 随特征数线性膨胀，真正有信息量的只有尺寸和特征序列 |
| 每一行 API 调用都是潜在幻觉点：参数顺序、枚举值、单位制 | 生成即风险，错一个参数就要整轮重跑 |
| MCP 逐条工具调用时，一个 N 特征零件需要 N 次往返（SolidWorks COM 还在全局锁下串行） | 往返次数 = 特征数 |
| 中间失败要诊断的是「脚本/调用哪里错了」，而不是「建模意图哪里错了」 | 排错对象错位 |

### 我们的方案：把「建模意图」和「执行引擎」分离

```text
自然语言需求
   │  AI Agent —— 只产出意图层（一份 JSON）
   ▼
参数化 CAD JSON（.json）
   │  sw_build_from_json.py —— 固定已验证引擎，单进程、单次连接
   ▼
SolidWorks COM（attach-only 附着运行中实例）
   ▼
SLDPRT + STEP + *_review_report.json + 自审查预览图
```

效率提升来自机制设计，而不是参数微调：

1. **意图层最短**。JSON 只包含 `parameters` 和有序 `features`，没有任何 import、会话或 COM 样板。以 `examples/cell_cover_plate.json` 为例：5 个特征的完整零件定义（含输出配置）总共 59 行，其中建模意图约 30 行。
2. **幻觉面收敛**。Agent 可写的字段被限定在 v1 规格内；执行器逐条调用上游真机验证过的 `sw_part.py` 特征函数，API 参数顺序、枚举值、毫米→米换算都不再经过 LLM。
3. **一次连接、一次审查**。整个零件在同一个 SolidWorks 会话里连续建模，全部特征完成后只做一次自审查并导出；默认 `attach_only` 附着已运行实例，绝不冷启动新实例。N 个特征 = 1 次执行，而不是 N 次工具往返。
4. **参数化可重建**。所有尺寸通过 `$参数名` 引用 `parameters`，改一处数值重跑同一命令即可重建整个零件，不重新生成任何代码。

## 核心方案：参数化 CAD JSON

一份 CAD JSON 就是一个零件的完整定义（示例摘自 `examples/cell_cover_plate.json`）：

```json
{
  "schemaVersion": "1.0",
  "documentId": "cell_cover_plate",
  "title": "电芯盖板（方形铝壳，占位参数）",
  "units": "mm",
  "parameters": {
    "cover_L": 148.0, "cover_W": 27.0, "cover_T": 2.0,
    "pos_X": 40.0, "pos_D": 8.0, "pos_H": 5.0,
    "face_pick_X": 70.0
  },
  "features": [
    {
      "id": "cover_plate",
      "type": "extrude",
      "operation": "add",
      "params": { "sketch": "rect", "w": "$cover_L", "h": "$cover_W", "depth": "$cover_T" }
    },
    {
      "id": "pos_terminal",
      "type": "extrude",
      "operation": "add",
      "params": { "sketch": "circle", "face": ["$face_pick_X", "$cover_T", 0], "cx": "$pos_X", "cy": 0, "d": "$pos_D", "depth": "$pos_H" }
    }
  ],
  "output": {
    "dir": "C:/temp/cell_cover_plate",
    "part": "cell_cover_plate.sldprt",
    "step": "cell_cover_plate.step"
  }
}
```

### v1 规格速览

| 字段 | 说明 |
| --- | --- |
| `units` | 仅支持 `mm`（调用 SolidWorks API 时统一转米） |
| `parameters` | 数值字典；`features` 内以 `$name` 引用 |
| `features[].type` | `extrude`（v1 唯一特征类型） |
| `features[].operation` | `add` 凸台 / `cut` 切除 |
| `features[].merge` | `false` 生成独立实体（多实体建模），默认 `true` |
| `params.sketch` | `rect` / `circle` / `circles` / `slot` |
| `params.plane` | 草图基准面，默认 `Top Plane` |
| `params.face` | `[x, y, z]`（mm 世界坐标）：选中该点所在面再画草图，用于在顶面等已有面上定位特征 |
| `params.depth` | 拉伸深度（mm） |
| `params.direction` | 凸台默认 `false`（+法向，Top Plane 为 +Y 向上）；切除默认 `true`（-法向，切进材料） |
| `params.through` | 切除贯穿全深 |
| `params.start_offset` | 从草图平面偏移该距离后再拉伸（mm），用于盖板等顶置特征 |
| `params.midplane` | `true` 沿草图平面两侧对称拉伸，`depth` 为总深度 |
| `output` | 输出目录与文件名；命令行 `--out-dir` 可覆盖 |

草图几何约定：`Top Plane` = XZ 平面，+Y 为「上」；`rect` 以原点为中心（`w` 沿 X、`h` 沿 Z）；`circle` 用 `(cx, cy)` + 直径 `d`；`circles` 为 `[{cx, cy, d}, ...]` 列表；`slot`（跑道形/长圆槽）的 `r` 为半宽、`half_len` 为直线段半长、`dir` 为中心线方向（`x`/`y`）。

## 快速开始

### 前置条件

- Windows 10/11，Python 3.10+
- 已安装并至少启动过一次 SolidWorks（2024 / 2026 已真机验证）
- 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

### 运行第一个示例

先手动打开 SolidWorks（执行器默认只附着已运行实例，不冷启动）：

```powershell
python scripts/sw_build_from_json.py examples/cell_cover_plate.json
python scripts/sw_build_from_json.py examples/cell_314.json --out-dir D:/out/cell_314
```

完成后输出目录包含：`*.sldprt`、`*.step`、`<documentId>_review_report.json`、`<documentId>_review_summary.md` 和自审查预览图。

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--out-dir` | 覆盖 JSON 中的 `output.dir` |
| `--views` | 自审查视角，默认 `isometric`，可写 `front,top,right,isometric` |
| `--allow-launch` | 附着失败时允许冷启动新 SolidWorks 实例（默认禁止） |
| `--close-after` | 结束后关闭本次创建的文档（默认保留在 SolidWorks 中） |
| `--no-step` | 不导出 STEP |
| `--version` | 指定 SolidWorks 年份（如 `2024`），默认自动检测 |

### 参数化重建

修改 JSON 中 `parameters` 的数值，重跑同一命令即可——不修改特征、不重新生成任何代码。

## 已验证示例

| 示例 | 特征数 | 说明 |
| --- | --- | --- |
| `examples/cell_314.json` | 9 | 314 方壳电芯多实体建模：铝壳与型腔、跑道形芯体（`Right Plane` 草图，双向对称拉伸、`merge: false` 独立实体）、顶置盖板（`start_offset`）、正/负极柱、防爆阀槽、注液孔 |
| `examples/cell_cover_plate.json` | 5 | 方形电芯盖板：基板、注液孔（贯穿切除）、正/负极柱、防爆阀膜片；所有顶面特征通过 `face` 世界坐标拾取定位 |

> 示例中的尺寸为演示管线的占位参数，不作为真实电芯设计依据；产出的零件用于制造前仍需工程师复核。

## 面向 Agent 的使用约定

- 需求能用「拉伸体 + 孔/槽」表达时，**优先产出 CAD JSON**，而不是逐行生成 Python 脚本或逐条调用 MCP 工具。
- 超出 v1 特征面（放样、抽壳、钣金、装配、工程图等）的需求，回退到上游继承的工作流：`scripts/sw_*.py`、`subskills/` 子技能、或 MCP Server 对应工具。
- JSON 必须自包含：所有尺寸进 `parameters`，特征内只引用；一个 JSON 对应一个零件文档，多实体用 `merge: false` 表达。
- 退出码：`0` 成功；`2` 自审查结论为 `fail`——此时读取 `*_review_report.json`，修正参数或特征后重试。

## 与上游的差异（本仓库二次开发清单）

| 文件 | 改动 | 目的 |
| --- | --- | --- |
| `scripts/sw_build_from_json.py` | **新增** | CAD JSON 执行器：单次连接、按序执行特征、末尾一次自审查、SLDPRT + STEP 输出 |
| `scripts/sw_connect.py` | 新增 `connect_solidworks(attach_only=...)` 附着模式；新增 `close_document(discard_unsaved=...)` | 自动化默认绝不冷启动新实例；关闭文档不弹「是否保存」对话框，不阻塞流程 |
| `scripts/sw_part.py` | 修复 `sketch_slot` 宽度语义（半宽 → 全宽，并显式指定 CenterCenter）；`extrude_boss` 新增 `start_offset`；`extrude_midplane` 新增 `merge` 参数 | 槽口几何正确性；盖板等顶置拉伸；多实体建模 |
| `examples/cell_314.json`、`examples/cell_cover_plate.json` | **新增** | 电芯结构件真实案例，作为 CAD JSON 的参考实现 |

## 继承自上游的能力

除上述差异外，本仓库完整继承上游能力体系（细节见[上游 README](https://github.com/wzyn20051216/solidworks-automation-skill#readme)）：

- **Skill / CLI**：`scripts/sw_*.py` 全套 SolidWorks COM 封装——零件、孔槽、装配、工程图、导出、外观、Motion Study、配置族、钣金、焊件、齿轮
- **MCP Server**：`mcp-server/server.py`，供 Codex / Claude Code / Cursor / Windsurf 等客户端本地调用
- **CAD Studio 桌面端**：React + Tauri 客户端（`apps/workbench-ui`）、PySide6 原型（`apps/desktop`）、本地自动化队列与 Python worker
- **子技能矩阵**：VibeCAD 规划、CNC 圆角倒角、螺纹孔、GB/T 工程图、AutoCAD（`subskills/`，见 [SUBSKILLS.md](SUBSKILLS.md)）
- **无 CAD 开放格式后端**：OCCT 无头写入 STEP/IGES/BREP/STL/OBJ/GLB，二维后端写入 DXF/SVG/PDF/PNG——不装 SolidWorks 也能出开放格式
- **试点能力**：CalculiX FEA 求解管线、DFM 制造复核、Routing 中性复核
- **桌面端用户手册**：[docs/CAD_STUDIO_USER_MANUAL.md](docs/CAD_STUDIO_USER_MANUAL.md)

## 可靠性边界

与上游保持同一纪律：`capabilities.yaml` 是能力唯一真源，等级为 `verified` / `pilot` / `reference_only` / `not_implemented`；`reference_only` 与 `not_implemented` 不会被包装成无人值守交付，`pilot` 结果必须经人工复核。CAD JSON v1 目前只覆盖拉伸族特征（凸台/切除 × 矩形/圆/多圆/槽口草图），超出部分会被执行器显式拒绝而不是猜测执行。

## Roadmap

- [ ] CAD JSON v2：倒角/圆角、抽壳、拔模、筋等特征类型
- [ ] JSON Schema 正式化 + 执行前校验（缺参、越界、引用不存在参数时提前报错）
- [ ] 装配级 CAD JSON：多零件 + Mate 关系
- [ ] 工程图 JSON：三视图 + 尺寸链驱动出图
- [ ] 将 CAD JSON 特征覆盖度纳入上游 `capabilities.yaml` 能力清单

## License 与致谢

- 本项目延续 **MIT License** 开源，并保留上游的版权与许可声明（见 [LICENSE](LICENSE)）。
- 感谢上游项目 [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill) 及其贡献者提供的 SolidWorks 自动化基座——`scripts/sw_*.py` 封装层、能力清单体系、自审查与交付管线均来自上游；本仓库在其上新增的 CAD JSON 管线同样以 MIT 协议开源回馈社区。

---

## English Summary

**CadJSON Studio** is a secondary development of the open-source [SolidWorks Automation Skill](https://github.com/wzyn20051216/solidworks-automation-skill) (MIT). Upstream ships a battle-tested SolidWorks COM layer, a capability manifest (`capabilities.yaml`), a self-review/delivery pipeline, an MCP server, and a desktop client. This fork adds a **declarative CAD JSON pipeline**: the AI agent emits only a parameterized JSON document (`parameters` + ordered `features`, no code), and a fixed executor (`scripts/sw_build_from_json.py`) drives SolidWorks in a single attached session — building every feature through upstream-verified APIs, saving SLDPRT, exporting STEP, and running one self-review at the end. This removes per-line script generation from the loop: no boilerplate tokens, no API-parameter hallucinations, no per-feature tool round-trips, and full parametric rebuilds by editing JSON values. Battery-cell examples are included (`cell_314.json` multi-body prismatic cell, `cell_cover_plate.json`). All upstream capabilities are inherited; the honesty boundary stays the same — `capabilities.yaml` remains the single source of truth and `pilot`-level results always require human review. Licensed MIT with full attribution to the upstream project.
