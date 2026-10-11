"""Manage OMNI-PHARM user accounts (turns on login for every API call when at least one user exists).

  python scripts/users.py add <name> [--role admin|clinician]     (asks for the password, not echoed)
  python scripts/users.py passwd <name>
  python scripts/users.py remove <name>
  python scripts/users.py list

Removing the last user switches login off again (the optional shared PIN then applies).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omnipharm import security


def ask_password() -> str:
    p1 = getpass.getpass("비밀번호(8자 이상): ")
    p2 = getpass.getpass("비밀번호 확인: ")
    if p1 != p2:
        sys.exit("비밀번호가 서로 다릅니다.")
    return p1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "passwd", "remove", "list"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--role", default="clinician", choices=security.ROLES)
    a = ap.parse_args()
    if a.cmd == "list":
        for u in security.list_users() or [{"user": "(없음 - 로그인 꺼짐)", "role": "", "updated": ""}]:
            print(f"{u['user']:20s} {u['role']:10s} {u['updated'] or ''}")
        return
    if not a.name:
        sys.exit("사용자 이름을 지정하세요.")
    if a.cmd == "remove":
        print("삭제했습니다." if security.remove_user(a.name) else "없는 사용자입니다.")
        return
    role = a.role
    if a.cmd == "passwd":
        cur = {u["user"]: u["role"] for u in security.list_users()}
        if a.name not in cur:
            sys.exit("없는 사용자입니다.")
        role = cur[a.name]
    try:
        security.set_user(a.name, ask_password(), role)
    except ValueError as e:
        sys.exit(str(e))
    print(f"저장했습니다: {a.name} ({role}). 서버 재시작 없이 바로 적용됩니다.")


if __name__ == "__main__":
    main()
