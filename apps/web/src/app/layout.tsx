import type { Metadata, Viewport } from "next";
import { Manrope } from "next/font/google";

import { AppShell } from "@/components/shell/AppShell";

import "./globals.css";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
});

const description =
  "Understand your extended health or private health insurance benefits. Redaction happens in your browser; only the images you approve are sent.";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://benefura.com"),
  title: { default: "Benefura", template: "%s · Benefura" },
  description,
  applicationName: "Benefura",
  openGraph: { type: "website", siteName: "Benefura", title: "Benefura", description },
  twitter: { card: "summary_large_image", title: "Benefura", description },
};

export const viewport: Viewport = {
  themeColor: "#ececef",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${manrope.variable} h-full`}>
      <body className="min-h-full">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-white"
        >
          Skip to content
        </a>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
