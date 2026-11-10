const target = process.env.PRODGUILD_BACKEND_URL || 'http://127.0.0.1:18080';
module.exports = {
  '/auth/**': { target, changeOrigin: true },
  '/api/**': { target, changeOrigin: true },
  '/healthcheck': { target, changeOrigin: true },
  '/ready': { target, changeOrigin: true },
  '/metrics': { target, changeOrigin: true },
};
