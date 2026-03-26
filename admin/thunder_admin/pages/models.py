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
            "models": {},
            "nodes": {},
            "assignments": {},
            "external_endpoints": [],
        }
        st.session_state.setdefault("loaded_config_id", None)

    models = config.get("models", {})

    # Models table
    if models:
        for name, model in list(models.items()):
            source = model.get("source", {})
            with st.expander(f"**{name}** — {source.get('repo', 'N/A')}", expanded=False):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Disk:** {model.get('disk_gb', 0)} GB")
                col2.write(f"**RAM override:** {model.get('ram_gb', 'auto')}")
                col3.write(f"**Max context:** {model.get('max_context', 0):,}")

                col4, col5, col6 = st.columns(3)
                col4.write(f"**KV/32k:** {model.get('kv_per_32k_gb', 0)} GB")
                col5.write(f"**Serving:** {model.get('serving', '') or 'mlx_lm.server'}")
                thinking_label = {True: "Enabled", False: "Disabled"}.get(model.get("enable_thinking"), "Default")
                col6.write(f"**Thinking:** {thinking_label}")

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
                            "Disk GB",
                            value=float(model.get("disk_gb", 0)),
                            step=0.1,
                            key=f"edit_disk_{name}",
                        )
                        new_ram = st.number_input(
                            "RAM GB override (0 = auto)",
                            value=float(model.get("ram_gb", 0) or 0),
                            step=0.1,
                            key=f"edit_ram_{name}",
                        )
                        new_context = st.number_input(
                            "Max context",
                            value=model.get("max_context", 0),
                            step=1024,
                            key=f"edit_ctx_{name}",
                        )
                        new_kv = st.number_input(
                            "KV per 32k GB",
                            value=float(model.get("kv_per_32k_gb", 0)),
                            step=0.01,
                            key=f"edit_kv_{name}",
                        )
                        new_serving = st.selectbox(
                            "Serving",
                            ["", "embedding", "cli", "mlx-openai-server"],
                            index=["", "embedding", "cli", "mlx-openai-server"].index(model.get("serving", ""))
                            if model.get("serving", "") in ["", "embedding", "cli", "mlx-openai-server"]
                            else 0,
                            key=f"edit_srv_{name}",
                        )
                        _THINKING_OPTIONS = ["Default", "Enabled", "Disabled"]
                        _THINKING_FROM_VAL = {None: 0, True: 1, False: 2}
                        new_thinking_label = st.selectbox(
                            "Thinking mode",
                            _THINKING_OPTIONS,
                            index=_THINKING_FROM_VAL.get(model.get("enable_thinking"), 0),
                            key=f"edit_thinking_{name}",
                        )
                        new_notes = st.text_area(
                            "Notes",
                            value=model.get("notes", ""),
                            key=f"edit_notes_{name}",
                        )

                        with st.expander("Server Tuning (Advanced)", expanded=False):
                            sa = model.get("server_args") or {}
                            st.caption("Leave blank to use mlx_lm.server defaults.")
                            c1, c2, c3 = st.columns(3)
                            new_decode_concurrency = c1.number_input(
                                "Decode concurrency",
                                value=int(sa.get("decode_concurrency") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 32. 0 = use default.",
                                key=f"edit_sa_decode_{name}",
                            )
                            new_prompt_concurrency = c2.number_input(
                                "Prompt concurrency",
                                value=int(sa.get("prompt_concurrency") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 8. 0 = use default.",
                                key=f"edit_sa_prompt_{name}",
                            )
                            new_prefill_step = c3.number_input(
                                "Prefill step size",
                                value=int(sa.get("prefill_step_size") or 0),
                                min_value=0,
                                step=256,
                                help="mlx default: 2048. 0 = use default.",
                                key=f"edit_sa_prefill_{name}",
                            )
                            c4, c5 = st.columns(2)
                            new_cache_size = c4.number_input(
                                "Prompt cache size",
                                value=int(sa.get("prompt_cache_size") or 0),
                                min_value=0,
                                step=1,
                                help="KV cache entry count. 0 = use default.",
                                key=f"edit_sa_cache_size_{name}",
                            )
                            new_cache_bytes = c5.number_input(
                                "Prompt cache bytes",
                                value=int(sa.get("prompt_cache_bytes") or 0),
                                min_value=0,
                                step=1073741824,
                                help="KV cache size in bytes. 0 = use default.",
                                key=f"edit_sa_cache_bytes_{name}",
                            )
                            st.markdown("**Sampling defaults**")
                            c6, c7, c8, c9, c10 = st.columns(5)
                            new_max_tokens = c6.number_input(
                                "Max tokens",
                                value=int(sa.get("max_tokens") or 0),
                                min_value=0,
                                step=256,
                                help="mlx default: 512. 0 = use default.",
                                key=f"edit_sa_max_tokens_{name}",
                            )
                            new_temp = c7.number_input(
                                "Temp",
                                value=float(sa.get("temp") or 0.0),
                                min_value=0.0,
                                max_value=2.0,
                                step=0.05,
                                help="mlx default: 0.0",
                                key=f"edit_sa_temp_{name}",
                            )
                            new_top_p = c8.number_input(
                                "Top-p",
                                value=float(sa.get("top_p") or 0.0),
                                min_value=0.0,
                                max_value=1.0,
                                step=0.05,
                                help="mlx default: 1.0. 0 = use default.",
                                key=f"edit_sa_top_p_{name}",
                            )
                            new_top_k = c9.number_input(
                                "Top-k",
                                value=int(sa.get("top_k") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 0 (disabled)",
                                key=f"edit_sa_top_k_{name}",
                            )
                            new_min_p = c10.number_input(
                                "Min-p",
                                value=float(sa.get("min_p") or 0.0),
                                min_value=0.0,
                                max_value=1.0,
                                step=0.01,
                                help="mlx default: 0.0 (disabled)",
                                key=f"edit_sa_min_p_{name}",
                            )
                            st.markdown("**Speculative decoding**")
                            c11, c12 = st.columns(2)
                            new_draft_model = c11.text_input(
                                "Draft model",
                                value=sa.get("draft_model") or "",
                                help="HF repo or local path for speculative decoding draft model.",
                                key=f"edit_sa_draft_model_{name}",
                            )
                            new_num_draft_tokens = c12.number_input(
                                "Num draft tokens",
                                value=int(sa.get("num_draft_tokens") or 0),
                                min_value=0,
                                step=1,
                                help="mlx default: 3. 0 = use default.",
                                key=f"edit_sa_num_draft_{name}",
                            )
                            existing_extra = "\n".join(model.get("extra_args") or [])
                            new_extra_args_text = st.text_area(
                                "Extra args (one flag per line)",
                                value=existing_extra,
                                help="Arbitrary mlx_lm.server flags. These are appended after all named args above.",
                                key=f"edit_sa_extra_{name}",
                            )

                        col_save, col_cancel = st.columns(2)
                        if col_save.form_submit_button("Save"):
                            model["disk_gb"] = new_disk
                            model["ram_gb"] = new_ram if new_ram > 0 else None
                            model["max_context"] = new_context
                            model["kv_per_32k_gb"] = new_kv
                            model["serving"] = new_serving
                            new_thinking = {"Default": None, "Enabled": True, "Disabled": False}[new_thinking_label]
                            if new_thinking is None:
                                model.pop("enable_thinking", None)
                            else:
                                model["enable_thinking"] = new_thinking
                            model["notes"] = new_notes

                            # Build server_args dict — only non-zero/non-empty values
                            new_sa = {}
                            if new_decode_concurrency > 0:
                                new_sa["decode_concurrency"] = new_decode_concurrency
                            if new_prompt_concurrency > 0:
                                new_sa["prompt_concurrency"] = new_prompt_concurrency
                            if new_prefill_step > 0:
                                new_sa["prefill_step_size"] = new_prefill_step
                            if new_cache_size > 0:
                                new_sa["prompt_cache_size"] = new_cache_size
                            if new_cache_bytes > 0:
                                new_sa["prompt_cache_bytes"] = new_cache_bytes
                            if new_max_tokens > 0:
                                new_sa["max_tokens"] = new_max_tokens
                            if new_temp > 0.0:
                                new_sa["temp"] = new_temp
                            if new_top_p > 0.0:
                                new_sa["top_p"] = new_top_p
                            if new_top_k > 0:
                                new_sa["top_k"] = new_top_k
                            if new_min_p > 0.0:
                                new_sa["min_p"] = new_min_p
                            if new_draft_model.strip():
                                new_sa["draft_model"] = new_draft_model.strip()
                            if new_num_draft_tokens > 0:
                                new_sa["num_draft_tokens"] = new_num_draft_tokens
                            model["server_args"] = new_sa if new_sa else None

                            # Parse extra_args text area
                            parsed_extra = [line for line in new_extra_args_text.splitlines() if line.strip()]
                            model["extra_args"] = parsed_extra if parsed_extra else None

                            if save_config_or_error(st, config, user, f"Updated model '{name}'"):
                                del st.session_state[f"editing_model_{name}"]
                                st.success(f"Updated '{name}'")
                                st.rerun()
                        if col_cancel.form_submit_button("Cancel"):
                            del st.session_state[f"editing_model_{name}"]
                            st.rerun()

                if btn_col2.button("Delete", key=f"delete_{name}", type="secondary"):
                    # Check for assignments referencing this model
                    refs = []
                    for node_name, slots in config.get("assignments", {}).items():
                        for slot in slots:
                            if slot.get("model") == name:
                                refs.append(f"{node_name}:{slot.get('port', '?')}")
                    if refs:
                        st.warning(f"This model is assigned to: {', '.join(refs)}. Remove assignments and delete?")
                        if st.button("Confirm delete", key=f"confirm_delete_{name}"):
                            for node_name in list(config.get("assignments", {}).keys()):
                                config["assignments"][node_name] = [
                                    s for s in config["assignments"][node_name] if s.get("model") != name
                                ]
                            del config["models"][name]
                            if save_config_or_error(
                                st,
                                config,
                                user,
                                f"Deleted model '{name}' and its assignments",
                            ):
                                st.success(f"Deleted '{name}'")
                                st.rerun()
                    else:
                        del config["models"][name]
                        if save_config_or_error(st, config, user, f"Deleted model '{name}'"):
                            st.success(f"Deleted '{name}'")
                            st.rerun()
    else:
        st.info("No models configured. Add one below.")

    # Add model form
    st.subheader("Add Model")
    with st.form("add_model"):
        name = st.text_input("Model name (short identifier, e.g. 'coder')")
        repo = st.text_input("HuggingFace repo (e.g. mlx-community/Qwen3-Coder-Next-4bit)")
        auto_fill = st.form_submit_button("Fetch from HuggingFace")

    if auto_fill and repo:
        try:
            with st.spinner("Fetching model info..."):
                info = fetch_model_info(repo)
                config_json = fetch_config_json(repo) or {}
                meta = parse_model_metadata(info, config_json)
            st.session_state["hf_pending"] = {"name": name, "repo": repo, "meta": meta}
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg:
                st.error("This repo requires authentication. Set HF_TOKEN in docker-compose environment.")
            else:
                st.warning(f"HF API unavailable: {error_msg}. Enter values manually.")

    if st.session_state.get("hf_pending"):
        pending = st.session_state["hf_pending"]
        pending_name = pending["name"]
        pending_repo = pending["repo"]
        meta = pending["meta"]

        if not meta["has_safetensors"]:
            st.warning("Repo does not contain safetensors files.")
        if not meta["has_tokenizer"]:
            st.warning("Repo missing tokenizer_config.json (required by mlx_lm.server).")

        with st.form("confirm_model"):
            st.write(f"**Repo:** {pending_repo}")
            disk_gb = st.number_input("Disk GB", value=meta["disk_gb"], step=0.1)
            ram_gb = st.number_input("RAM GB override (0 = auto)", value=0.0, step=0.1)
            max_context = st.number_input("Max context", value=meta["max_context"], step=1024)
            kv_per_32k_gb = st.number_input("KV per 32k GB", value=meta["kv_per_32k_gb"], step=0.01)
            revision = st.text_input("Revision", value=meta["revision"])
            serving = st.selectbox("Serving", ["", "embedding", "cli", "mlx-openai-server"])
            thinking_label = st.selectbox("Thinking mode", ["Default", "Enabled", "Disabled"])
            notes = st.text_area("Notes")
            col_add, col_cancel = st.columns(2)

            if col_add.form_submit_button("Add Model"):
                if not pending_name:
                    st.error("Model name is required")
                elif pending_name in models:
                    st.error(f"Model '{pending_name}' already exists")
                else:
                    new_thinking = {"Default": None, "Enabled": True, "Disabled": False}[thinking_label]
                    new_model = {
                        "source": {
                            "type": "huggingface",
                            "repo": pending_repo,
                            "revision": revision,
                        },
                        "disk_gb": disk_gb,
                        "kv_per_32k_gb": kv_per_32k_gb,
                        "max_context": max_context,
                        "extra_args": None,
                        "serving": serving,
                        "notes": notes,
                    }
                    if new_thinking is not None:
                        new_model["enable_thinking"] = new_thinking
                    if ram_gb > 0:
                        new_model["ram_gb"] = ram_gb
                    config["models"][pending_name] = new_model
                    if save_config_or_error(st, config, user, f"Added model '{pending_name}'"):
                        del st.session_state["hf_pending"]
                        st.success(f"Added '{pending_name}'")
                        st.rerun()

            if col_cancel.form_submit_button("Cancel"):
                del st.session_state["hf_pending"]
                st.rerun()
