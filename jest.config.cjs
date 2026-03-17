// jest.config.cjs
module.exports = {
  testEnvironment: 'node',
  testMatch: ['**/tests/**/*.test.js'],
  clearMocks: true,
  transform: {},
  setupFiles: ['./tests/setup.js']
};