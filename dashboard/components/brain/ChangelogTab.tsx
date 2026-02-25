'use client';

import { useState, useEffect, useCallback } from 'react';
import { getSupabase } from '@/lib/supabase';
import type { ChangelogEntry, Trade } from '@/lib/types';
import { ChangelogForm } from './ChangelogForm';
import { ChangelogEntryCard } from './ChangelogEntry';

interface Props {
  trades: Trade[];
  onAnalyzeEntry?: (entry: ChangelogEntry) => void;
}

export function ChangelogTab({ trades, onAnalyzeEntry }: Props) {
  const [entries, setEntries] = useState<ChangelogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchEntries = useCallback(async () => {
    try {
      const sb = getSupabase();
      if (!sb) return;
      const { data, error } = await sb
        .from('changelog')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(100);
      if (error) throw error;
      setEntries((data as ChangelogEntry[]) || []);
    } catch (err) {
      console.error('Failed to fetch changelog:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchEntries(); }, [fetchEntries]);

  return (
    <div className="space-y-4">
      <ChangelogForm onSaved={fetchEntries} />

      {loading ? (
        <div className="text-center text-zinc-600 text-sm py-8">Loading changelog...</div>
      ) : entries.length === 0 ? (
        <div className="text-center text-zinc-600 text-sm py-8">
          No changelog entries yet. Add one above to start tracking changes.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map(entry => (
            <ChangelogEntryCard
              key={entry.id}
              entry={entry}
              trades={trades}
              onAnalyze={onAnalyzeEntry}
            />
          ))}
        </div>
      )}
    </div>
  );
}
