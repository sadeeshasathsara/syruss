import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/format";

/**
 * Syruss writes GitHub-flavoured markdown (headings, lists, tables, code). Links to other sites open
 * in a new tab; wide tables scroll inside their own box. Styles are the `.md` rules in globals.css.
 */
export const markdownComponents = {
  a: ({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const external = !!href && /^https?:/i.test(href);
    return (
      <a href={href} {...rest} {...(external ? { target: "_blank", rel: "noreferrer noopener" } : {})}>
        {children}
      </a>
    );
  },
  table: ({ children }: React.TableHTMLAttributes<HTMLTableElement>) => (
    <div className="md-table">
      <table>{children}</table>
    </div>
  ),
};

export const remarkPlugins = [remarkGfm];

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("md text-body text-ink", className)}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

/** Markdown reduced to its words, for one- or two-line previews. */
export function plainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~>|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
