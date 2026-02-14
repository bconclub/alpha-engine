import type { Metadata } from 'next';
import './globals.css';
import { SupabaseProvider } from '@/components/providers/SupabaseProvider';
import { Sidebar } from '@/components/ui/Sidebar';

export const metadata: Metadata = {
  title: 'Alpha Dashboard — Trading Command Center',
  description: 'Real-time trading command center for Alpha crypto bot',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#0d1117] text-white antialiased">
        <SupabaseProvider>
          <div className="min-h-screen flex">
            {/* Left sidebar */}
            <Sidebar />

            {/* Main content — offset by sidebar width */}
            <main className="flex-1 ml-56 min-h-screen">
              <div className="max-w-[1920px] mx-auto px-5 py-4">
                {children}
              </div>
            </main>
          </div>
        </SupabaseProvider>
      </body>
    </html>
  );
}
