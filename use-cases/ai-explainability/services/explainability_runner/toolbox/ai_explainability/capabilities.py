"""Lightweight capability contract shared by the explainer and setup assistant."""

MODEL_ROUTES = {
    "tabular": {
        "random_forest": ("analysis.tabular.tree_based", "RFExplainer"),
        "xgboost": ("analysis.tabular.tree_based", "RFExplainer"),
        "feedforward": ("analysis.tabular.neural", "FeedForwardExplainer"),
        "mlp": ("analysis.tabular.neural", "FeedForwardExplainer"),
        "neural_net": ("analysis.tabular.neural", "FeedForwardExplainer"),
    },
    "timeseries": {
        "arima": ("analysis.timeseries.arima_stats", "ARIMAExplainer"),
        "lstm": ("analysis.timeseries.lstm_pytorch", "LSTMExplainer"),
    },
}

NEURAL_EXPLAINERS = ("kernel", "deep", "gradient")
LSTM_EXPLAINERS = ("gradient", "deep")
EXCEL_MODELS = frozenset(MODEL_ROUTES["tabular"]) | {"lstm"}

CONFIG_FIELDS = frozenset({
    "analysis", "model_type", "package", "framework",
    "model_path", "dataset_path", "background_data_path", "test_data_path",
    "feature_names", "target_index", "output_labels",
    "output_dir", "save_excel", "generate_notebook",
    "dataset_scope", "subset_end", "explainer_type",
    "background_size", "kernel_nsamples",
    "input_dim", "hidden_size", "look_back", "look_ahead", "explain_subset",
})
