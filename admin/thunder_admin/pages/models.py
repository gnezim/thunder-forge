# admin/thunder_admin/pages/models.py
"""Models config page — CRUD with HuggingFace auto-fill."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.config import save_config_or_error
from thunder_admin.hf import fetch_config_json, fetch_model_info, parse_model_metadata


def render(user: dict):
    st.header("Models")

    current = db.get_current_config()
    if current:
        config = current["config"]
        st.session_state.setdefault("loaded_config_id", current["id"])
    else:
        config = {
            "models": {}, "nodes": {},
            "assignments": {}, "external_endpoints": [],
        }
        st.session_state.setdefault("loaded_config_id", None)

    models = config.get("models", {})

    # Models table
    if models:
        for name, model in models.items():
            source = model.get("source", {})
            with st.expander(
                f"**{name}** — {source.get('repo', 'N/A')}", expanded=False
            ):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Disk:** {model.get('disk_gb', 0)} GB")
                col2.write(f"**RAM override:** {model.get('ram_gb', 'auto')}")
                col3.write(f"**Max context:** {model.get('max_context', 0):,}")

                col4, col5 = st.columns(2)
                col4.write(f"**KV/32k:** {model.get('kv_per_32k_gb', 0)} GB")
                col5.write(
                    f"**Serving:** {model.get('serving', '') or 'mlx_lm.server'}"
                )

                if model.get("notes"):
                    st.caption(model["notes"])

                btn_col1, btn_col2 = st.columns(2)

                # Edit model
                if btn_col1.button("Edit", key=f"edit_{name}"):
                    st.session_state[f"editing_model_{name}"] = True
                    st.rerun()

                if st.session_state.get(f"editing_model_{name}"):
                    with st.form(f"edit_model_{name}"):
                        new_disk = st.number_input(
                            "Disk GB", value=float(model.get("disk_gb", 0)), step=0.1,
                            key=f"edit_disk_{name}",
                        )
                        new_ram = st.number_input(
                            "RAM GB override (0 = auto)",
                            value=float(model.get("ram_gb", 0) or 0), step=0.1,
                            key=f"edit_ram_{name}",
                        )
                        new_context = st.number_input(
                            "Max context", value=model.get("max_context", 0), step=1024,
                            key=f"edit_ctx_{name}",
                        )
                        new_kv = st.number_input(
                            "KV per 32k GB",
                            value=float(model.get("kv_per_32k_gb", 0)), step=0.01,
                            key=f"edit_kv_{name}",
                        )
                        new_serving = st.selectbox(
                            "Serving",
                            ["", "embedding", "cli", "mlx-openai-server"],
                            index=["", "embedding", "cli", "mlx-openai-server"].index(
                                model.get("serving", "")
                            ) if model.get("serving", "") in ["", "embedding", "cli", "mlx-openai-server"] else 0,
                            key=f"edit_srv_{name}",
                        )
                        new_notes = st.text_area(
                            "Notes", value=model.get("notes", ""),
                            key=f"edit_notes_{name}",
                        )
                        col_save, col_cancel = st.columns(2)
                        if col_save.form_submit_button("Save"):
                            model["disk_gb"] = new_disk
                            model["ram_gb"] = new_ram if new_ram > 0 else None
                            model["max_context"] = new_context
                            model["kv_per_32k_gb"] = new_kv
                            model["serving"] = new_serving
                            model["notes"] = new_notes
                            if save_config_or_error(st,
                                config, user, f"Updated model '{name}'"
                            ):
                                del st.session_state[f"editing_model_{name}"]
                                st.success(f"Updated '{name}'")
                                st.rerun()
                        if col_cancel.form_submit_button("Cancel"):
                            del st.session_state[f"editing_model_{name}"]
                            st.rerun()

                if btn_col2.button(
                    "Delete", key=f"delete_{name}", type="secondary"
                ):
                    # Check for assignments referencing this model
                    refs = []
                    for node_name, slots in config.get("assignments", {}).items():
                        for slot in slots:
                            if slot.get("model") == name:
                                refs.append(
                                    f"{node_name}:{slot.get('port', '?')}"
                                )
                    if refs:
                        st.warning(
                            f"This model is assigned to: {', '.join(refs)}. "
                            f"Remove assignments and delete?"
                        )
                        if st.button(
                            "Confirm delete", key=f"confirm_delete_{name}"
                        ):
                            for node_name in list(
                                config.get("assignments", {}).keys()
                            ):
                                config["assignments"][node_name] = [
                                    s
                                    for s in config["assignments"][node_name]
                                    if s.get("model") != name
                                ]
                            del config["models"][name]
                            if save_config_or_error(st,
                                config, user,
                                f"Deleted model '{name}' and its assignments",
                            ):
                                st.success(f"Deleted '{name}'")
                                st.rerun()
                    else:
                        del config["models"][name]
                        if save_config_or_error(st,
                            config, user, f"Deleted model '{name}'"
                        ):
                            st.success(f"Deleted '{name}'")
                            st.rerun()
    else:
        st.info("No models configured. Add one below.")

    # Add model form
    st.subheader("Add Model")
    with st.form("add_model"):
        name = st.text_input("Model name (short identifier, e.g. 'coder')")
        repo = st.text_input(
            "HuggingFace repo (e.g. mlx-community/Qwen3-Coder-Next-4bit)"
        )
        auto_fill = st.form_submit_button("Fetch from HuggingFace")

    if auto_fill and repo:
        try:
            with st.spinner("Fetching model info..."):
                info = fetch_model_info(repo)
                config_json = fetch_config_json(repo) or {}
                meta = parse_model_metadata(info, config_json)

            if not meta["has_safetensors"]:
                st.warning("Repo does not contain safetensors files.")
            if not meta["has_tokenizer"]:
                st.warning(
                    "Repo missing tokenizer_config.json "
                    "(required by mlx_lm.server)."
                )

            with st.form("confirm_model"):
                st.write(f"**Repo:** {repo}")
                disk_gb = st.number_input(
                    "Disk GB", value=meta["disk_gb"], step=0.1
                )
                ram_gb = st.number_input(
                    "RAM GB override (0 = auto)", value=0.0, step=0.1
                )
                max_context = st.number_input(
                    "Max context", value=meta["max_context"], step=1024
                )
                kv_per_32k_gb = st.number_input(
                    "KV per 32k GB", value=meta["kv_per_32k_gb"], step=0.01
                )
                revision = st.text_input("Revision", value=meta["revision"])
                serving = st.selectbox(
                    "Serving", ["", "embedding", "cli", "mlx-openai-server"]
                )
                notes = st.text_area("Notes")

                if st.form_submit_button("Add Model"):
                    if not name:
                        st.error("Model name is required")
                    elif name in models:
                        st.error(f"Model '{name}' already exists")
                    else:
                        new_model = {
                            "source": {
                                "type": "huggingface",
                                "repo": repo,
                                "revision": revision,
                            },
                            "disk_gb": disk_gb,
                            "kv_per_32k_gb": kv_per_32k_gb,
                            "max_context": max_context,
                            "extra_args": None,
                            "serving": serving,
                            "notes": notes,
                        }
                        if ram_gb > 0:
                            new_model["ram_gb"] = ram_gb
                        config["models"][name] = new_model
                        if save_config_or_error(st,
                            config, user, f"Added model '{name}'"
                        ):
                            st.success(f"Added '{name}'")
                            st.rerun()

        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg:
                st.error(
                    "This repo requires authentication. "
                    "Set HF_TOKEN in docker-compose environment."
                )
            else:
                st.warning(
                    f"HF API unavailable: {error_msg}. "
                    f"Enter values manually."
                )
