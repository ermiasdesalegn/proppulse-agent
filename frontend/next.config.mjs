/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // ESLint runs in CI (npm run lint); the build itself only type-checks.
  eslint: { ignoreDuringBuilds: true },
  poweredByHeader: false,
  // A separate build folder (NEXT_DIST_DIR) lets a production build run while a dev server is open.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The Docker image runs the smallest possible server; plain `next dev` and `next start` are unaffected.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" } : {}),
};

export default nextConfig;
