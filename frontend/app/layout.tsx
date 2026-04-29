import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "@glideapps/glide-data-grid/dist/index.css";
import "./globals.css";

export const metadata: Metadata = {
	title: "Data Agentics Workbench",
	description: "IDE-style data analysis workbench backed by Python agents.",
};

const geistSans = Geist({
	subsets: ["latin"],
	display: "swap",
	variable: "--font-geist-sans",
});

const geistMono = Geist_Mono({
	subsets: ["latin"],
	display: "swap",
	variable: "--font-geist-mono",
});

export default function RootLayout({
	children,
}: Readonly<{ children: React.ReactNode }>) {
	return (
		<html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
			<body>{children}</body>
		</html>
	);
}
