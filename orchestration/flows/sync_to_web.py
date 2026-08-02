"""Sync warehouse job data -> web box DB. MOT CHIEU, moi ngay mot lan.

Vi sao ton tai (xem docs/website-warehouse-db-split.md):

Backend JOIN `app.job_applications` voi `dbt_dev_gold.fct_jobs_daily` TRONG MOT
CAU SQL, ma Postgres khong join xuyen database. Khi du lieu app tach sang DB
rieng, web box BUOC phai giu mot ban sao chi-doc cua cac bang job. Day la rang
buoc, khong phai tien nghi.

Vi sao khong dung logical replication: dbt DROP va tao lai bang moi lan chay, nen
publication (theo doi bang theo identity) se dut va phai re-sync tay. Con view thi
khong publish duoc, giet luon hai doi tuong silver. Mot job "nap staging roi swap
trong mot transaction" chiu duoc drop/recreate, xu ly duoc view, va chuyen vai MB
trong duoi mot giay.

CHIEU DUY NHAT LA WAREHOUSE -> WEB. Khong co kenh nguoc, va khong duoc co: sync hai
chieu nghia la hai ben cung ghi mot dong, tuc xung dot ma khong ai giai quyet.
"""
from __future__ import annotations

import os
import time

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

# Doc JSON/JSONB ve dang CHUOI THO thay vi parse thanh dict.
#
# Mac dinh psycopg2 parse jsonb -> dict Python, nhung luc ghi lai no khong biet
# chuyen dict thanh SQL va nem "can't adapt type 'dict'". Job nay chi chuyen byte
# tu A sang B, khong doc noi dung JSON, nen tat parse la vua dung vua nhanh.
# Da vap that: silver_job_detail chet o lan chay thu dau tien.
psycopg2.extras.register_default_json(loads=lambda x: x)
psycopg2.extras.register_default_jsonb(loads=lambda x: x)
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import fmt_duration
from src.utils.config import config

# Doi tuong phai dong bo. Cot `is_view` la cho DE SAI NHAT: silver la VIEW, khong co
# storage de copy — phai SELECT * roi ghi vao mot bang THAT ben web. Bo qua chi tiet
# nay thi lan chay dau tien gay.
#
# GIU NGUYEN ten schema `dbt_dev_*` ben web. Dua het vao mot schema `analytics` cho
# gon thi nghe hay hon, nhung phai sua tien to schema trong 7 file SQL viet tay de
# doi lay zero loi ich chuc nang — moi lan sua la mot co hoi sinh bug.
OBJECTS: list[tuple[str, str, bool]] = [
    ("dbt_dev_gold", "fct_jobs_daily", False),
    ("dbt_dev_gold", "mart_company_hiring", False),
    ("dbt_dev_gold", "mart_salary_by_level", False),
    ("dbt_dev_gold", "mart_skill_demand", False),
    ("dbt_dev_feature", "job_features", False),
    ("dbt_dev_silver", "silver_job_detail", True),
    ("dbt_dev_silver", "silver_skill_long", True),
]


def _web_dsn() -> str:
    """DSN cua Postgres tren web box.

    KHONG co gia tri mac dinh: mot default tro nham vao chinh kho se lam job ghi de
    len nguon cua no. Tha gay ngay luc khoi dong con hon chay am tham sai cho.
    """
    dsn = os.getenv("WEB_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "WEB_DATABASE_URL chua duoc dat. Tro no vao Postgres cua web box qua "
            "tailnet, vi du: postgresql://user:pass@<WEB_TAILNET_IP>:5432/talentpulse"
        )
    return dsn


