#!/bin/sh
# Patches LiteLLM streaming bug where /v1/messages returns input_json_delta
# instead of text_delta for openai/ backend models.
#
# Bug: choice.delta.tool_calls=[] (empty list) is not None, so the adapter
# enters the tool_calls branch and emits input_json_delta with empty partial_json,
# discarding the actual text content.
#
# Affected: LiteLLM >= 1.82.0 (when /v1/messages started routing openai models
# through the Anthropic adapter). No upstream fix as of 2026-03-29.
#
# Tracked: https://github.com/BerriAI/litellm/issues/TBD
# Remove this patch when LiteLLM fixes the bug upstream.

PATCH_FILE="/usr/local/lib/python3.12/site-packages/litellm/llms/anthropic/experimental_pass_through/adapters/transformation.py"
LITELLM_VERSION=$(python -c "import litellm; print(litellm.version)" 2>/dev/null || echo "unknown")

# Only patch versions known to be affected (1.82.x)
case "$LITELLM_VERSION" in
  1.82.*)
    if [ -f "$PATCH_FILE" ]; then
      if grep -q 'if choice.delta.tool_calls is not None:' "$PATCH_FILE"; then
        sed -i 's/if choice.delta.tool_calls is not None:/if choice.delta.tool_calls is not None and len(choice.delta.tool_calls) > 0:/' "$PATCH_FILE"
        echo "[litellm-entrypoint] Patched streaming bug in LiteLLM $LITELLM_VERSION"
      else
        echo "[litellm-entrypoint] LiteLLM $LITELLM_VERSION — patch already applied or code changed"
      fi
    else
      echo "[litellm-entrypoint] WARNING: patch file not found at $PATCH_FILE"
    fi
    ;;
  *)
    echo "[litellm-entrypoint] LiteLLM $LITELLM_VERSION — patch not needed (only 1.82.x affected)"
    ;;
esac

exec "$@"
