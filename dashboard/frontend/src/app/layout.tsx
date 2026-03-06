import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Global Sentinel | Dashboard",
  description: "Geopolitical risk intelligence & supervised execution",
  viewport: "width=device-width, initial-scale=1, maximum-scale=1",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased min-h-screen">
        {children}
      </body>
    </html>
  );
}
