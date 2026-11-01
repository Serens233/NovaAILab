import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
    title: "Nova · 你的 AI 工作空间",
    description: "在本机思考、创造与成长。由 Nova 统一管理模型与对话。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
    return (
        <html lang="zh-CN" suppressHydrationWarning>
            <body>{children}</body>
        </html>
    );
}
