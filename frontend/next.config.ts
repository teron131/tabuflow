import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));

const nextConfig: NextConfig = {
	devIndicators: false,
	reactStrictMode: true,
	turbopack: {
		root: frontendRoot,
	},
};

export default nextConfig;
