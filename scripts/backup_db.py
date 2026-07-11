"""SQLite DB 일자별 백업 — {db_dir}/backup/trade_dash_YYYYMMDD.db, 7일 보관.

PostgreSQL 등 SQLite가 아닌 DATABASE_URL이면 아무것도 하지 않는다.
실행: python scripts/backup_db.py (저장소 루트에서)
"""
from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402

KEEP_DAYS = 7


def main() -> int:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        print(f"[backup] SQLite가 아님 ({url.split(':', 1)[0]}) — 스킵")
        return 0

    db_path = Path(url.removeprefix("sqlite:///"))
    if not db_path.exists():
        print(f"[backup] DB 파일 없음: {db_path} — 스킵")
        return 0

    backup_dir = db_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"trade_dash_{date.today():%Y%m%d}.db"
    shutil.copy2(db_path, dest)
    print(f"[backup] 저장: {dest} ({dest.stat().st_size:,} bytes)")

    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for old in backup_dir.glob("trade_dash_*.db"):
        if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
            old.unlink()
            removed += 1
    if removed:
        print(f"[backup] {KEEP_DAYS}일 경과 백업 {removed}개 삭제")
    return 0


if __name__ == "__main__":
    sys.exit(main())
