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

Alpha is a high-frequency scalp trading bot on Delta Exchange India. It trades BTC, ETH, XRP, SOL perpetual futures with 20-50x leverage. Trades last 30s to 30min. Entry: 11-signal gate (3/4 minimum — momentum, volume, RSI, BB, VWAP, etc). Exit: 3-phase system (Phase 1 hands-off, Phase 2 trailing, Phase 3 cut).

Key context:
- P&L is in USD (small amounts, $0.01-$2.00 per trade due to small capital)
- At 20x leverage, a 0.30% SL hit = 6% capital loss per trade
- Fee drag is REAL: ~0.083% round trip (entry + exit) at 20x = 1.66% capital per trade
- Exit types: TRAIL (trailing stop), SL (stop loss), FLAT (flatline timeout), TP (take profit), TIMEOUT (max hold), BREAKEVEN, MOMENTUM_FADE (momentum fading while in profit), DEAD_MOMENTUM (dead momentum while losing), REVERSAL
- A good trade: 3+/4 signals, trails to +0.5-2% profit, exits TRAIL
- A bad trade: weak confluence, hits SL quickly
- The bot has been through 19+ GPFCs (General Purpose Fixes/Changes)

You will receive RAW trade data — every row from the database. Find the patterns yourself.

Format your response EXACTLY as:

## WHAT'S WORKING
Which exits are profitable? Which pairs? Which setups? Which hours? Be specific with numbers.

## WHAT'S BLEEDING
Biggest loss categories. Fee drag analysis. Bad patterns. Pair-specific issues. Quantify everything.

## SPECIFIC FIXES
Parameter changes with EXACT values. Format each as: "Change X from Y to Z because [data-backed reason]"

## RISK SCORE
Single number 1-10. 1 = extremely safe/profitable. 10 = about to blow up. Explain briefly.`;

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
      breakdowns,
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

    // Current config snapshot
    if (current_params) {
      userPrompt += `## Current Bot Parameters\n${JSON.stringify(current_params, null, 2)}\n\n`;
    }

    // Recent changelog
    if (changelog.length > 0 && analysis_type !== 'changelog_impact') {
      userPrompt += `## Recent Changelog\n`;
      for (const c of changelog.slice(0, 10)) {
        userPrompt += `- [${c.change_type}] ${c.title} (${c.version || 'N/A'}) — ${c.status} ${c.deployed_at ? `on ${new Date(c.deployed_at).toLocaleDateString()}` : ''}\n`;
      }
      userPrompt += '\n';
    }

    // Pre-computed breakdowns
    if (breakdowns) {
      userPrompt += `## Pre-Computed Breakdowns\n`;
      if (breakdowns.by_pair) {
        userPrompt += `### Win Rate by Pair\n${JSON.stringify(breakdowns.by_pair, null, 2)}\n\n`;
      }
      if (breakdowns.by_exit) {
        userPrompt += `### Win Rate by Exit Type\n${JSON.stringify(breakdowns.by_exit, null, 2)}\n\n`;
      }
      if (breakdowns.by_hour) {
        userPrompt += `### Win Rate by Hour (UTC)\n${JSON.stringify(breakdowns.by_hour, null, 2)}\n\n`;
      }
      if (breakdowns.fee_analysis) {
        userPrompt += `### Fee Analysis\n${JSON.stringify(breakdowns.fee_analysis, null, 2)}\n\n`;
      }
    }

    // Raw trade data
    if (trades.length > 0) {
      const closed = trades.filter((t: any) => t.status === 'closed');
      const wins = closed.filter((t: any) => t.pnl >= 0).length;
      const totalPnl = closed.reduce((s: number, t: any) => s + t.pnl, 0);

      userPrompt += `## Aggregate Stats (${closed.length} closed trades)\n`;
      userPrompt += `- Win rate: ${closed.length > 0 ? ((wins / closed.length) * 100).toFixed(1) : 0}%\n`;
      userPrompt += `- Total PnL: $${totalPnl.toFixed(4)}\n`;
      userPrompt += `- Avg PnL: $${closed.length > 0 ? (totalPnl / closed.length).toFixed(4) : 0}\n\n`;

      // Expanded trade table with entry/exit prices, gross, fees, peak
      userPrompt += `## Raw Trade Data (last ${Math.min(trades.length, 100)})\n`;
      userPrompt += `| # | Pair | Side | Entry | Exit | Net PnL | PnL% | Gross | Fees | Peak% | Exit Reason | Hold(s) | Setup | Lev |\n`;
      userPrompt += `|---|------|------|-------|------|---------|------|-------|------|-------|-------------|---------|-------|-----|\n`;
      for (let i = 0; i < Math.min(trades.length, 100); i++) {
        const t = trades[i];
        const hold = t.closed_at && t.timestamp
          ? Math.round((new Date(t.closed_at).getTime() - new Date(t.timestamp).getTime()) / 1000)
          : '?';
        const fees = ((t.entry_fee || 0) + (t.exit_fee || 0)).toFixed(4);
        userPrompt += `| ${i + 1} | ${t.pair?.split('/')[0] || '?'} | ${t.position_type || t.side} | ${t.price?.toFixed(2) || '?'} | ${t.exit_price?.toFixed(2) || '?'} | $${t.pnl?.toFixed(4) || '?'} | ${t.pnl_pct?.toFixed(2) || '?'}% | $${(t.gross_pnl ?? t.pnl)?.toFixed(4) || '?'} | $${fees} | ${t.peak_pnl?.toFixed(2) || '?'}% | ${t.exit_reason || '?'} | ${hold} | ${t.setup_type || '-'} | ${t.leverage || 1}x |\n`;
      }
      userPrompt += '\n';
    }

    userPrompt += 'Analyze the data above. Be specific and data-driven.';

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
        max_tokens: 3000,
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

    // Extract summary from WHAT'S WORKING section
    const summaryMatch = analysisText.match(/## WHAT'S WORKING\n+([\s\S]*?)(?=\n## |$)/);
    const summary = summaryMatch ? summaryMatch[1].trim().slice(0, 300) : analysisText.slice(0, 200);

    // Extract recommendations from SPECIFIC FIXES
    let recommendations = null;
    const recsMatch = analysisText.match(/## SPECIFIC FIXES\n+([\s\S]*?)(?=\n## |$)/);
    if (recsMatch) {
      const lines = recsMatch[1].trim().split('\n').filter((l: string) =>
        l.startsWith('- ') || l.startsWith('* ') || /^\d+\./.test(l),
      );
      recommendations = lines.map((l: string) => {
        const text = l.replace(/^[-*\d.)\s]+/, '');
        const priority = text.match(/\**(HIGH|MEDIUM|LOW)\**/i)?.[1]?.toLowerCase() || 'medium';
        return { action: text, priority, reason: '' };
      });
    }

    // Extract risk score
    const riskMatch = analysisText.match(/## RISK SCORE\n+(\d+)/);
    const riskScore = riskMatch ? parseInt(riskMatch[1]) : null;

    // Save to database
    const analysisRecord = {
      changelog_entry_id: changelog_entry_id || null,
      analysis_type,
      prompt_context: {
        trades_count: trades.length,
        changelog_count: changelog.length,
        has_breakdowns: !!breakdowns,
        has_config: !!current_params,
        risk_score: riskScore,
      },
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
        risk_score: riskScore,
      },
    });
  } catch (err: any) {
    console.error('[Brain API] Error:', err);
    return NextResponse.json({ error: err.message || 'Internal error' }, { status: 500 });
  }
}
