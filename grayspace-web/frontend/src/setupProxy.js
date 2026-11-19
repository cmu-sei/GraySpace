/**
 * Development server only (npm start): forward /api and /deployments (one deployment's routes,
 * docs/deployments.md) to the backend, so the browser makes its
 * requests to one origin, as it does behind nginx in a deployment. Websockets go to the backend
 * directly (REACT_APP_WS_URL in .env.development): the dev server keeps /ws for hot reload. The
 * Aspire AppHost sets GRAYSPACE_API_URL; without it the backend is expected on localhost:8000.
 */
const { createProxyMiddleware } = require('http-proxy-middleware');

module.exports = function (app) {
  const target = process.env.GRAYSPACE_API_URL || 'http://localhost:8000';
  app.use(
    createProxyMiddleware(['/api', '/deployments'], {
      target,
      changeOrigin: true,
      logLevel: 'warn',
    }),
  );
};
