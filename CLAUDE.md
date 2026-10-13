# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**CadJSON Studio** is the working name of this repository — a secondary development of the upstream open-source project [SolidWorks Automation Skill](https://github.com/wzyn20051216/solidworks-automation-skill) (MIT, by wzyn20051216 and contributors; this clone's `origin` still points there). The fork's own delta over upstream is the declarative CAD JSON pipeline: `scripts/sw_build_from_json.py` (untracked new executor), the battery-cell examples `examples/cell_314.json` / `examples/cell_cover_plate.json`, and enabling API additions in `scripts/sw_connect.py` (`attach_only`, `close_document`) and `scripts/sw_part.py` (`sketch_slot` width fix, `extrude_boss` start_offset, `extrude_midplane` merge). Attribution to upstream is mandatory in user-facing docs.

Beyond that delta it is a Windows-first CAD automation platform ("CAD Studio") with four peer entry points sharing one core:

- **Skill/CLI** — `SKILL.md` is the agent entry point; `scripts/sw_*.py` wraps the SolidWorks COM API in Python (pywin32/comtypes).
- **MCP server** — `mcp-server/server.py`, a FastMCP stdio server exposing whitelisted CAD tools. All SolidWorks COM operations run under one global lock (SolidWorks is a single-threaded COM desktop app).
- **CAD Studio desktop** — `apps/workbench-ui` (React + Tauri 2 + Rust shell, the official client) and `apps/desktop` (PySide6 prototype; its `cad_workbench` package holds the queue worker, agent providers, reviewer gate, artifact ledger, and JSON schemas).
- **C# Add-in host** — `dotnet/CadStudio.SolidWorks.AddinHost` (net48/x64), for in-process events/TaskPane/PropertyManagerPage that out-of-process COM cannot do.

There are also five self-contained subskills under `subskills/` (VibeCAD planning, CNC fillet/chamfer, threaded holes, GB/T engineering drawings, AutoCAD), each with its own `SKILL.md`/`README.md`/`manifest.yaml`/`scripts`/`references`.

## Honesty boundary (project-defining)

`capabilities.yaml` is the single source of truth for what works and at which level: `verified`, `pilot`, `reference_only`, `not_implemented`. `reference_only`/`not_implemented` must never be presented as unattended deliverables; `pilot` output requires explicit human review. Never fake support — no extension renaming, empty files, or demo data passed off as real output. Mock execution exists only behind `--enable-mock` and `isDemo: true` flags. When changing what a capability can do, update `capabilities.yaml` — Skill, MCP, queue worker, and UI all read it through `scripts/capabilities.py`.

## Commands

Python side targets Windows 10/11 + Python 3.10+. The `tests/test_*.py` suite is no-COM (fake COM objects), but Windows-only modules import pywin32 lazily and interactively prompt to install when missing — so on macOS/Linux pytest cannot even collect. UI/TS tooling runs anywhere.

### Python

```powershell
python -m pytest -q                                # full no-COM unit suite (Windows)
python -m pytest tests/test_dfm_review.py -q       # single file
python -m pytest tests/test_dfm_review.py -k name  # single test
```

Real-machine regression scripts (`tests/*_regression.py`, `tests/*_version_matrix.py`) have no `test_` prefix, so pytest never collects them. They require actual SolidWorks/AutoCAD and are run explicitly on a Windows machine or by the `windows-cad-regression.yml` workflow on a self-hosted `[self-hosted, Windows, CAD]` runner:

```powershell
python tests/solidworks_week3_delivery_regression.py
```

Contract/release validators (run in release CI):

```powershell
python scripts/validate_skill.py
python scripts/validate_mcp.py
python scripts/release_check.py
```

Diagnostics:

```powershell
python scripts/sw_preflight.py            # preflight; auto-offers installing pywin32/comtypes
python scripts/cad_doctor.py              # environment doctor with remediations
python scripts/stability_regression.py    # lifecycle stability simulation
```

Unified CLI (`scripts/cad_studio.py`): `doctor`, `run`, `status`, `retry`, `cancel`, `write-open-format`, `preview-dxf`, `check-dfm`, `check-routing`, `fea-preflight`, `prepare-fea`, `run-fea`, `run-fea-convergence`, `review-advanced-geometry`, `create-ocp-loft`, `create-ocp-surface`.

Queue worker (drives desktop jobs outside the UI):

