import type { Metadata } from 'next';
import './globals.css';
import { SupabaseProvider } from '@/components/providers/SupabaseProvider';
import { SidebarProvider } from '@/components/providers/SidebarProvider';
import { Sidebar } from '@/components/ui/Sidebar';
import { MainContent } from '@/components/ui/MainContent';

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
      <body className="bg-[#0d1117] text-white antialiased">
        <SupabaseProvider>
          <SidebarProvider>
            <div className="min-h-screen">
              {/* Left sidebar */}
              <Sidebar />

              {/* Main content — offset by sidebar width on desktop */}
              <MainContent>{children}</MainContent>
            </div>
          </SidebarProvider>
        </SupabaseProvider>
      </body>
    </html>
  );
}
