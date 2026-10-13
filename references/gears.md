# 齿轮实体建模与装配联动路由

## 先判断用户要的是什么

| 用户意图 | 正确路线 | 禁止做法 |
|---|---|---|
| 画齿轮、建齿轮零件、生成真实齿形 | 标准外啮合直齿轮使用 `scripts/sw_gear.py`；其他齿制转专用建模路线 | 用 Gear Mate 代替齿轮实体 |
| 两个齿轮联动、可拖动传动、配合比 | `sw_assembly.add_gear_mate_by_cylinders()` | 用脚本假动画冒充 Gear Mate |
| 工业生产级齿轮 | 确认齿制、变位、侧隙、公差、修形、材料与强度，使用专用齿轮工具并工程师复核 | 把可视化齿形宣称为可投产齿形 |

用户只说“画个齿轮”时，不要因为启用了 Skill 就自动跳到装配配合。制造任务必须取得模数、齿数、压力角、齿宽和轴孔尺寸；仅用于视觉演示时，可使用默认参数，但必须在结果中明示。

## 当前可用范围

`SpurGearSpec` 仅覆盖标准全齿高、外啮合、直齿、渐开线圆柱齿轮：

- 分度圆直径：`d = m * z`
- 基圆直径：`db = d * cos(alpha)`
- 齿顶圆直径：`da = d + 2m`
- 齿根圆直径：`df = d - 2.5m`
- 分度圆齿厚：`s = pi * m / 2`

齿面使用基圆渐开线 `inv(alpha) = tan(alpha) - alpha` 采样。当齿根圆低于基圆时，当前实现使用径向过渡和齿根圆弧，并不是滚刀实际生成的 trochoid 齿根。脚本会报告理论根切风险，但不会自动做变位设计。

## 最小调用

```python
from sw_gear import SpurGearSpec, create_external_spur_gear

spec = SpurGearSpec(
    module_mm=2.0,
    teeth=24,
    pressure_angle_deg=20.0,
    face_width_mm=10.0,
    bore_diameter_mm=10.0,
    involute_segments=14,
)
feature, evidence = create_external_spur_gear(model, spec)
```

`evidence` 返回解析尺寸、根切风险、轮廓密度和已知限制。必须再使用 `collect_geometry_measurements()` 从 SolidWorks B-Rep 回读齿顶包围尺寸与中心孔，并执行保存、STEP 导出、四视图目视和关闭重开验证。

## 已验证案例

SolidWorks 2026 SP1.1（Revision 34.1.1）真机案例：`m=2`、`z=24`、`alpha=20°`、齿宽 `10 mm`、轴孔 `10 mm`。结果为单实体，创建后与重开后 B-Rep 均回读 `52 × 52 × 10 mm`，内圆柱面直径 `10 mm`，SLDPRT 与 STEP 均成功落盘，四视图规则审查为 `pass/100`。因为生产齿根、侧隙和公差未覆盖，能力仍保持 `pilot` 和 `review_required`。

## 不得自动宣称支持的范围

- 斜齿轮、人字齿、锥齿轮、内齿轮、齿条、蜗轮蜗杆；
- 变位系数、侧隙、齿顶/齿向修形、精确滚刀齿根；
- ISO/GB/AGMA 公差等级、材料、热处理、接触/弯曲强度或 NVH 验证；
- 把高采样密度当作无成本默认：弦线段越多，SolidWorks 建模、STEP 导出和 B-Rep 遍历越慢。

## 参考

- KHK，Calculation of Gear Dimensions：<https://khkgears.net/gear-knowledge/gear-technical-reference/calculation-gear-dimensions/>
- SOLIDWORKS API Help，`ISketchManager::CreateSpline3`：<https://help.solidworks.com/2021/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISketchManager~CreateSpline3.html>

`CreateSpline3` 参考仅用于记录平滑曲线试验边界：SW2026 中强类型 `SAFEARRAY(double)` 可创建样条，但当前整圈齿廓仍不能稳定拉伸。因此已验证路线保留弦线离散，不把“COM 返回了样条对象”冒充成功实体。