```powershell
python -m apps.desktop.cad_workbench.queue_worker --watch --queue-dir "<dir>"
# --enable-mock for protocol dev only; --enable-codex for the real agent executor
```

Desktop prototype: `pip install -r apps/desktop/requirements.txt` then `python apps/desktop/run.py`.

### Workbench UI

```powershell
cd apps/workbench-ui
npm ci
npm test            # vitest
npm run build       # tsc -b && vite build
npm run dev         # Vite browser preview
npm run desktop:dev | desktop:build | desktop:bundle   # Tauri; exe only on Windows
```

`npm run sync:skill` (invoked by the desktop targets) bundles the skill + Python worker into the app via `scripts/sync_bundled_skill.py`. Rust backend checks (CI enforces all three), from `apps/workbench-ui/src-tauri`: `cargo fmt -- --check`, `cargo test`, `cargo clippy -- -D warnings`.

### Release / E2E / MCP

```powershell
powershell -File scripts/build_windows_release.ps1    # full Windows package build
powershell -File scripts/verify_updater_release.ps1   # updater contract check
node ai_team/release-portable-e2e.cjs                 # Playwright e2e against built packages
python mcp-server/server.py                           # stdio MCP server
powershell -ExecutionPolicy Bypass -File mcp-server/register_all_ai_mcp.ps1 -InstallDependencies
```

`ai_team/*.cjs|ps1` are E2E harnesses (release, installer, provider UI, preview viewport, delivery gate); release CI runs the portable + NSIS ones, the rest are run manually.

## Architecture

**Capability manifest + backend routing.** `capabilities.yaml` also defines per-atomic-operation routing among backends: Python pywin32/comtypes (out-of-process COM), C# PIA, C# in-process add-in, native C++, SWBasic macros, headless OCCT/OCP, and external solvers (CalculiX). `scripts/backend_router.py` resolves an operation to a backend (`--list`, `--operation`); rules in `references/language-backend-routing.md`.

**Headless open-format path.** Without SolidWorks/AutoCAD installed, only native formats (SLDPRT/SLDASM/SLDDRW/DWG) are blocked; everything else routes to `scripts/headless_cad_writer.py` and `scripts/headless_occt_service.py` (isolated OCCT process) which really write STEP/IGES/BREP/STL/OBJ/GLB and DXF/SVG/PDF/PNG with geometry + SHA-256 evidence. Unsupported geometry must come back `blocked`, never silently approximated.

**Shared data contracts.** JSON Schemas in `apps/desktop/cad_workbench/schemas/` — Automation Job, NeutralCadDocument, Preview Manifest, Evidence Graph, Codex Final Response — are consumed by all entry points. The desktop↔worker protocol is `apps/workbench-ui/docs/automation-queue-protocol.md`; `golden-workflows.yaml` defines golden workflows, required inputs, and required artifacts (target first-pass rate 0.90).

**Review/delivery contract.** Every CAD-generating workflow must produce the native file (when applicable), requested exports, a parameter JSON for reproducibility, a `sw_review.run_review()` report, and at least an isometric preview. Delivery packages carry `manifest.json` + SHA-256 ledgers (`scripts/sw_delivery.py`, `apps/desktop/cad_workbench/artifact_ledger.py`); the reviewer gate and worker health modules enforce human review before unattended claims. `scripts/sw_macro_guard.py` guards against hallucinated multi-model macro output.

**Two-tier testing philosophy.** Tier 1: `tests/test_*.py` pytest suite with fake COM objects — no CAD needed. Tier 2: real-machine regression scripts run against live SolidWorks/AutoCAD (the only way to validate native-format claims). New native behavior needs both.

## Conventions

- Docs, code comments, docstrings, and commit messages are written in Chinese. Docstrings open with a `@brief` line. Commit prefixes: `添加:` / `修复:` / `更新:` / `文档:` / `重构:`.
- Code style: 4-space indent, snake_case functions/variables, PascalCase classes, UPPER_CASE constants.
- New SolidWorks API wrappers go in the matching `scripts/sw_*.py` with a `references/` doc and an `examples/` entry; unwrapped APIs are looked up in official API Help / local SDK first, then implemented, run, self-reviewed, and pitfalls sunk into `references/`.
- New subskill directory layout: `SKILL.md`, `README.md`, `manifest.yaml`, `scripts/`, `references/`, `examples/` — then update `SUBSKILLS.md`, `README.md`, `project.yaml`, and `docs/skill-map.md`.
- Set `PYTHONUTF8=1` when running Python on Windows.
