import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/format";

/** The site a link really goes to, when it isn't this app (shown beside the link's words, so none can disguise it). */
function otherSite(href: string): string | null {
  try {
    const url = new URL(href, window.location.origin);
    return url.origin === window.location.origin ? null : url.host;
  } catch {
    return null;
  }
}

/**
 * Syruss writes GitHub-flavoured markdown (headings, lists, tables, code). Links to other sites open
 * in a new tab; wide tables scroll inside their own box. Styles are the `.md` rules in globals.css.
 *
 * What the agent writes can have been steered by a document it read, so: images are never loaded (an image's
 * address can carry data out the moment it loads; its description is shown instead), and a link to another
 * site says which site, sends no referrer and opens in a new tab.
 */
export const markdownComponents = {
  a: ({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const site = href && typeof window !== "undefined" ? otherSite(href) : null;
    const text = typeof children === "string" ? children : null;
    return (
      <a href={href} {...rest} {...(site ? { target: "_blank", rel: "noreferrer noopener nofollow", title: href } : {})}>
        {children}
        {site && text !== href && <span className="text-muted"> ({site})</span>}
      </a>
    );
  },
  img: ({ alt }: React.ImgHTMLAttributes<HTMLImageElement>) => <span className="text-muted">[{alt || "image"}]</span>,
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
