"""
TalentPulse Telegram Bot — user-facing alert subscription manager.

Run:
    TELEGRAM_BOT_TOKEN=xxx python -m src.telegram_bot.bot

Commands:
    /start [token]    Welcome + (optional) bind dashboard link token
    /add <filter>     Add subscription. Example: /add python hcmc 20m
    /list             Show your subscriptions
    /pause [days]     Pause all alerts for N days (default 7)
    /resume           Resume alerts
    /delete <id>      Delete subscription by ID
    /stop             Unsubscribe completely (delete all data)
    /help             Show usage

Architecture:
  - python-telegram-bot v21 (async)
  - Long-polling (no webhook setup needed for dev)
  - Postgres backend (asyncpg) — schema `user_alerts`
  - Stateless: each command queries DB fresh

Rate limit handling: telegram-bot library auto-retries with backoff.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.telegram_bot.filter_parser import parse_filter

LOG = logging.getLogger("telegram_bot")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:password@localhost:5432/warehouse",
)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# ─── DB helpers ─────────────────────────────────────────────────────
async def db_pool() -> asyncpg.Pool:
    """Connection pool, attached to bot context (created on startup)."""
    return await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=5, command_timeout=10
    )


async def upsert_subscriber(
    pool: asyncpg.Pool, chat_id: int, username: Optional[str]
) -> None:
    """Insert subscriber if new; update last_seen_at."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_alerts.subscribers (chat_id, username)
            VALUES ($1, $2)
            ON CONFLICT (chat_id) DO UPDATE
              SET username = EXCLUDED.username,
                  last_seen_at = now()
            """,
            chat_id,
            username,
        )


async def consume_link_token(
    pool: asyncpg.Pool, token: str, chat_id: int
) -> bool:
    """Mark a pending dashboard link token as consumed by this chat. Returns True if valid token."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE user_alerts.pending_links
               SET consumed_by = $2
             WHERE token = $1
               AND expires_at > now()
               AND consumed_by IS NULL
            RETURNING token
            """,
            token,
            chat_id,
        )
        return row is not None


# ─── Command handlers ──────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    await upsert_subscriber(pool, chat.id, user.username if user else None)

    # Optional link token from dashboard
    args = context.args or []
    if args:
        token = args[0]
        ok = await consume_link_token(pool, token, chat.id)
        link_msg = (
            "\n✅ Đã liên kết với dashboard."
            if ok
            else "\n⚠️ Link token không hợp lệ hoặc đã hết hạn."
        )
    else:
        link_msg = ""

    await update.message.reply_text(
        "👋 <b>Chào mừng đến TalentPulse Alerts!</b>\n"
        "Em sẽ gửi tin khi có job DE/AI mới khớp tiêu chí của cô."
        + link_msg
        + "\n\n"
        "<b>Bắt đầu nhanh:</b>\n"
        "<code>/add python hcmc 20m</code> — alert job Python tại HCMC ≥ 20M VND\n"
        "<code>/add python,sql,spark hcmc,hanoi senior 30m</code> — multi-filter\n\n"
        "Gõ /help để xem hết lệnh.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>📚 Hướng dẫn TalentPulse Alerts</b>\n\n"
        "<b>/add</b> &lt;filter&gt; — thêm subscription\n"
        "  Filter tokens (cách nhau bằng khoảng trắng):\n"
        "  • Skills: <code>python</code>, <code>sql,spark</code> (comma = OR)\n"
        "  • Cities: <code>hcmc</code>, <code>hanoi</code>, <code>danang</code>\n"
        "  • Levels: <code>intern</code>, <code>fresher</code>, <code>senior</code>, <code>manager</code>\n"
        "  • Salary: <code>20m</code>, <code>25M</code>, <code>30tr</code> (tối thiểu)\n\n"
        "<b>/list</b> — xem subscriptions hiện tại\n"
        "<b>/delete</b> &lt;id&gt; — xóa 1 subscription\n"
        "<b>/pause</b> [days] — tạm ngưng tất cả (mặc định 7 ngày)\n"
        "<b>/resume</b> — tiếp tục nhận alerts\n"
        "<b>/stop</b> — hủy hoàn toàn, xóa data\n\n"
        "<b>Ví dụ:</b>\n"
        "<code>/add python hcmc 20m</code>\n"
        "<code>/add ml,ai,llm hcmc,hanoi senior 35m</code>\n"
        "<code>/add airflow,kafka hcmc</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    raw = " ".join(context.args or [])
    if not raw:
        await update.message.reply_text(
            "Cô cần truyền filter sau /add.\n"
            "Ví dụ: <code>/add python hcmc 20m</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    parsed = parse_filter(raw)
    if parsed.is_empty():
        await update.message.reply_text(
            "❌ Không nhận diện được filter nào trong:\n"
            f"<code>{raw}</code>\n\n"
            "Gõ /help để xem syntax.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Ensure subscriber row exists
    await upsert_subscriber(
        pool,
        chat_id,
        update.effective_user.username if update.effective_user else None,
    )

    args = parsed.to_subscription_args()
    label = raw[:60]  # use raw input as label, cap at 60 chars

    async with pool.acquire() as conn:
        sub_id = await conn.fetchval(
            """
            INSERT INTO user_alerts.subscriptions
              (chat_id, label, skills, cities, job_levels, companies, min_salary_vnd)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            chat_id,
            label,
            args["skills"],
            args["cities"],
            args["job_levels"],
            args["companies"],
            args["min_salary_vnd"],
        )

    msg = f"✅ <b>Đã tạo subscription #{sub_id}</b>\n\n{parsed.describe()}"
    if parsed.unrecognized:
        msg += (
            f"\n\n⚠️ Tokens không nhận diện (đã bỏ qua): "
            f"<code>{', '.join(parsed.unrecognized)}</code>"
        )
    msg += "\n\nEm sẽ thông báo khi có job mới khớp."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    async with pool.acquire() as conn:
        sub_rows = await conn.fetch(
            """
            SELECT id, label, skills, cities, job_levels, companies,
                   min_salary_vnd, active, created_at
            FROM user_alerts.subscriptions
            WHERE chat_id = $1
            ORDER BY id
            """,
            chat_id,
        )
        sb_row = await conn.fetchrow(
            "SELECT paused_until FROM user_alerts.subscribers WHERE chat_id = $1",
            chat_id,
        )

    if not sub_rows:
        await update.message.reply_text(
            "Cô chưa có subscription nào.\nGõ /add để tạo.",
        )
        return

    lines = ["<b>📋 Subscriptions của cô:</b>\n"]
    if sb_row and sb_row["paused_until"]:
        lines.append(
            f"⏸ <b>Tất cả đang tạm ngưng tới {sb_row['paused_until']:%Y-%m-%d %H:%M}</b>\n"
            "Gõ /resume để tiếp tục.\n"
        )

    for r in sub_rows:
        status = "🟢" if r["active"] else "🔴"
        lines.append(f"{status} <b>#{r['id']}</b> — <i>{r['label'] or '(no label)'}</i>")
        if r["skills"]:
            lines.append(f"  🔧 {', '.join(r['skills'])}")
        if r["cities"]:
            lines.append(f"  📍 {', '.join(r['cities'])}")
        if r["job_levels"]:
            lines.append(f"  🎯 {', '.join(r['job_levels'])}")
        if r["companies"]:
            lines.append(f"  🏢 {', '.join(r['companies'])}")
        if r["min_salary_vnd"]:
            lines.append(f"  💰 ≥ {r['min_salary_vnd'] / 1_000_000:.0f}M VND")
        lines.append("")

    lines.append("Xóa: <code>/delete &lt;id&gt;</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    if not context.args:
        await update.message.reply_text("Dùng: <code>/delete &lt;id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID phải là số.")
        return

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_alerts.subscriptions WHERE id = $1 AND chat_id = $2",
            sub_id,
            chat_id,
        )

    if result == "DELETE 0":
        await update.message.reply_text(f"❌ Không tìm thấy subscription #{sub_id} của cô.")
    else:
        await update.message.reply_text(f"🗑 Đã xóa subscription #{sub_id}.")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    days = 7
    if context.args:
        try:
            days = int(context.args[0])
            if days < 1 or days > 365:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Số ngày phải từ 1 đến 365.")
            return

    async with pool.acquire() as conn:
        await upsert_subscriber(pool, chat_id, update.effective_user.username if update.effective_user else None)
        await conn.execute(
            """
            UPDATE user_alerts.subscribers
               SET paused_until = now() + make_interval(days => $2)
             WHERE chat_id = $1
            """,
            chat_id,
            days,
        )
    await update.message.reply_text(
        f"⏸ Đã tạm ngưng alerts trong {days} ngày. Gõ /resume để tiếp tục sớm."
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_alerts.subscribers SET paused_until = NULL WHERE chat_id = $1",
            chat_id,
        )
    await update.message.reply_text("▶ Đã tiếp tục nhận alerts.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    keyboard = [
        [
            InlineKeyboardButton("✅ Có, xóa hết", callback_data=f"stop_confirm:{chat_id}"),
            InlineKeyboardButton("❌ Hủy", callback_data="stop_cancel"),
        ]
    ]
    await update.message.reply_text(
        "⚠️ Cô có chắc muốn hủy đăng ký và xóa toàn bộ data?\n"
        "Subscriptions + lịch sử alerts sẽ bị xóa vĩnh viễn.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    if query.data == "stop_cancel":
        await query.edit_message_text("OK, không xóa gì cả.")
        return

    chat_id_str = query.data.split(":", 1)[1]
    chat_id = int(chat_id_str)
    if chat_id != query.from_user.id:
        await query.edit_message_text("⚠️ Chat ID không khớp, không xóa.")
        return

    async with pool.acquire() as conn:
        # CASCADE deletes subscriptions + alert_log
        await conn.execute("DELETE FROM user_alerts.subscribers WHERE chat_id = $1", chat_id)

    await query.edit_message_text("👋 Đã xóa toàn bộ data. Hẹn gặp lại!")


# ─── App lifecycle ──────────────────────────────────────────────────
async def post_init(app: Application) -> None:
    app.bot_data["pool"] = await db_pool()
    LOG.info("DB pool created")


async def post_shutdown(app: Application) -> None:
    pool: asyncpg.Pool = app.bot_data.get("pool")
    if pool:
        await pool.close()
        LOG.info("DB pool closed")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN env var required")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(callback_stop, pattern=r"^stop_"))

    LOG.info("Bot starting (long-polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
