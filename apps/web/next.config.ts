import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // Monorepo root, so the demo seed can import /samples.
  turbopack: { root: path.join(__dirname, "../..") },
};

export default nextConfig;
