import type { MemoryResult } from './types.js';

/**
 * Strip markdown formatting from text, preserving plain content.
 * Keeps pipe-delimited metadata (e.g. "| When: ... | Involving: ...").
 */
export function stripMarkdown(text: string): string {
  let result = text;

  // Remove code fences (``` blocks)
  result = result.replace(/```[\s\S]*?```/g, (match) => {
    // Extract content inside fences
    const lines = match.split('\n');
    // Drop first and last lines (the ``` markers)
    return lines.slice(1, -1).join('\n');
  });

  // Remove headers (## Header -> Header)
  result = result.replace(/^#{1,6}\s+/gm, '');

  // Remove bold (**text** or __text__)
  result = result.replace(/\*\*(.+?)\*\*/g, '$1');
  result = result.replace(/__(.+?)__/g, '$1');

  // Remove italic (*text* or _text_) — but not inside words like snake_case
  result = result.replace(/(?<!\w)\*([^*]+?)\*(?!\w)/g, '$1');
  result = result.replace(/(?<!\w)_([^_]+?)_(?!\w)/g, '$1');

  // Remove inline code (`text`)
  result = result.replace(/`([^`]+?)`/g, '$1');

  // Remove links [text](url) -> text
  result = result.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');

  // Remove image syntax ![alt](url)
  result = result.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');

  // Remove bullet points (- item or * item) at start of line -> item
  result = result.replace(/^[\t ]*[-*]\s+/gm, '');

  // Remove numbered list markers (1. item) -> item
  result = result.replace(/^[\t ]*\d+\.\s+/gm, '');

  // Remove horizontal rules (---, ***, ___)
  result = result.replace(/^[-*_]{3,}\s*$/gm, '');

  // Collapse multiple blank lines into one
  result = result.replace(/\n{3,}/g, '\n\n');

  return result.trim();
}

/**
 * Deduplicate memory results by Jaccard similarity of word tokens.
 * Results are processed in order (best-ranked first); near-duplicates are dropped.
 */
export function deduplicateByJaccard(
  results: MemoryResult[],
  threshold = 0.65,
): MemoryResult[] {
  if (results.length <= 1) return results;

  const tokenize = (text: string): Set<string> =>
    new Set(text.toLowerCase().match(/\b\w+\b/g) || []);

  const jaccard = (a: Set<string>, b: Set<string>): number => {
    if (a.size === 0 && b.size === 0) return 1;
    let intersection = 0;
    for (const token of a) {
      if (b.has(token)) intersection++;
    }
    const union = a.size + b.size - intersection;
    return union === 0 ? 0 : intersection / union;
  };

  const kept: { result: MemoryResult; tokens: Set<string> }[] = [];

  for (const result of results) {
    const tokens = tokenize(result.text);
    const isDuplicate = kept.some(
      (k) => jaccard(tokens, k.tokens) >= threshold,
    );
    if (!isDuplicate) {
      kept.push({ result, tokens });
    }
  }

  return kept.map((k) => k.result);
}

/**
 * Format a relative date string from an ISO date.
 */
function formatRelativeDate(isoDate: string): string {
  const date = new Date(isoDate);
  if (isNaN(date.getTime())) return '';

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 14) return `${diffDays}d ago`;
  if (diffDays < 60) return `${Math.floor(diffDays / 7)}w ago`;

  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  const month = months[date.getMonth()];
  const day = date.getDate();

  if (date.getFullYear() === now.getFullYear()) {
    return `${month} ${day}`;
  }
  return `${month} ${day}, ${date.getFullYear()}`;
}

const TYPE_LABELS: Record<string, string> = {
  world: 'fact',
  observation: 'insight',
  experience: 'experience',
};

/**
 * Format memory results into a compact one-line-per-memory text block.
 * Applies markdown stripping and uses relative dates.
 */
export function formatMemoriesCompact(results: MemoryResult[]): string {
  if (results.length === 0) return '';

  return results
    .map((r) => {
      const type = TYPE_LABELS[r.type] || r.type;
      const dateSource = r.occurred_start || r.mentioned_at;
      const date = dateSource ? formatRelativeDate(dateSource) : '';
      const cleanText = stripMarkdown(r.text);

      if (date) {
        return `[${type}, ${date}] ${cleanText}`;
      }
      return `[${type}] ${cleanText}`;
    })
    .join('\n');
}
