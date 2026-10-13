"""标准外啮合直齿轮渐开线几何测试。"""
from __future__ import annotations

import math
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sw_gear import SpurGearSpec, calculate_external_spur_gear  # noqa: E402


def _unwrapped_angles(points: tuple[tuple[float, float], ...]) -> list[float]:
    """@brief 将闭合轮廓的极角展开为单调角度序列。"""
    result: list[float] = []
    offset = 0.0
    previous = None
    for x, y in points:
        angle = math.atan2(y, x) + offset
        if previous is not None and angle < previous - 1e-10:
            offset += 2.0 * math.pi
            angle += 2.0 * math.pi
        result.append(angle)
        previous = angle
    return result


def test_standard_module_two_twenty_tooth_dimensions_match_reference_equations() -> None:
    """@brief 验证标准全齿高直齿轮的主要解析尺寸。"""
    geometry = calculate_external_spur_gear(
        SpurGearSpec(module_mm=2.0, teeth=20, face_width_mm=10.0, bore_diameter_mm=8.0)
    )

    assert geometry.pitch_diameter_mm == pytest.approx(40.0)
    assert geometry.outside_diameter_mm == pytest.approx(44.0)
    assert geometry.root_diameter_mm == pytest.approx(35.0)
    assert geometry.base_diameter_mm == pytest.approx(40.0 * math.cos(math.radians(20.0)))
    assert geometry.circular_pitch_mm == pytest.approx(2.0 * math.pi)
    assert geometry.tooth_thickness_at_pitch_mm == pytest.approx(math.pi)
    assert geometry.undercut_risk is False


def test_outline_is_closed_by_construction_counterclockwise_and_within_design_radii() -> None:
    """@brief 齿廓必须连续绕圆周前进，且所有点落在齿根圆与齿顶圆之间。"""
    geometry = calculate_external_spur_gear(
        SpurGearSpec(
            module_mm=2.5,
            teeth=24,
            face_width_mm=12.0,
            involute_segments=16,
            tip_arc_segments=6,
            root_arc_segments=7,
        )
    )
    radii_mm = [math.hypot(x, y) * 1000.0 for x, y in geometry.outline_points_m]
    angles = _unwrapped_angles(geometry.outline_points_m)

    assert len(geometry.outline_points_m) > geometry.spec.teeth * 30
    assert min(radii_mm) == pytest.approx(geometry.root_diameter_mm / 2.0, abs=1e-8)
    assert max(radii_mm) == pytest.approx(geometry.outside_diameter_mm / 2.0, abs=1e-8)
    assert all(second >= first - 1e-10 for first, second in zip(angles, angles[1:]))
    assert angles[-1] - angles[0] < 2.0 * math.pi
    assert len(set(geometry.outline_points_m)) == len(geometry.outline_points_m)


def test_low_tooth_count_is_allowed_but_explicitly_reports_undercut_risk() -> None:
    """@brief 低齿数不能伪装成无根切的生产级齿形。"""
    geometry = calculate_external_spur_gear(
        SpurGearSpec(module_mm=1.0, teeth=12, face_width_mm=6.0)
    )

    assert geometry.undercut_risk is True
    assert geometry.minimum_teeth_without_undercut >= 17
    assert any("根切风险" in warning for warning in geometry.warnings)
    assert geometry.evidence()["profile"]["root_form"] == "radial_transition_plus_root_arc"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (SpurGearSpec(module_mm=0.0, teeth=20, face_width_mm=10.0), "模数"),
        (SpurGearSpec(module_mm=2.0, teeth=5, face_width_mm=10.0), "6..300"),
        (SpurGearSpec(module_mm=2.0, teeth=20, face_width_mm=0.0), "齿宽"),
        (SpurGearSpec(module_mm=2.0, teeth=20, face_width_mm=10.0, pressure_angle_deg=40.0), "压力角"),
        (SpurGearSpec(module_mm=2.0, teeth=20, face_width_mm=10.0, bore_diameter_mm=36.0), "轴孔直径"),
        (SpurGearSpec(module_mm=2.0, teeth=20, face_width_mm=10.0, involute_segments=2), "渐开线段数"),
    ],
)
def test_invalid_or_degenerate_specs_are_rejected(spec: SpurGearSpec, message: str) -> None:
    """@brief 拒绝退化齿形和会造成失控草图规模的参数。"""
    with pytest.raises(ValueError, match=message):
        calculate_external_spur_gear(spec)
