from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


DEFAULT_DB_ROOT = Path(r"E:\NapCatQQ\style_data\exports\wechat_decrypt\decrypted")
DEFAULT_OUTPUT = Path(r"E:\WeChatCustomerBot\data\raw_wechat")


@dataclass
class ContactCandidate:
    target: str
    username: str
    display_name: str
    remark: str
    nick_name: str
    source: str
    score: int


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def text(value: object) -> str:
    return str(value or "").strip()


def clean_message(value: object) -> str:
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            return ""
        if "\x00" in decoded or "\ufffd" in decoded:
            return ""
        raw = decoded
    else:
        raw = text(value)
    if not raw:
        return ""
    return raw.replace("\u2005", " ").replace("\u200b", "").strip()


def safe_name(value: str) -> str:
    return "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in value).strip() or "unknown"


def find_contacts(db_root: Path, targets: list[str]) -> list[ContactCandidate]:
    candidates: dict[tuple[str, str], ContactCandidate] = {}
    contact_db = db_root / "contact" / "contact.db"
    session_db = db_root / "session" / "session.db"

    if contact_db.exists():
        with connect(contact_db) as con:
            rows = con.execute(
                """
                select username, remark, nick_name, alias, quan_pin, remark_quan_pin, description
                from contact
                where delete_flag = 0 or delete_flag is null
                """
            ).fetchall()
            for row in rows:
                fields = {
                    "remark": text(row["remark"]),
                    "nick_name": text(row["nick_name"]),
                    "alias": text(row["alias"]),
                    "quan_pin": text(row["quan_pin"]),
                    "remark_quan_pin": text(row["remark_quan_pin"]),
                    "description": text(row["description"]),
                    "username": text(row["username"]),
                }
                haystack = "\n".join(fields.values())
                for target in targets:
                    if target in haystack:
                        score = 100 if target in {fields["remark"], fields["nick_name"]} else 50
                        add_candidate(candidates, target, fields["username"], fields["remark"], fields["nick_name"], "contact", score)

    if session_db.exists():
        with connect(session_db) as con:
            for table, title_col in [("SessionNoContactInfoTable", "session_title")]:
                if table_exists(con, table):
                    for row in con.execute(f"select username, {title_col} as title from {table}"):
                        haystack = f"{text(row['username'])}\n{text(row['title'])}"
                        for target in targets:
                            if target in haystack:
                                add_candidate(candidates, target, text(row["username"]), text(row["title"]), "", table, 80)
            if table_exists(con, "SessionTable"):
                for row in con.execute("select username, summary, last_sender_display_name from SessionTable"):
                    haystack = f"{text(row['username'])}\n{text(row['summary'])}\n{text(row['last_sender_display_name'])}"
                    for target in targets:
                        if target in haystack:
                            add_candidate(candidates, target, text(row["username"]), target, "", "SessionTable", 40)

    return sorted(candidates.values(), key=lambda x: (x.target, -x.score, x.display_name, x.username))


def add_candidate(
    store: dict[tuple[str, str], ContactCandidate],
    target: str,
    username: str,
    remark: str,
    nick_name: str,
    source: str,
    score: int,
) -> None:
    if not username:
        return
    key = (target, username)
    display = remark or nick_name or username
    current = store.get(key)
    if current is None or score > current.score:
        store[key] = ContactCandidate(target, username, display, remark, nick_name, source, score)


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone()
    return row is not None


def message_table_for_username(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def iter_message_dbs(db_root: Path) -> Iterable[Path]:
    msg_dir = db_root / "message"
    yield from sorted(msg_dir.glob("message_*.db"))


def export_messages(db_root: Path, candidate: ContactCandidate, limit: int) -> list[dict[str, object]]:
    table = message_table_for_username(candidate.username)
    messages: list[dict[str, object]] = []
    for db_path in iter_message_dbs(db_root):
        with connect(db_path) as con:
            if not table_exists(con, table):
                continue
            contact_sender_id = contact_rowid(con, candidate.username)
            rows = con.execute(
                f"""
                select local_id, server_id, local_type, real_sender_id, create_time, source,
                       message_content, compress_content
                from {table}
                order by create_time asc, sort_seq asc
                """
            ).fetchall()
            for row in rows:
                kind = message_kind(row["local_type"])
                content = clean_message(row["message_content"]) or clean_message(row["compress_content"])
                if not content and kind != "text":
                    content = f"[{kind}]"
                if not content:
                    continue
                messages.append(
                    {
                        "db": db_path.name,
                        "local_id": row["local_id"],
                        "server_id": row["server_id"],
                        "type": kind,
                        "raw_type": row["local_type"],
                        "role": infer_role(row["real_sender_id"], contact_sender_id, kind),
                        "created_at": format_time(row["create_time"]),
                        "text": content,
                    }
                )
    if limit > 0:
        return messages[-limit:]
    return messages


def contact_rowid(con: sqlite3.Connection, username: str) -> int | None:
    if not table_exists(con, "Name2Id"):
        return None
    row = con.execute("select rowid from Name2Id where user_name=?", (username,)).fetchone()
    return int(row["rowid"]) if row else None


def infer_role(real_sender_id: object, contact_sender_id: int | None, kind: str) -> str:
    if kind == "system":
        return "system"
    try:
        value = int(real_sender_id or 0)
    except Exception:
        value = 0
    if contact_sender_id is not None and value == contact_sender_id:
        return "customer"
    return "self"


def message_kind(local_type: object) -> str:
    try:
        value = int(local_type)
    except Exception:
        return "unknown"
    if value == 1:
        return "text"
    if value == 34:
        return "voice"
    if value in {3, 43, 48}:
        return "image"
    if value == 47:
        return "emoji"
    if value == 10000:
        return "system"
    return "attachment"


def format_time(value: object) -> str:
    try:
        ts = int(value)
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return text(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--limit", type=int, default=800)
    args = parser.parse_args()

    targets = args.target or ["已读不回", "煊", "叶浪", "鸿烨"]
    args.output.mkdir(parents=True, exist_ok=True)

    candidates = find_contacts(args.db_root, targets)
    print("CONTACT_CANDIDATES")
    for item in candidates:
        print(json.dumps(asdict(item), ensure_ascii=False))

    grouped: dict[str, list[ContactCandidate]] = {}
    for item in candidates:
        grouped.setdefault(item.target, []).append(item)

    summary = {"targets": targets, "exports": []}
    for target in targets:
        options = grouped.get(target, [])
        if not options:
            summary["exports"].append({"target": target, "status": "not_found"})
            continue
        candidate = options[0]
        messages = export_messages(args.db_root, candidate, args.limit)
        payload = {
            "target": target,
            "contact": asdict(candidate),
            "message_count": len(messages),
            "messages": messages,
        }
        out_path = args.output / f"single_{safe_name(target)}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["exports"].append(
            {"target": target, "status": "exported", "path": str(out_path), "message_count": len(messages)}
        )
        print(f"EXPORTED {target} {len(messages)} {out_path}")

    (args.output / "export_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
