import type { NextConfig } from 'next';

// Local `next dev` reads the repo-root .env (CLAUDE_API_KEY for /experiment-api). In Docker there is no such file;
// compose passes the key in. Values already in the environment are not overridden.
try { process.loadEnvFile(new URL('../.env', import.meta.url)); } catch { /* no root .env */ }
const backend = (process.env.LOGICCRITIC_BACKEND_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const config: NextConfig = {
  output: 'standalone',
  turbopack: { root: import.meta.dirname },
  devIndicators: false,
  // Synthesis performs two model passes, each with a 90-second backend timeout.
  experimental: { proxyTimeout: 240_000, proxyClientMaxBodySize: '11mb' },
  async rewrites() { return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }, { source: '/docs', destination: `${backend}/docs` }, { source: '/openapi.json', destination: `${backend}/openapi.json` }]; },
};
export default config;
