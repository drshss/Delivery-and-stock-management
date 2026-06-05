"""Throwaway diagnostic for the manager login issue."""
from app.core.database import SessionLocal
from app.core.security import verify_password
from app.models.user import User

db = SessionLocal()
try:
    users = db.query(User).all()
    print("Total users:", len(users))
    for u in users:
        print(
            f"  id={u.id} email={u.email} role={u.role.value} "
            f"active={u.is_active} hash_prefix={u.hashed_password[:7]} "
            f"hash_len={len(u.hashed_password)}"
        )

    m = db.query(User).filter(User.email == "manager@delivery.com").first()
    print("\nmanager found:", m is not None)
    if m:
        print("verify manager123 ->", verify_password("manager123", m.hashed_password))
finally:
    db.close()