@task(name="sync_object", retries=2, retry_delay_seconds=20, timeout_seconds=600)
def sync_object(schema: str, name: str, is_view: bool) -> dict:
    """Chuyen mot doi tuong tu kho sang web, doi cho trong MOT transaction.

    Nap vao bang staging `<name>__staging` truoc, roi doi cho trong cung mot
    transaction. Nho vay reader ben web KHONG BAO GIO thay bang dang nap do, va mot
    lan sync that bai se de nguyen du lieu hom qua thay vi de lai bang rong.

    Du lieu job cu mot ngay thi khong sao. `fct_jobs_daily` rong tren website that
    thi khong chap nhan duoc — do la che do that bai can thiet ke de tranh.

    `is_view` khong doi cach tao bang (ca hai truong hop deu tao bang that ben web);
    no o day de danh dau ro nhung doi tuong KHONG co storage ben kho, vi do la thu
    de quen nhat khi them doi tuong moi vao OBJECTS.
    """
    logger = get_run_logger()
    t0 = time.time()
    src_fq = f"{schema}.{name}"
    staging = f"{name}__staging"

    with psycopg2.connect(config.get_db_uri()) as wh:
        with wh.cursor() as wc:
            # Doi tuong nguon co the CHUA TON TAI (dbt chua chay het cac model).
            # Mot bang thieu KHONG duoc phep chan nhung bang con lai: spec doi
            # "loud but harmless" — bao that to, nhung du lieu hom qua o web van
            # nguyen ven va cac doi tuong khac van duoc cap nhat.
            # Da vap that: silver_skill_long chua build lam abort ca flow.
            try:
                wc.execute(f"SELECT * FROM {src_fq}")
            except psycopg2.errors.UndefinedTable:
                logger.error(f"{src_fq}: KHONG TON TAI o kho — bo qua, giu nguyen ban cu o web")
                return {"object": src_fq, "rows": 0, "seconds": 0.0, "missing": True}
            rows = wc.fetchall()
            cols = [d[0] for d in wc.description]

            # Lay KIEU THAT cua tung cot tu nguon.
            #
            # Truoc day khi bang dich chua ton tai, code tao moi cot la `text`.
            # Sync bao "thanh cong, 3019 dong" nhung schema sinh ra lam GAY app:
            # fct_jobs_daily.is_active thanh text, va `WHERE f.is_active AND ...`
            # nem "argument of AND must be type boolean, not type text".
            # Doan kieu la sai; hoi Postgres moi dung.
            wc.execute(
                """
                SELECT attname, format_type(atttypid, atttypmod)
                FROM pg_attribute
                WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped
                ORDER BY attnum
                """,
                (src_fq,),
            )
            coltypes = dict(wc.fetchall())

    with psycopg2.connect(_web_dsn()) as web:
        with web.cursor() as bc:
            bc.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            bc.execute(f"DROP TABLE IF EXISTS {schema}.{staging}")

            # Clone cau truc tu bang dich khi no da co — giu nguyen index/kieu qua
            # cac lan sync. NHUNG chi khi tap cot hai ben con KHOP NHAU.
            #
            # `LIKE <bang dich>` sao chep schema CU. Hom nao mot model dbt them cot
            # (vi du job_features them `source` vao khoa thuc the) thi staging se
            # thieu dung cot do va lenh INSERT ngay duoi nem
            # `column "source" of relation "..." does not exist` — sync gay, va gay
            # MAI cho toi khi co nguoi drop bang dich bang tay.
            #
            # So tap cot truoc: lech thi dung sang tao moi tu KIEU THAT o nguon,
            # tuc dung nhanh ma lan dau tien van di. Doi schema o dbt tu do khong
            # con can thao tac tay o web box nua.
            bc.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{name}",))
            has_target = bc.fetchone()[0]
            target_cols: list[str] = []
            if has_target:
                bc.execute(
                    """
                    SELECT attname FROM pg_attribute
                     WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped
                     ORDER BY attnum
                    """,
                    (f"{schema}.{name}",),
                )
                target_cols = [r[0] for r in bc.fetchall()]

            # So bang TAP HOP: thu tu cot khac nhau khong sao (INSERT liet ke ten
            # cot tuong minh), chi them/bot cot moi la van de.
            if has_target and set(target_cols) == set(cols):
                bc.execute(
                    f"CREATE TABLE {schema}.{staging} "
                    f"(LIKE {schema}.{name} INCLUDING DEFAULTS)"
                )
            else:
                if has_target:
                    logger.warning(
                        f"{src_fq}: tap cot da doi ({len(target_cols)} -> {len(cols)}), "
                        "tao lai bang dich theo kieu that o nguon"
                    )
                col_defs = ", ".join(f'"{c}" {coltypes.get(c, "text")}' for c in cols)
                bc.execute(f"CREATE TABLE {schema}.{staging} ({col_defs})")

            if rows:
                collist = ", ".join(f'"{c}"' for c in cols)
                execute_values(
                    bc,
                    f"INSERT INTO {schema}.{staging} ({collist}) VALUES %s",
                    rows,
                    page_size=1000,
                )

            # Doi cho. Ca hai lenh nam trong cung transaction cua `web` va chi commit
            # khi thoat khoi `with`, nen reader thay bang cu cho toi dung khoanh khac
            # doi. CASCADE vi co the co view/khoa ngoai tro toi.
            bc.execute(f"DROP TABLE IF EXISTS {schema}.{name} CASCADE")
            bc.execute(f"ALTER TABLE {schema}.{staging} RENAME TO {name}")

    dur = time.time() - t0
    logger.info(f"{src_fq}: {len(rows)} rows in {fmt_duration(dur)}")
    return {"object": src_fq, "rows": len(rows), "seconds": round(dur, 2), "missing": False}


