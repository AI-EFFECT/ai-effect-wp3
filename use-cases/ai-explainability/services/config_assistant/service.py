"""Configuration assistant limited to the explainability toolbox capabilities."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
from json import JSONDecodeError
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import HTTPException
from pydantic import BaseModel, Field

from ai_explainability.capabilities import (
    CONFIG_FIELDS,
    EXCEL_MODELS,
    LSTM_EXPLAINERS,
    MODEL_ROUTES,
    NEURAL_EXPLAINERS,
)
from common.concurrent import (
    ExecuteRequest,
    ExecuteResponse,
    TaskManager,
    create_app,
    run_in_background,
    task_manager,
)

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ai-explainability-ollama:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "180"))

TABULAR_MODELS = set(MODEL_ROUTES["tabular"])
TIMESERIES_MODELS = set(MODEL_ROUTES["timeseries"])
MODEL_ALIASES = {
    "random forest": "random_forest",
    "random-forest": "random_forest",
    "neural network": "neural_net",
    "neural-network": "neural_net",
    "feed forward": "feedforward",
    "feed-forward": "feedforward",
}
MODEL_TYPE_HINT = (
    "Model type is the algorithm used to train your model. "
    "Check its training code or ask the model provider; the file extension alone "
    "does not identify it. Do you see Random Forest, XGBoost, MLP, LSTM, or ARIMA?"
)
SYSTEM_PROMPT = f"""You are a concise setup assistant only for this AI Explainability service.
It currently explains:
- Tabular models: {', '.join(MODEL_ROUTES['tabular'])}.
- Time-series models: {', '.join(MODEL_ROUTES['timeseries'])}.
Tree models use SHAP TreeExplainer. Feedforward/MLP neural networks use PyTorch
or TensorFlow, with SHAP backends {', '.join(NEURAL_EXPLAINERS)}.
LSTM uses PyTorch with SHAP backends {', '.join(LSTM_EXPLAINERS)} and a single
forecast output, not multi-step forecasting. ARIMA explains fitted coefficients
and statistical significance, not SHAP values.
Excel export is supported for {', '.join(sorted(EXCEL_MODELS))}, not ARIMA.
For ARIMA, generate_notebook produces coefficient and diagnostic plots, not
a notebook. output_dir is accepted but the workflow controls the output location.
Supported configuration fields: {', '.join(sorted(CONFIG_FIELDS))}.
Do not promise support for arbitrary neural architectures or model training.
File contents, architecture compatibility, and installed optional dependencies
are checked by the runner; a supported setup is not a successful analysis.
Tabular analysis needs an analysis type and model type. Time-series analysis needs
analysis="timeseries" and model type arima or lstm. LSTM also needs background and
test tensor files shaped (samples, look_back, features). The user will select or
upload files on the next screen. Never invent file paths, model types, capabilities,
or analysis methods. Do not assist with unrelated requests. If required analysis
details are missing, ask one short follow-up question. If the request is unsupported,
briefly say so and list only the supported model families above.

