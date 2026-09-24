import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  p: ({ children }) => <p className="leading-relaxed [&:not(:first-child)]:mt-3">{children}</p>,
  ul: ({ children }) => <ul className="mt-3 flex list-disc flex-col gap-1.5 pl-5 marker:text-faint">{children}</ul>,
  ol: ({ children }) => <ol className="mt-3 flex list-decimal flex-col gap-1.5 pl-5 marker:text-muted">{children}</ol>,
  li: ({ children }) => <li className="pl-1 leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="rounded-sm underline decoration-faint underline-offset-4 hover:decoration-ink">
      {children}
    </a>
  ),
  code: ({ children }) => <code className="rounded-md bg-sunken px-1.5 py-0.5 font-mono text-[0.9em]">{children}</code>,
  pre: ({ children }) => <pre className="mt-3 overflow-x-auto rounded-tile bg-sunken p-3 text-sm">{children}</pre>,
  h1: ({ children }) => <p className="mt-3 font-semibold">{children}</p>,
  h2: ({ children }) => <p className="mt-3 font-semibold">{children}</p>,
  h3: ({ children }) => <p className="mt-3 font-semibold">{children}</p>,
  table: ({ children }) => (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border-b border-line py-2 pr-4 font-medium">{children}</th>,
  td: ({ children }) => <td className="border-b border-line py-2 pr-4 tabular-nums">{children}</td>,
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="text-base text-ink sm:text-lg">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml disallowedElements={["img"]} unwrapDisallowed>
        {text}
      </ReactMarkdown>
    </div>
  );
}