@flow(name="sync-to-web")
def sync_to_web_flow() -> dict:
    """Dong bo cac bang job tu kho sang web box.

    Lich chay: SAU skill-extraction (VN 15:00), de no cong bo mot kho da hoan chinh
    chu khong phai mot kho dang build do.
    """
    logger = get_run_logger()
    t0 = time.time()

    results = [sync_object(schema, name, is_view) for schema, name, is_view in OBJECTS]
    total_rows = sum(r["rows"] for r in results)

    # Mot lan sync rong phai NHIN THAY DUOC. Thieu cho nay thi `fct_jobs_daily` = 0
    # dong se troi qua im lang va website hien bang trong ma khong ai biet.
    empty = [r["object"] for r in results if r["rows"] == 0 and not r.get("missing")]
    missing = [r["object"] for r in results if r.get("missing")]

    lines = ["| Object | Rows | Seconds |", "|---|---|---|"]
    lines += [f"| {r['object']} | {r['rows']} | {r['seconds']} |" for r in results]
    if empty:
        lines.append("")
        lines.append(f"**RONG: {', '.join(empty)}** — kiem tra dbt da chay xong chua.")
    if missing:
        lines.append("")
        lines.append(f"**THIEU O KHO: {', '.join(missing)}** — dbt chua build model nay.")

    create_markdown_artifact(
        key="sync-to-web",
        markdown="\n".join(lines),
        description=f"Warehouse -> web: {total_rows} rows in {fmt_duration(time.time() - t0)}",
    )

    if missing:
        logger.error(f"Doi tuong KHONG co o kho: {missing}")
    if empty:
        logger.error(f"Sync hoan tat nhung co bang RONG: {empty}")
    else:
        logger.info(f"Sync xong: {len(results)} objects, {total_rows} rows")

    return {"objects": len(results), "total_rows": total_rows, "empty": empty, "missing": missing}


if __name__ == "__main__":
    import os

    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        # Registers a long-lived Prefect deployment. NOT how production runs this:
        # the schedule that actually fires is the cron in
        # .github/workflows/pipeline-sync-to-web.yml, and run-flow deliberately
        # never forwards PREFECT_DEPLOY (setting it would turn a scheduled job
        # into a process that blocks forever). Kept for parity with the other
        # flows and for anyone running a Prefect worker locally. The cron below
        # mirrors the workflow's "0 9 * * *" UTC == 16:00 VN — change both or
        # neither.
        sync_to_web_flow.serve(
            name="sync-to-web-daily",
            cron="0 9 * * *",
            tags=["sync", "web"],
        )
    else:
        sync_to_web_flow()
