import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

type MarkdownContentProps = {
  content: string;
  className?: string;
};

export function MarkdownContent({ content, className = "" }: MarkdownContentProps) {
  const classes = ["markdown-content", className].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
        {normalizeMathDelimiters(content)}
      </ReactMarkdown>
    </div>
  );
}

function normalizeMathDelimiters(content: string): string {
  const normalizedLines = content
    .split("\n")
    .map((line) => {
      const latexResult = line.match(/^(\s*LaTeX result:\s*)(.+?)\s*$/);
      if (!latexResult) {
        return line;
      }

      const expression = latexResult[2].trim();
      if (isWrappedMath(expression)) {
        return line;
      }

      return `${latexResult[1]}\n$$\n${expression}\n$$`;
    })
    .join("\n");

  return normalizedLines
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, expression: string) => `\n$$\n${expression.trim()}\n$$\n`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, expression: string) => `$${expression.trim()}$`);
}

function isWrappedMath(expression: string): boolean {
  return (
    expression.startsWith("$") ||
    expression.startsWith("\\(") ||
    expression.startsWith("\\[")
  );
}
