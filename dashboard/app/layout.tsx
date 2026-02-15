import type { Metadata } from 'next';
import './globals.css';
import { SupabaseProvider } from '@/components/providers/SupabaseProvider';
import { Sidebar } from '@/components/ui/Sidebar';

export const metadata: Metadata = {
  title: 'Alpha Dashboard — Trading Command Center',
  description: 'Real-time trading command center for Alpha crypto bot',
  icons: {
    icon: '/icon.png',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#0d1117] text-white antialiased overflow-x-hidden">
        <SupabaseProvider>
          <div className="min-h-screen flex overflow-x-hidden">
            {/* Left sidebar */}
            <Sidebar />

            {/* Main content — offset by sidebar width on desktop */}
            <main className="flex-1 ml-0 md:ml-56 min-h-screen pt-14 md:pt-0 pb-16 md:pb-0">
              <div className="max-w-[1920px] mx-auto px-3 py-3 md:px-5 md:py-4">
                {children}
              </div>
            </main>
          </div>
        </SupabaseProvider>
      </body>
    </html>
  );
}
