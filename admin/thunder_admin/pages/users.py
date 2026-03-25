# admin/thunder_admin/pages/users.py
"""User management page (admin only)."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.auth import hash_password
from thunder_admin.bootstrap import generate_password


def render(user: dict):
    st.header("Users")

    if not user.get("is_admin"):
        st.error("Admin access required")
        return

    users = db.list_users()

    if users:
        for u in users:
            col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
            col1.write(u["username"])
            col2.write("Admin" if u["is_admin"] else "User")
            col3.write(u["created_at"].strftime("%Y-%m-%d"))

            with col4:
                btn1, btn2 = st.columns(2)
                if btn1.button("Reset PW", key=f"reset_{u['id']}"):
                    new_pw = generate_password()
                    db.update_user_password(u["id"], hash_password(new_pw))
                    st.info(
                        f"New password for {u['username']}: `{new_pw}`"
                    )
                # Can't delete yourself
                if u["id"] != user["id"]:
                    if btn2.button(
                        "Delete", key=f"del_{u['id']}", type="secondary"
                    ):
                        db.delete_user(u["id"])
                        st.success(f"Deleted {u['username']}")
                        st.rerun()

    # Add user form
    st.subheader("Create User")
    with st.form("create_user"):
        username = st.text_input("Username")
        is_admin = st.checkbox("Admin")

        if st.form_submit_button("Create User"):
            if not username:
                st.error("Username is required")
            elif db.get_user_by_username(username):
                st.error(f"User '{username}' already exists")
            else:
                password = generate_password()
                db.create_user(
                    username, hash_password(password), is_admin=is_admin
                )
                st.success(
                    f"Created user '{username}'. Password: `{password}`"
                )
                st.rerun()
