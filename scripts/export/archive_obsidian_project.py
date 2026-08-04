#!/usr/bin/env python3
"""Generate an Obsidian knowledge base for the Chenyiyun2087 project."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DISPLAY_REPO_ROOT = Path("/Users/chenyiyun/PycharmProjects/Chenyiyun2087")
VAULT_ROOT = Path("/Users/chenyiyun/Documents/Obsidian Vault")
PROJECT_ROOT = VAULT_ROOT / "02_项目" / "Chenyiyun2087"
CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"
SESSION_CWD = str(REPO_ROOT)
PROJECT_NAME = "Chenyiyun2087"
PROJECT_SLUG = "chenyiyun2087"
THREADS_TABLE = "threads"
GENERATED_START = "<!-- AUTO-GENERATED START -->"
GENERATED_END = "<!-- AUTO-GENERATED END -->"

NO_CONTENT_TITLES = {
    "logout",
    "codex logout",
    "cd easymoney",
}


@dataclass
class SessionRecord:
    session_id: str
    title: str
    first_user_message: str
    preview: str
    rollout_path: Path
    created_at_ms: int
    updated_at_ms: int
    tokens_used: int
    archived: bool

    @property
    def summary(self) -> str:
        text = self.first_user_message.strip() or self.preview.strip() or self.title.strip()
        return one_line(text)[:180]

    @property
    def updated_date(self) -> str:
        return fmt_date(self.updated_at_ms)

    @property
    def updated_local(self) -> str:
        return fmt_datetime(self.updated_at_ms)


@dataclass
class DecisionSpec:
    slug: str
    title: str
    category: str
    status: str
    session_ids: list[str]
    background: str
    decisions: list[str]
    reasons: list[str]
    impacts: list[str]
    rollback: str


def one_line(text: str) -> str:
    return " ".join(text.split())


def fmt_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def fmt_datetime(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def md_link(label: str, target: Path) -> str:
    target_str = str(target)
    if " " in target_str:
        return f"[{label}](<{target_str}>)"
    return f"[{label}]({target_str})"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def replace_generated_block(path: Path, generated_body: str) -> None:
    generated = f"{GENERATED_START}\n{generated_body.rstrip()}\n{GENERATED_END}\n"
    if not path.exists():
        path.write_text(generated, encoding="utf-8")
        return

    existing = path.read_text(encoding="utf-8")
    if GENERATED_START in existing and GENERATED_END in existing:
        start = existing.index(GENERATED_START)
        end = existing.index(GENERATED_END) + len(GENERATED_END)
        updated = existing[:start] + generated + existing[end:]
    else:
        preserved = existing.rstrip()
        if preserved:
            updated = preserved + "\n\n" + generated
        else:
            updated = generated
    path.write_text(updated, encoding="utf-8")


def write_generated_page(path: Path, content: str, notes: str | None = None) -> None:
    body = content.rstrip()
    if notes:
        body += "\n\n" + notes.rstrip() + "\n"
    replace_generated_block(path, body)


def fetch_sessions() -> list[SessionRecord]:
    con = sqlite3.connect(CODEX_STATE_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"""
        select
            id,
            title,
            first_user_message,
            preview,
            rollout_path,
            created_at_ms,
            updated_at_ms,
            tokens_used,
            archived
        from {THREADS_TABLE}
        where cwd = ?
        order by created_at_ms
        """,
        (SESSION_CWD,),
    ).fetchall()
    con.close()
    return [
        SessionRecord(
            session_id=row["id"],
            title=row["title"] or "",
            first_user_message=row["first_user_message"] or "",
            preview=row["preview"] or "",
            rollout_path=Path(row["rollout_path"]),
            created_at_ms=row["created_at_ms"] or 0,
            updated_at_ms=row["updated_at_ms"] or 0,
            tokens_used=row["tokens_used"] or 0,
            archived=bool(row["archived"]),
        )
        for row in rows
    ]


def classify_session(session: SessionRecord) -> tuple[str, str]:
    title = one_line(session.title).lower()
    summary = session.summary.lower()
    text = f"{title} {summary}"
    if title in NO_CONTENT_TITLES:
        return "utility/no_content", "utility/no_content"
    if "automation:" in text or "automation id:" in text or "周度b点模型监控" in text or "月度b点信号增强闭环" in text:
        return "自动化监控", "自动化监控"
    if any(token in text for token in ("批量", "scheduler", "web控制台", "sina_score", "0830", "定时", "调度", "前端提示")):
        return "调度与批量任务", "调度与批量任务"
    if any(token in text for token in ("回测", "m7", "m8", "评分", "买点", "股票池", "策略", "signal", "top5")):
        return "策略研究与回测", "策略研究与回测"
    if any(token in text for token in ("mysql", "redis", "mongodb", "启动", "重启", "nohup", "代理", "proxy", "部署", "运行是否正常")):
        return "运行与环境", "运行与环境"
    if any(token in text for token in ("readme", "review", "文档", "git", "目录", "归档", "优化计划")):
        return "项目工程与文档", "项目工程与文档"
    return "项目工程与文档", "项目工程与文档"


def collect_docs() -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    mapping = {
        "docs/00_project_overview": "项目总览与运行手册",
        "docs/01_strategy_research": "策略研究",
        "docs/02_stock_research": "个股研究",
        "docs/03_backtest_reports": "回测报告",
        "docs/06_prompt_library": "提示词库",
        "docs/tasks": "开发任务记录",
    }
    for pattern, label in mapping.items():
        base = REPO_ROOT / pattern
        for path in sorted(base.rglob("*")):
            if path.is_file():
                groups[label].append(path)
    production_usage = REPO_ROOT / "docs" / "production_trusted_strategy_usage.md"
    if production_usage.exists():
        groups["生产操作说明"].append(production_usage)
    return groups


def collect_exports() -> dict[str, list[Path]]:
    export_groups: dict[str, list[Path]] = defaultdict(list)
    targets = {
        "backtest/results": "历史回测结果 JSON",
        "exports/bs_model_walkforward": "模型 Walk-Forward 验证",
        "exports/bs_signal_cycles": "B 点信号增强周期",
        "exports/bs_signal_models": "B 点模型训练报告",
        "exports/production_candidates": "生产候选导出",
        "exports/reports": "人工整理报告导出",
        "exports/score_backfill": "评分补齐日志",
        "exports/signal_enhancement": "信号增强数据集",
        "exports/signal_research": "策略研究批次报告",
    }
    for rel, label in targets.items():
        base = REPO_ROOT / rel
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json", ".csv"}:
                export_groups[label].append(path)
    return export_groups


def choose_representative(paths: list[Path], limit: int = 8) -> list[Path]:
    def score(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()
        priority = 0
        if "report" in name:
            priority += 4
        if "summary" in name:
            priority += 3
        if "manifest" in name:
            priority += 2
        if name.endswith(".md"):
            priority += 2
        return (priority, int(path.stat().st_mtime), str(path))

    return sorted(paths, key=score, reverse=True)[:limit]


def render_session_index(sessions: list[SessionRecord], categories: dict[str, list[SessionRecord]]) -> str:
    lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: session_index",
        f"source: {CODEX_STATE_DB}",
        f"thread_filter: cwd = {SESSION_CWD}",
        f"session_count: {len(sessions)}",
        f"generated_at: {fmt_date(max(s.updated_at_ms for s in sessions)) if sessions else fmt_date(int(datetime.now().timestamp() * 1000))}",
        "---",
        "",
        "# Session Index",
        "",
        "## 索引说明",
        "",
        "本页保留 session 元数据、分类和原始 rollout 路径，不复制完整对话。",
        "",
        "## Sessions",
        "",
        "| 分类 | 更新时间 | Session ID | Tokens | 标题摘要 | Rollout |",
        "|---|---|---|---:|---|---|",
    ]
    for session in sorted(sessions, key=lambda s: s.updated_at_ms, reverse=True):
        category, _ = classify_session(session)
        title = one_line(session.title)[:90]
        rollout = md_link("rollout", session.rollout_path)
        lines.append(
            f"| {category} | {session.updated_date} | `{session.session_id}` | {session.tokens_used} | {title} | {rollout} |"
        )
    lines.extend(
        [
            "",
            "## 分类校验",
            "",
            "| 分类 | 数量 |",
            "|---|---:|",
        ]
    )
    for category in sorted(categories):
        lines.append(f"| {category} | {len(categories[category])} |")
    lines.append(f"| 合计 | {len(sessions)} |")
    return "\n".join(lines)


def render_session_moc(sessions: list[SessionRecord], categories: dict[str, list[SessionRecord]]) -> str:
    lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: session_moc",
        "source: codex_threads",
        f"generated_at: {fmt_date(max(s.updated_at_ms for s in sessions)) if sessions else ''}",
        f"vault_path: {VAULT_ROOT}",
        f"repo_path: {DISPLAY_REPO_ROOT}",
        "---",
        "",
        f"# {PROJECT_NAME} Session MOC",
        "",
        "## 总览",
        "",
        "本知识库页汇总 Chenyiyun2087 相关 Codex sessions。目标是沉淀可复用的项目知识，而不是复制完整聊天记录。",
        "",
        "当前归档基于：",
        "",
        f"- SQLite 索引：`{CODEX_STATE_DB}`",
        f"- 表：`{THREADS_TABLE}`",
        f"- 过滤条件：`cwd = {SESSION_CWD}`",
        f"- 原始会话：`{Path.home() / '.codex' / 'sessions'}` 与 `~/.codex/archived_sessions/*.jsonl`",
        "",
        "## 分类入口",
        "",
        "| 分类 | Sessions | 主题页 | 说明 |",
        "|---|---:|---|---|",
    ]
    desc_map = {
        "策略研究与回测": "评分体系、M7/M8、B 点增强、全量池回测、可信性控制",
        "调度与批量任务": "定时任务、失败排查、落库修复、批次一致性",
        "运行与环境": "MySQL/Redis/Mongo、代理、启动脚本、部署与运行问题",
        "项目工程与文档": "README、目录治理、代码审查、文档同步、归档维护",
        "自动化监控": "周度与月度巡检自动化，不与人工会话混排",
        "utility/no_content": "工具型空会话，仅保留索引痕迹",
    }
    link_map = {
        "策略研究与回测": "[[02_主题沉淀/策略研究与回测]]",
        "调度与批量任务": "[[02_主题沉淀/调度与批量任务]]",
        "运行与环境": "[[02_主题沉淀/运行与环境]]",
        "项目工程与文档": "[[02_主题沉淀/项目工程与文档]]",
        "自动化监控": "[[01_会话归档/自动化监控]]",
        "utility/no_content": "[[01_会话归档/Session Index]]",
    }
    for category in sorted(categories):
        lines.append(f"| {category} | {len(categories[category])} | {link_map[category]} | {desc_map[category]} |")
    lines.extend(
        [
            "",
            "## 推荐阅读顺序",
            "",
            "1. [[01_会话归档/Session Index]]：确认所有 session 来源、时间和原始路径。",
            "2. [[02_主题沉淀/调度与批量任务]]：理解日常批量主线和常见故障模式。",
            "3. [[02_主题沉淀/策略研究与回测]]：理解评分、B 点增强和回测口径演进。",
            "4. [[02_主题沉淀/运行与环境]]：理解启动、代理、数据库与部署问题。",
            "5. [[02_主题沉淀/项目工程与文档]]：理解 README、目录治理和项目文档沉淀。",
            "",
            "## 高价值决策",
            "",
            "- [[03_关键决策/2026-02-26 M7 调仓卖出链路修复]]",
            "- [[03_关键决策/2026-03-18 Sina 图片任务直连不走代理]]",
            "- [[03_关键决策/2026-05-10 B 点评分与综合建议增强]]",
            "- [[03_关键决策/2026-05-11 批量评分异常与任务治理]]",
            "- [[03_关键决策/2026-06-04 全量池 Top5 20日持有回测口径]]",
            "- [[03_关键决策/2026-06-05 升级优化文档驱动实施]]",
            "- [[03_关键决策/2026-06-09 Chenyiyun2087 Obsidian 归档规范]]",
        ]
    )
    return "\n".join(lines)


def render_archive_state(sessions: list[SessionRecord], categories: dict[str, list[SessionRecord]]) -> str:
    last_updated = max((s.updated_at_ms for s in sessions), default=0)
    first_created = min((s.created_at_ms for s in sessions), default=0)
    lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: archive_state",
        f"source: {CODEX_STATE_DB}",
        f"generated_at: {fmt_date(last_updated) if last_updated else ''}",
        "---",
        "",
        "# archive_state",
        "",
        "## 当前归档状态",
        "",
        f"- repo_path: `{DISPLAY_REPO_ROOT}`",
        f"- repo_thread_filter: `{SESSION_CWD}`",
        f"- vault_project_path: `{PROJECT_ROOT}`",
        f"- archived_session_count: `{len(sessions)}`",
        f"- first_created_at_ms_local: `{fmt_datetime(first_created) if first_created else ''}`",
        f"- last_updated_at_ms_local: `{fmt_datetime(last_updated) if last_updated else ''}`",
        f"- last_updated_at_ms: `{last_updated}`",
        "",
        "## 分类计数",
        "",
    ]
    for category in sorted(categories):
        lines.append(f"- {category}: `{len(categories[category])}`")
    lines.extend(
        [
            "",
            "## 增量归档规则",
            "",
            "1. 查询 `~/.codex/state_5.sqlite` 的 `threads` 表。",
            f"2. 过滤 `cwd = {SESSION_CWD}`。",
            f"3. 只处理 `updated_at_ms > {last_updated}` 的线程。",
            "4. 新线程先追加到 [[01_会话归档/Session Index]]。",
            "5. 再按主题归入对应主题页或自动化监控页。",
            "6. 若产生稳定决策、回测口径、任务治理规则或归档规范，则新增 `03_关键决策/` 页面。",
            "",
            "## 已处理 Session IDs",
            "",
        ]
    )
    for session in sessions:
        lines.append(f"`{session.session_id}`")
    return "\n".join(lines)


def render_topic_page(title: str, sessions: list[SessionRecord], description: str) -> str:
    lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: topic_page",
        f"category: {title}",
        f"generated_at: {fmt_date(max(s.updated_at_ms for s in sessions)) if sessions else ''}",
        "---",
        "",
        f"# {title}",
        "",
        description,
        "",
        f"## Sessions（{len(sessions)}）",
        "",
    ]
    for session in sorted(sessions, key=lambda s: s.updated_at_ms, reverse=True):
        lines.extend(
            [
                f"### {session.updated_date} | `{session.session_id}`",
                "",
                f"- 标题：{one_line(session.title)}",
                f"- 摘要：{session.summary}",
                f"- Tokens：`{session.tokens_used}`",
                f"- 原始 rollout：{md_link(session.rollout_path.name, session.rollout_path)}",
                "",
            ]
        )
    return "\n".join(lines)


def render_docs_index(groups: dict[str, list[Path]]) -> dict[str, str]:
    pages: dict[str, str] = {}
    overview_lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: docs_index",
        "---",
        "",
        "# 项目文档索引",
        "",
        "本目录只建立入口和摘要，不复制仓库原文。",
        "",
        "## 分类入口",
        "",
    ]
    for label, paths in groups.items():
        note_name = f"{label}.md"
        overview_lines.append(f"- [[04_项目文档索引/{label}]]：{len(paths)} 个文件")
        lines = [
            "---",
            f"project: {PROJECT_NAME}",
            "type: docs_index_detail",
            f"category: {label}",
            "---",
            "",
            f"# {label}",
            "",
            f"共 {len(paths)} 个文件，以下链接均指向仓库原始文档：",
            "",
        ]
        for path in paths:
            display = str(path.relative_to(REPO_ROOT))
            lines.append(f"- {md_link(display, path)}")
        pages[note_name] = "\n".join(lines)
    pages["00_文档总览.md"] = "\n".join(overview_lines)
    return pages


def render_exports_index(groups: dict[str, list[Path]]) -> dict[str, str]:
    pages: dict[str, str] = {}
    overview_lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: export_index",
        "---",
        "",
        "# 导出与回测报告索引",
        "",
        "本目录只保留批次入口和代表性报告，不复制大 JSON / CSV 内容。",
        "",
        "## 分类入口",
        "",
    ]
    for label, paths in groups.items():
        note_name = f"{label}.md"
        overview_lines.append(f"- [[05_导出与回测报告索引/{label}]]：{len(paths)} 个文件")
        lines = [
            "---",
            f"project: {PROJECT_NAME}",
            "type: export_index_detail",
            f"category: {label}",
            "---",
            "",
            f"# {label}",
            "",
            f"- 文件总数：`{len(paths)}`",
            "",
            "## 代表性入口",
            "",
        ]
        for path in choose_representative(paths):
            display = str(path.relative_to(REPO_ROOT))
            lines.append(f"- {md_link(display, path)}")
        if len(paths) > 8:
            lines.extend(
                [
                    "",
                    "## 最新批次（按路径排序截取）",
                    "",
                ]
            )
            for path in sorted(paths)[-8:]:
                display = str(path.relative_to(REPO_ROOT))
                lines.append(f"- {md_link(display, path)}")
        pages[note_name] = "\n".join(lines)
    pages["00_导出总览.md"] = "\n".join(overview_lines)
    return pages


def build_decisions() -> list[DecisionSpec]:
    return [
        DecisionSpec(
            slug="2026-02-26 M7 调仓卖出链路修复",
            title="2026-02-26 M7 调仓卖出链路修复",
            category="策略研究与回测",
            status="adopted",
            session_ids=["019c979b-67b6-7293-bbf1-7aa008b87fd0", "019ca974-4641-7082-afca-e2427cc86aa7"],
            background="M7 调仓在前端点击执行无响应，后续又进入链路优化阶段，需要在现有 Flask 与任务体系中做增量修复。",
            decisions=[
                "M7 卖出链路按增量改造推进，不重写现有任务体系。",
                "前端点击执行、后端任务入口、调仓逻辑和评估脚本要作为同一链路验证。",
                "调仓卖出能力需要保留现有模块边界，避免脱离既有 `web/`、`scripts/ops/` 和策略模块另起系统。",
            ],
            reasons=[
                "M7 是现有策略体系的一部分，脱离当前链路重做会破坏调度、前端和评估的一致性。",
                "问题既涉及触发执行，也涉及卖出规则本身，必须以链路视角处理。",
            ],
            impacts=["M7 前端执行入口", "调仓卖出脚本", "策略评估与验证链路"],
            rollback="保留旧入口和参数兼容性；若新链路异常，先回退到旧执行方式并保留评估工具。",
        ),
        DecisionSpec(
            slug="2026-03-18 Sina 图片任务直连不走代理",
            title="2026-03-18 Sina 图片任务直连不走代理",
            category="运行与环境",
            status="adopted",
            session_ids=["019cfff8-69e8-7511-b5c0-dad0b7629dcc", "019d48d7-da9b-7ad1-b525-bd4039ade64c", "019d4936-ace1-7760-a3bf-9d6cb85d4a89"],
            background="管理台中的 `sina_picture` 任务走代理，导致代理流量异常消耗，并影响抓取稳定性。",
            decisions=[
                "Sina 图片抓取任务默认直连，不走 127.0.0.1 代理配置。",
                "代理相关排查保留在运行诊断层，不嵌入日常生产抓取路径。",
                "对图片抓取链路增加显式检查，避免环境变量或公共 requests 配置误伤任务。",
            ],
            reasons=[
                "截图任务是高频生产链路，代理误用会带来成本和稳定性双重问题。",
                "直连语义更清晰，也更符合该任务的真实执行需求。",
            ],
            impacts=["Sina 图片抓取任务", "运行环境配置", "代理排查文档"],
            rollback="若目标站点后续确需代理访问，应通过任务级显式配置开启，而不是依赖全局代理。",
        ),
        DecisionSpec(
            slug="2026-05-10 B 点评分与综合建议增强",
            title="2026-05-10 B 点评分与综合建议增强",
            category="策略研究与回测",
            status="adopted",
            session_ids=["019e10af-2e9f-7283-8fc9-160b8b52566c", "019e117c-8559-7492-bceb-11bfb6dd117d"],
            background="`/sina/scores` 页面展示了买点后涨幅、评分和综合建议，需要从展示口径升级为可复用的评分与建议体系。",
            decisions=[
                "综合分和综合建议不只作为前端展示字段，而要纳入日终批量可复用结果。",
                "B 点评分增强需要把技术分、规则分、研究建议与后续批量流程连接起来。",
                "评分解释口径要能支持页面阅读、研究复盘和候选导出三种场景。",
            ],
            reasons=[
                "只在页面临时计算的综合建议无法稳定服务回测、导出和生产复核。",
                "B 点增强价值在于形成完整闭环，而不是孤立页面标签。",
            ],
            impacts=["Sina scores 页面", "日终评分批量", "B 点增强研究链路"],
            rollback="保留旧页面字段作为兼容展示；新综合分可先作为附加列影子运行。",
        ),
        DecisionSpec(
            slug="2026-05-11 批量评分异常与任务治理",
            title="2026-05-11 批量评分异常与任务治理",
            category="调度与批量任务",
            status="adopted",
            session_ids=["019e1764-b3b5-7c63-a58f-b4aadcd4744c", "019e1a13-6701-75c0-b200-5313964cdbe1"],
            background="全股票评分出现错误，且前一日批量任务需要优化，说明评分与调度链路需要统一排障和治理。",
            decisions=[
                "把评分异常修复与批量任务治理放在同一视角下处理，而不是只做单点补丁。",
                "重点校验任务执行结果、落库完整性、日志可追溯性和次日可重复执行能力。",
                "优化后的批量任务要兼顾日常运行稳定性和研究链路扩展性。",
            ],
            reasons=[
                "评分错误往往不是单点故障，而是任务、数据、落库和校验之间的链路问题。",
                "如果只修单个异常，不建立治理规则，后续仍会反复出现同类问题。",
            ],
            impacts=["批量任务调度", "全股票评分", "任务日志与校验流程"],
            rollback="若新治理逻辑影响生产节奏，可先保留原任务入口并在旁路增加校验。",
        ),
        DecisionSpec(
            slug="2026-06-04 全量池 Top5 20日持有回测口径",
            title="2026-06-04 全量池 Top5 20日持有回测口径",
            category="策略研究与回测",
            status="proposed",
            session_ids=["019e1af8-cfe5-7171-bf35-07ccb3339617"],
            background="用户要求对全量股票池按评分前 5 等权持有、平均持有 20 天做收益回测，需要明确这是研究口径而非直接生产策略。",
            decisions=[
                "将全量池 Top5 20 日持有视为独立研究口径，单独归档结果。",
                "回测结果重点沉淀方法、成本假设、持有口径和代表性批次入口，不复制超大 JSON 内容。",
                "研究结论与生产候选逻辑分离，避免把实验口径直接混入可信生产链路。",
            ],
            reasons=[
                "该回测请求覆盖面大、结果文件也大，更适合在 Obsidian 中做索引和结论归档。",
                "将研究口径与生产链路分开，有助于后续比较不同持有窗口和评分规则。",
            ],
            impacts=["全量池回测研究", "exports/signal_research", "backtest 结果索引"],
            rollback="若该口径被证明无参考价值，可保留索引但下调在总览中的优先级。",
        ),
        DecisionSpec(
            slug="2026-06-05 升级优化文档驱动实施",
            title="2026-06-05 升级优化文档驱动实施",
            category="项目工程与文档",
            status="adopted",
            session_ids=["019e9022-8f67-7800-bff6-d81bbf8aa0bb"],
            background="项目已经形成大量升级文档，需要先阅读文档再制定升级优化计划，说明文档正在承担实施入口角色。",
            decisions=[
                "升级实施以现有文档为入口，先吸收计划和约束，再做代码层推进。",
                "Obsidian 归档中要为升级方案、任务计划和项目文档建立可导航入口。",
                "文档与执行之间保留清晰映射，避免方案散落在 exports、docs 和会话中难以追踪。",
            ],
            reasons=[
                "当前项目的研究和工程已经高度文档驱动，归档若不强化文档入口，知识会继续分散。",
                "升级方案往往跨多个模块，单靠代码路径很难快速建立全局视角。",
            ],
            impacts=["升级文档", "任务计划", "Obsidian 项目总览"],
            rollback="若某份方案文档失效，不删除索引，只在总览中标记过时状态并补充替代入口。",
        ),
        DecisionSpec(
            slug="2026-06-09 Chenyiyun2087 Obsidian 归档规范",
            title="2026-06-09 Chenyiyun2087 Obsidian 归档规范",
            category="项目工程与文档",
            status="adopted",
            session_ids=["019eaccc-ed00-7913-845d-fab5f9f21991"],
            background="项目会话和资料已经分散在 Codex、docs、exports 和 backtest 结果中，需要建立单独的 Obsidian 项目归档层。",
            decisions=[
                "Obsidian 目录固定为 `02_项目/Chenyiyun2087`。",
                "session 只保留元数据、分类、主题沉淀和关键决策，原始对话通过 rollout 路径追溯。",
                "docs 建立索引，exports 只保留批次入口和代表性报告，不复制大文件。",
                "自动化监控会话单独分类，不与人工工作流混排。",
            ],
            reasons=[
                "项目资料规模已经足够大，如果没有单独归档层，后续检索和复用成本会持续升高。",
                "索引化归档能兼顾可读性、可维护性和对原始产物的尊重。",
            ],
            impacts=["Obsidian 项目目录", "Codex session 索引", "docs 与 exports 入口组织"],
            rollback="若后续需要迁移到 `Projects/` 结构，可复用本脚本重定向输出路径，不需要改动仓库原始资料。",
        ),
    ]


def render_decision_page(spec: DecisionSpec, sessions_by_id: dict[str, SessionRecord]) -> str:
    source_paths = [sessions_by_id[sid].rollout_path for sid in spec.session_ids if sid in sessions_by_id]
    lines = [
        "---",
        f"project: {PROJECT_NAME}",
        f"category: {spec.category}",
        "type: decision",
        "session_ids:",
    ]
    lines.extend(f"  - {session_id}" for session_id in spec.session_ids)
    lines.extend(
        [
        f"status: {spec.status}",
        f"source: {source_paths[0] if source_paths else ''}",
        f"repo_path: {DISPLAY_REPO_ROOT}",
        "---",
        "",
        f"# {spec.title}",
        "",
        "## 背景",
        "",
        spec.background,
        "",
        "## 决策",
        "",
        ]
    )
    lines.extend(f"- {item}" for item in spec.decisions)
    lines.extend(["", "## 原因", ""])
    lines.extend(f"- {item}" for item in spec.reasons)
    lines.extend(["", "## 影响范围", ""])
    lines.extend(f"- {item}" for item in spec.impacts)
    lines.extend(["", "## 回滚/替代方案", "", spec.rollback, "", "## 来源会话", ""])
    for sid in spec.session_ids:
        session = sessions_by_id.get(sid)
        if session:
            lines.append(f"- `{sid}` | {session.updated_date} | {md_link(session.rollout_path.name, session.rollout_path)}")
    return "\n".join(lines)


def render_project_overview(
    sessions: list[SessionRecord],
    categories: dict[str, list[SessionRecord]],
    doc_groups: dict[str, list[Path]],
    export_groups: dict[str, list[Path]],
) -> str:
    lines = [
        "---",
        f"project: {PROJECT_NAME}",
        "type: project_overview",
        "---",
        "",
        f"# {PROJECT_NAME}",
        "",
        "## 项目定位",
        "",
        "该页是 Chenyiyun2087 在 Obsidian 中的总入口，串联会话归档、项目文档、关键决策和导出报告索引。",
        "",
        "## 快速导航",
        "",
        "- [[01_会话归档/Session MOC]]",
        "- [[01_会话归档/Session Index]]",
        "- [[04_项目文档索引/00_文档总览]]",
        "- [[05_导出与回测报告索引/00_导出总览]]",
        "",
        "## 归档摘要",
        "",
        f"- Codex sessions：`{len(sessions)}`",
        f"- 人工文档文件：`{sum(len(v) for v in doc_groups.values())}`",
        f"- 导出/回测索引文件：`{sum(len(v) for v in export_groups.values())}`",
        f"- 真实 session 过滤路径：`{SESSION_CWD}`",
        f"- 显示仓库路径：`{DISPLAY_REPO_ROOT}`",
        "",
        "## 会话分类",
        "",
    ]
    for category in sorted(categories):
        lines.append(f"- {category}：`{len(categories[category])}`")
    lines.extend(
        [
            "",
            "## 项目文档入口",
            "",
            "- [[04_项目文档索引/项目总览与运行手册]]",
            "- [[04_项目文档索引/策略研究]]",
            "- [[04_项目文档索引/个股研究]]",
            "- [[04_项目文档索引/回测报告]]",
            "- [[04_项目文档索引/提示词库]]",
            "- [[04_项目文档索引/开发任务记录]]",
            "",
            "## 导出入口",
            "",
            "- [[05_导出与回测报告索引/策略研究批次报告]]",
            "- [[05_导出与回测报告索引/生产候选导出]]",
            "- [[05_导出与回测报告索引/B 点模型训练报告]]",
            "- [[05_导出与回测报告索引/历史回测结果 JSON]]",
            "",
            "## 关键说明",
            "",
            "- 原始聊天不复制到 Obsidian，统一通过 rollout 路径追溯。",
            "- `docs/` 建索引，`exports/` 建批次入口，不复制大文件。",
            "- 自动化监控会话单独分类，避免污染人工工作流沉淀。",
        ]
    )
    return "\n".join(lines)


def sanitize_name(name: str) -> str:
    return name.replace("/", "_")


def generate() -> None:
    sessions = fetch_sessions()
    if not sessions:
        raise SystemExit(f"No sessions found for cwd={SESSION_CWD}")

    categories: dict[str, list[SessionRecord]] = defaultdict(list)
    for session in sessions:
        category, _ = classify_session(session)
        categories[category].append(session)

    doc_groups = collect_docs()
    export_groups = collect_exports()
    sessions_by_id = {session.session_id: session for session in sessions}

    ensure_dir(PROJECT_ROOT)
    session_dir = PROJECT_ROOT / "01_会话归档"
    topic_dir = PROJECT_ROOT / "02_主题沉淀"
    decision_dir = PROJECT_ROOT / "03_关键决策"
    docs_dir = PROJECT_ROOT / "04_项目文档索引"
    exports_dir = PROJECT_ROOT / "05_导出与回测报告索引"
    for path in (session_dir, topic_dir, decision_dir, docs_dir, exports_dir):
        ensure_dir(path)

    write_generated_page(
        PROJECT_ROOT / "00_项目总览.md",
        render_project_overview(sessions, categories, doc_groups, export_groups),
        notes="## 手写备注\n\n在此区域补充人工观察，脚本更新时会保留本段。",
    )
    write_generated_page(session_dir / "Session Index.md", render_session_index(sessions, categories))
    write_generated_page(session_dir / "Session MOC.md", render_session_moc(sessions, categories))
    write_generated_page(session_dir / "archive_state.md", render_archive_state(sessions, categories))

    topic_specs = {
        "策略研究与回测": "聚合评分体系、B 点增强、M7/M8、全量池与可信性相关会话。",
        "调度与批量任务": "聚合定时任务、批量执行、失败排查、落库与治理相关会话。",
        "运行与环境": "聚合数据库、代理、启动、重启、部署和运行稳定性相关会话。",
        "项目工程与文档": "聚合 README、代码审查、目录治理、文档同步和归档规范相关会话。",
        "自动化监控": "聚合周度/月度监控自动化线程；这些会话独立存放，不和人工任务混排。",
    }
    for title, description in topic_specs.items():
        if title != "自动化监控":
            write_generated_page(topic_dir / f"{title}.md", render_topic_page(title, categories.get(title, []), description))
    write_generated_page(
        session_dir / "自动化监控.md",
        render_topic_page("自动化监控", categories.get("自动化监控", []), topic_specs["自动化监控"]),
    )

    for spec in build_decisions():
        write_generated_page(decision_dir / f"{spec.slug}.md", render_decision_page(spec, sessions_by_id))

    for filename, content in render_docs_index(doc_groups).items():
        write_generated_page(docs_dir / filename, content)
    for filename, content in render_exports_index(export_groups).items():
        write_generated_page(exports_dir / filename, content)


def main() -> None:
    generate()


if __name__ == "__main__":
    main()
