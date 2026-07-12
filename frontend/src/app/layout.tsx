"use client";

import "./globals.css";

// Polyfill for crypto.randomUUID when the page loads over plain HTTP
// at a non-localhost origin. crypto.randomUUID is Secure-Context-only
// (HTTPS or localhost); CopilotKit calls it internally and Next 16
// throws an uncaught TypeError before hydration completes. Using
// crypto.getRandomValues, which IS available in insecure contexts,
// to produce a spec-compliant v4 UUID.
// Remove this once the app is served over HTTPS (domain + ACM).
const randomUUIDPolyfill = `(function(){
  if (typeof window === 'undefined') return;
  var c = window.crypto || window.msCrypto;
  if (!c || typeof c.randomUUID === 'function') return;
  c.randomUUID = function() {
    var b = new Uint8Array(16);
    c.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = [];
    for (var i = 0; i < 16; i++) h.push(('0' + b[i].toString(16)).slice(-2));
    return h[0]+h[1]+h[2]+h[3]+'-'+h[4]+h[5]+'-'+h[6]+h[7]+'-'+h[8]+h[9]+'-'+h[10]+h[11]+h[12]+h[13]+h[14]+h[15];
  };
})();`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: randomUUIDPolyfill }} />
        <link
          href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;700;800&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@300;500;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased overflow-hidden h-screen">
        {children}
      </body>
    </html>
  );
}
