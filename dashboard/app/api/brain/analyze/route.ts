import { NextRequest, NextResponse } from 'next/server';
import { getServerSupabase } from '@/lib/supabase-server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Simple in-memory rate limiter (10 requests/hour)
const rateMap = new Map<string, number[]>();
const RATE_LIMIT = 10;
const RATE_WINDOW_MS = 60 * 60 * 1000; // 1 hour

function checkRateLimit(ip: string): boolean {
  const now = Date.now();
  const timestamps = rateMap.get(ip) || [];
  const recent = timestamps.filter(t => now - t < RATE_WINDOW_MS);
  rateMap.set(ip, recent);
  if (recent.length >= RATE_LIMIT) return false;
  recent.push(now);
  return true;
}

// ── System Prompt ──────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are Alpha Brain, the analytical intelligence layer for the Alpha crypto trading bot.

Alpha is a high-frequency scalp trading bot running on Delta Exchange India. It trades BTC, ETH, XRP, and SOL perpetual futures with 20-50x leverage. Trades last 30 seconds to 30 minutes. The bot uses a multi-signal entry system (momentum, volume, RSI, Bollinger Bands, VWAP, etc.) and a phased exit system (Phase 1 hands-off, Phase 2 trailing, Phase 3 cut).

Your role is to:
1. Analyze trade performance data and identify patterns
2. Evaluate the impact of parameter/strategy changes (GPFCs)
3. Provide actionable recommendations for improving bot performance
4. Be direct and quantitative — use numbers, not vague language

Key context:
- P&L is in USD (small amounts, $0.01-$2.00 per trade due to small capital)
- Win rate matters more than individual trade size at 20-50x leverage
- Exit reasons: TRAIL (trailing stop), SL (stop loss), FLAT (flatline timeout), TP (take profit), TIMEOUT (max hold), BE (breakeven), MOM_FADE (momentum fade), DEAD_MOM (dead momentum), REV (reversal)
- A good trade: enters on 3+/4 signals, trails to +0.5-2% profit, exits TRAIL
- A bad trade: enters on weak signals, hits SL quickly (-0.3% loss * 20x = -6% capital)
- The bot has been through 17+ GPFCs (General Purpose Fixes/Changes)

Format your response in markdown with:
## Key Findings
(3-5 bullet points with specific numbers)

## Impact Assessment
(if analyzing a specific change — was it positive or negative?)

## Recommendations
(actionable items with priority: HIGH/MEDIUM/LOW)

