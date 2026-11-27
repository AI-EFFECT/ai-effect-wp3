#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 CONFIG.json" >&2
  exit 1
fi

CONFIG_FILE="$1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="$SCRIPT_DIR/assets"
API_URL="${API_URL:-http://localhost:18000}"
API_URL="${API_URL%/}"

for command in curl jq base64 realpath find; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command not found: $command" >&2
    exit 1
  fi
done

if [[ ! -d "$ASSETS_DIR/models" || ! -d "$ASSETS_DIR/data" ]]; then
  echo "Expected assets/models and assets/data directories under $ASSETS_DIR." >&2
  exit 1
fi
ASSETS_ROOT="$(realpath -e -- "$ASSETS_DIR")"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Configuration file not found: $CONFIG_FILE" >&2
  exit 1
fi

for file in blueprint.json dockerinfo.json; do
  if [[ ! -f "$SCRIPT_DIR/export/$file" ]]; then
    echo "Missing export/$file. Run ./generate-export.sh first." >&2
    exit 1
  fi
done

CONFIG=$(jq -ce '
  if type == "object" then .
  else error("Configuration must be a JSON object")
  end
' "$CONFIG_FILE")

asset_path() {
  local field="$1"
  local value="$2"
  local path
  case "$value" in
    "$ASSETS_ROOT"/*|"$SCRIPT_DIR/assets"/*)
      path="$value"
      ;;
    /assets/*)
      path="$ASSETS_DIR/${value#/assets/}"
      ;;
    assets/*)
      path="$ASSETS_DIR/${value#assets/}"
      ;;
    /*)
      path="$value"
      ;;
    *)
      path="$ASSETS_DIR/$value"
      ;;
  esac

  if [[ ! -f "$path" ]]; then
    echo "$field does not point to an existing file in assets/: $value" >&2
    return 1
  fi
  path="$(realpath -e -- "$path")"
  if [[ "$path" != "$ASSETS_ROOT/"* ]]; then
    echo "$field must reference a file inside $ASSETS_DIR: $value" >&2
    return 1
  fi
  printf '%s' "${path#"$ASSETS_ROOT"/}"
}

select_asset() {
  local field="$1"
  local directory="$2"
  local extensions="$3"
  local pattern="${4:-}"
  local value
  local file
  local suffix
  local -a candidates=()

  value="$(jq -r --arg field "$field" '.[$field] // empty' <<<"$CONFIG")"
  if [[ -n "$value" ]]; then
    value="$(asset_path "$field" "$value")" || return 1
  else
    while IFS= read -r -d '' file; do
      [[ -n "$pattern" && "${file,,}" != *"$pattern"* ]] && continue
      suffix="${file##*.}"
      suffix="${suffix,,}"
      if [[ "|$extensions|" == *"|$suffix|"* ]]; then
        candidates+=("$file")
      fi
    done < <(find "$ASSETS_DIR/$directory" -type f -print0)

    if [[ ${#candidates[@]} -ne 1 ]]; then
      echo "Cannot choose $field automatically: found ${#candidates[@]} matching files in assets/$directory. Set $field explicitly in $CONFIG_FILE." >&2
      return 1
    fi
    value="${candidates[0]#"$ASSETS_ROOT"/}"
  fi

  CONFIG="$(jq -c --arg field "$field" --arg value "$value" '.[$field] = $value' <<<"$CONFIG")"
}

ANALYSIS="$(jq -r '.analysis // empty' <<<"$CONFIG")"
MODEL_TYPE="$(jq -r '.model_type // empty' <<<"$CONFIG")"
if [[ -z "$ANALYSIS" || -z "$MODEL_TYPE" ]]; then
  echo "CONFIG.json must include analysis and model_type." >&2
  exit 1
fi

select_asset model_path models 'pkl|pickle|joblib|pt|pth|bin|keras|h5|hdf5'
if [[ "$ANALYSIS" == "tabular" ]]; then
  select_asset dataset_path data 'csv|tsv|parquet'
elif [[ "$ANALYSIS" == "timeseries" && "$MODEL_TYPE" == "lstm" ]]; then
  select_asset background_data_path data 'pt|pth|bin' background
  select_asset test_data_path data 'pt|pth|bin' test
elif [[ "$ANALYSIS" != "timeseries" ]]; then
  echo "analysis must be 'tabular' or 'timeseries'." >&2
  exit 1
fi

INPUT="$(jq -ce '{config: .}' <<<"$CONFIG")"

INPUT_B64=$(printf '%s' "$INPUT" | base64 | tr -d '\n')

PAYLOAD=$(jq -n \
  --slurpfile blueprint "$SCRIPT_DIR/export/blueprint.json" \
  --slurpfile dockerinfo "$SCRIPT_DIR/export/dockerinfo.json" \
  --arg input "$INPUT_B64" \
  '{
    blueprint: $blueprint[0],
    dockerinfo: $dockerinfo[0],
    inputs: [{
      protocol: "inline",
      uri: $input,
      format: "json"
    }]
  }')

HEADERS=(-H "Content-Type: application/json")
if [[ -n "${ORCHESTRATOR_API_KEY:-}" ]]; then
  HEADERS+=(-H "Authorization: Bearer $ORCHESTRATOR_API_KEY")
fi

echo "Submitting workflow to $API_URL..."

RESPONSE=$(curl --fail-with-body --silent --show-error \
  --connect-timeout 10 --max-time 60 \
  "${HEADERS[@]}" \
  --data-binary "$PAYLOAD" \
  "$API_URL/workflows")

WORKFLOW_ID=$(printf '%s' "$RESPONSE" |
  jq -er '.workflow_id | select(type == "string" and length > 0)')

echo "Workflow submitted: $WORKFLOW_ID"
echo "Status URL: $API_URL/workflows/$WORKFLOW_ID"
echo "Submission succeeded; analysis completion must be checked separately."