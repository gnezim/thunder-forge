#!/bin/sh
# Patches LiteLLM streaming bug where /v1/messages returns input_json_delta
# instead of text_delta for openai/ backend models.
#
# Bug: choice.delta.tool_calls=[] (empty list) is not None, so the adapter
# enters the tool_calls branch and emits input_json_delta with empty partial_json,
# discarding the actual text content.
#
# Affected: LiteLLM 1.81.x, 1.82.x. No upstream fix as of 2026-03-29.
# Remove this patch when LiteLLM fixes the bug upstream.

PATCH_FILE=$(python -c "
import litellm.llms.anthropic.experimental_pass_through.adapters.transformation as t
print(t.__file__)
" 2>/dev/null)

LITELLM_VERSION=$(python -c "
try:
    import litellm
    v = getattr(litellm, '__version__', None) or getattr(litellm, 'version', None)
    if v: print(v)
    else:
        from importlib.metadata import version
        print(version('litellm'))
except Exception:
    print('unknown')
" 2>/dev/null)

# Patch versions known to be affected (1.81.x, 1.82.x)
case "$LITELLM_VERSION" in
  1.81.*|1.82.*)
    if [ -n "$PATCH_FILE" ] && [ -f "$PATCH_FILE" ]; then
      if grep -q 'if choice.delta.tool_calls is not None:' "$PATCH_FILE"; then
        sed -i 's/if choice.delta.tool_calls is not None:/if choice.delta.tool_calls is not None and len(choice.delta.tool_calls) > 0:/' "$PATCH_FILE"
        echo "[litellm-entrypoint] Patched streaming bug in LiteLLM $LITELLM_VERSION ($PATCH_FILE)"
      else
        echo "[litellm-entrypoint] LiteLLM $LITELLM_VERSION — patch already applied or code changed"
      fi
    else
      echo "[litellm-entrypoint] WARNING: could not locate transformation.py (PATCH_FILE=$PATCH_FILE)"
    fi
    ;;
  *)
    echo "[litellm-entrypoint] LiteLLM $LITELLM_VERSION — patch not needed (only 1.81.x/1.82.x affected)"
    ;;
esac

exec "$@"
