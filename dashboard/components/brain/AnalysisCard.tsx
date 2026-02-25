'use client';

import type { AlphaAnalysis } from '@/lib/types';

interface Props {
  analysis: AlphaAnalysis | { analysis_text: string; summary?: string | null; model_used: string; input_tokens?: number | null; output_tokens?: number | null; created_at?: string };
}

export function AnalysisCard({ analysis }: Props) {
  const createdAt = analysis.created_at
    ? new Date(analysis.created_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : 'Just now';

  const tokens = (analysis.input_tokens || 0) + (analysis.output_tokens || 0);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-zinc-800/50">
        <span className="text-[10px] text-zinc-500">{createdAt}</span>
        <span className="text-[10px] font-mono text-zinc-600">{analysis.model_used}</span>
        {tokens > 0 && (
          <span className="text-[10px] font-mono text-zinc-700">{tokens.toLocaleString()} tokens</span>
        )}
      </div>

      {/* Markdown content */}
      <div className="px-4 py-3 prose prose-invert prose-sm max-w-none
        prose-headings:text-white prose-headings:text-sm prose-headings:font-bold prose-headings:mt-4 prose-headings:mb-2
        prose-p:text-zinc-300 prose-p:text-xs prose-p:leading-relaxed
        prose-li:text-zinc-300 prose-li:text-xs
        prose-strong:text-white prose-strong:font-semibold
        prose-code:text-blue-400 prose-code:text-[11px]
        prose-table:text-xs
        prose-th:text-zinc-400 prose-th:font-medium prose-th:text-left prose-th:pb-2
        prose-td:text-zinc-300 prose-td:py-1
      ">
        {/* Simple markdown rendering — no react-markdown dep needed for MVP */}
        {analysis.analysis_text.split('\n').map((line, i) => {
          // Headers
          if (line.startsWith('## ')) return <h2 key={i}>{line.slice(3)}</h2>;
          if (line.startsWith('### ')) return <h3 key={i}>{line.slice(4)}</h3>;
          // List items
          if (line.startsWith('- ') || line.startsWith('* ')) {
            const text = line.slice(2);
            return (
              <div key={i} className="flex gap-2 py-0.5">
                <span className="text-zinc-600 flex-shrink-0">&bull;</span>
                <span className="text-xs text-zinc-300">{renderInline(text)}</span>
              </div>
            );
          }
          // Numbered list
          const numMatch = line.match(/^(\d+)\.\s+(.+)/);
          if (numMatch) {
            return (
              <div key={i} className="flex gap-2 py-0.5">
                <span className="text-zinc-600 flex-shrink-0 text-xs font-mono">{numMatch[1]}.</span>
                <span className="text-xs text-zinc-300">{renderInline(numMatch[2])}</span>
              </div>
            );
          }
          // Empty line
          if (!line.trim()) return <div key={i} className="h-2" />;
          // Normal text
          return <p key={i} className="text-xs text-zinc-300 leading-relaxed">{renderInline(line)}</p>;
        })}
      </div>
    </div>
  );
}

/** Minimal inline markdown: **bold**, `code`, *italic* */
function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let remaining = text;
  let key = 0;

  while (remaining.length > 0) {
    // Bold
    const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
    // Code
    const codeMatch = remaining.match(/`(.+?)`/);

    const matches = [
      boldMatch ? { idx: boldMatch.index!, len: boldMatch[0].length, node: <strong key={key++} className="text-white font-semibold">{boldMatch[1]}</strong>, content: boldMatch[1] } : null,
      codeMatch ? { idx: codeMatch.index!, len: codeMatch[0].length, node: <code key={key++} className="text-blue-400 text-[11px] bg-blue-500/10 px-1 rounded">{codeMatch[1]}</code>, content: codeMatch[1] } : null,
    ].filter(Boolean).sort((a, b) => a!.idx - b!.idx);

    if (matches.length === 0) {
      parts.push(remaining);
      break;
    }

    const first = matches[0]!;
    if (first.idx > 0) parts.push(remaining.slice(0, first.idx));
    parts.push(first.node);
    remaining = remaining.slice(first.idx + first.len);
  }

  return <>{parts}</>;
}
