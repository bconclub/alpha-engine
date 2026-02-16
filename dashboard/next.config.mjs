import { readFileSync } from 'fs';

// Read dashboard version from VERSION file (auto-bumped by CI on every push)
let appVersion = '?.?.?';
try {
  appVersion = readFileSync('./VERSION', 'utf-8').trim();
} catch {
  // Fallback to package.json version
  try {
    const pkg = JSON.parse(readFileSync('./package.json', 'utf-8'));
    appVersion = pkg.version;
  } catch { /* keep default */ }
}

// Read engine version from engine/VERSION (one level up from dashboard/)
let engineVersion = '?.?.?';
try {
  engineVersion = readFileSync('../engine/VERSION', 'utf-8').trim();
} catch { /* keep default */ }

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    APP_VERSION: appVersion,
    ENGINE_VERSION: engineVersion,
  },
};

export default nextConfig;