Reply with only a JSON object. "status" must be exactly "ready", "clarification",
or "unsupported":
{{"status":"ready","message":"brief user-facing text",
"config":{{"analysis":"...","model_type":"..."}}}}
Include only config values explicitly provided or clearly identified by the user.
Use canonical model_type identifiers exactly as listed above."""


class ConfigureRequest(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1, max_length=12)


class _AssistantError(ValueError):
    """A concise configuration error suitable for display to the user."""


class _AssistantUnavailable(_AssistantError):
    """The local language model could not be reached or used."""


def _decode_reference(reference: dict[str, Any]) -> Any:
    protocol = reference.get("protocol")
    uri = str(reference.get("uri", ""))
    try:
        if protocol == "inline":
            return json.loads(base64.b64decode(uri, validate=True).decode("utf-8"))
        if protocol == "file":
            return json.loads(Path(uri).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, JSONDecodeError, ValueError) as exc:
        raise _AssistantError("Workflow input must contain valid UTF-8 JSON.") from exc
    raise _AssistantError(f"Unsupported input protocol: {protocol!r}")


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    unknown_fields = set(config) - CONFIG_FIELDS
    if unknown_fields:
        raise _AssistantError(
            "Unsupported configuration settings: "
            f"{', '.join(sorted(unknown_fields))}. Remove them or revise the configuration."
        )

    normalized = {key: value for key, value in config.items() if key in CONFIG_FIELDS}
    analysis = normalized.get("analysis")
    if isinstance(analysis, str):
        analysis = analysis.strip().lower()
        normalized["analysis"] = {
            "time-series": "timeseries",
            "time series": "timeseries",
            "time_series": "timeseries",
        }.get(analysis, analysis)
    elif analysis is not None:
        return _clarification(
            "Is this a tabular or time-series model? I support tabular models and ARIMA/LSTM time-series models.",
            normalized,
        )
    model_type = normalized.get("model_type")
    if isinstance(model_type, str):
        model_type = model_type.strip().lower()
        normalized["model_type"] = MODEL_ALIASES.get(model_type, model_type)
    elif model_type is not None:
        return _clarification(MODEL_TYPE_HINT, normalized)

    model_type = normalized.get("model_type")
    if not normalized.get("analysis"):
        if model_type in TIMESERIES_MODELS:
            normalized["analysis"] = "timeseries"
        elif model_type in TABULAR_MODELS:
            normalized["analysis"] = "tabular"
    supported_models = (
        TABULAR_MODELS if normalized.get("analysis") == "tabular"
        else TIMESERIES_MODELS if normalized.get("analysis") == "timeseries"
        else set()
    )
    if normalized.get("analysis") and normalized["analysis"] not in {"tabular", "timeseries"}:
        return {
            "status": "unsupported",
            "message": "I can help with tabular analysis or time-series ARIMA/LSTM analysis.",
            "config": {},
        }
    if normalized.get("analysis") not in {"tabular", "timeseries"}:
        return _clarification(
            "Is this a tabular or time-series model? I support tabular models and ARIMA/LSTM time-series models.",
            normalized,
        )
    if not model_type:
        models = (
            "random_forest, xgboost, or a feedforward neural network"
            if normalized["analysis"] == "tabular"
            else "ARIMA or LSTM"
        )
        return _clarification(
            f"Which model type are you using? Supported: {models}. "
            "Check the training code or ask the model provider. You can reply \"I don't know\".",
            normalized,
        )
    if model_type not in supported_models:
        return {
            "status": "unsupported",
            "message": "That model type is not supported for this analysis. I can explain the supported tabular, ARIMA, and LSTM models.",
            "config": {},
        }
    if normalized.get("save_excel") and model_type not in EXCEL_MODELS:
        return {
            "status": "unsupported",
            "message": "ARIMA supports coefficient analysis and diagnostic plots, not Excel export. Set save_excel to false.",
            "config": {},
        }
    backends = (
        LSTM_EXPLAINERS if model_type == "lstm"
        else NEURAL_EXPLAINERS
        if MODEL_ROUTES[normalized["analysis"]][model_type][1] == "FeedForwardExplainer"
        else None
    )
    if backends is not None and "explainer_type" in normalized:
        if normalized["explainer_type"] not in backends:
            return {
                "status": "unsupported",
                "message": f"{model_type} supports explainer_type: {', '.join(backends)}.",
                "config": {},
            }
    if model_type == "lstm":
        return {
            "status": "ready",
            "message": "Supported setup: LSTM time-series analysis. Add background and test tensor files shaped (samples, look_back, features) on the next screen.",
            "config": normalized,
        }
    return {
        "status": "ready",
        "message": f"Supported setup: {normalized['analysis']} analysis with {model_type}. Select or upload the required files next.",
        "config": normalized,
    }


def _clarification(message: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"status": "clarification", "message": message, "config": config}


def _parse_assistant_response(content: str) -> dict[str, Any]:
    try:
        result = json.loads(content)
    except JSONDecodeError as exc:
        raise _AssistantError("The setup assistant returned an invalid response. Please try again.") from exc
    if not isinstance(result, dict) or not isinstance(result.get("config", {}), dict):
        raise _AssistantError("The setup assistant returned an invalid response. Please try again.")

    status = result.get("status")
    config = result.get("config", {})
    if status == "unsupported":
        return {
            "status": "unsupported",
            "message": "I can help with tabular models (random forest, XGBoost, feedforward neural networks) and time-series ARIMA or LSTM analysis.",
            "config": {},
        }
    if status not in {"ready", "clarification"}:
        raise _AssistantError("The setup assistant returned an invalid response. Please try again.")

    try:
        validated = _normalize_config(config)
    except _AssistantError as exc:
        return {
            "status": "unsupported",
            "message": str(exc),
            "config": {},
        }
    if validated["status"] != "ready":
        return validated
    return validated


def _ask_assistant(messages: list[dict[str, str]]) -> dict[str, Any]:
    safe_messages = [
        {"role": item["role"], "content": item["content"][:4000]}
        for item in messages[-10:]
        if item.get("role") in {"user", "assistant"} and item.get("content", "").strip()
    ]
    if not safe_messages or safe_messages[-1]["role"] != "user":
        raise _AssistantError("Please enter a request about an explainability analysis.")
    answer = safe_messages[-1]["content"].strip().lower().rstrip(".")
    if answer in {"i don't know", "i dont know", "not sure", "i'm not sure", "unsure"}:
        return _clarification(MODEL_TYPE_HINT, {})
    confirmed_models = {
        **{name: name for name in TABULAR_MODELS | TIMESERIES_MODELS},
        **MODEL_ALIASES,
    }
    for model in TIMESERIES_MODELS:
        for suffix in ("timeseries", "time-series", "time series"):
            confirmed_models[f"{model} {suffix}"] = model
    short_answer = re.sub(r"\s+models?$", "", re.sub(r"\s+", " ", answer))
    confirmed_model = confirmed_models.get(short_answer)
    if confirmed_model:
        return _normalize_config({"model_type": confirmed_model})
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "format": "json",
                "stream": False,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *safe_messages],
            },
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
    except (requests.RequestException, KeyError, TypeError, JSONDecodeError) as exc:
        logger.exception("Explainability configuration assistant request failed")
        raise _AssistantUnavailable(
            "The setup assistant is unavailable. Please try again or continue to the manual setup."
        ) from exc
    if not isinstance(content, str):
        raise _AssistantError("The setup assistant returned an invalid response. Please try again.")
    return _parse_assistant_response(content)


def _prepare_input(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _AssistantError("Workflow input must be a JSON object.")

    config: Any = payload.get("config")
    config_json = payload.get("config_json")
    if isinstance(config_json, str) and config_json.strip():
        try:
            config = json.loads(config_json)
        except JSONDecodeError as exc:
            raise _AssistantError("Analysis configuration must be valid JSON.") from exc
    elif config is None and ("analysis" in payload or "model_type" in payload):
        config = {
            key: value for key, value in payload.items()
            if key not in {"config", "config_json", "messages", "prompt"}
        }

    if isinstance(config, dict):
        result = _normalize_config(config)
        if result["status"] == "ready":
            _validate_run_config(result["config"])
        return result
    if isinstance(payload.get("messages"), list):
        result = _ask_assistant(payload["messages"])
        if result["status"] == "ready":
            _validate_run_config(result["config"])
        return result
    if isinstance(payload.get("prompt"), str) and payload["prompt"].strip():
        result = _ask_assistant([{"role": "user", "content": payload["prompt"]}])
        if result["status"] == "ready":
            _validate_run_config(result["config"])
        return result
    raise _AssistantError("Provide an analysis configuration or a request for one.")


def _validate_run_config(config: dict[str, Any]) -> None:
    required = ["model_path"]
    if config["analysis"] == "tabular":
        required.append("dataset_path")
    if config["model_type"] == "lstm":
        required.extend(("background_data_path", "test_data_path"))
    missing = [field for field in required if not config.get(field)]
    if missing:
        names = ", ".join(missing)
        raise _AssistantError(
            f"Add or upload {names} on the setup screen before running this analysis."
        )


def configure_messages(messages: list[dict[str, str]]) -> dict[str, Any]:
    if not messages or not messages[-1].get("content", "").strip():
        raise HTTPException(status_code=400, detail="Enter a request before continuing.")
    try:
        return _ask_assistant(messages)
    except _AssistantUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except _AssistantError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _publish_output(workflow_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, str]:
    output_dir = DATA_DIR / workflow_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{task_id}.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"protocol": "file", "uri": str(output_path), "format": "json"}


def execute_ConfigureAnalysis(request: ExecuteRequest) -> ExecuteResponse:
    task_manager.register_task(request.task_id, request)
    run_in_background(request.task_id, _configure_analysis, request)
    return ExecuteResponse(status="running", task_id=request.task_id)


def _configure_analysis(
    task_id: str,
    request: ExecuteRequest,
    manager: TaskManager,
) -> None:
    try:
        supplied = _decode_reference(request.inputs[0]) if request.inputs else {}
        result = _prepare_input(supplied)
        if result["status"] != "ready":
            raise _AssistantError(result["message"])
        manager.complete_task(
            task_id,
            _publish_output(
                request.workflow_id,
                task_id,
                {"status": "ready", "message": result["message"], "config": result["config"]},
            ),
        )
    except _AssistantError as exc:
        logger.info("Analysis setup needs revision: %s", exc)
        manager.fail_task(task_id, str(exc))
    except Exception:
        logger.exception("Could not prepare explainability configuration")
        manager.fail_task(task_id, "Could not prepare the explainability configuration.")


app = create_app(sys.modules[__name__])


@app.post("/assistant/configure")
def preview_configuration(request: ConfigureRequest) -> dict[str, Any]:
    return configure_messages(request.messages)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
    )
