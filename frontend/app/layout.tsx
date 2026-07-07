import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "OceanPing — Coastal Hazard Intelligence",
  description:
    "Crowdsourced coastal hazard reports, cross-verified against live ocean instruments.",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <div className="brand">
            Ocean<span>Ping</span>
          </div>
          <nav>
            <Link href="/">Live map</Link>
            <Link href="/report">Report a hazard</Link>
            <Link href="/analyst">Analyst</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
