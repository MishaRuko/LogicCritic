import type { NextConfig } from 'next';
const backend = (process.env.LOGICCRITIC_BACKEND_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const config: NextConfig = {
  output: 'standalone',
  turbopack: { root: import.meta.dirname },
  devIndicators: false,
  // Synthesis performs two model passes, each with a 90-second backend timeout.
  experimental: { proxyTimeout: 240_000, proxyClientMaxBodySize: '101mb' },
  async rewrites() {
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/docs', destination: `${backend}/docs` },
      { source: '/openapi.json', destination: `${backend}/openapi.json` },
    ];
  },
};
export default config;
