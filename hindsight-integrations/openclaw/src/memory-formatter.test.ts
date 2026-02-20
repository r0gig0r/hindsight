import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  stripMarkdown,
  deduplicateByJaccard,
  formatMemoriesCompact,
} from './memory-formatter.js';
import type { MemoryResult } from './types.js';

// ---------------------------------------------------------------------------
// Helper to build a MemoryResult with defaults
// ---------------------------------------------------------------------------
function makeResult(overrides: Partial<MemoryResult> = {}): MemoryResult {
  return {
    id: 'test-id',
    text: 'Some memory text',
    type: 'world',
    entities: [],
    context: '',
    occurred_start: null,
    occurred_end: null,
    mentioned_at: null,
    document_id: null,
    metadata: null,
    chunk_id: null,
    tags: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// stripMarkdown
// ---------------------------------------------------------------------------

describe('stripMarkdown', () => {
  it('removes header markers', () => {
    expect(stripMarkdown('## Updated Knowledge')).toBe('Updated Knowledge');
    expect(stripMarkdown('# Title\n## Subtitle')).toBe('Title\nSubtitle');
    expect(stripMarkdown('### Deep header')).toBe('Deep header');
  });

  it('removes bold markers', () => {
    expect(stripMarkdown('**Key point**: Igor is CTO')).toBe(
      'Key point: Igor is CTO',
    );
    expect(stripMarkdown('__also bold__')).toBe('also bold');
  });

  it('removes italic markers', () => {
    expect(stripMarkdown('*emphasized* text')).toBe('emphasized text');
    expect(stripMarkdown('_italic_ word')).toBe('italic word');
  });

  it('does not break snake_case or words_with_underscores', () => {
    expect(stripMarkdown('use my_variable_name here')).toBe(
      'use my_variable_name here',
    );
  });

  it('removes bullet points', () => {
    expect(stripMarkdown('- Item one\n- Item two')).toBe(
      'Item one\nItem two',
    );
    expect(stripMarkdown('* Star bullet')).toBe('Star bullet');
  });

  it('removes numbered list markers', () => {
    expect(stripMarkdown('1. First\n2. Second')).toBe('First\nSecond');
  });

  it('removes inline code backticks', () => {
    expect(stripMarkdown('Use `console.log` for debug')).toBe(
      'Use console.log for debug',
    );
  });

  it('removes code fences and keeps inner content', () => {
    const input = '```python\nprint("hello")\n```';
    expect(stripMarkdown(input)).toBe('print("hello")');
  });

  it('removes links but keeps text', () => {
    expect(stripMarkdown('[Click here](https://example.com)')).toBe(
      'Click here',
    );
  });

  it('removes horizontal rules', () => {
    expect(stripMarkdown('Above\n---\nBelow')).toBe('Above\n\nBelow');
  });

  it('preserves pipe-delimited metadata', () => {
    const text = 'Igor is a CTO | Involving: Igor (CTO)';
    expect(stripMarkdown(text)).toBe(text);
  });

  it('preserves plain text unchanged', () => {
    const text = 'Igor Vaisman is the CTO of a large software company.';
    expect(stripMarkdown(text)).toBe(text);
  });

  it('collapses multiple blank lines', () => {
    expect(stripMarkdown('Line 1\n\n\n\nLine 2')).toBe('Line 1\n\nLine 2');
  });

  it('handles a real-world bloated observation', () => {
    const bloated = `## Updated Knowledge

**Key point**: Igor Vaisman is the CTO and co-owner of a large software company.

- **Involvement**: Igor
- **Context**: Reaffirms his leadership role

He reported cognitive decline at age almost 49.`;

    const cleaned = stripMarkdown(bloated);
    expect(cleaned).not.toContain('##');
    expect(cleaned).not.toContain('**');
    expect(cleaned).not.toContain('- ');
    expect(cleaned).toContain('Key point: Igor Vaisman is the CTO');
    expect(cleaned).toContain('He reported cognitive decline');
  });
});

// ---------------------------------------------------------------------------
// deduplicateByJaccard
// ---------------------------------------------------------------------------

describe('deduplicateByJaccard', () => {
  it('returns empty array for empty input', () => {
    expect(deduplicateByJaccard([])).toEqual([]);
  });

  it('returns single item unchanged', () => {
    const results = [makeResult({ text: 'Igor is the CTO' })];
    expect(deduplicateByJaccard(results)).toHaveLength(1);
  });

  it('removes near-duplicate results', () => {
    const results = [
      makeResult({ id: '1', text: 'Igor is the CTO of the company' }),
      makeResult({ id: '2', text: 'Igor is CTO of the company' }),
    ];
    const deduped = deduplicateByJaccard(results);
    expect(deduped).toHaveLength(1);
    expect(deduped[0].id).toBe('1'); // keeps first (best-ranked)
  });

  it('keeps dissimilar results', () => {
    const results = [
      makeResult({ id: '1', text: 'Igor is the CTO of a large software company' }),
      makeResult({ id: '2', text: 'Igor joined Acme in 2024 and debugged the feed reader' }),
    ];
    const deduped = deduplicateByJaccard(results);
    expect(deduped).toHaveLength(2);
  });

  it('preserves order (best-ranked first)', () => {
    const results = [
      makeResult({ id: '1', text: 'The sky is blue and clear today' }),
      makeResult({ id: '2', text: 'Weather report says it will rain tomorrow afternoon' }),
      makeResult({ id: '3', text: 'The sky is blue and clear this morning' }),
    ];
    const deduped = deduplicateByJaccard(results);
    // #3 is near-dupe of #1, should be dropped
    expect(deduped.map((r) => r.id)).toEqual(['1', '2']);
  });

  it('respects custom threshold', () => {
    const results = [
      makeResult({ id: '1', text: 'Igor is CTO' }),
      makeResult({ id: '2', text: 'Igor is the CTO of Acme' }),
    ];
    // Very high threshold — keeps both
    expect(deduplicateByJaccard(results, 0.95)).toHaveLength(2);
    // Very low threshold — drops second
    expect(deduplicateByJaccard(results, 0.3)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// formatMemoriesCompact
// ---------------------------------------------------------------------------

describe('formatMemoriesCompact', () => {
  // Fix time for date tests
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-20T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns empty string for empty results', () => {
    expect(formatMemoriesCompact([])).toBe('');
  });

  it('formats world type as fact', () => {
    const results = [
      makeResult({
        type: 'world',
        text: 'Igor is the CTO',
        occurred_start: '2026-02-20T10:00:00Z',
      }),
    ];
    expect(formatMemoriesCompact(results)).toBe('[fact, today] Igor is the CTO');
  });

  it('formats observation type as insight', () => {
    const results = [
      makeResult({
        type: 'observation',
        text: 'Igor prefers functional programming',
        occurred_start: '2026-02-19T10:00:00Z',
      }),
    ];
    expect(formatMemoriesCompact(results)).toBe(
      '[insight, yesterday] Igor prefers functional programming',
    );
  });

  it('formats experience type as experience', () => {
    const results = [
      makeResult({
        type: 'experience',
        text: 'Igor debugged the cache issue',
        occurred_start: '2026-02-17T10:00:00Z',
      }),
    ];
    expect(formatMemoriesCompact(results)).toBe(
      '[experience, 3d ago] Igor debugged the cache issue',
    );
  });

  it('uses relative dates for recent memories', () => {
    const results = [
      makeResult({
        text: 'Today event',
        occurred_start: '2026-02-20T08:00:00Z',
      }),
      makeResult({
        text: 'Yesterday event',
        occurred_start: '2026-02-19T08:00:00Z',
      }),
      makeResult({
        text: 'Five days ago',
        occurred_start: '2026-02-15T08:00:00Z',
      }),
      makeResult({
        text: 'Three weeks ago',
        occurred_start: '2026-01-30T08:00:00Z',
      }),
    ];
    const output = formatMemoriesCompact(results);
    const lines = output.split('\n');
    expect(lines[0]).toContain('today');
    expect(lines[1]).toContain('yesterday');
    expect(lines[2]).toContain('5d ago');
    expect(lines[3]).toContain('3w ago');
  });

  it('uses absolute date for older memories in current year', () => {
    // Shift fake time to June to allow >60 days within same year
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'));
    const results = [
      makeResult({
        text: 'Old event',
        occurred_start: '2026-01-15T08:00:00Z',
      }),
    ];
    const output = formatMemoriesCompact(results);
    expect(output).toContain('Jan 15');
    expect(output).not.toContain('2026');
  });

  it('includes year for memories from previous years', () => {
    const results = [
      makeResult({
        text: 'Last year event',
        occurred_start: '2025-06-10T08:00:00Z',
      }),
    ];
    const output = formatMemoriesCompact(results);
    expect(output).toContain('Jun 10, 2025');
  });

  it('falls back to mentioned_at when occurred_start is null', () => {
    const results = [
      makeResult({
        text: 'No start date',
        occurred_start: null,
        mentioned_at: '2026-02-20T08:00:00Z',
      }),
    ];
    const output = formatMemoriesCompact(results);
    expect(output).toContain('today');
  });

  it('omits date when both date fields are null', () => {
    const results = [
      makeResult({
        text: 'No date at all',
        occurred_start: null,
        mentioned_at: null,
      }),
    ];
    const output = formatMemoriesCompact(results);
    expect(output).toBe('[fact] No date at all');
  });

  it('applies markdown stripping to text', () => {
    const results = [
      makeResult({
        text: '## Updated Knowledge\n\n**Key point**: Igor is CTO',
        occurred_start: '2026-02-20T08:00:00Z',
      }),
    ];
    const output = formatMemoriesCompact(results);
    expect(output).not.toContain('##');
    expect(output).not.toContain('**');
    expect(output).toContain('Key point: Igor is CTO');
  });

  it('formats multiple results as one-per-line', () => {
    const results = [
      makeResult({ id: '1', text: 'Fact one', occurred_start: '2026-02-20T08:00:00Z' }),
      makeResult({
        id: '2',
        type: 'observation',
        text: 'Insight two',
        occurred_start: '2026-02-19T08:00:00Z',
      }),
    ];
    const lines = formatMemoriesCompact(results).split('\n');
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatch(/^\[fact, today\] Fact one$/);
    expect(lines[1]).toMatch(/^\[insight, yesterday\] Insight two$/);
  });
});
