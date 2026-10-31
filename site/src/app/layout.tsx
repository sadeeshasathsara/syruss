import type { Metadata, Viewport } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

const plex = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Syruss: drop the page, data takes the stage", template: "%s · Syruss" },
  description:
    "Syruss reads your receipts, invoices, forms and any other documents, keeps every file safe, and sends the details to the systems you already use.",
};

export const viewport: Viewport = { themeColor: "#0b0b0c", colorScheme: "dark light" };

// Runs before the page paints: marks that script is available, and applies a saved light choice (dark is the default).
const BOOT = "document.documentElement.classList.add('js');try{if(localStorage.getItem('syruss-theme')==='light')document.documentElement.dataset.theme='light'}catch(e){}";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The boot script changes <html> before React loads, and extensions such as Grammarly add attributes to <body>;
    // both would otherwise be reported as hydration mismatches.
    <html lang="en" className={plex.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: BOOT }} />
      </head>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