## Risk Alerts
(anything concerning that needs attention)`;

// ── Route Handler ──────────────────────────────────────────────────────────────

export async function POST(req: NextRequest) {
  const ip = req.headers.get('x-forwarded-for') || 'unknown';
  if (!checkRateLimit(ip)) {
    return NextResponse.json({ error: 'Rate limit exceeded (10/hour)' }, { status: 429 });
  }

  const apiKey = process.env.CLAUDE_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: 'CLAUDE_API_KEY not configured' }, { status: 500 });
  }

  const db = getServerSupabase();

  try {
    const body = await req.json();
    const {
      analysis_type = 'general',
      changelog_entry_id,
      trades = [],
      changelog = [],
      current_params,
      snapshots,
    } = body;

    // Build user prompt
    let userPrompt = '';

    if (analysis_type === 'changelog_impact' && changelog.length > 0) {
      const entry = changelog[0];
      userPrompt += `Analyze the impact of this specific change:\n\n`;
      userPrompt += `## Change\nTitle: ${entry.title}\nType: ${entry.change_type}\nVersion: ${entry.version || 'N/A'}\nDeployed: ${entry.deployed_at || 'N/A'}\nDescription: ${entry.description || 'No description'}\n\n`;

      if (entry.parameters_before || entry.parameters_after) {
        userPrompt += `## Parameters Changed\nBEFORE: ${JSON.stringify(entry.parameters_before, null, 2)}\nAFTER: ${JSON.stringify(entry.parameters_after, null, 2)}\n\n`;
      }

      if (snapshots) {
        userPrompt += `## Performance Comparison (${snapshots.before.trade_count} trades before vs ${snapshots.after.trade_count} trades after)\n`;
        userPrompt += `BEFORE: WR ${snapshots.before.win_rate.toFixed(1)}%, Avg PnL $${snapshots.before.avg_pnl.toFixed(4)}, Avg Hold ${snapshots.before.avg_hold_seconds}s, Exits: ${JSON.stringify(snapshots.before.exit_breakdown)}\n`;
        userPrompt += `AFTER:  WR ${snapshots.after.win_rate.toFixed(1)}%, Avg PnL $${snapshots.after.avg_pnl.toFixed(4)}, Avg Hold ${snapshots.after.avg_hold_seconds}s, Exits: ${JSON.stringify(snapshots.after.exit_breakdown)}\n\n`;
      }
    } else {
      userPrompt += `Analyze the current performance of my trading bot.\n\n`;
    }

    if (current_params) {
      userPrompt += `## Current Parameters\n${JSON.stringify(current_params, null, 2)}\n\n`;
    }

    if (changelog.length > 0 && analysis_type !== 'changelog_impact') {
      userPrompt += `## Recent Changelog\n`;
      for (const c of changelog.slice(0, 10)) {
        userPrompt += `- [${c.change_type}] ${c.title} (${c.version || 'N/A'}) — ${c.status} ${c.deployed_at ? `on ${new Date(c.deployed_at).toLocaleDateString()}` : ''}\n`;
      }
      userPrompt += '\n';
    }

    if (trades.length > 0) {
      // Aggregate stats
      const closed = trades.filter((t: any) => t.status === 'closed');
      const wins = closed.filter((t: any) => t.pnl >= 0).length;
      const totalPnl = closed.reduce((s: number, t: any) => s + t.pnl, 0);

      userPrompt += `## Aggregate Stats (${closed.length} closed trades)\n`;
      userPrompt += `- Win rate: ${closed.length > 0 ? ((wins / closed.length) * 100).toFixed(1) : 0}%\n`;
      userPrompt += `- Total PnL: $${totalPnl.toFixed(4)}\n`;
      userPrompt += `- Avg PnL: $${closed.length > 0 ? (totalPnl / closed.length).toFixed(4) : 0}\n\n`;

      // Trade table (last 100)
      userPrompt += `## Last ${Math.min(trades.length, 100)} Trades\n`;
      userPrompt += `| # | Pair | Side | PnL | PnL% | Exit | Hold(s) | Setup | Leverage |\n`;
      userPrompt += `|---|------|------|-----|------|------|---------|-------|----------|\n`;
      for (let i = 0; i < Math.min(trades.length, 100); i++) {
        const t = trades[i];
        const hold = t.closed_at && t.timestamp
          ? Math.round((new Date(t.closed_at).getTime() - new Date(t.timestamp).getTime()) / 1000)
          : '?';
        userPrompt += `| ${i + 1} | ${t.pair?.split('/')[0] || '?'} | ${t.position_type || t.side} | $${t.pnl?.toFixed(4) || '?'} | ${t.pnl_pct?.toFixed(2) || '?'}% | ${t.exit_reason || '?'} | ${hold} | ${t.setup_type || '-'} | ${t.leverage || 1}x |\n`;
      }
      userPrompt += '\n';
    }

    userPrompt += 'What patterns do you see? What should I change next?';

    // Call Claude API
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 2048,
        system: SYSTEM_PROMPT,
        messages: [{ role: 'user', content: userPrompt }],
      }),
    });

    if (!response.ok) {
      const errText = await response.text();
      console.error('[Brain API] Claude error:', response.status, errText);
      return NextResponse.json({ error: `Claude API error: ${response.status}` }, { status: 502 });
    }

    const data = await response.json();
    const analysisText = data.content?.[0]?.text || 'No response from Claude';
    const inputTokens = data.usage?.input_tokens || 0;
    const outputTokens = data.usage?.output_tokens || 0;

    // Extract summary (first paragraph or first 200 chars)
    const summaryMatch = analysisText.match(/## Key Findings\n+([\s\S]*?)(?=\n## |$)/);
    const summary = summaryMatch ? summaryMatch[1].trim().slice(0, 300) : analysisText.slice(0, 200);

    // Try to extract structured recommendations
    let recommendations = null;
    const recsMatch = analysisText.match(/## Recommendations\n+([\s\S]*?)(?=\n## |$)/);
    if (recsMatch) {
      const lines = recsMatch[1].trim().split('\n').filter((l: string) => l.startsWith('- ') || l.startsWith('* '));
      recommendations = lines.map((l: string) => {
        const text = l.replace(/^[-*]\s*/, '');
        const priority = text.match(/\**(HIGH|MEDIUM|LOW)\**/i)?.[1]?.toLowerCase() || 'medium';
        return { action: text, priority, reason: '' };
      });
    }

    // Save to database
    const analysisRecord = {
      changelog_entry_id: changelog_entry_id || null,
      analysis_type,
      prompt_context: { trades_count: trades.length, changelog_count: changelog.length },
      model_used: 'claude-sonnet-4-20250514',
      analysis_text: analysisText,
      summary,
      recommendations,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      triggered_by: 'manual',
    };

    let savedId = null;
    if (db) {
      const { data: inserted, error: dbErr } = await db
        .from('alpha_analysis')
        .insert(analysisRecord)
        .select('id')
        .single();
      if (dbErr) {
        console.error('[Brain API] DB save error:', dbErr);
      } else {
        savedId = inserted?.id;
      }
    }

    return NextResponse.json({
      analysis: {
        id: savedId,
        analysis_text: analysisText,
        summary,
        recommendations,
        model_used: 'claude-sonnet-4-20250514',
        input_tokens: inputTokens,
        output_tokens: outputTokens,
      },
    });
  } catch (err: any) {
    console.error('[Brain API] Error:', err);
    return NextResponse.json({ error: err.message || 'Internal error' }, { status: 500 });
  }
}
