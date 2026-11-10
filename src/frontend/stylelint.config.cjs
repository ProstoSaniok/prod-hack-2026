module.exports = {
  extends: ['stylelint-config-standard'],
  ignoreFiles: [
    'node_modules/**',
    '.angular/**',
    'dist/**',
    'out-tsc/**',
    'playwright-report/**',
    'test-results/**',
    'public/**',
  ],
  rules: {
    // Keep component state overrides near their owning blocks.
    'no-descending-specificity': null,
    // Keep explicit Safari fallbacks used by the glass and gradient effects.
    'property-no-vendor-prefix': [
      true,
      { ignoreProperties: ['backdrop-filter', 'background-clip'] },
    ],
    // CSS custom elements include Angular and Taiga UI components.
    'selector-type-no-unknown': [true, { ignoreTypes: [/^(app|tui)-/] }],
  },
};
