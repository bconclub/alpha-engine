import { readFileSync } from 'fs';

// Single Alpha version — read from engine/VERSION (one level up from dashboard/)
let alphaVersion = '?.?.?';
try {
  alphaVersion = readFileSync('../engine/VERSION', 'utf-8').trim();
} catch {
  // Fallback: try local VERSION if it exists (legacy)
  try {
    alphaVersion = readFileSync('./VERSION', 'utf-8').trim();
  } catch { /* keep default */ }
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    ALPHA_VERSION: alphaVersion,
    // Keep legacy env vars pointing to the same value for compat
    APP_VERSION: alphaVersion,
    ENGINE_VERSION: alphaVersion,
  },
};

export default nextConfig;
