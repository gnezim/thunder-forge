# admin/thunder_admin/pages/deploy.py
"""Deploy page — trigger deploys, view streaming output, deploy history."""

from __future__ import annotations

import difflib
import time
from datetime import datetime, timezone

import streamlit as st

from thunder_admin import db
from thunder_admin.config import jsonb_to_yaml
from thunder_admin.deploy import (
    check_gateway_lock_alive,
    kill_gateway_deploy,
    read_gateway_lock,
    start_deploy,
)


def render(user: dict):
    st.header("Deploy")

    current = db.get_current_config()
    if not current:
        st.warning("No config to deploy. Add models and nodes first.")
        return

    # Diff: current config vs last deployed
    st.subheader("Changes to Deploy")
    current_yaml = jsonb_to_yaml(current["config"])
    last_deploy = db.get_last_successful_deploy()

    if last_deploy:
        last_config = db.get_config_version(last_deploy["config_id"])
        if last_config:
            last_yaml = jsonb_to_yaml(last_config["config"])
            diff = difflib.unified_diff(
                last_yaml.splitlines(keepends=True),
                current_yaml.splitlines(keepends=True),
                fromfile=f"v{last_config['id']} (deployed)",
                tofile=f"v{current['id']} (current)",
            )
            diff_str = "".join(diff)
            if diff_str:
                st.code(diff_str, language="diff")
            else:
                st.info("No changes since last deploy")
        else:
            st.code(current_yaml, language="yaml")
    else:
        st.caption("First deploy — full config:")
        st.code(current_yaml, language="yaml")

    # Deploy button or running status
    running = db.get_running_deploy()
    if running:
        st.warning(
            f"Deploy in progress (started by "
            f"{running.get('triggered_by_name', '?')} at "
            f"{running['started_at'].strftime('%H:%M')})"
        )

        # Show streaming output
        output_placeholder = st.empty()
        deploy = db.get_deploy(running["id"])
        if deploy:
            output_placeholder.code(
                deploy.get("output", "") or "Waiting for output..."
            )
            if deploy["status"] == "running":
                # Poll for updates
                time.sleep(2)
                st.rerun()
            elif deploy["status"] == "success":
                st.success("Deploy completed successfully!")
            else:
                st.error(f"Deploy {deploy['status']}")

        # Cancel button
        lock = read_gateway_lock()
        if lock:
            lock_status = check_gateway_lock_alive(lock)
            pid = lock.get("PID")
            if lock_status == "alive" and pid:
                if st.button("Cancel Deploy", type="secondary"):
                    if kill_gateway_deploy(pid):
                        db.update_deploy(
                            running["id"],
                            status="cancelled",
                            finished_at=datetime.now(timezone.utc),
                        )
                        st.warning("Deploy cancelled")
                        st.rerun()
                    else:
                        st.error("Failed to cancel deploy")
            elif lock_status == "stale" and pid:
                st.warning(
                    f"Deploy process (PID {pid}) appears stuck. "
                    f"Force cancel?"
                )
                if st.button("Force Cancel"):
                    if kill_gateway_deploy(str(pid)):
                        db.update_deploy(
                            running["id"],
                            status="cancelled",
                            finished_at=datetime.now(timezone.utc),
                        )
                        st.rerun()
    else:
        if st.button("Deploy", type="primary"):
            deploy_id, error = start_deploy(
                current["id"], user["id"], current_yaml
            )
            if deploy_id:
                st.success(f"Deploy started (ID: {deploy_id})")
                st.rerun()
            else:
                st.error(error)

    # Deploy history
    st.subheader("Deploy History")
    deploys = db.list_deploys(limit=20)
    if deploys:
        for d in deploys:
            status_icons = {
                "success": "green", "failed": "red",
                "running": "orange", "cancelled": "grey",
            }
            color = status_icons.get(d["status"], "grey")
            duration = ""
            if d.get("finished_at") and d.get("started_at"):
                secs = (d["finished_at"] - d["started_at"]).total_seconds()
                duration = f" ({int(secs)}s)"

            with st.expander(
                f":{color}[Deploy #{d['id']}] — "
                f"{d.get('triggered_by_name', '?')} — "
                f"{d['started_at'].strftime('%Y-%m-%d %H:%M')} — "
                f"config v{d['config_id']} — {d['status']}{duration}",
                expanded=False,
            ):
                if d.get("output"):
                    st.code(d["output"])
                else:
                    st.caption("No output recorded")
    else:
        st.info("No deploys yet.")
