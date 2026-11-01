module.exports = {
  rootDir: __dirname,
  testEnvironment: 'node',
  testMatch: ['<rootDir>/cases/*.test.js'],
  reporters: [
    'default',
    ['jest-junit', {
      suiteName: 'junit-evidence-gate synthetic Jest fixtures',
      suiteNameTemplate: '{filename}',
      reportTestSuiteErrors: 'true',
      noStackTrace: 'true',
      includeConsoleOutput: 'false',
    }],
  ],
};
