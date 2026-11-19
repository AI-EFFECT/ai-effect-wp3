// AI Explainability plugin for the shared workflow UI (workflow-ui/).
//
// The shared UI writes uploaded file paths into config_json. This plugin adds
// the explainability-specific rules on top: placeholder keys, feature-name
// suggestions from inspected files, model_type guidance, and validation of
// the files each model type needs.

(globalThis.WorkflowUIPlugins ||= []).push({
  name: 'ai-explainability',
  suggestedFeatures: null,
  suggestedFeatureSource: null,

  reset() {
    this.suggestedFeatures = null;
    this.suggestedFeatureSource = null;
  },

  applyUploads({config, uploads}) {
    if ([...uploads.values()].some(state => state.path)) {
      for (const key of ['analysis', 'model_type']) {
        if (!Object.hasOwn(config, key)) config[key] = '';
      }
    }

    // Suggest feature names from the file the model actually reads: the LSTM
    // tensor for LSTM models, otherwise the tabular dataset. Names the user
    // typed are never replaced; earlier automatic suggestions are.
    const tensorSource = ['test_data_upload', 'dataset_upload', 'background_data_upload']
      .find(name => uploads.get(name)?.metadata?.kind === 'tensor');
    const source = config.model_type === 'lstm' ? tensorSource : 'dataset_upload';
    const metadata = uploads.get(source)?.metadata;
    const canSuggest = metadata?.kind === 'table'
      ? config.analysis !== 'timeseries' && !['lstm', 'arima'].includes(config.model_type)
      : metadata?.kind === 'tensor' && config.model_type === 'lstm';
    const namesAreManaged = !Object.hasOwn(config, 'feature_names')
      || (this.suggestedFeatures !== null
        && JSON.stringify(config.feature_names) === JSON.stringify(this.suggestedFeatures));
    if (canSuggest && namesAreManaged) {
      config.feature_names = metadata.feature_names;
      this.suggestedFeatures = [...metadata.feature_names];
      this.suggestedFeatureSource = source;
    }
  },

  uploadRemoved({field, config}) {
    if (this.suggestedFeatureSource === field.name
        && JSON.stringify(config.feature_names) === JSON.stringify(this.suggestedFeatures)) {
      delete config.feature_names;
      this.reset();
    }
  },

  uploadGuidance({config, uploads}) {
    if (config.model_type || !uploads.size) return null;
    return {
      attention: true,
      messages: ['Specify model_type in the configuration or use the assistant; file extensions do not identify the algorithm.'],
    };
  },

  validate({config, uploads}) {
    if (!config) return;
    const required = ['model_path'];
    if (config.analysis === 'tabular') required.push('dataset_path');
    if (config.model_type === 'lstm') required.push('background_data_path', 'test_data_path');
    const missing = required.filter(field => !config[field]);
    if (missing.length) {
      throw new Error(`Add or upload ${missing.join(', ')} before running this analysis.`);
    }
    if (config.model_type !== 'lstm') return;
    for (const state of uploads.values()) {
      if (state.metadata?.kind !== 'tensor') continue;
      if (![config.test_data_path, config.background_data_path].includes(state.path)) continue;
      const [, steps, features] = state.metadata.shape;
      if (config.feature_names?.length && config.feature_names.length !== features) {
        throw new Error(`feature_names must have ${features} entries to match ${state.file.name}.`);
      }
      if (config.look_back !== undefined && config.look_back !== steps) {
        throw new Error(`look_back must be ${steps} to match ${state.file.name}.`);
      }
      if (config.input_dim !== undefined && config.input_dim !== features) {
        throw new Error(`input_dim must be ${features} to match ${state.file.name}.`);
      }
    }
  },
});
