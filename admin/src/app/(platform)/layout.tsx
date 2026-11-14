import { AdminFrame } from "@/components/shell/AdminFrame";

export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  return <AdminFrame>{children}</AdminFrame>;
}
