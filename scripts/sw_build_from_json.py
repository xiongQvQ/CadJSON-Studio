"""
从参数化 CAD JSON 一次性驱动 SolidWorks 原生建模。

用法:
    python scripts/sw_build_from_json.py examples/cell_cover_plate.json

设计目标:
    - 一个 Python 进程，只连接一次 SolidWorks（默认只附着，不冷启动新实例）
    - 按 features 顺序调用 sw_part.py 已验证的特征函数
    - 只在末尾做一次自审查（默认 isometric 单视角）并导出 STEP
    - 结束后默认保留本次新建的文档在 SolidWorks 中；传入 --close-after 才关闭

JSON 约定:
    - 顶层: schemaVersion / documentId / title / units(仅 mm) / parameters / features / output
    - parameters 的数值可在 features.params 中用 $name 引用
    - v1 特征类型: extrude(add|cut, sketch=rect|circle|circles|slot)
      - slot: 跑道形/长圆槽，r=半宽、half_len=直线段半长、dir="x"|"y"（中心线方向）、cx/cy=中心
      - merge: false 生成独立实体（多实体建模，特征级字段）
      - start_offset: 从草图平面偏移该距离（mm）后开始拉伸
      - midplane: true 沿草图平面两侧对称拉伸（中面），depth 为总深度，忽略 direction

坐标系约定（Top Plane = XZ 平面，+Y 为“上”):
    - rect 以原点为中心，w 沿 X，h 沿 Z
    - circle 用 (cx, cy) 定位，d 为直径；circles 用列表 [{cx,cy,d}, ...]
    - 所有尺寸单位 mm，调用 SolidWorks API 时统一转米
    - 可选 params.face = [x, y, z]（mm 世界坐标）：先选中该点所在面再画草图，
      用于把极柱/孔画在盖板顶面；不传 face 则画在 params.plane（默认 Top Plane）
    - params.direction（凸台默认 false，切除默认 true）：false=+法向（Top Plane 为 +Y 向上），
      true=-法向；切除默认向下切进材料
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from sw_connect import close_document, connect_solidworks, create_empty_dispatch_variant, get_com_member, mm, new_document, save_document  # noqa: E402
from sw_export import export_to_step  # noqa: E402
from sw_part import (  # noqa: E402
    current_sketch_name,
    end_sketch,
    extrude_boss,
    extrude_cut,
    extrude_midplane,
    sketch_circle,
    sketch_rectangle,
    sketch_slot,
    start_sketch,
)
from sw_review import run_review  # noqa: E402

_PARAM_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _num(value, default=0.0):
    return float(value) if value is not None else float(default)


def _resolve(value, params):
    if isinstance(value, str):
        return _PARAM_REF.sub(lambda match: str(params[match.group(1)]), value)
    if isinstance(value, dict):
        return {key: _resolve(val, params) for key, val in value.items()}
    if isinstance(value, list):
        return [_resolve(val, params) for val in value]
    return value


def _draw_sketch(model, params):
    """进入草图并绘制轮廓，返回草图名。"""
    kind = str(params.get("sketch") or "").lower()
    face = params.get("face")

    if face:
        fx, fy, fz = (mm(float(value)) for value in face)
        if not model.Extension.SelectByID2("", "FACE", fx, fy, fz, False, 0, create_empty_dispatch_variant(), 0):
            raise RuntimeError(f"无法选择面: ({fx}, {fy}, {fz})")
        model.SketchManager.InsertSketch(True)
    else:
        start_sketch(model, str(params.get("plane") or "Top Plane"))

    if kind == "circle":
        sketch_circle(model, mm(_num(params.get("cx"))), mm(_num(params.get("cy"))), mm(_num(params.get("d")) / 2.0))
    elif kind == "circles":
        for item in params.get("circles") or []:
            sketch_circle(model, mm(_num(item.get("cx"))), mm(_num(item.get("cy"))), mm(_num(item.get("d")) / 2.0))
    elif kind in ("rect", "rectangle"):
        sketch_rectangle(model, 0, 0, mm(_num(params.get("w"))), mm(_num(params.get("h"))))
    elif kind == "slot":
        radius = mm(_num(params.get("r")))
        half_len = mm(_num(params.get("half_len")))
        cx = mm(_num(params.get("cx")))
        cy = mm(_num(params.get("cy")))
        if str(params.get("dir") or "x").lower() == "y":
            sketch_slot(model, cx, cy - half_len, cx, cy + half_len, radius)
        else:
            sketch_slot(model, cx - half_len, cy, cx + half_len, cy, radius)
    else:
        raise ValueError(f"未支持的草图类型: {kind}")

    reference = end_sketch(model)
    name = reference.name if reference else None
    return name or current_sketch_name(model)


def _extrude(model, params, *, cut, merge=True):
    depth = _num(params.get("depth"))
    sketch_name = _draw_sketch(model, params)
    # 方向约定（经验证）: direction=False 沿 +法向（Top Plane 为 +Y），True 沿 -法向。
    # 凸台默认向上(+法向)，切除默认向下(-法向，切进材料)。
    direction = bool(params.get("direction", (True if cut else False)))
    if cut:
        through = bool(params.get("through", False))
        extrude_cut(model, sketch_name, mm(0 if through else depth), direction=direction)
    else:
        if bool(params.get("midplane", False)):
            extrude_midplane(model, sketch_name, mm(depth), merge=merge)
            return
        extrude_boss(
            model,
            sketch_name,
            mm(depth),
            direction=direction,
            merge=merge,
            start_offset=mm(_num(params.get("start_offset"))),
        )


def _close_existing_doc(sw, model, part_path):
    """若目标路径已作为其它文档打开，先关闭它，避免 SaveAs 撞名失败。"""
    try:
        existing = get_com_member(sw, "GetOpenDocumentByName", part_path)
    except Exception:
        existing = None
    if existing is None or existing is model:
        return
    title = get_com_member(existing, "GetTitle")
    try:
        sw.CloseDoc(title)
        print(f"已关闭同名已打开文档: {title}")
    except Exception as exc:
        print(f"提示: 关闭同名文档 {title} 失败: {exc}")


def build_features(model, document):
    params = dict(document.get("parameters") or {})
    for feature in document.get("features") or []:
        feature_type = str(feature.get("type") or "").lower()
        operation = str(feature.get("operation") or "add").lower()
        resolved = _resolve(feature.get("params") or {}, params)
        merge = bool(feature.get("merge", True))
        if feature_type == "extrude":
            _extrude(model, resolved, cut=(operation == "cut"), merge=merge)
        else:
            raise ValueError(f"未支持的特征类型: {feature_type}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="从参数化 CAD JSON 一步驱动 SolidWorks 原生建模")
    parser.add_argument("json_path")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--no-step", action="store_true", help="不导出 STEP")
    parser.add_argument("--version", type=int, default=None, help="SolidWorks 年份，如 2024")
    parser.add_argument("--allow-launch", action="store_true", help="附着失败时允许冷启动新实例（默认禁止）")
    parser.add_argument("--views", default="isometric", help="自审查视角，逗号分隔")
    parser.add_argument("--close-after", action="store_true", help="结束后关闭本次创建的文档（默认保留）")
    args = parser.parse_args(argv)

    json_path = Path(args.json_path).expanduser().resolve()
    document = json.loads(json_path.read_text(encoding="utf-8"))
    if str(document.get("units", "mm")).lower() != "mm":
        raise ValueError("v1 仅支持 mm 单位")

    sw, _ = connect_solidworks(version=args.version, attach_only=not args.allow_launch)
    model = new_document(sw, "part")
    try:
        build_features(model, document)

        output = document.get("output") or {}
        out_dir = Path(args.out_dir or output.get("dir") or (json_path.parent / "output")).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        basename = str(document.get("documentId") or json_path.stem)
        part_path = out_dir / str(output.get("part") or f"{basename}.sldprt")
        _close_existing_doc(sw, model, str(part_path))
        save_document(model, str(part_path))

        expected = [str(part_path)]
        if not args.no_step:
            step_path = out_dir / str(output.get("step") or f"{basename}.step")
            export_to_step(model, str(step_path))
            expected.append(str(step_path))

        views = tuple(view.strip() for view in args.views.split(",") if view.strip())
        report, report_path = run_review(model, out_dir, basename=basename, views=views, expected_outputs=expected)
        evaluation = report["evaluation"]

        print(f"\n[完成] 零件: {part_path}")
        print(f"[审查] 状态: {evaluation['status']} / 分数: {evaluation['score']}")
        print(f"[报告] {report_path}")
        for issue in evaluation.get("issues", []):
            print(f"  - [{issue['severity']}] {issue['code']}: {issue['message']}")
        return 0 if evaluation["status"] != "fail" else 2
    finally:
        if args.close_after:
            close_document(sw, model, discard_unsaved=True)
            print("已关闭本次创建的文档")


if __name__ == "__main__":
    raise SystemExit(main())
