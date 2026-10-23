/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // ESLint runs in CI (npm run lint); the build itself only type-checks.
  eslint: { ignoreDuringBuilds: true },
  poweredByHeader: false,
  // The Docker image runs the smallest possible server; plain `next dev` and `next start` are unaffected.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" } : {}),
};

export default nextConfig;
