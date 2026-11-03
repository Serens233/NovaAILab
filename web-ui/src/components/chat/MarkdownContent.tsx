import { isValidElement, memo, useState, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { Check, Copy } from 'lucide-react';
import { copyText } from '@/utils/clipboard';

/**
 * AI 生成内容的安全 Markdown 渲染：
 *  - react-markdown 默认不渲染原始 HTML；
 *  - 再经 rehype-sanitize 白名单过滤（防 XSS）；
 *  - 链接一律新窗口打开并加 noopener / noreferrer；javascript: 等协议由默认 urlTransform 过滤。
 */
const schema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), ['className', /^language-[\w-]+$/]],
  },
};

function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return '';
}

function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const child = Array.isArray(children) ? children[0] : children;
  const className = isValidElement<{ className?: string }>(child) ? (child.props.className ?? '') : '';
  const lang = /language-([\w-]+)/.exec(className)?.[1];
  const code = textOf(children).replace(/\n$/, '');
  return (
    <div className="group/code relative my-2 overflow-hidden rounded-lg border border-white/10" style={{ background: 'var(--nova-code-bg)' }}>
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1 text-[0.7rem] text-white/60">
        <span>{lang ?? 'code'}</span>
        <button
          type="button"
          onClick={async () => {
            if (await copyText(code)) {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }
          }}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-white/10 hover:text-white focus-visible:outline-white"
          aria-label="复制代码"
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-[0.82rem] leading-relaxed" style={{ color: 'var(--nova-code-fg)', fontFamily: 'var(--nova-font-mono)' }}>
        {children}
      </pre>
    </div>
  );
}

const components: Components = {
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow">
      {children}
    </a>
  ),
  img: ({ alt }) => <span className="text-muted-foreground">[图片：{alt || '未命名'}]</span>,
};

export const MarkdownContent = memo(function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="nova-prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeSanitize, schema]]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
